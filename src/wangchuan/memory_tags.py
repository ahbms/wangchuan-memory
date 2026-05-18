from __future__ import annotations

"""WangChuan memory tag helpers.

低风险拆分目标：
- 把 tag CRUD 与 tag table schema 从 memory_api.py 抽离
- 保持 Memory 的公开方法签名不变
- 不触碰 remember / recall / status 主链
"""

from typing import Any, Dict, List


def ensure_tags_table(memory_obj: Any) -> None:
    """确保标签表存在。"""
    conn = memory_obj._conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(memory_id, tag)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_memory ON memory_tags(memory_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_tag ON memory_tags(tag)")
        conn.commit()
    finally:
        conn.close()


def add_tag(memory_obj: Any, memory_id: int, tag: str) -> Dict:
    """为记忆添加标签。"""
    try:
        ensure_tags_table(memory_obj)
        conn = memory_obj._conn()
        normalized_tag = str(tag or "").strip().lower()
        conn.execute(
            "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
            (memory_id, normalized_tag),
        )
        conn.commit()
        conn.close()
        return {"success": True, "message": f"✅ 已添加标签: {normalized_tag}"}
    except Exception as e:
        return {"success": False, "message": f"❌ {e}"}


def remove_tag(memory_obj: Any, memory_id: int, tag: str) -> Dict:
    """移除记忆的标签。"""
    try:
        conn = memory_obj._conn()
        conn.execute(
            "DELETE FROM memory_tags WHERE memory_id = ? AND tag = ?",
            (memory_id, str(tag or "").strip().lower()),
        )
        conn.commit()
        conn.close()
        return {"success": True, "message": "✅ 已移除标签"}
    except Exception as e:
        return {"success": False, "message": f"❌ {e}"}


def get_tags(memory_obj: Any, memory_id: int) -> List[str]:
    """获取记忆的所有标签。"""
    try:
        conn = memory_obj._conn()
        rows = conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ?",
            (memory_id,),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def find_by_tag(memory_obj: Any, tag: str, limit: int = 10) -> List[Dict]:
    """按标签搜索记忆。"""
    try:
        conn = memory_obj._conn()
        rows = conn.execute(
            """
            SELECT m.id, m.content, m.created_at
            FROM memories m
            JOIN memory_tags t ON m.id = t.memory_id
            WHERE t.tag = ?
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            (str(tag or "").strip().lower(), limit),
        ).fetchall()
        conn.close()
        return [{"memory_id": r[0], "content": r[1], "created_at": r[2]} for r in rows]
    except Exception:
        return []


def list_all_tags(memory_obj: Any) -> List[Dict]:
    """列出所有标签及使用次数。"""
    try:
        conn = memory_obj._conn()
        rows = conn.execute(
            "SELECT tag, COUNT(*) as count FROM memory_tags GROUP BY tag ORDER BY count DESC"
        ).fetchall()
        conn.close()
        return [{"tag": r[0], "count": r[1]} for r in rows]
    except Exception:
        return []
