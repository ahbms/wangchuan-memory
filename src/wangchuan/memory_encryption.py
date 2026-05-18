from __future__ import annotations

"""WangChuan encrypted memory helpers.

低风险拆分目标：
- 把 memory_encrypted schema 与加/解密逻辑从 memory_api.py 抽离
- 保持 Memory 的公开方法签名不变
- 不触碰 remember / recall / status 主链
"""

import os
from typing import Any, Dict


def ensure_encrypted_table(memory_obj: Any) -> None:
    """确保加密记忆表存在。"""
    conn = memory_obj._conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_encrypted (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                encrypted_content BLOB NOT NULL,
                iv BLOB NOT NULL,
                algorithm TEXT DEFAULT 'AES-GCM',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (memory_id) REFERENCES memories(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_encrypted_memory ON memory_encrypted(memory_id)")
        conn.commit()
    finally:
        conn.close()


def encrypt_memory(memory_obj: Any, memory_id: int, key: str = None) -> Dict:
    """加密指定记忆。"""
    from cryptography.fernet import Fernet

    try:
        if key is None:
            key = os.getenv("MEMORY_ENCRYPTION_KEY")
        if not key:
            return {"success": False, "message": "❌ 未设置加密密钥 MEMORY_ENCRYPTION_KEY"}

        conn = memory_obj._conn()
        row = conn.execute("SELECT content FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if not row:
            conn.close()
            return {"success": False, "message": "❌ 记忆不存在"}

        content = row[0]
        f = Fernet(key.encode() if isinstance(key, str) else key)
        encrypted = f.encrypt(content.encode())

        ensure_encrypted_table(memory_obj)
        conn.execute(
            "INSERT INTO memory_encrypted (memory_id, encrypted_content, iv, algorithm) VALUES (?, ?, ?, ?)",
            (memory_id, encrypted, b"", "FERNET"),
        )
        conn.execute("UPDATE memories SET content = '[已加密]' WHERE id = ?", (memory_id,))
        conn.commit()
        conn.close()

        return {"success": True, "message": "✅ 记忆已加密"}
    except ImportError:
        return {"success": False, "message": "❌ 请安装 cryptography: pip install cryptography"}
    except Exception as e:
        return {"success": False, "message": f"❌ {e}"}


def decrypt_memory(memory_obj: Any, memory_id: int, key: str = None) -> Dict:
    """解密指定记忆。"""
    from cryptography.fernet import Fernet

    try:
        if key is None:
            key = os.getenv("MEMORY_ENCRYPTION_KEY")
        if not key:
            return {"success": False, "message": "❌ 未设置解密密钥", "content": None}

        conn = memory_obj._conn()
        row = conn.execute(
            "SELECT encrypted_content FROM memory_encrypted WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        if not row:
            conn.close()
            return {"success": False, "message": "❌ 加密记忆不存在", "content": None}

        f = Fernet(key.encode() if isinstance(key, str) else key)
        decrypted = f.decrypt(row[0]).decode()

        conn.execute("UPDATE memories SET content = ? WHERE id = ?", (decrypted, memory_id))
        conn.execute("DELETE FROM memory_encrypted WHERE memory_id = ?", (memory_id,))
        conn.commit()
        conn.close()

        return {"success": True, "content": decrypted, "message": "✅ 记忆已解密"}
    except Exception as e:
        return {"success": False, "content": None, "message": f"❌ 解密失败: {e}"}
