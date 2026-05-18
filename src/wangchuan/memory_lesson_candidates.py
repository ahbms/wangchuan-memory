from __future__ import annotations

"""WangChuan lesson candidate workflow helpers.

这一层承接 memory_api 中 lesson 语义写入与 candidate/promote 工作流：
- lesson semantic remember write
- candidate sidecar 持久化
- candidate promote / skip 决策

约束：
- 不改写正式 remember/recall 主链协议
- 仍由调用方（Memory）提供 build_metadata / remember / write_gate sidelog
- 优先保持与 memory_api 现有 candidate JSON 结构和返回口径一致
"""

from datetime import datetime
from typing import Any, Dict
import json

try:
    from wangchuan.paths import state_root
except ImportError:
    from wangchuan.paths import state_root


def _lesson_candidates_dir():
    base_dir = state_root() / "lesson_candidates"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def persist_lesson_candidate(memory_obj: Any, lesson: Dict[str, Any]) -> Dict[str, Any]:
    base_dir = _lesson_candidates_dir()
    lesson_id = str(lesson.get("id") or f"lesson_candidate_{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
    candidate_path = base_dir / f"{lesson_id}.json"
    payload = dict(lesson)
    payload["id"] = lesson_id
    payload.setdefault("type", "lesson")
    payload.setdefault("status", "candidate")
    payload.update(memory_obj._build_memory_metadata(payload.get("content", ""), payload.get("tags") or [], payload))
    payload["stored_at"] = datetime.now().isoformat(timespec="seconds")
    payload["storage_kind"] = "lesson_candidate"
    candidate_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    memory_obj._write_gate_sidelog({
        "result": "candidate",
        "reason": f"lesson_status:{payload.get('status', 'candidate')}",
        "db_path": memory_obj.db_path,
        "candidate_path": str(candidate_path),
        "content_preview": str(payload.get('content', ''))[:160],
        "tags": list(payload.get('tags') or []),
        "metadata": {k: payload.get(k) for k in ["source_layer", "source_anchor", "source_session", "turn_signature", "memory_type", "user_explicit", "is_test_data", "promotion_reason", "hot_memory_candidate"]},
    })
    return {
        "success": True,
        "memory_id": None,
        "message": f"📝 lesson candidate stored: {lesson_id}",
        "reason": f"lesson_candidate:{payload.get('status', 'candidate')}",
        "write_gate": "candidate",
        "candidate_path": str(candidate_path),
        "candidate_id": lesson_id,
        "metadata": {k: payload.get(k) for k in ["source_layer", "source_anchor", "source_session", "turn_signature", "memory_type", "user_explicit", "is_test_data", "promotion_reason", "hot_memory_candidate"]},
    }


def remember_lesson(memory_obj: Any, lesson: Any) -> Dict[str, Any]:
    """以 lesson 语义写入长期记忆，候选态继续走 candidate sidecar。"""
    if hasattr(lesson, "to_dict"):
        lesson = lesson.to_dict()
    lesson = lesson or {}

    content = str(lesson.get("content", "")).strip()
    if not content:
        return {"success": False, "memory_id": None, "message": "❌ empty lesson content"}

    status = str(lesson.get("status", "") or "candidate").strip().lower()
    if status in {"candidate", "reviewed", "pending_review"}:
        return persist_lesson_candidate(memory_obj, lesson)

    tags = list(lesson.get("tags") or []) + ["lesson"]
    tags = list(dict.fromkeys(str(t).strip() for t in tags if str(t).strip()))

    meta_parts = []
    for key in ["source_task", "source_session", "source_trace", "applicable_when", "status"]:
        value = lesson.get(key)
        if value:
            meta_parts.append(f"{key}={value}")
    confidence = lesson.get("confidence")
    importance = lesson.get("importance", 0.6)
    if confidence is not None:
        meta_parts.append(f"confidence={confidence}")
    evidence = lesson.get("evidence") or []
    if evidence:
        meta_parts.append(f"evidence_count={len(evidence)}")

    final_content = content
    if meta_parts:
        final_content += "\n[lesson-meta] " + " | ".join(meta_parts)

    return memory_obj.remember(
        content=final_content,
        importance=float(importance),
        tags=tags,
        metadata={
            "source_layer": lesson.get("source_layer") or "scar",
            "source_anchor": lesson.get("source_anchor") or "",
            "source_session": lesson.get("source_session") or "",
            "turn_signature": lesson.get("turn_signature") or "",
            "memory_type": lesson.get("memory_type") or "lesson",
            "user_explicit": lesson.get("user_explicit"),
            "is_test_data": lesson.get("is_test_data"),
            "promotion_reason": lesson.get("promotion_reason") or "",
            "hot_memory_candidate": lesson.get("hot_memory_candidate"),
            "tags": tags,
            "status": lesson.get("status") or "active",
        },
    )


def promote_lesson_candidate(memory_obj: Any, candidate_id: str, decision: str = "promoted") -> Dict[str, Any]:
    candidate_path = _lesson_candidates_dir() / f"{candidate_id}.json"
    if not candidate_path.exists():
        return {
            "success": False,
            "memory_id": None,
            "message": f"❌ lesson candidate not found: {candidate_id}",
            "reason": "candidate_not_found",
        }

    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    status = str(payload.get("status", "candidate") or "candidate").strip().lower()
    if status not in {"candidate", "reviewed", "pending_review"}:
        return {
            "success": False,
            "memory_id": None,
            "message": f"❌ lesson candidate status not promotable: {status}",
            "reason": f"status_not_promotable:{status}",
        }

    decision = str(decision or "promoted").strip().lower()
    if decision == "skipped":
        payload["status"] = "skipped"
        payload["promotion_decision"] = "skipped"
        payload["promoted_at"] = datetime.now().isoformat(timespec="seconds")
        candidate_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        memory_obj._write_gate_sidelog({
            "result": "promotion_skipped",
            "reason": "lesson_candidate_skipped",
            "db_path": memory_obj.db_path,
            "candidate_path": str(candidate_path),
            "content_preview": str(payload.get('content', ''))[:160],
            "tags": list(payload.get('tags') or []),
        })
        return {
            "success": True,
            "memory_id": None,
            "message": f"⏭️ lesson candidate skipped: {candidate_id}",
            "reason": "candidate_skipped",
            "write_gate": "skipped",
            "candidate_path": str(candidate_path),
            "candidate_id": candidate_id,
        }

    promoted_payload = dict(payload)
    promoted_payload["status"] = "active"
    promoted_payload["promotion_reason"] = payload.get("promotion_reason") or "manual_promote"
    result = memory_obj.remember_lesson(promoted_payload)
    if result.get("success"):
        payload["status"] = "promoted"
        payload["promotion_decision"] = "promoted"
        payload["promoted_at"] = datetime.now().isoformat(timespec="seconds")
        payload["memory_id"] = result.get("memory_id")
        candidate_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
