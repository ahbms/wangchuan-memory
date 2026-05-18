from __future__ import annotations

"""WangChuan trace repair helpers.

这一层承接 memory_api.repair_trace_metadata 中的修复流程：
- 扫描缺失 trace 的 schema index 记录
- 对非 reflection_event 记录做 metadata / static / neighbor 补推断
- enrich 缺失 trace
- 回写 source_anchor/source_session/turn_signature 等字段
"""

from typing import Any, Dict


def _build_trace_repair_updates(enriched: Dict[str, Any], original_item: Dict[str, Any]) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    for key in (
        "source_anchor",
        "source_session",
        "turn_signature",
        "provenance",
        "memory_type",
        "source_layer",
        "recall_source_type",
        "subject_domain",
        "content_preview",
        "promotion_state",
    ):
        if enriched.get(key) and enriched.get(key) != original_item.get(key):
            updates[key] = enriched.get(key)
    if bool(enriched.get("user_explicit")) != bool(original_item.get("user_explicit")):
        updates["user_explicit"] = bool(enriched.get("user_explicit"))
    return updates


def repair_trace_metadata(memory_obj: Any, limit: int = 0) -> Dict[str, Any]:
    memory_obj._ensure_memory_schema_index_table()
    conn = memory_obj._conn()
    try:
        sql = (
            "SELECT m.id, m.content, m.created_at, "
            "COALESCE(msi.source_anchor, ''), COALESCE(msi.source_session, ''), COALESCE(msi.turn_signature, ''), "
            "COALESCE(msi.promotion_reason, ''), COALESCE(msi.memory_type, ''), COALESCE(msi.source_layer, ''), COALESCE(msi.user_explicit, 0) "
            "FROM memories m "
            "LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id "
            "AND (COALESCE(msi.source_anchor, '') = '' OR COALESCE(msi.source_session, '') = '' OR COALESCE(msi.turn_signature, '') = '') "
            "ORDER BY m.id DESC"
        )
        if limit and int(limit) > 0:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()

    scanned = len(rows)
    repaired = 0
    unresolved = 0

    for memory_id, content, created_at, source_anchor, source_session, turn_signature, promotion_reason, memory_type, source_layer, user_explicit in rows:
        item = {
            "memory_id": memory_id,
            "content": content,
            "created_at": created_at,
            "source_anchor": source_anchor,
            "source_session": source_session,
            "turn_signature": turn_signature,
            "promotion_reason": promotion_reason,
            "memory_type": memory_type,
            "source_layer": source_layer,
            "user_explicit": bool(user_explicit),
            "subject_domain": "",
            "content_preview": "",
            "promotion_state": "",
        }
        original_item = dict(item)

        inferred = memory_obj._infer_memory_metadata(str(content or ""), str(source_layer or "mixed") or "mixed")

        if str(promotion_reason or "").strip().lower() != "reflection_event":
            effective_memory_type = str(memory_type or inferred.get("memory_type") or "")
            static_trace = memory_obj._lookup_static_context_trace(str(content or ""), memory_type=effective_memory_type)
            neighbor_trace = memory_obj._lookup_related_memory_trace(
                str(content or ""),
                memory_type=effective_memory_type,
                exclude_memory_id=memory_id,
            )
            if not static_trace and not neighbor_trace:
                unresolved += 1
                continue
            if not item.get("memory_type") and inferred.get("memory_type"):
                item["memory_type"] = inferred.get("memory_type")
            if not item.get("source_layer") and inferred.get("source_layer"):
                item["source_layer"] = inferred.get("source_layer")
            if not item.get("user_explicit") and inferred.get("user_explicit") is not None:
                item["user_explicit"] = bool(inferred.get("user_explicit"))
            item.setdefault("recall_source_type", inferred.get("recall_source_type") or item.get("source_layer") or "")

        if inferred.get("subject_domain"):
            item["subject_domain"] = inferred.get("subject_domain")
        if inferred.get("content_preview"):
            item["content_preview"] = inferred.get("content_preview")
        if inferred.get("promotion_state"):
            item["promotion_state"] = inferred.get("promotion_state")

        enriched = memory_obj._enrich_missing_trace_metadata(item)
        updates = _build_trace_repair_updates(enriched, original_item)
        if updates:
            memory_obj._update_memory_schema_fields(memory_id, updates)
            repaired += 1
        else:
            unresolved += 1

    return {
        "success": True,
        "scanned": scanned,
        "repaired": repaired,
        "unresolved": unresolved,
        "table": "memory_schema_index",
    }
