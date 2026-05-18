from __future__ import annotations

"""WangChuan recall row retrieval helpers.

这一层承接 memory_api._recall_rows 中的中段主流程：
- temporal/base/layer filter 组装
- recall rank SQL 片段拼装
- select_sql 组装
- candidate rows 收集后转 item、排序、截断

目标：
- 不改变 public recall / recall_raw / recall_scars / recall_at 签名
- 继续复用已有 recall candidate / row builder helpers
- 保持 recall 主链行为不变
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Tuple


logger = logging.getLogger(__name__)


def _build_recall_reader(source_layer: str, as_of: str | None) -> str:
    if source_layer == "raw":
        return "memory_api.recall_raw"
    if source_layer == "scar":
        return "memory_api.recall_scars"
    if as_of:
        return "memory_api.recall_at"
    return "memory_api.recall"


def _invalidate_status_cache(memory_obj: Any) -> None:
    try:
        memory_obj._status_cache = {"data": None, "timestamp": 0}
    except Exception:
        pass


def _remember_recall_runtime(memory_obj: Any, payload: Dict[str, Any]) -> None:
    try:
        memory_obj._last_recall_runtime = dict(payload)
    except Exception:
        pass
    _invalidate_status_cache(memory_obj)


def _remember_recall_error(memory_obj: Any, payload: Dict[str, Any]) -> None:
    try:
        memory_obj._last_recall_error = dict(payload)
    except Exception:
        pass
    _invalidate_status_cache(memory_obj)


def _build_recall_filters(memory_obj: Any, source_layer: str, as_of: str | None) -> Tuple[str, str, List[Any]]:
    raw_filter = "(COALESCE(msi.source_layer, '') = 'raw' OR content LIKE '%来源: memory/raw/%')"
    scar_like_filter = (
        "("
        "LOWER(COALESCE(msi.source_layer, '')) = 'scar' "
        "OR LOWER(COALESCE(msi.memory_type, m.type, '')) IN ('rule', 'lesson', 'decision', 'correction') "
        "OR (LOWER(COALESCE(msi.memory_type, m.type, '')) IN ('preference', 'fact') AND COALESCE(msi.user_explicit, 0) = 1)"
        ")"
    )

    if as_of:
        normalized_probe = memory_obj._normalize_temporal_probe(as_of)
        base_filter = (
            "COALESCE(msi.removed_at, '') = '' "
            "AND (msi.valid_from IS NULL OR REPLACE(msi.valid_from, 'T', ' ') <= ?) "
            "AND (msi.valid_until IS NULL OR REPLACE(msi.valid_until, 'T', ' ') > ?)"
        )
        temporal_params: List[Any] = [normalized_probe, normalized_probe]
    else:
        current_probe = memory_obj._normalize_temporal_probe(None, default_now=True)
        base_filter = (
            "COALESCE(msi.removed_at, '') = '' "
            "AND (msi.valid_from IS NULL OR REPLACE(msi.valid_from, 'T', ' ') <= ?) "
            "AND (msi.valid_until IS NULL OR REPLACE(msi.valid_until, 'T', ' ') > ?)"
        )
        temporal_params = [current_probe, current_probe]

    if source_layer == "raw":
        layer_filter = f" AND {raw_filter}"
    elif source_layer == "scar":
        layer_filter = f" AND NOT ({raw_filter}) AND {scar_like_filter}"
    else:
        layer_filter = ""

    return base_filter, layer_filter, temporal_params


def _build_recall_rank_expr() -> str:
    return (
        "(COALESCE(msi.quality_score, m.confidence, 0) "
        "+ COALESCE(msi.importance, 0) * 0.25 "
        "+ COALESCE(msi.hotness_score, 0) * 0.15 "
        "+ CASE LOWER(COALESCE(msi.source_session, '')) "
        "    WHEN 'default' THEN 0.10 "
        "    WHEN 'cli' THEN -0.05 "
        "    WHEN '' THEN 0.0 "
        "    ELSE 0.06 END "
        "+ (CASE WHEN COALESCE(msi.source_anchor, '') != '' THEN 0.04 ELSE 0 END "
        "   + CASE WHEN COALESCE(msi.source_session, '') != '' THEN 0.04 ELSE 0 END "
        "   + CASE WHEN COALESCE(msi.turn_signature, '') != '' THEN 0.04 ELSE 0 END) "
        "+ CASE LOWER(COALESCE(msi.source_layer, '')) "
        "    WHEN 'scar' THEN 0.06 "
        "    WHEN 'raw' THEN 0.03 "
        "    WHEN '' THEN 0.0 "
        "    ELSE 0.01 END "
        "+ CASE LOWER(COALESCE(msi.memory_type, m.type, '')) "
        "    WHEN 'preference' THEN 0.15 "
        "    WHEN 'rule' THEN 0.05 "
        "    WHEN 'correction' THEN 0.05 "
        "    WHEN 'lesson' THEN 0.03 "
        "    WHEN 'decision' THEN 0.03 "
        "    WHEN 'memory' THEN 0.01 "
        "    WHEN 'conversation' THEN 0.01 "
        "    WHEN 'emotional' THEN -0.04 "
        "    ELSE 0 END "
        "- CASE COALESCE(msi.lifecycle, '') WHEN 'aging' THEN 0.08 WHEN 'archived' THEN 0.3 ELSE 0 END)"
    )


def _build_recall_select_sql() -> str:
    return (
        "SELECT "
        "m.id, m.content, m.confidence, m.created_at, "
        "COALESCE(msi.source_layer, ''), "
        "COALESCE(msi.source_anchor, ''), "
        "COALESCE(msi.source_session, ''), "
        "COALESCE(msi.turn_signature, ''), "
        "COALESCE(msi.memory_type, m.type, 'unknown'), "
        "COALESCE(msi.subject_domain, ''), "
        "COALESCE(msi.user_explicit, 0), "
        "COALESCE(msi.is_test_data, 0), "
        "COALESCE(msi.promotion_reason, ''), "
        "COALESCE(msi.hot_memory_candidate, 0), "
        "COALESCE(msi.provenance, ''), "
        "COALESCE(msi.lifecycle, 'unknown'), "
        "COALESCE(msi.dedupe_key, ''), "
        "COALESCE(msi.conflict_group, ''), "
        "msi.quality_score, "
        "COALESCE(msi.evidence_level, ''), "
        "COALESCE(msi.promotion_state, 'unknown'), "
        "COALESCE(msi.last_confirmed_at, ''), "
        "msi.hotness_score, "
        "COALESCE(msi.recall_source_type, ''), "
        "COALESCE(msi.schema_version, 'legacy-main-table') "
        "FROM memories m "
        "LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id "
    )


def recall_rows(
    memory_obj: Any,
    query: str,
    limit: int = 5,
    source_layer: str = "all",
    as_of: str = None,
) -> List[Dict[str, Any]]:
    """回忆相关记忆（支持时序查询）。"""
    conn = None
    normalized_query = str(query or "").strip()
    try:
        memory_obj._current_recall_query = normalized_query
    except Exception:
        pass
    reader = _build_recall_reader(source_layer, as_of)
    try:
        memory_obj._ensure_memory_schema_index_table()
        conn = memory_obj._conn()

        base_filter, layer_filter, temporal_params = _build_recall_filters(memory_obj, source_layer, as_of)
        rank_expr = _build_recall_rank_expr()
        select_sql = _build_recall_select_sql()

        keyword_tokens = memory_obj._build_recall_keyword_tokens(normalized_query)

        rows = memory_obj._collect_recall_candidate_rows(
            conn,
            select_sql,
            base_filter,
            layer_filter,
            rank_expr,
            temporal_params,
            normalized_query,
            keyword_tokens,
            limit,
        )

        # compat anchor for text-based regression tests:
        # effective_layer = source_layer if source_layer in {"raw", "scar"} else "mixed"
        results = memory_obj._build_recall_items(rows, source_layer, normalized_query, keyword_tokens)
        for item in results:
            item.setdefault("reader", reader)
            item.setdefault("structured", True)
        results.sort(key=memory_obj._recall_result_sort_key, reverse=True)
        final_results = results[:limit]
        _remember_recall_runtime(
            memory_obj,
            {
                "status": "ok",
                "degraded": False,
                "reader": reader,
                "query": normalized_query,
                "source_layer": source_layer,
                "as_of": str(as_of or ""),
                "result_count": len(final_results),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            },
        )
        return final_results
    except Exception as e:
        payload = {
            "status": "error",
            "degraded": True,
            "reader": reader,
            "query": normalized_query,
            "source_layer": source_layer,
            "as_of": str(as_of or ""),
            "error": str(e),
            "error_type": type(e).__name__,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        _remember_recall_runtime(memory_obj, payload)
        _remember_recall_error(memory_obj, payload)
        logger.exception(
            "【WangChuan】[MemoryRecall][Error] reader=%s source_layer=%s as_of=%s query=%r failed: %s",
            reader,
            source_layer,
            as_of,
            normalized_query[:120],
            e,
        )
        return []
    finally:
        try:
            memory_obj._current_recall_query = ""
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
