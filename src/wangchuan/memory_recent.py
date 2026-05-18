from __future__ import annotations

"""WangChuan recent/read helpers.

低风险拆分目标：
- 抽离 recent 这类纯读面辅助能力
- 保持 Memory 的公开方法签名不变
- 不触碰 remember / recall 主链实现
"""

from typing import Any, Dict, List


def recent(memory_obj: Any, limit: int = 10) -> List[Dict[str, Any]]:
    """获取最近的记忆。"""
    try:
        memory_obj._ensure_memory_schema_index_table()
        conn = memory_obj._conn()
        rows = conn.execute(
            """
            SELECT
                m.id,
                m.content,
                m.type,
                m.confidence,
                m.created_at,
                COALESCE(msi.memory_type, m.type, 'unknown') AS memory_type,
                COALESCE(msi.lifecycle, 'unknown') AS lifecycle,
                COALESCE(msi.promotion_state, 'unknown') AS promotion_state,
                COALESCE(msi.recall_source_type, 'unknown') AS recall_source_type,
                COALESCE(msi.hot_memory_candidate, 0) AS hot_memory_candidate,
                msi.quality_score,
                COALESCE(msi.schema_version, 'legacy-main-table') AS schema_version,
                COALESCE(msi.source_layer, 'unknown') AS source_layer
            FROM memories m
            LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
            WHERE COALESCE(msi.removed_at, '') = ''
            ORDER BY datetime(m.created_at) DESC, m.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "content": r[1],
                "type": r[2],
                "confidence": r[3],
                "created_at": r[4],
                "memory_type": r[5],
                "lifecycle": r[6],
                "promotion_state": r[7],
                "recall_source_type": r[8],
                "hot_memory_candidate": bool(r[9]),
                "quality_score": r[10],
                "schema_version": r[11],
                "source_layer": r[12],
                "reader": "memory_api.recent",
                "structured": True,
            }
            for r in rows
        ]
    except Exception:
        return []
