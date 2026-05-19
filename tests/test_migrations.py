from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from wangchuan import Memory
from wangchuan.migrations import MigrationManager



def _table_exists(db_path: Path, table_name: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()
    return bool(row)



def _table_columns(db_path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}



def test_migration_manager_initializes_schema_version_and_meta(tmp_path, monkeypatch):
    monkeypatch.setenv("WANGCHUAN_HOME", str(tmp_path))

    memory = Memory()
    written = memory.remember("schema version bootstrap", importance=0.7, tags=["migration"])
    assert written["success"] is True

    db_path = Path(memory.db_path)
    manager = MigrationManager(str(db_path))
    status = manager.status()

    assert db_path.exists()
    assert status["current_version"] == "001_baseline"
    assert status["meta_schema_version"] == "001_baseline"
    assert status["pending_count"] == 0
    assert status["version_matches_meta"] is True
    assert _table_exists(db_path, "schema_version")
    assert _table_exists(db_path, "meta")



def test_baseline_migration_is_idempotent_and_preserves_data(tmp_path, monkeypatch):
    monkeypatch.setenv("WANGCHUAN_HOME", str(tmp_path))

    memory = Memory()
    db_path = Path(memory.db_path)
    manager = MigrationManager(str(db_path))

    written = memory.remember("迁移幂等测试样本", importance=0.8, tags=["migration"])
    assert written["success"] is True

    first_status = manager.status()
    rerun = manager.run_migrations()
    second_status = manager.status()

    assert rerun == []
    assert first_status["current_version"] == second_status["current_version"] == "001_baseline"
    assert second_status["meta_schema_version"] == "001_baseline"
    assert memory.recall("迁移幂等测试样本", limit=3)



def test_legacy_missing_tables_and_columns_are_repaired(tmp_path):
    db_path = tmp_path / ".index" / "index.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL)")
        conn.execute("CREATE TABLE memory_schema_index (memory_id INTEGER PRIMARY KEY)")
        conn.commit()

    manager = MigrationManager(str(db_path))
    status = manager.status()
    columns = _table_columns(db_path, "memory_schema_index")

    assert status["current_version"] == "001_baseline"
    assert status["current_version"] == "001_baseline"
    assert status["meta_schema_version"] == "001_baseline"
    assert _table_exists(db_path, "gm_messages")
    assert _table_exists(db_path, "schema_version")
    assert {"valid_from", "valid_until", "superseded_by", "supersession_chain", "schema_version"}.issubset(columns)



def test_backup_restore_recovers_written_memories(tmp_path, monkeypatch):
    monkeypatch.setenv("WANGCHUAN_HOME", str(tmp_path))

    memory = Memory()
    db_path = Path(memory.db_path)

    for idx in range(3):
        result = memory.remember(f"备份恢复样本 {idx}", importance=0.7, tags=["backup", f"item-{idx}"])
        assert result["success"] is True

    backup_path = tmp_path / "backup.sqlite"
    shutil.copy2(db_path, backup_path)
    db_path.unlink()
    shutil.copy2(backup_path, db_path)

    restored = Memory(str(db_path))
    recalled = restored.recall("备份恢复样本", limit=5)

    assert len(recalled) >= 3
    assert any("备份恢复样本 0" in item["content"] for item in recalled)
    assert MigrationManager(str(db_path)).status()["current_version"] == "001_baseline"
