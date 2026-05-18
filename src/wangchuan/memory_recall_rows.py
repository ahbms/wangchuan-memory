from __future__ import annotations

"""WangChuan recall row -> item assembly helpers.

这一层承接 memory_api 中 recall rows 主流程里较低风险的组装逻辑：
- sqlite row 解包
- row -> structured item
- enrich / infer / schema merge
- recall score / explain 填充

约束：
- 不改写 recall 查询 SQL 与向量补充主流程
- 仍由调用方（Memory）提供 enrich/infer/read_schema/ranking helper
- 优先保持与 memory_api 现有 item 结构一致
"""

from typing import Any, Dict, List

try:
    from wangchuan.memory_rules import looks_like_questionish_rule_noise
except ImportError:
    from wangchuan.memory_rules import looks_like_questionish_rule_noise


def build_recall_trace(
    item: Dict[str, Any],
    *,
    normalized_query: str,
    requested_layer: str,
) -> Dict[str, Any]:
    explain = dict(item.get("recall_explain") or {})
    filtered_reasons: List[str] = []
    if float(explain.get("lifecycle_penalty") or 0.0) > 0:
        filtered_reasons.append("lifecycle_penalty")
    if float(explain.get("raw_conversation_penalty") or 0.0) > 0:
        filtered_reasons.append("raw_conversation_penalty")
    if float(explain.get("profile_noise_penalty") or 0.0) > 0:
        filtered_reasons.append("profile_noise_penalty")
    if bool(item.get("is_test_data")):
        filtered_reasons.append("is_test_data")

    return {
        "context_type": "memory",
        "route_scope": f"memory:{requested_layer or 'all'}",
        "query": normalized_query,
        "selected_memory_id": item.get("memory_id"),
        "selected_uri": str(item.get("source_anchor") or ""),
        "candidate_source": {
            "source_layer": item.get("source_layer"),
            "recall_source_type": item.get("recall_source_type"),
            "source_session": item.get("source_session"),
            "provenance": item.get("provenance"),
        },
        "score": {
            "text_match": explain.get("text_match"),
            "final_rank_score": explain.get("final_rank_score"),
        },
        "explain_summary": explain.get("summary") or "",
        "filtered_reasons": filtered_reasons,
    }


def build_recall_item(
    memory_obj: Any,
    row: tuple,
    *,
    effective_layer: str,
    normalized_query: str,
    keyword_tokens: List[str],
) -> Dict[str, Any] | None:
    (
        memory_id,
        content,
        confidence,
        created_at,
        idx_source_layer,
        idx_source_anchor,
        idx_source_session,
        idx_turn_signature,
        idx_memory_type,
        idx_subject_domain,
        idx_user_explicit,
        idx_is_test_data,
        idx_promotion_reason,
        idx_hot_memory_candidate,
        idx_provenance,
        idx_lifecycle,
        idx_dedupe_key,
        idx_conflict_group,
        idx_quality_score,
        idx_evidence_level,
        idx_promotion_state,
        idx_last_confirmed_at,
        idx_hotness_score,
        idx_recall_source_type,
        idx_schema_version,
    ) = row

    item = {
        "memory_id": memory_id,
        "content": content,
        "score": confidence,
        "created_at": created_at,
        "source_layer": idx_source_layer or effective_layer,
        "source_anchor": idx_source_anchor,
        "source_session": idx_source_session,
        "turn_signature": idx_turn_signature,
        "memory_type": idx_memory_type,
        "subject_domain": idx_subject_domain,
        "user_explicit": bool(idx_user_explicit),
        "is_test_data": bool(idx_is_test_data),
        "promotion_reason": idx_promotion_reason,
        "hot_memory_candidate": bool(idx_hot_memory_candidate),
        "provenance": idx_provenance,
        "lifecycle": idx_lifecycle,
        "dedupe_key": idx_dedupe_key,
        "conflict_group": idx_conflict_group,
        "quality_score": idx_quality_score,
        "evidence_level": idx_evidence_level,
        "promotion_state": idx_promotion_state,
        "last_confirmed_at": idx_last_confirmed_at,
        "hotness_score": idx_hotness_score,
        "recall_source_type": idx_recall_source_type or effective_layer,
        "schema_version": idx_schema_version,
    }
    item = memory_obj._enrich_missing_trace_metadata(item)
    inferred = memory_obj._infer_memory_metadata(content, effective_layer)
    schema = memory_obj._read_memory_schema(memory_id)

    # merge priority: inferred fallback < schema sidecar < sqlite recall row
    merged = dict(inferred)
    merged.update({k: v for k, v in schema.items() if v not in (None, "", [])})
    merged.update({k: v for k, v in item.items() if v not in (None, "", [])})
    schema_version = str(item.get("schema_version") or "").strip().lower()
    weak_legacy_types = {
        "",
        "unknown",
        "memory",
        "user_defined",
        "identity",
        "skill",
        "aversion",
        "habit",
        "instruction",
        "strategy",
        "technical",
        "knowledge",
        "milestone",
        "status",
        "session",
        "event",
        "extracted",
        "user",
        "fact",
    }
    reclassifiable_emotional_types = {"rule", "correction", "decision"}
    weak_general_schema = schema_version.startswith("phase2.") or schema_version in {"", "legacy-main-table"}
    if schema_version in {"", "legacy-main-table"}:
        row_memory_type = str(item.get("memory_type") or "").strip().lower()
        if row_memory_type in weak_legacy_types and inferred.get("memory_type"):
            merged["memory_type"] = inferred.get("memory_type")
        elif row_memory_type == "emotional":
            inferred_memory_type = str(inferred.get("memory_type") or "").strip().lower()
            if inferred_memory_type in reclassifiable_emotional_types:
                merged["memory_type"] = inferred_memory_type
        row_subject_domain = str(item.get("subject_domain") or "").strip().lower()
        inferred_subject_domain = str(inferred.get("subject_domain") or "").strip().lower()
        if row_subject_domain in {"", "general"} and inferred_subject_domain not in {"", "general"}:
            merged["subject_domain"] = inferred_subject_domain
        for key in ("source_anchor", "source_session", "turn_signature", "promotion_reason", "recall_source_type"):
            if item.get(key) in (None, "", []) and inferred.get(key) not in (None, "", []):
                merged[key] = inferred.get(key)
        if item.get("promotion_state") in (None, "", "unknown") and inferred.get("promotion_state"):
            merged["promotion_state"] = inferred.get("promotion_state")
        if not bool(item.get("user_explicit")) and inferred.get("user_explicit"):
            merged["user_explicit"] = True
        if not bool(item.get("hot_memory_candidate")) and inferred.get("hot_memory_candidate"):
            merged["hot_memory_candidate"] = True
        if not bool(item.get("is_test_data")) and inferred.get("is_test_data"):
            merged["is_test_data"] = True

    if weak_general_schema:
        row_memory_type = str(item.get("memory_type") or "").strip().lower()
        inferred_memory_type = str(inferred.get("memory_type") or "").strip().lower()
        if row_memory_type in weak_legacy_types and inferred_memory_type and inferred_memory_type not in {"", row_memory_type}:
            merged["memory_type"] = inferred_memory_type
        elif row_memory_type == "emotional" and inferred_memory_type in reclassifiable_emotional_types:
            merged["memory_type"] = inferred_memory_type

        row_subject_domain = str(item.get("subject_domain") or "").strip().lower()
        schema_subject_domain = str(schema.get("subject_domain") or "").strip().lower()
        inferred_subject_domain = str(inferred.get("subject_domain") or "").strip().lower()
        if (
            inferred_subject_domain not in {"", "general"}
            and row_subject_domain == "general"
            and schema_subject_domain in {"", "general"}
        ):
            merged["subject_domain"] = inferred_subject_domain

        if not bool(item.get("user_explicit")) and inferred.get("user_explicit"):
            merged["user_explicit"] = True

        source_layer = str(merged.get("source_layer") or effective_layer or "").strip().lower()
        if not bool(merged.get("hot_memory_candidate")) and source_layer != "raw":
            merged["hot_memory_candidate"] = bool(inferred.get("hot_memory_candidate"))

        if not merged.get("content_preview"):
            merged["content_preview"] = str(content or "")[:160]

    item = merged
    item["_recall_query"] = normalized_query
    item["recall_text_match_score"] = memory_obj._recall_text_match_score(content, normalized_query, keyword_tokens)
    item["recall_rank_score"] = round(memory_obj._recall_rank_score(item) + item["recall_text_match_score"], 6)
    item["recall_explain"] = memory_obj._build_recall_explain(item)
    if content.startswith("情感事件:") and item["recall_text_match_score"] < 0.45:
        return None
    return item


def build_recall_items(
    memory_obj: Any,
    rows: List[tuple],
    *,
    source_layer: str,
    normalized_query: str,
    keyword_tokens: List[str],
) -> List[Dict[str, Any]]:
    effective_layer = source_layer if source_layer in {"raw", "scar"} else "mixed"
    results: List[Dict[str, Any]] = []
    for row in rows:
        item = build_recall_item(
            memory_obj,
            row,
            effective_layer=effective_layer,
            normalized_query=normalized_query,
            keyword_tokens=keyword_tokens,
        )
        if item is not None:
            if source_layer == "scar":
                memory_type = str(item.get("memory_type") or "").strip().lower()
                item_source_layer = str(item.get("source_layer") or "").strip().lower()
                user_explicit = bool(item.get("user_explicit"))
                if not (
                    item_source_layer == "scar"
                    or memory_type in {"rule", "lesson", "decision", "correction"}
                    or (memory_type in {"preference", "fact"} and user_explicit)
                ):
                    continue
                if looks_like_questionish_rule_noise(str(item.get("content") or "")):
                    continue
            item["recall_trace"] = build_recall_trace(
                item,
                normalized_query=normalized_query,
                requested_layer=source_layer,
            )
            results.append(item)
    return results
