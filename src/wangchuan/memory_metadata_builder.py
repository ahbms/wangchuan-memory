from __future__ import annotations

"""WangChuan memory metadata builder helpers.

这一层承接 memory_api._build_memory_metadata 中的主构建逻辑：
- trace/source 推断与补齐
- memory_type / source_layer / lifecycle 推断
- hot-memory / quality / hotness 默认值
- 可选 LLM entity extract
- scope / supersession 字段整理

约束：
- 不改写记忆 schema 真值协议
- 仍由调用方（Memory）提供 tracing、noise、normalize、priority helper 能力
"""

import os
from datetime import datetime
from typing import Any, Dict, List

try:
    from wangchuan.memory_hot_priority import (
        compute_hot_memory_candidate as _compute_hot_memory_candidate_impl,
        compute_hotness_score as _compute_hotness_score_impl,
        compute_quality_score as _compute_quality_score_impl,
    )
except ImportError:
    from wangchuan.memory_hot_priority import (
        compute_hot_memory_candidate as _compute_hot_memory_candidate_impl,
        compute_hotness_score as _compute_hotness_score_impl,
        compute_quality_score as _compute_quality_score_impl,
    )

try:
    from wangchuan.memory_rules import classify_write_time_test_data as _classify_write_time_test_data_impl
except ImportError:
    from wangchuan.memory_rules import classify_write_time_test_data as _classify_write_time_test_data_impl

try:
    from wangchuan.v3.llm_memory import LLMExtractor
except ImportError:
    from wangchuan.v3.llm_memory import LLMExtractor


def build_memory_metadata(memory_obj: Any, content: str, tags: List[str] | None = None,
                          metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
    metadata = dict(metadata or {})
    normalized_tags = memory_obj._normalize_tags(tags or metadata.get("tags") or [])
    lowered_tags = {t.lower() for t in normalized_tags}
    text = str(content or "")

    source_anchor = str(metadata.get("source_anchor") or memory_obj._extract_source_anchor(text)).strip()
    source_session = str(metadata.get("source_session") or "").strip()
    turn_signature = str(metadata.get("turn_signature") or memory_obj._extract_turn_signature(text)).strip()
    inferred_trace_metadata = memory_obj._infer_memory_metadata(text, str(metadata.get("source_layer") or "scar"))
    explicit_memory_type = str(metadata.get("memory_type") or "").strip().lower()
    if explicit_memory_type:
        memory_type = explicit_memory_type
    elif "preference" in lowered_tags or "user" in lowered_tags:
        memory_type = "preference"
    elif "rule" in lowered_tags:
        memory_type = "rule"
    elif "decision" in lowered_tags:
        memory_type = "decision"
    elif "conversation" in lowered_tags or "raw" in lowered_tags:
        memory_type = "conversation"
    elif "lesson" in lowered_tags:
        memory_type = "lesson"
    else:
        memory_type = str(inferred_trace_metadata.get("memory_type") or "").strip().lower()
    promotion_reason = str(metadata.get("promotion_reason") or "").strip()

    if promotion_reason == "reflection_event" and (not source_anchor or not source_session or not turn_signature):
        inferred_trace = memory_obj._lookup_message_trace(
            text,
            created_at=str(metadata.get("created_at") or metadata.get("stored_at") or ""),
        )
        source_anchor = source_anchor or str(inferred_trace.get("source_anchor") or "").strip()
        source_session = source_session or str(inferred_trace.get("source_session") or "").strip()
        turn_signature = turn_signature or str(inferred_trace.get("turn_signature") or "").strip()
        if inferred_trace.get("provenance") and not metadata.get("provenance"):
            metadata["provenance"] = inferred_trace.get("provenance")

    if not memory_type:
        memory_type = "memory"

    if not source_anchor or not source_session or not turn_signature:
        static_trace = memory_obj._lookup_static_context_trace(text, memory_type=memory_type)
        source_anchor = source_anchor or str(static_trace.get("source_anchor") or "").strip()
        source_session = source_session or str(static_trace.get("source_session") or "").strip()
        turn_signature = turn_signature or str(static_trace.get("turn_signature") or "").strip()
        if static_trace.get("provenance") and not metadata.get("provenance"):
            metadata["provenance"] = static_trace.get("provenance")

    if not source_anchor or not source_session or not turn_signature:
        neighbor_trace = memory_obj._lookup_related_memory_trace(
            text,
            memory_type=memory_type,
            exclude_memory_id=metadata.get("memory_id"),
        )
        source_anchor = source_anchor or str(neighbor_trace.get("source_anchor") or "").strip()
        source_session = source_session or str(neighbor_trace.get("source_session") or "").strip()
        turn_signature = turn_signature or str(neighbor_trace.get("turn_signature") or "").strip()
        if neighbor_trace.get("provenance") and not metadata.get("provenance"):
            metadata["provenance"] = neighbor_trace.get("provenance")

    source_layer = str(metadata.get("source_layer") or "").strip().lower()
    if not source_layer:
        if source_anchor.startswith("memory/raw/") or "来源: memory/raw/" in text:
            source_layer = "raw"
        elif "candidate" in lowered_tags or str(metadata.get("status") or "").lower() in {"candidate", "reviewed", "pending_review"}:
            source_layer = "candidate"
        else:
            source_layer = "scar"

    user_explicit = memory_obj._coerce_bool(metadata.get("user_explicit")) or bool(
        lowered_tags & {"user", "preference", "rule", "lesson", "memory"}
    )
    explicit_is_test_data = memory_obj._coerce_bool(metadata.get("is_test_data"))
    write_time_test_reason = _classify_write_time_test_data_impl(text)
    is_test_data = (
        explicit_is_test_data
        or bool(lowered_tags & memory_obj.WRITE_GATE_BLOCK_TAGS)
        or bool(write_time_test_reason)
    )
    hot_memory_candidate = memory_obj._coerce_bool(metadata.get("hot_memory_candidate"))
    if not hot_memory_candidate:
        hot_memory_candidate = _compute_hot_memory_candidate_impl(
            memory_obj,
            text,
            normalized_tags,
            source_layer=source_layer,
            is_test_data=is_test_data,
            user_explicit=user_explicit,
            promotion_reason=promotion_reason,
            source_anchor=source_anchor,
            turn_signature=turn_signature,
        )

    evidence_level = str(metadata.get("evidence_level") or ("raw" if source_layer == "raw" else "summarized")).strip().lower()
    provenance = str(metadata.get("provenance") or source_anchor or source_session or source_layer or "memory").strip()
    lifecycle = str(metadata.get("lifecycle") or ("candidate" if source_layer == "candidate" else "active")).strip().lower()
    if promotion_reason and lifecycle == "active":
        promotion_state = str(metadata.get("promotion_state") or "promoted").strip().lower()
    elif lifecycle == "candidate":
        promotion_state = str(metadata.get("promotion_state") or "candidate").strip().lower()
    else:
        promotion_state = str(metadata.get("promotion_state") or "accepted").strip().lower()
    dedupe_key = str(metadata.get("dedupe_key") or turn_signature or memory_obj._canonical_hot_memory_key(text)[:96]).strip()
    conflict_group = str(metadata.get("conflict_group") or memory_type or "memory").strip().lower()

    quality_score = metadata.get("quality_score")
    if quality_score is None:
        quality_score = _compute_quality_score_impl(
            memory_obj,
            text,
            normalized_tags,
            user_explicit=user_explicit,
            promotion_reason=promotion_reason,
            source_anchor=source_anchor,
            turn_signature=turn_signature,
        )

    last_confirmed_at = str(metadata.get("last_confirmed_at") or datetime.now().isoformat(timespec="microseconds")).strip()
    hotness_score = metadata.get("hotness_score")
    if hotness_score is None:
        hotness_score = _compute_hotness_score_impl(
            hot_memory_candidate=hot_memory_candidate,
            user_explicit=user_explicit,
            promotion_reason=promotion_reason,
        )
    recall_source_type = str(metadata.get("recall_source_type") or source_layer or "memory").strip().lower()

    extracted_entities = []
    enable_llm_extract = os.getenv("WANGCHUAN_LLM_EXTRACT", "").lower() in {"1", "true", "yes"}
    if enable_llm_extract:
        try:
            extractor = LLMExtractor()
            results = extractor.extract(text)
            for row in results:
                if row.get("entities"):
                    extracted_entities.extend(row.get("entities", []))
            extracted_entities = list(set(extracted_entities))[:10]
        except Exception:
            pass

    subject_domain = str(metadata.get("subject_domain") or "").strip().lower()
    if not subject_domain:
        if "preference" in lowered_tags or "user" in lowered_tags:
            subject_domain = "user"
        elif "rule" in lowered_tags or "decision" in lowered_tags:
            subject_domain = "rule"
        else:
            subject_domain = str(inferred_trace_metadata.get("subject_domain") or "general").strip().lower()

    content_preview = str(metadata.get("content_preview") or text)[:160]

    scope_level = str(metadata.get("scope_level") or "").strip().lower()
    scope_value = str(metadata.get("scope_value") or "").strip()
    scope_user_id = str(metadata.get("scope_user_id") or "").strip()
    scope_session_id = str(metadata.get("scope_session_id") or "").strip()
    scope_agent_id = str(metadata.get("scope_agent_id") or "").strip()
    valid_from = str(metadata.get("valid_from") or datetime.now().isoformat(timespec="microseconds")).strip()
    valid_until_raw = metadata.get("valid_until")
    valid_until = str(valid_until_raw).strip() if valid_until_raw not in (None, "") else None
    superseded_by_raw = metadata.get("superseded_by")
    try:
        superseded_by = int(superseded_by_raw) if superseded_by_raw not in (None, "") else None
    except Exception:
        superseded_by = None
    supersession_chain = str(metadata.get("supersession_chain") or "").strip()

    return {
        "source_layer": source_layer,
        "source_anchor": source_anchor,
        "source_session": source_session,
        "turn_signature": turn_signature,
        "memory_type": memory_type,
        "user_explicit": user_explicit,
        "is_test_data": is_test_data,
        "is_test_data_explicit": explicit_is_test_data,
        "test_data_reason": write_time_test_reason,
        "promotion_reason": promotion_reason,
        "hot_memory_candidate": hot_memory_candidate,
        "provenance": provenance,
        "lifecycle": lifecycle,
        "dedupe_key": dedupe_key,
        "conflict_group": conflict_group,
        "quality_score": round(float(quality_score), 3),
        "evidence_level": evidence_level,
        "promotion_state": promotion_state,
        "last_confirmed_at": last_confirmed_at,
        "hotness_score": round(float(hotness_score), 3),
        "recall_source_type": recall_source_type,
        "subject_domain": subject_domain,
        "content_preview": content_preview,
        "tags": normalized_tags,
        "extracted_entities": extracted_entities,
        "scope_level": scope_level,
        "scope_value": scope_value,
        "scope_user_id": scope_user_id,
        "scope_session_id": scope_session_id,
        "scope_agent_id": scope_agent_id,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "superseded_by": superseded_by,
        "supersession_chain": supersession_chain,
    }
