from __future__ import annotations

"""WangChuan remember() post-write helpers.

这一层承接 memory_api.remember 中“主表插入之后”的副作用链：
- persist schema sidecar / index
- scope index
- entity linking
- hot memory sync
- local vector embed

目标：
- 不改变 remember 的入口签名与前置 dedupe / gate 行为
- 让 remember 主函数更像 orchestration 壳
"""

from typing import Any, Dict, List

try:
    from wangchuan.v3.llm_memory import MultiLevelMemory
except ImportError:
    from wangchuan.v3.llm_memory import MultiLevelMemory


def run_remember_postwrite(
    memory_obj: Any,
    memory_id: int,
    content: str,
    importance: float,
    tags: List[str] | None,
    structured_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    schema_record = memory_obj._persist_memory_schema(memory_id, structured_metadata, content, importance, tags)

    scope_level = str(structured_metadata.get("scope_level") or "").strip().lower()
    scope_value = str(structured_metadata.get("scope_value") or "").strip()
    if scope_level in {"user", "session", "agent"} and scope_value:
        MultiLevelMemory(memory_obj.db_path)._add_memory_scope_index(memory_id, scope_level, scope_value)

    if structured_metadata.get("extracted_entities"):
        for entity in structured_metadata["extracted_entities"]:
            memory_obj._get_entity_linker().link_entity(entity, memory_id)

    if importance >= 0.7 and structured_metadata.get("hot_memory_candidate"):
        memory_obj._sync_to_memory_md(content, tags)

    try:
        memory_obj._get_local_vector().embed_memory(memory_id, content)
    except Exception:
        pass

    return {
        "schema_record": schema_record,
        "memory_id": memory_id,
    }
