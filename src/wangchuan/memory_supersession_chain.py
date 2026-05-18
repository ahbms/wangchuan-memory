from __future__ import annotations

"""WangChuan supersession chain helpers.

这一层承接 memory_api.get_supersession_chain 中的版本链读取逻辑：
- 读取 supersession_chain anchor
- 归一化 chain ids
- 拉取版本链记录
- 输出 truth_state 视图
"""

from typing import Any, Dict, List


def _normalize_supersession_chain_ids(anchor: Any, memory_id: int) -> List[int]:
    chain_ids: List[int] = []
    if anchor and anchor[0]:
        for part in str(anchor[0]).split(','):
            part = part.strip()
            if part.isdigit():
                chain_ids.append(int(part))
    chain_ids.append(int(memory_id))
    return sorted(set(chain_ids))


def get_supersession_chain(memory_obj: Any, memory_id: int) -> List[Dict[str, Any]]:
    """获取某个记忆的版本迁移链。"""
    try:
        memory_obj._ensure_memory_schema_index_table()
        conn = memory_obj._conn()

        anchor = conn.execute(
            """
            SELECT COALESCE(msi.supersession_chain, '')
            FROM memory_schema_index msi
            WHERE msi.memory_id = ?
            LIMIT 1
            """,
            (memory_id,),
        ).fetchone()

        chain_ids = _normalize_supersession_chain_ids(anchor, memory_id)
        placeholders = ",".join("?" for _ in chain_ids)
        rows = conn.execute(
            f"""
            SELECT m.id, m.content, msi.valid_from, msi.valid_until, msi.supersession_chain,
                   COALESCE(msi.lifecycle, 'active')
            FROM memories m
            JOIN memory_schema_index msi ON m.id = msi.memory_id
            WHERE msi.memory_id IN ({placeholders})
            ORDER BY msi.valid_from ASC
            """,
            tuple(chain_ids),
        ).fetchall()

        conn.close()

        return [
            {
                "id": row[0],
                "content": row[1],
                "valid_from": row[2],
                "valid_until": row[3],
                "supersession_chain": row[4],
                "lifecycle": row[5],
                "truth_state": "historical" if row[3] else ("superseded" if row[5] == "superseded" else "current"),
            }
            for row in rows
        ]
    except Exception as e:
        return [{"error": str(e)}]
