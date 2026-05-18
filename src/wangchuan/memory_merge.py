from __future__ import annotations

"""WangChuan merge/version mutation helpers.

低风险拆分目标：
- merge 版本迁移链写面
- 保持 Memory 公开方法签名与返回口径不变
- 不触碰 remember / recall 主链
"""

from datetime import datetime
from typing import Any, Dict


def _direct_insert_memory(conn: Any, content: str, importance: float, created_at: str) -> int:
    cursor = conn.execute(
        "INSERT INTO memories (content, type, confidence, evidence_count, created_at) "
        "VALUES (?, ?, ?, 1, ?)",
        (content, "user_defined", importance, created_at),
    )
    return int(cursor.lastrowid)


def merge(memory_obj: Any, old_query: str, new_content: str, importance: float = 0.8) -> Dict[str, Any]:
    """合并/更新记忆（解决冲突）- 支持版本迁移链。"""
    conn = None
    try:
        memory_obj._ensure_memory_schema_index_table()
        query_text = str(old_query or "").strip()
        now = datetime.now().isoformat(timespec="microseconds")
        conn = memory_obj._conn()

        if query_text.lower().startswith("id:"):
            target_id = int(query_text.split(":", 1)[1].strip())
            rows = conn.execute(
                """
                SELECT m.id, m.content, COALESCE(msi.supersession_chain, ''),
                       COALESCE(msi.conflict_group, ''), COALESCE(msi.memory_type, m.type, 'memory')
                FROM memories m
                LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
                WHERE m.id = ?
                LIMIT 1
                """,
                (target_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT m.id, m.content, COALESCE(msi.supersession_chain, ''),
                       COALESCE(msi.conflict_group, ''), COALESCE(msi.memory_type, m.type, 'memory')
                FROM memories m
                LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
                WHERE m.content LIKE ?
                ORDER BY m.confidence DESC, m.created_at DESC LIMIT 1
                """,
                (f"%{query_text}%",),
            ).fetchall()

        if rows:
            old_id = int(rows[0][0])
            old_content = rows[0][1]
            old_chain = rows[0][2] or ""
            conflict_group = str(rows[0][3] or rows[0][4] or "memory").strip().lower() or "memory"
            new_chain = f"{old_chain}{old_id}," if old_chain else f"{old_id},"

            new_id = _direct_insert_memory(conn, new_content, importance, now)

            if hasattr(memory_obj, "_upsert_memory_schema_index"):
                memory_obj._upsert_memory_schema_index(
                    {
                        "memory_id": old_id,
                        "lifecycle": "superseded",
                        "valid_until": now,
                        "superseded_by": new_id,
                        "supersession_chain": old_chain,
                        "conflict_group": conflict_group,
                        "updated_at": now,
                    },
                    conn=conn,
                )
                memory_obj._upsert_memory_schema_index(
                    {
                        "memory_id": new_id,
                        "memory_type": conflict_group,
                        "conflict_group": conflict_group,
                        "lifecycle": "active",
                        "valid_from": now,
                        "valid_until": None,
                        "superseded_by": None,
                        "supersession_chain": new_chain,
                        "importance": round(float(importance), 3),
                        "confidence": round(float(importance), 3),
                        "updated_at": now,
                    },
                    conn=conn,
                )
            else:
                conn.execute(
                    """
                    UPDATE memory_schema_index
                    SET valid_until = ?, superseded_by = ?, lifecycle = 'superseded'
                    WHERE memory_id = ?
                    """,
                    (now, new_id, old_id),
                )
                conn.execute(
                    """
                    UPDATE memory_schema_index
                    SET supersession_chain = ?, valid_from = ?, valid_until = NULL,
                        superseded_by = NULL, lifecycle = 'active', conflict_group = ?
                    WHERE memory_id = ?
                    """,
                    (new_chain, now, conflict_group, new_id),
                )

            conn.commit()

            if hasattr(memory_obj, "_update_memory_schema_fields"):
                memory_obj._update_memory_schema_fields(
                    old_id,
                    {
                        "lifecycle": "superseded",
                        "valid_until": now,
                        "superseded_by": new_id,
                    },
                )
                memory_obj._update_memory_schema_fields(
                    new_id,
                    {
                        "lifecycle": "active",
                        "valid_from": now,
                        "valid_until": None,
                        "superseded_by": None,
                        "supersession_chain": new_chain,
                        "conflict_group": conflict_group,
                    },
                )

            return {
                "success": True,
                "message": f"✅ 已更新: {old_content[:30]} → {new_content[:30]}",
                "old_id": old_id,
                "new_id": new_id,
                "supersession_chain": new_chain,
                "conflict_group": conflict_group,
            }

        new_id = _direct_insert_memory(conn, new_content, importance, now)

        if hasattr(memory_obj, "_upsert_memory_schema_index"):
            memory_obj._upsert_memory_schema_index(
                {
                    "memory_id": new_id,
                    "memory_type": "memory",
                    "conflict_group": "memory",
                    "lifecycle": "active",
                    "valid_from": now,
                    "valid_until": None,
                    "superseded_by": None,
                    "supersession_chain": "",
                    "importance": round(float(importance), 3),
                    "confidence": round(float(importance), 3),
                    "updated_at": now,
                },
                conn=conn,
            )

        conn.commit()

        if hasattr(memory_obj, "_update_memory_schema_fields"):
            memory_obj._update_memory_schema_fields(
                new_id,
                {
                    "lifecycle": "active",
                    "valid_from": now,
                    "valid_until": None,
                    "superseded_by": None,
                    "supersession_chain": "",
                    "conflict_group": "memory",
                },
            )

        return {
            "success": True,
            "memory_id": new_id,
            "message": f"✅ 已新增记忆: {new_content[:30]}",
            "conflict_group": "memory",
            "supersession_chain": "",
        }
    except Exception as e:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        return {"success": False, "message": f"❌ {e}"}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
