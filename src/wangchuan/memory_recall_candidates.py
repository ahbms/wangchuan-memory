from __future__ import annotations

"""WangChuan recall candidate collection helpers.

这一层承接 memory_api 中 recall 主流程里的候选收集逻辑：
- FTS 检索
- LIKE / keyword LIKE 补充
- 本地向量补充
- rows_by_id 聚合

约束：
- 不改写最终 row -> item 组装与排序逻辑
- 仍由调用方（Memory）提供 local vector 与 noise/query helper
- 优先保持与 memory_api 现有召回补充口径一致
"""

from typing import Any, Dict, List
import re

try:
    from wangchuan.fts_utils import build_safe_fts_match_query
except ImportError:
    from wangchuan.fts_utils import build_safe_fts_match_query

try:
    from wangchuan.memory_rules import classify_historical_noise_memory
except ImportError:
    from wangchuan.memory_rules import classify_historical_noise_memory

try:
    from wangchuan.memory_rules import classify_low_value_emotional_memory
except ImportError:
    from wangchuan.memory_rules import classify_low_value_emotional_memory

try:
    from wangchuan.memory_recall_query import build_dynamic_profile_query_tokens
except ImportError:
    from wangchuan.memory_recall_query import build_dynamic_profile_query_tokens

try:
    from wangchuan.memory_recall_query import query_looks_like_user_profile
except ImportError:
    from wangchuan.memory_recall_query import query_looks_like_user_profile


def _is_candidate_noise(memory_obj: Any, row: Any) -> bool:
    content = str(row[1] or "")
    if classify_historical_noise_memory(content):
        return True

    lowered = content.strip().lower()
    # Keep candidate filtering aligned with ranking semantics:
    # strong topical emotional memories may still be valid recall targets,
    # while low-value runtime emotional noise should be blocked early.
    if lowered.startswith("情感事件:"):
        if classify_low_value_emotional_memory(content):
            return True
    elif memory_obj._is_recall_noise(content):
        return True

    try:
        memory_type = str(row[8] or "").strip().lower()
    except Exception:
        memory_type = ""
    try:
        subject_domain = str(row[9] or "").strip().lower()
    except Exception:
        subject_domain = ""

    if subject_domain == "general":
        current_query = str(getattr(memory_obj, "_current_recall_query", "") or "")
        dynamic_profile_tokens = {
            str(token).strip().lower()
            for token in build_dynamic_profile_query_tokens(memory_obj, current_query)
            if str(token).strip()
        }
        profile_like_query = query_looks_like_user_profile(memory_obj, current_query)

        if "记住这个" in lowered:
            return True
        if lowered.startswith("测试：") or lowered.startswith("测试:"):
            return True

        if profile_like_query and memory_type in {"", "unknown", "identity", "instruction", "memory", "preference", "aversion", "user_defined"}:
            if dynamic_profile_tokens and "用户" not in lowered and len(re.sub(r"\s+", "", lowered)) <= 48:
                compact_content = re.sub(r"[^\w\u4e00-\u9fff]+", "", lowered)
                compact_tokens = {
                    re.sub(r"[^\w\u4e00-\u9fff]+", "", token)
                    for token in dynamic_profile_tokens
                    if len(re.sub(r"[^\w\u4e00-\u9fff]+", "", token)) >= 2
                }
                if compact_tokens and any(token in compact_content for token in compact_tokens):
                    return True
            if dynamic_profile_tokens and re.search(r"^(爱喝|欢喝|厌喝|喜欢喝|喜欢).{0,20}", lowered):
                return True

    return False


def collect_recall_candidate_rows(
    memory_obj: Any,
    conn: Any,
    *,
    select_sql: str,
    base_filter: str,
    layer_filter: str,
    rank_expr: str,
    temporal_params: List[Any],
    normalized_query: str,
    keyword_tokens: List[str],
    limit: int,
) -> List[tuple]:
    rows_by_id: Dict[int, Any] = {}

    fts_rows = []
    try:
        match_query = build_safe_fts_match_query(normalized_query, max_terms=12)
        if match_query:
            fts_rows = conn.execute(
                select_sql
                + "JOIN fts_memories f ON m.id = f.rowid "
                + "WHERE " + base_filter + " AND fts_memories MATCH ?" + layer_filter + " "
                + f"ORDER BY {rank_expr} DESC, m.confidence DESC LIMIT ?",
                (*temporal_params, match_query, max(limit * 3, limit))
            ).fetchall()
    except Exception:
        fts_rows = []

    for row in fts_rows:
        rows_by_id[int(row[0])] = row

    if normalized_query:
        like_rows = conn.execute(
            select_sql
            + "WHERE " + base_filter + " AND m.content LIKE ?" + layer_filter + " "
            + f"ORDER BY {rank_expr} DESC, m.confidence DESC LIMIT ?",
            (*temporal_params, f"%{normalized_query}%", limit * 4)
        ).fetchall()
        for row in like_rows:
            rows_by_id.setdefault(int(row[0]), row)

    if keyword_tokens:
        like_clauses = " OR ".join(["m.content LIKE ?" for _ in keyword_tokens])
        params = [f"%{token}%" for token in keyword_tokens]
        keyword_rows = conn.execute(
            select_sql
            + f"WHERE {base_filter} AND ({like_clauses})" + layer_filter + " "
            + f"ORDER BY {rank_expr} DESC, m.confidence DESC LIMIT ?",
            (*temporal_params, *params, limit * 6)
        ).fetchall()
        for row in keyword_rows:
            rows_by_id.setdefault(int(row[0]), row)

    rows = list(rows_by_id.values())
    rows = [row for row in rows if not _is_candidate_noise(memory_obj, row)]
    rows_by_id = {int(row[0]): row for row in rows}
    rows = memory_obj._rrf_fusion(rows, normalized_query, keyword_tokens)

    if len(rows) < limit * 2:
        try:
            vector_results = memory_obj._get_local_vector().search(normalized_query, top_k=limit * 3)
            for vr in vector_results:
                if int(vr['memory_id']) not in rows_by_id:
                    row = conn.execute(
                        select_sql + f"WHERE {base_filter} AND m.id = ?" + layer_filter,
                        (*temporal_params, int(vr['memory_id']))
                    ).fetchone()
                    if row and not _is_candidate_noise(memory_obj, row):
                        rows_by_id[int(vr['memory_id'])] = row
            if vector_results:
                rows = list(rows_by_id.values())
                rows = [row for row in rows if not _is_candidate_noise(memory_obj, row)]
        except Exception:
            pass

    return rows
