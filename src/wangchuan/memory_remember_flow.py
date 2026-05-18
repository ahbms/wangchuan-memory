from __future__ import annotations

"""WangChuan remember() orchestration helpers.

这一层承接 memory_api.remember 中的主流程编排：
- normalize tags
- build structured metadata
- exact / semantic duplicate guard
- write gate evaluate
- main table insert
- postwrite side effects + outcome return

目标：
- 不改变 remember 的公共签名
- 不改写 write gate / postwrite / outcome helper 协议
- 让 memory_api.remember 退化成薄委托
"""

from datetime import datetime
from typing import Any, Dict, List


def remember(
    memory_obj: Any,
    content: str,
    importance: float = 0.6,
    tags: List[str] | None = None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    tags = memory_obj._normalize_tags(tags)
    structured_metadata = memory_obj._build_memory_metadata(content, tags, metadata)
    structured_metadata["confidence"] = round(float(importance), 3)
    structured_metadata["importance"] = round(float(importance), 3)

    exact_duplicate_memory_id = memory_obj._find_existing_exact_reflection_memory(content, structured_metadata)
    if exact_duplicate_memory_id is not None:
        return memory_obj._remember_deduped_outcome(
            exact_duplicate_memory_id,
            content,
            tags,
            structured_metadata,
            "reflection_exact_duplicate",
        )

    duplicate_memory_id = memory_obj._find_existing_reflection_memory(content, structured_metadata)
    if duplicate_memory_id is not None:
        return memory_obj._remember_deduped_outcome(
            duplicate_memory_id,
            content,
            tags,
            structured_metadata,
            "reflection_event_duplicate",
        )

    gate = memory_obj._evaluate_write_gate(content, tags, structured_metadata)
    if not gate["allowed"]:
        return memory_obj._remember_blocked_outcome(content, tags, structured_metadata, gate)

    conn = None
    try:
        conn = memory_obj._conn()
        cursor = conn.execute(
            "INSERT INTO memories (content, type, confidence, evidence_count, created_at) "
            "VALUES (?, ?, ?, 1, ?)",
            (content, "user_defined", importance, datetime.now().isoformat()),
        )
        memory_id = cursor.lastrowid
        conn.commit()

        postwrite = memory_obj._run_remember_postwrite(memory_id, content, importance, tags, structured_metadata)
        return memory_obj._remember_allowed_outcome(memory_id, content, tags, structured_metadata, gate, postwrite)
    except Exception as e:
        return {"success": False, "memory_id": None, "message": f"❌ {e}"}
    finally:
        try:
            conn.close()
        except Exception:
            pass
