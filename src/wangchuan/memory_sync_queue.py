from __future__ import annotations

"""WangChuan distributed sync queue helpers.

低风险拆分目标：
- 把 memory_sync 队列表 schema 与最小 CRUD 从 memory_api.py 抽离
- 保持 Memory 的公开方法签名不变
- 不触碰 remember / recall / status 主链
"""

from typing import Any, Dict, List


def ensure_sync_table(memory_obj: Any) -> None:
    """确保分布式同步表存在。"""
    conn = memory_obj._conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_sync (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                operation TEXT NOT NULL,
                node_id TEXT NOT NULL,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                synced INTEGER DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_memory ON memory_sync(memory_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_synced ON memory_sync(synced)")
        conn.commit()
    finally:
        conn.close()


def sync_to_node(memory_obj: Any, memory_id: int, node_id: str, operation: str = "create") -> Dict:
    """同步记忆到指定节点。"""
    try:
        ensure_sync_table(memory_obj)
        conn = memory_obj._conn()
        conn.execute(
            "INSERT INTO memory_sync (memory_id, operation, node_id) VALUES (?, ?, ?)",
            (memory_id, operation, node_id),
        )
        conn.commit()
        conn.close()
        return {"success": True, "message": "✅ 已添加到同步队列"}
    except Exception as e:
        return {"success": False, "message": f"❌ {e}"}


def get_sync_status(memory_obj: Any, memory_id: int) -> List[Dict]:
    """获取记忆的同步状态。"""
    try:
        conn = memory_obj._conn()
        rows = conn.execute(
            "SELECT operation, node_id, synced, timestamp FROM memory_sync WHERE memory_id = ?",
            (memory_id,),
        ).fetchall()
        conn.close()
        return [{"operation": r[0], "node_id": r[1], "synced": bool(r[2]), "timestamp": r[3]} for r in rows]
    except Exception:
        return []


def sync_all_pending(memory_obj: Any, node_id: str = None) -> Dict:
    """同步所有待同步的记忆。"""
    try:
        conn = memory_obj._conn()
        if node_id:
            rows = conn.execute(
                "SELECT id, memory_id, operation FROM memory_sync WHERE synced = 0 AND node_id = ?",
                (node_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, memory_id, operation FROM memory_sync WHERE synced = 0"
            ).fetchall()

        count = len(rows)
        for row in rows:
            conn.execute("UPDATE memory_sync SET synced = 1 WHERE id = ?", (row[0],))
        conn.commit()
        conn.close()
        return {"success": True, "synced_count": count, "message": f"✅ 已同步 {count} 条记录"}
    except Exception as e:
        return {"success": False, "synced_count": 0, "message": f"❌ {e}"}
