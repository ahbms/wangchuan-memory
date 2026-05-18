from __future__ import annotations

"""WangChuan schema sidecar record / registry helpers.

这一层承接 memory_api 中 schema sidecar JSON 真值层的低风险文件读写逻辑：
- registry / record path
- sidecar record 持久化与读取
- 更新字段 / 标记 removed / 批量删除 registry 项

约束：
- 不改写 memory_schema_index SQL 协议
- 仍由调用方（Memory）提供 schema_dir、normalize_tags、upsert 与字段常量
- 优先保持与 memory_api 现有 sidecar JSON 结构一致
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
import json


SCHEMA_VERSION = "phase2.1-sidecar-v1"


def memory_schema_registry_path(memory_obj: Any) -> Path:
    return memory_obj._memory_schema_dir() / "schema_registry.json"


def memory_schema_record_path(memory_obj: Any, memory_id: Any) -> Path:
    return memory_obj._memory_schema_dir() / f"memory_{memory_id}.json"


def _load_registry(memory_obj: Any) -> Dict[str, Any]:
    registry_path = memory_schema_registry_path(memory_obj)
    registry = {
        "schema_version": SCHEMA_VERSION,
        "fields": memory_obj.SCHEMA_FIELDS,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "records": {},
    }
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        registry.setdefault("fields", memory_obj.SCHEMA_FIELDS)
        registry.setdefault("records", {})
    return registry


def _write_registry(memory_obj: Any, registry: Dict[str, Any], *, indent: int | None = 2) -> Dict[str, Any]:
    registry_path = memory_schema_registry_path(memory_obj)
    registry["schema_version"] = SCHEMA_VERSION
    registry["updated_at"] = datetime.now().isoformat(timespec="seconds")
    registry["fields"] = memory_obj.SCHEMA_FIELDS
    if indent is None:
        registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    else:
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=indent), encoding="utf-8")
    return registry


def persist_memory_schema(memory_obj: Any, memory_id: Any, metadata: Dict[str, Any], content: str, importance: float, tags: List[str] | None = None) -> Dict[str, Any]:
    payload = {
        "memory_id": memory_id,
        "content_preview": str(content or "")[:160],
        "subject_domain": metadata.get("subject_domain"),
        "importance": round(float(importance), 3),
        "tags": memory_obj._normalize_tags(tags),
        "stored_at": datetime.now().isoformat(timespec="seconds"),
        "schema_version": SCHEMA_VERSION,
    }
    payload.update({key: metadata.get(key) for key in [
        "source_layer", "source_anchor", "source_session", "turn_signature", "memory_type",
        "user_explicit", "is_test_data", "promotion_reason", "hot_memory_candidate",
        "provenance", "lifecycle", "dedupe_key", "conflict_group", "quality_score",
        "evidence_level", "promotion_state", "last_confirmed_at", "hotness_score", "recall_source_type",
        "valid_from", "valid_until", "superseded_by", "supersession_chain",
    ]})
    payload["confidence"] = metadata.get("confidence")
    payload["trigger_count"] = metadata.get("trigger_count")
    payload["last_recall"] = metadata.get("last_recall")
    payload.setdefault("valid_from", payload.get("stored_at"))
    payload.setdefault("valid_until", None)
    payload.setdefault("superseded_by", None)
    payload.setdefault("supersession_chain", "")

    record_path = memory_schema_record_path(memory_obj, memory_id)
    record_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    registry = _load_registry(memory_obj)
    registry["records"][str(memory_id)] = {
        "record_path": str(record_path),
        "dedupe_key": payload.get("dedupe_key"),
        "lifecycle": payload.get("lifecycle"),
        "promotion_state": payload.get("promotion_state"),
        "recall_source_type": payload.get("recall_source_type"),
        "updated_at": payload.get("stored_at"),
    }
    _write_registry(memory_obj, registry, indent=2)
    memory_obj._upsert_memory_schema_index(payload)
    return payload


def read_memory_schema(memory_obj: Any, memory_id: Any) -> Dict[str, Any]:
    record_path = memory_schema_record_path(memory_obj, memory_id)
    if not record_path.exists():
        return {}
    try:
        return json.loads(record_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def mark_memory_schema_removed(memory_obj: Any, memory_id: Any, existing: Dict[str, Any] | None = None) -> Dict[str, Any]:
    memory_obj._ensure_memory_schema_index_table()
    try:
        normalized_memory_id = int(memory_id)
    except Exception:
        normalized_memory_id = memory_id

    payload = dict(existing or read_memory_schema(memory_obj, memory_id) or {})
    now_iso = datetime.now().isoformat(timespec="seconds")
    payload["memory_id"] = normalized_memory_id
    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload.setdefault("source_layer", payload.get("source_layer") or "scar")
    payload.setdefault("memory_type", payload.get("memory_type") or "unknown")
    payload.setdefault("subject_domain", payload.get("subject_domain") or "general")
    payload.setdefault("content_preview", str(payload.get("content_preview") or "")[:160])
    payload.setdefault("recall_source_type", payload.get("recall_source_type") or payload.get("source_layer") or "scar")
    payload.setdefault("lifecycle", payload.get("lifecycle") or "archived")
    payload.setdefault("promotion_state", payload.get("promotion_state") or "removed")
    payload["removed_at"] = now_iso
    payload["updated_at"] = now_iso

    memory_obj._upsert_memory_schema_index(payload)
    return payload


def batch_mark_memory_schema_removed(memory_obj: Any, memory_ids: List[Any]) -> int:
    if not memory_ids:
        return 0

    memory_obj._ensure_memory_schema_index_table()
    registry = _load_registry(memory_obj)
    now_iso = datetime.now().isoformat(timespec="seconds")
    conn = memory_obj._conn()
    removed = 0
    try:
        for memory_id in memory_ids:
            memory_id_text = str(memory_id)
            existing_payload = read_memory_schema(memory_obj, memory_id_text)
            try:
                normalized_memory_id = int(memory_id_text)
            except Exception:
                normalized_memory_id = memory_id_text

            payload = dict(existing_payload or {})
            payload["memory_id"] = normalized_memory_id
            payload.setdefault("schema_version", SCHEMA_VERSION)
            payload.setdefault("source_layer", payload.get("source_layer") or "scar")
            payload.setdefault("memory_type", payload.get("memory_type") or "unknown")
            payload.setdefault("subject_domain", payload.get("subject_domain") or "general")
            payload.setdefault("content_preview", str(payload.get("content_preview") or "")[:160])
            payload.setdefault("recall_source_type", payload.get("recall_source_type") or payload.get("source_layer") or "scar")
            payload.setdefault("lifecycle", payload.get("lifecycle") or "archived")
            payload.setdefault("promotion_state", payload.get("promotion_state") or "removed")
            payload["removed_at"] = now_iso
            payload["updated_at"] = now_iso

            memory_obj._upsert_memory_schema_index(payload, conn=conn)

            record_path = memory_schema_record_path(memory_obj, memory_id_text)
            if record_path.exists():
                try:
                    record_path.unlink()
                except Exception:
                    pass
            registry["records"].pop(memory_id_text, None)
            removed += 1

        conn.commit()
    finally:
        conn.close()

    _write_registry(memory_obj, registry, indent=None)
    return removed


def update_memory_schema_fields(memory_obj: Any, memory_id: Any, updates: Dict[str, Any], remove: bool = False) -> Dict[str, Any]:
    memory_id = str(memory_id)
    record_path = memory_schema_record_path(memory_obj, memory_id)
    registry = _load_registry(memory_obj)

    if remove:
        existing_payload = read_memory_schema(memory_obj, memory_id)
        mark_memory_schema_removed(memory_obj, memory_id, existing=existing_payload)
        if record_path.exists():
            try:
                record_path.unlink()
            except Exception:
                pass
        registry["records"].pop(memory_id, None)
        _write_registry(memory_obj, registry, indent=2)
        return {"memory_id": memory_id, "removed": True}

    payload = read_memory_schema(memory_obj, memory_id)
    payload.update({k: v for k, v in (updates or {}).items() if v is not None})
    payload["memory_id"] = int(memory_id) if str(memory_id).isdigit() else memory_id
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload.setdefault("subject_domain", payload.get("subject_domain") or "general")
    payload.setdefault("content_preview", str(payload.get("content_preview") or "")[:160])
    record_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    registry["records"][memory_id] = {
        "record_path": str(record_path),
        "dedupe_key": payload.get("dedupe_key"),
        "lifecycle": payload.get("lifecycle"),
        "promotion_state": payload.get("promotion_state"),
        "recall_source_type": payload.get("recall_source_type"),
        "updated_at": payload.get("updated_at"),
    }
    _write_registry(memory_obj, registry, indent=2)
    memory_obj._upsert_memory_schema_index(payload)
    return payload
