from __future__ import annotations

"""WangChuan multimodal memory helpers.

低风险拆分目标：
- 把 memory_multimodal schema 与最小图片/音频描述能力从 memory_api.py 抽离
- 保持 Memory 的公开方法签名不变
- 不触碰 remember / recall / status 主链
"""

from typing import Any, Dict, List


def ensure_multimodal_table(memory_obj: Any) -> None:
    """确保多模态记忆表存在。"""
    conn = memory_obj._conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_multimodal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                modality TEXT NOT NULL,
                content TEXT NOT NULL,
                mime_type TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (memory_id) REFERENCES memories(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_multimodal_memory ON memory_multimodal(memory_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_multimodal_modality ON memory_multimodal(modality)")
        conn.commit()
    finally:
        conn.close()


def add_image(memory_obj: Any, memory_id: int, image_description: str, mime_type: str = "image/png") -> Dict:
    """为记忆添加图像描述。"""
    try:
        ensure_multimodal_table(memory_obj)
        conn = memory_obj._conn()
        conn.execute(
            "INSERT INTO memory_multimodal (memory_id, modality, content, mime_type) VALUES (?, ?, ?, ?)",
            (memory_id, "image", image_description, mime_type),
        )
        conn.commit()
        conn.close()
        return {"success": True, "message": "✅ 已添加图像描述"}
    except Exception as e:
        return {"success": False, "message": f"❌ {e}"}


def add_audio(memory_obj: Any, memory_id: int, audio_description: str, mime_type: str = "audio/mpeg") -> Dict:
    """为记忆添加音频描述。"""
    try:
        ensure_multimodal_table(memory_obj)
        conn = memory_obj._conn()
        conn.execute(
            "INSERT INTO memory_multimodal (memory_id, modality, content, mime_type) VALUES (?, ?, ?, ?)",
            (memory_id, "audio", audio_description, mime_type),
        )
        conn.commit()
        conn.close()
        return {"success": True, "message": "✅ 已添加音频描述"}
    except Exception as e:
        return {"success": False, "message": f"❌ {e}"}


def get_multimodal(memory_obj: Any, memory_id: int) -> List[Dict]:
    """获取记忆的多模态内容。"""
    try:
        conn = memory_obj._conn()
        rows = conn.execute(
            "SELECT id, modality, content, mime_type FROM memory_multimodal WHERE memory_id = ?",
            (memory_id,),
        ).fetchall()
        conn.close()
        return [
            {"id": r[0], "modality": r[1], "content": r[2], "mime_type": r[3]}
            for r in rows
        ]
    except Exception:
        return []
