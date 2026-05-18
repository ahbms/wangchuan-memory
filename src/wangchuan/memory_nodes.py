from __future__ import annotations

"""WangChuan node registry helpers.

低风险拆分目标：
- 把 memory_nodes registry 的 schema 与 CRUD 从 memory_api.py 抽离
- 保持 Memory 的公开方法签名不变
- 不触碰 remember / recall / status 主链
"""

import time
from typing import Any, Dict, List


def ensure_nodes_table(memory_obj: Any) -> None:
    """确保节点表存在。"""
    conn = memory_obj._conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_nodes (
                node_id TEXT PRIMARY KEY,
                node_url TEXT NOT NULL,
                node_name TEXT,
                status TEXT DEFAULT 'active',
                last_seen TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def register_node(memory_obj: Any, node_url: str, node_name: str = None) -> Dict:
    """注册分布式节点。"""
    try:
        ensure_nodes_table(memory_obj)
        conn = memory_obj._conn()
        node_id = f"node_{int(time.time() * 1000)}"
        conn.execute(
            "INSERT OR REPLACE INTO memory_nodes (node_id, node_url, node_name, status) VALUES (?, ?, ?, ?)",
            (node_id, node_url, node_name or node_id, "active"),
        )
        conn.commit()
        conn.close()
        return {"success": True, "node_id": node_id, "message": f"✅ 节点已注册: {node_id}"}
    except Exception as e:
        return {"success": False, "node_id": None, "message": f"❌ {e}"}


def list_nodes(memory_obj: Any) -> List[Dict]:
    """列出所有注册的节点。"""
    try:
        ensure_nodes_table(memory_obj)
        conn = memory_obj._conn()
        rows = conn.execute("SELECT node_id, node_url, node_name, status FROM memory_nodes").fetchall()
        conn.close()
        return [{"node_id": r[0], "node_url": r[1], "node_name": r[2], "status": r[3]} for r in rows]
    except Exception:
        return []
