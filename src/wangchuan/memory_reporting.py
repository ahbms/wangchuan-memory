from __future__ import annotations

"""WangChuan memory reporting/export helpers.

低风险拆分目标：
- 把统计、质量指标、导出能力从 memory_api.py 抽离
- 保持 Memory 的公开方法签名不变
- 不触碰 remember / recall / status 主链
"""

import csv
import io
import json
import os
from typing import Any, Dict


def get_memory_stats(memory_obj: Any) -> Dict[str, Any]:
    """获取记忆系统详细统计信息。"""
    try:
        conn = memory_obj._conn()

        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

        layer_stats = conn.execute(
            """
            SELECT COALESCE(msi.source_layer, 'unknown'), COUNT(*)
            FROM memories m
            LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
            GROUP BY msi.source_layer
            """
        ).fetchall()

        type_stats = conn.execute(
            """
            SELECT COALESCE(msi.memory_type, 'unknown'), COUNT(*)
            FROM memories m
            LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
            GROUP BY msi.memory_type
            """
        ).fetchall()

        confidence_stats = conn.execute(
            """
            SELECT
                CASE
                    WHEN confidence >= 0.9 THEN 'high'
                    WHEN confidence >= 0.7 THEN 'medium'
                    ELSE 'low'
                END as conf_level,
                COUNT(*)
            FROM memories
            GROUP BY conf_level
            """
        ).fetchall()

        recent = conn.execute(
            """
            SELECT content, created_at, confidence
            FROM memories
            ORDER BY created_at DESC
            LIMIT 10
            """
        ).fetchall()

        temporal_stats = conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN valid_until IS NOT NULL THEN 1 ELSE 0 END) as expired,
                SUM(CASE WHEN superseded_by IS NOT NULL THEN 1 ELSE 0 END) as superseded
            FROM memory_schema_index
            """
        ).fetchone()

        conn.close()

        return {
            "total": total,
            "by_layer": {row[0]: row[1] for row in layer_stats},
            "by_type": {row[0]: row[1] for row in type_stats},
            "by_confidence": {row[0]: row[1] for row in confidence_stats},
            "recent_activity": [
                {"content": r[0][:50], "created_at": r[1], "confidence": r[2]}
                for r in recent
            ],
            "temporal": {
                "total": temporal_stats[0] or 0,
                "expired": temporal_stats[1] or 0,
                "superseded": temporal_stats[2] or 0,
            },
        }
    except Exception as e:
        return {"error": str(e)}


def get_quality_metrics(memory_obj: Any) -> Dict[str, Any]:
    """获取记忆系统质量监控指标。"""
    try:
        conn = memory_obj._conn()

        confidence_stats = conn.execute(
            """
            SELECT
                AVG(confidence) as avg_conf,
                MIN(confidence) as min_conf,
                MAX(confidence) as max_conf,
                COUNT(*) as total
            FROM memories
            """
        ).fetchone()

        confidence_dist = conn.execute(
            """
            SELECT
                CASE
                    WHEN confidence >= 0.9 THEN 'excellent'
                    WHEN confidence >= 0.7 THEN 'good'
                    WHEN confidence >= 0.5 THEN 'fair'
                    ELSE 'poor'
                END as level,
                COUNT(*) as count
            FROM memories
            GROUP BY level
            """
        ).fetchall()

        noise_count = 0
        total_count = confidence_stats[3] or 0
        if total_count > 0:
            sample_size = min(100, total_count)
            samples = conn.execute(
                "SELECT content FROM memories ORDER BY RANDOM() LIMIT ?",
                (sample_size,),
            ).fetchall()
            noise_count = sum(1 for row in samples if memory_obj._is_recall_noise(row[0]))
            noise_count = int(noise_count * total_count / sample_size)

        noise_rate = noise_count / total_count if total_count > 0 else 0.0

        recent_recalls = conn.execute(
            """
            SELECT COUNT(*) FROM memories
            WHERE created_at > datetime('now', '-7 days')
            """
        ).fetchone()[0] or 0

        try:
            health = memory_obj.user_healthcheck()
        except Exception:
            health = {"passed": 0, "total": 0, "status": "unknown"}

        conn.close()

        return {
            "confidence": {
                "avg": round(confidence_stats[0] or 0.0, 3),
                "min": round(confidence_stats[1] or 0.0, 3),
                "max": round(confidence_stats[2] or 0.0, 3),
                "distribution": {row[0]: row[1] for row in confidence_dist},
            },
            "noise": {
                "rate": round(noise_rate, 3),
                "count": noise_count,
                "total": total_count,
            },
            "retrieval": {
                "recent_new_memories": recent_recalls,
                "7d_activity": round(recent_recalls / 7, 1),
            },
            "health": {
                "passed": health.get("passed", 0),
                "total": health.get("total", 0),
                "status": health.get("status", "unknown"),
            },
        }
    except Exception as e:
        return {"error": str(e)}


def export_memories(memory_obj: Any, format: str = "json", filepath: str = None, limit: int = None) -> Dict[str, Any]:
    """导出记忆到文件。"""
    try:
        conn = memory_obj._conn()

        query = """
            SELECT m.id, m.content, m.confidence, m.created_at,
                   COALESCE(msi.source_layer, '') as source_layer,
                   COALESCE(msi.memory_type, '') as memory_type,
                   COALESCE(msi.valid_from, '') as valid_from,
                   COALESCE(msi.valid_until, '') as valid_until
            FROM memories m
            LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
            ORDER BY m.created_at DESC
        """

        if limit:
            query += f" LIMIT {limit}"

        rows = conn.execute(query).fetchall()
        conn.close()

        data = []
        for row in rows:
            data.append(
                {
                    "id": row[0],
                    "content": row[1],
                    "confidence": row[2],
                    "created_at": row[3],
                    "source_layer": row[4],
                    "memory_type": row[5],
                    "valid_from": row[6],
                    "valid_until": row[7],
                }
            )

        if format == "json":
            content = json.dumps(data, ensure_ascii=False, indent=2)
        elif format == "csv":
            output = io.StringIO()
            if data:
                writer = csv.DictWriter(output, fieldnames=data[0].keys())
                writer.writeheader()
                writer.writerows(data)
            content = output.getvalue()
        else:
            return {"success": False, "error": f"Unsupported format: {format}"}

        if filepath:
            os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
            with open(filepath, "w", encoding="utf-8", newline="" if format == "csv" else None) as f:
                f.write(content)
            return {"success": True, "format": format, "count": len(data), "data": None, "filepath": filepath}

        return {"success": True, "format": format, "count": len(data), "data": content, "filepath": None}
    except Exception as e:
        return {"success": False, "error": str(e)}
