from __future__ import annotations

"""WangChuan delete helpers.

低风险拆分目标：
- 抽离 forget / delete-ish 轻量维护动作
- 保持 Memory 公开方法签名不变
- 不触碰 remember / recall 主链
"""

import sqlite3
from typing import Any, Dict


def forget(memory_obj: Any, query: str) -> Dict[str, Any]:
    """删除匹配的记忆（谨慎使用）。"""
    try:
        conn = memory_obj._conn()
        cursor = conn.execute(
            "DELETE FROM memories WHERE content LIKE ?",
            (f"%{query}%",),
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()

        try:
            memory_obj._get_local_vector().ensure_table()
            with sqlite3.connect(memory_obj.db_path) as conn2:
                conn2.execute(
                    "DELETE FROM memory_embeddings WHERE memory_id IN (SELECT id FROM memories WHERE content LIKE ?)",
                    (f"%{query}%",),
                )
                conn2.commit()
        except Exception:
            pass

        return {
            "success": True,
            "deleted": deleted,
            "message": f"🗑️ 删除了 {deleted} 条记忆",
        }
    except Exception as e:
        return {"success": False, "deleted": 0, "message": f"❌ {e}"}
