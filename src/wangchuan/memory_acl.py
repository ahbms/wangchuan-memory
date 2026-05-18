from __future__ import annotations

"""WangChuan memory ACL and user-view helpers.

低风险拆分目标：
- 把 memory_acl schema 与最小授权/查询逻辑从 memory_api.py 抽离
- 保持 Memory 的公开方法签名不变
- 不触碰 remember / recall / status 主链
"""

from typing import Any, Dict, List


def ensure_acl_table(memory_obj: Any) -> None:
    """确保访问控制表存在。"""
    conn = memory_obj._conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_acl (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                memory_id INTEGER NOT NULL,
                permission TEXT DEFAULT 'read',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, memory_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_acl_user ON memory_acl(user_id)")
        conn.commit()
    finally:
        conn.close()


def grant_access(memory_obj: Any, user_id: str, memory_id: int, permission: str = "read") -> Dict:
    """授予用户访问记忆的权限。"""
    try:
        ensure_acl_table(memory_obj)
        conn = memory_obj._conn()
        conn.execute(
            "INSERT OR REPLACE INTO memory_acl (user_id, memory_id, permission) VALUES (?, ?, ?)",
            (user_id, memory_id, permission),
        )
        conn.commit()
        conn.close()
        return {"success": True, "message": "✅ 已授权"}
    except Exception as e:
        return {"success": False, "message": f"❌ {e}"}


def revoke_access(memory_obj: Any, user_id: str, memory_id: int) -> Dict:
    """撤销用户访问权限。"""
    try:
        conn = memory_obj._conn()
        conn.execute(
            "DELETE FROM memory_acl WHERE user_id = ? AND memory_id = ?",
            (user_id, memory_id),
        )
        conn.commit()
        conn.close()
        return {"success": True, "message": "✅ 已撤销授权"}
    except Exception as e:
        return {"success": False, "message": f"❌ {e}"}


def check_access(memory_obj: Any, user_id: str, memory_id: int, required_permission: str = "read") -> bool:
    """检查用户是否有访问权限。"""
    try:
        conn = memory_obj._conn()
        row = conn.execute(
            "SELECT permission FROM memory_acl WHERE user_id = ? AND memory_id = ?",
            (user_id, memory_id),
        ).fetchone()
        conn.close()
        if not row:
            return False
        permission = row[0]
        if required_permission == "read":
            return permission in ["read", "write", "admin"]
        if required_permission == "write":
            return permission in ["write", "admin"]
        return permission == "admin"
    except Exception:
        return False


def get_user_memories(memory_obj: Any, user_id: str, limit: int = 50) -> List[Dict]:
    """获取某用户可访问的记忆。"""
    try:
        conn = memory_obj._conn()
        rows = conn.execute(
            """
            SELECT m.id, m.content, m.type, m.created_at, acl.permission
            FROM memories m
            JOIN memory_acl acl ON m.id = acl.memory_id
            WHERE acl.user_id = ?
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        conn.close()
        return [
            {
                "memory_id": r[0],
                "content": r[1],
                "type": r[2],
                "created_at": r[3],
                "permission": r[4],
            }
            for r in rows
        ]
    except Exception:
        return []
