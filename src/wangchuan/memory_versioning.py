from __future__ import annotations

"""WangChuan versioning helpers.

低风险拆分目标：
- history / rollback 版本链读写
- 保留 Memory 公共签名不变
- 不触碰 remember / recall 主链
"""

from typing import Any, Dict, List


def history(memory_obj: Any, memory_id: int = None, query: str = None, limit: int = 10) -> List[Dict[str, Any]]:
    """获取记忆版本历史（时间旅行查询）。"""
    try:
        memory_obj._ensure_memory_schema_index_table()
        conn = memory_obj._conn()

        if memory_id:
            rows = conn.execute(
                """
                SELECT m.id, m.content, m.created_at, msi.valid_from, msi.valid_until, msi.superseded_by, msi.supersession_chain,
                       COALESCE(msi.lifecycle, 'active')
                FROM memories m
                LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
                WHERE msi.supersession_chain LIKE ? OR msi.superseded_by = ? OR m.id = ?
                ORDER BY COALESCE(msi.valid_from, m.created_at) DESC
                """,
                (f"%{memory_id},%", memory_id, memory_id),
            ).fetchall()
        elif query:
            rows = conn.execute(
                """
                SELECT m.id, m.content, m.created_at, msi.valid_from, msi.valid_until, msi.superseded_by, msi.supersession_chain,
                       COALESCE(msi.lifecycle, 'active')
                FROM memories m
                LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
                WHERE m.content LIKE ?
                ORDER BY COALESCE(msi.valid_from, m.created_at) DESC
                LIMIT ?
                """,
                (f"%{query}%", limit),
            ).fetchall()
        else:
            return []

        conn.close()

        results = []
        for row in rows:
            results.append(
                {
                    "memory_id": row[0],
                    "content": row[1],
                    "created_at": row[2],
                    "valid_from": row[3],
                    "valid_until": row[4],
                    "superseded_by": row[5],
                    "supersession_chain": row[6],
                    "lifecycle": row[7],
                    "truth_state": "historical" if row[4] else ("superseded" if row[7] == "superseded" else "current"),
                }
            )

        return results
    except Exception:
        return []


def rollback(memory_obj: Any, memory_id: int, target_version: int = None) -> Dict[str, Any]:
    """回滚记忆到指定版本。"""
    try:
        history_rows = memory_obj.history(memory_id=memory_id, limit=20)
        if not history_rows:
            return {"success": False, "message": "❌ 未找到版本历史"}

        if target_version is None:
            for item in history_rows:
                if item["superseded_by"] == memory_id:
                    target_version = item["memory_id"]
                    break
            if target_version is None:
                return {"success": False, "message": "❌ 没有可回滚的版本"}

        target = next((item for item in history_rows if item["memory_id"] == target_version), None)
        if not target:
            return {"success": False, "message": "❌ 目标版本不存在"}

        return memory_obj.merge(f"id:{memory_id}", target["content"])
    except Exception as e:
        return {"success": False, "message": f"❌ {e}"}
