from __future__ import annotations

"""WangChuan memory_schema_index SQL helpers.

这一层承接 memory_api 中 memory_schema_index 的底层 SQL 维护逻辑：
- ensure table / column / index
- upsert row
- delete row

约束：
- 不改写 sidecar JSON 协议和上层统计口径
- 仍由调用方（Memory）提供 conn/coerce helper
- 优先保持与 memory_api 现有 schema/index 结构一致
"""

from datetime import datetime
from typing import Any, Dict
import sqlite3


def ensure_memory_schema_index_table(memory_obj: Any) -> None:
    if getattr(memory_obj, "_memory_schema_index_ready", False):
        return
    conn = memory_obj._conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_schema_index (
                memory_id INTEGER PRIMARY KEY,
                schema_version TEXT,
                source_layer TEXT,
                source_anchor TEXT,
                source_session TEXT,
                turn_signature TEXT,
                memory_type TEXT,
                user_explicit INTEGER DEFAULT 0,
                is_test_data INTEGER DEFAULT 0,
                promotion_reason TEXT,
                hot_memory_candidate INTEGER DEFAULT 0,
                provenance TEXT,
                lifecycle TEXT,
                dedupe_key TEXT,
                conflict_group TEXT,
                quality_score REAL,
                evidence_level TEXT,
                promotion_state TEXT,
                last_confirmed_at TEXT,
                hotness_score REAL,
                recall_source_type TEXT,
                content_preview TEXT,
                subject_domain TEXT,
                importance REAL,
                confidence REAL,
                trigger_count INTEGER,
                last_recall TEXT,
                removed_at TEXT,
                updated_at TEXT,
                valid_from TEXT DEFAULT CURRENT_TIMESTAMP,
                valid_until TEXT,
                superseded_by INTEGER,
                supersession_chain TEXT
            )
            """
        )

        existing_cols = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(memory_schema_index)").fetchall()
        }

        def add_column(col_name: str, column_sql: str) -> None:
            if col_name in existing_cols:
                return
            conn.execute(f"ALTER TABLE memory_schema_index ADD COLUMN {column_sql}")
            existing_cols.add(col_name)

        add_column("schema_version", "schema_version TEXT")
        add_column("source_layer", "source_layer TEXT")
        add_column("source_anchor", "source_anchor TEXT")
        add_column("source_session", "source_session TEXT")
        add_column("turn_signature", "turn_signature TEXT")
        add_column("memory_type", "memory_type TEXT")
        add_column("user_explicit", "user_explicit INTEGER DEFAULT 0")
        add_column("is_test_data", "is_test_data INTEGER DEFAULT 0")
        add_column("promotion_reason", "promotion_reason TEXT")
        add_column("hot_memory_candidate", "hot_memory_candidate INTEGER DEFAULT 0")
        add_column("provenance", "provenance TEXT")
        add_column("lifecycle", "lifecycle TEXT")
        add_column("dedupe_key", "dedupe_key TEXT")
        add_column("conflict_group", "conflict_group TEXT")
        add_column("quality_score", "quality_score REAL")
        add_column("evidence_level", "evidence_level TEXT")
        add_column("promotion_state", "promotion_state TEXT")
        add_column("last_confirmed_at", "last_confirmed_at TEXT")
        add_column("hotness_score", "hotness_score REAL")
        add_column("recall_source_type", "recall_source_type TEXT")
        add_column("content_preview", "content_preview TEXT")
        add_column("subject_domain", "subject_domain TEXT")
        add_column("importance", "importance REAL")
        add_column("confidence", "confidence REAL")
        add_column("trigger_count", "trigger_count INTEGER")
        add_column("last_recall", "last_recall TEXT")
        add_column("removed_at", "removed_at TEXT")
        add_column("updated_at", "updated_at TEXT")
        add_column("valid_from", "valid_from TEXT")
        add_column("valid_until", "valid_until TEXT")
        add_column("superseded_by", "superseded_by INTEGER")
        add_column("supersession_chain", "supersession_chain TEXT")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_schema_index_promotion_state ON memory_schema_index(promotion_state)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_schema_index_lifecycle ON memory_schema_index(lifecycle)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_schema_index_dedupe_key ON memory_schema_index(dedupe_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_schema_index_recall_source_type ON memory_schema_index(recall_source_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_schema_index_valid_from ON memory_schema_index(valid_from)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_schema_index_valid_until ON memory_schema_index(valid_until)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_schema_index_superseded_by ON memory_schema_index(superseded_by)")
        conn.commit()
        memory_obj._memory_schema_index_ready = True
    finally:
        conn.close()


def upsert_memory_schema_index(memory_obj: Any, payload: Dict[str, Any], conn: sqlite3.Connection | None = None) -> None:
    if not payload or payload.get("memory_id") in (None, ""):
        return
    try:
        memory_id = int(payload.get("memory_id"))
    except Exception:
        return
    owns_conn = conn is None
    if owns_conn:
        ensure_memory_schema_index_table(memory_obj)
        conn = memory_obj._conn()
    try:
        conn.execute(
            """
            INSERT INTO memory_schema_index (
                memory_id, schema_version, source_layer, source_anchor, source_session, turn_signature,
                memory_type, user_explicit, is_test_data, promotion_reason, hot_memory_candidate,
                provenance, lifecycle, dedupe_key, conflict_group, quality_score, evidence_level,
                promotion_state, last_confirmed_at, hotness_score, recall_source_type,
                content_preview, subject_domain, importance, confidence, trigger_count, last_recall, removed_at, updated_at,
                valid_from, valid_until, superseded_by, supersession_chain
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
                schema_version=excluded.schema_version,
                source_layer=excluded.source_layer,
                source_anchor=excluded.source_anchor,
                source_session=excluded.source_session,
                turn_signature=excluded.turn_signature,
                memory_type=excluded.memory_type,
                user_explicit=excluded.user_explicit,
                is_test_data=excluded.is_test_data,
                promotion_reason=excluded.promotion_reason,
                hot_memory_candidate=excluded.hot_memory_candidate,
                provenance=excluded.provenance,
                lifecycle=excluded.lifecycle,
                dedupe_key=excluded.dedupe_key,
                conflict_group=excluded.conflict_group,
                quality_score=excluded.quality_score,
                evidence_level=excluded.evidence_level,
                promotion_state=excluded.promotion_state,
                last_confirmed_at=excluded.last_confirmed_at,
                hotness_score=excluded.hotness_score,
                recall_source_type=excluded.recall_source_type,
                content_preview=excluded.content_preview,
                subject_domain=excluded.subject_domain,
                importance=excluded.importance,
                confidence=excluded.confidence,
                trigger_count=excluded.trigger_count,
                last_recall=excluded.last_recall,
                removed_at=excluded.removed_at,
                updated_at=excluded.updated_at,
                valid_from=excluded.valid_from,
                valid_until=excluded.valid_until,
                superseded_by=excluded.superseded_by,
                supersession_chain=excluded.supersession_chain
            """,
            (
                memory_id,
                payload.get("schema_version") or "phase2.1-sidecar-v1",
                payload.get("source_layer"),
                payload.get("source_anchor"),
                payload.get("source_session"),
                payload.get("turn_signature"),
                payload.get("memory_type"),
                1 if memory_obj._coerce_bool(payload.get("user_explicit")) else 0,
                1 if memory_obj._coerce_bool(payload.get("is_test_data")) else 0,
                payload.get("promotion_reason"),
                1 if memory_obj._coerce_bool(payload.get("hot_memory_candidate")) else 0,
                payload.get("provenance"),
                payload.get("lifecycle"),
                payload.get("dedupe_key"),
                payload.get("conflict_group"),
                payload.get("quality_score"),
                payload.get("evidence_level"),
                payload.get("promotion_state"),
                payload.get("last_confirmed_at"),
                payload.get("hotness_score"),
                payload.get("recall_source_type"),
                payload.get("content_preview"),
                payload.get("subject_domain"),
                payload.get("importance"),
                payload.get("confidence"),
                payload.get("trigger_count"),
                payload.get("last_recall"),
                payload.get("removed_at"),
                payload.get("updated_at") or payload.get("stored_at") or datetime.now().isoformat(timespec="seconds"),
                payload.get("valid_from") or payload.get("stored_at") or datetime.now().isoformat(timespec="seconds"),
                payload.get("valid_until"),
                payload.get("superseded_by"),
                payload.get("supersession_chain"),
            ),
        )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


def delete_memory_schema_index(memory_obj: Any, memory_id: Any) -> None:
    ensure_memory_schema_index_table(memory_obj)
    try:
        memory_id = int(memory_id)
    except Exception:
        return
    conn = memory_obj._conn()
    try:
        conn.execute("DELETE FROM memory_schema_index WHERE memory_id = ?", (memory_id,))
        conn.commit()
    finally:
        conn.close()
