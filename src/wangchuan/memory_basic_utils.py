from __future__ import annotations

"""WangChuan basic utility helpers.

这一层承接 memory_api 中低风险基础工具簇：
- legacy memory_schema_index migration compatibility
- write gate sidelog append
- bool / float coercion
- turn/source anchor parsing
- message signature / anchor helpers
- ISO datetime parse
- normalized tags / schema dir / cli-mirror helpers

目标：
- 不改变 Memory 的公开/内部方法签名
- 收敛散落在 memory_api.py 中的基础工具实现
"""

from datetime import datetime
from typing import Any, Dict
import hashlib
import json
import os
import re
import sqlite3

from pathlib import Path
from typing import List

try:
    from wangchuan.paths import default_db_path, state_root, workspace_root
except ImportError:
    from wangchuan.paths import default_db_path, state_root, workspace_root


def migrate_schema(memory_obj: Any) -> None:
    """数据库迁移：补齐 legacy memory_schema_index 的时序字段。"""
    if not os.path.exists(memory_obj.db_path):
        return
    try:
        conn = sqlite3.connect(memory_obj.db_path)
        try:
            try:
                conn.execute("ALTER TABLE memory_schema_index ADD COLUMN valid_from TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE memory_schema_index ADD COLUMN valid_until TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE memory_schema_index ADD COLUMN superseded_by INTEGER")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE memory_schema_index ADD COLUMN supersession_chain TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def write_gate_sidelog(payload: Dict[str, Any]) -> None:
    side_path = state_root() / "memory_write_gate.log"
    side_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "event_ts": datetime.now().isoformat(timespec="seconds"),
        "stage": "memory_write_gate",
        **payload,
    }
    with side_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def extract_turn_signature(content: str) -> str:
    text = str(content or "")
    for pattern in [r"\bturn_signature=([^\s|]+)", r"\bturn[:=]\s*([^\s|]+)"]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def extract_source_anchor(content: str) -> str:
    text = str(content or "")
    match = re.search(r"来源:\s*([^\n]+)", text)
    if match:
        return match.group(1).strip()
    return ""


def message_content_signature(content: str) -> str:
    normalized = re.sub(r"\s+", " ", str(content or "").strip())
    return hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()[:16]


def build_turn_signature_from_message(session_id: str, message_id: Any, timestamp: str, content: str) -> str:
    base = "|".join([
        str(session_id or "").strip(),
        str(message_id or "").strip(),
        str(timestamp or "").strip(),
        message_content_signature(content),
    ])
    return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()


def build_message_anchor(session_id: str, message_id: str, turn_signature: str) -> str:
    session_token = str(session_id or "").strip()
    if not session_token and not turn_signature:
        return ""
    parts = []
    if message_id:
        parts.append(f"message_id={message_id}")
    if turn_signature:
        parts.append(f"turn={turn_signature}")
    suffix = "&".join(parts)
    if session_token and suffix:
        return f"gm_messages/{session_token}?{suffix}"
    if session_token:
        return f"gm_messages/{session_token}"
    return f"gm_messages?{suffix}" if suffix else "gm_messages"


def parse_iso_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def memory_schema_dir(db_path: str | None = None) -> Path:
    base_dir = state_root() / "memory_schema"
    base_dir.mkdir(parents=True, exist_ok=True)
    if not db_path:
        return base_dir

    try:
        resolved_db = Path(db_path).expanduser().resolve()
        default_db = default_db_path().expanduser().resolve()
    except Exception:
        return base_dir

    if resolved_db == default_db:
        return base_dir

    try:
        current_workspace = workspace_root().expanduser().resolve()
        if (
            resolved_db.parent == current_workspace
            or current_workspace in resolved_db.parents
            or resolved_db.parent == current_workspace.parent
        ):
            return base_dir
    except Exception:
        pass

    db_digest = hashlib.sha1(str(resolved_db).encode("utf-8", errors="ignore")).hexdigest()[:12]
    scoped_dir = base_dir / f"db_{db_digest}"
    scoped_dir.mkdir(parents=True, exist_ok=True)
    return scoped_dir


def normalize_tags(tags: List[str] | None) -> List[str]:
    return [str(t).strip() for t in (tags or []) if str(t).strip()]


def is_cli_mirror_session(session_id: str) -> bool:
    return str(session_id or "").strip().lower() in {"cli"}
