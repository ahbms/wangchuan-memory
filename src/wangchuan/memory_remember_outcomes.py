from __future__ import annotations

"""WangChuan remember() outcome helpers.

这一层承接 memory_api.remember 中各类结果出口：
- deduped outcome
- blocked outcome
- allowed outcome

目标：
- 不改变 remember 的前置判断链
- 收敛重复 sidelog / return payload 组装
"""

from typing import Any, Dict, List


_FULL_METADATA_KEYS = [
    "source_layer", "source_anchor", "source_session", "turn_signature", "memory_type",
    "user_explicit", "is_test_data", "promotion_reason", "hot_memory_candidate",
    "provenance", "lifecycle", "dedupe_key", "conflict_group", "quality_score",
    "evidence_level", "promotion_state", "last_confirmed_at", "hotness_score",
    "recall_source_type",
]

_BLOCKED_METADATA_KEYS = [
    "source_layer", "source_anchor", "source_session", "turn_signature", "memory_type",
    "user_explicit", "is_test_data", "promotion_reason", "hot_memory_candidate",
]


def _pick_metadata(metadata: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    metadata = dict(metadata or {})
    return {k: metadata.get(k) for k in keys}


def remember_deduped_outcome(
    memory_obj: Any,
    *,
    memory_id: int,
    content: str,
    tags: List[str] | None,
    structured_metadata: Dict[str, Any],
    reason: str,
) -> Dict[str, Any]:
    memory_obj._write_gate_sidelog({
        "result": "deduped",
        "reason": reason,
        "db_path": memory_obj.db_path,
        "memory_id": memory_id,
        "content_preview": str(content or '')[:160],
        "tags": tags,
        "metadata": _pick_metadata(structured_metadata, _FULL_METADATA_KEYS),
    })

    if reason == "reflection_exact_duplicate":
        message = f"⏭️ exact duplicate reflection skipped: {content[:50]}"
    else:
        message = f"⏭️ duplicate reflection_event skipped: {content[:50]}"

    return {
        "success": True,
        "memory_id": memory_id,
        "message": message,
        "reason": reason,
        "write_gate": "deduped",
        "deduped": True,
        "metadata": structured_metadata,
    }


def remember_blocked_outcome(
    memory_obj: Any,
    *,
    content: str,
    tags: List[str] | None,
    structured_metadata: Dict[str, Any],
    gate: Dict[str, Any],
) -> Dict[str, Any]:
    memory_obj._write_gate_sidelog({
        "result": "blocked",
        "reason": gate["reason"],
        "db_path": memory_obj.db_path,
        "content_preview": str(content or '')[:160],
        "tags": tags,
        "metadata": _pick_metadata(structured_metadata, _BLOCKED_METADATA_KEYS),
    })

    return {
        "success": False,
        "memory_id": None,
        "message": gate["message"],
        "reason": gate["reason"],
        "write_gate": "blocked",
        "metadata": structured_metadata,
    }


def remember_allowed_outcome(
    memory_obj: Any,
    *,
    memory_id: int,
    content: str,
    tags: List[str] | None,
    structured_metadata: Dict[str, Any],
    gate: Dict[str, Any],
    postwrite: Dict[str, Any] | None,
) -> Dict[str, Any]:
    postwrite = dict(postwrite or {})

    memory_obj._write_gate_sidelog({
        "result": "allowed",
        "reason": gate["reason"],
        "db_path": memory_obj.db_path,
        "memory_id": memory_id,
        "content_preview": str(content or '')[:160],
        "tags": tags,
        "metadata": _pick_metadata(structured_metadata, _FULL_METADATA_KEYS),
    })

    return {
        "success": True,
        "memory_id": memory_id,
        "message": f"✅ 已记住: {content[:50]}",
        "reason": gate["reason"],
        "write_gate": "allowed",
        "deduped": False,
        "metadata": structured_metadata,
        "schema_record": postwrite.get("schema_record"),
    }
