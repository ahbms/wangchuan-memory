from __future__ import annotations

from pathlib import Path


def _reset_memory_singleton() -> None:
    import wangchuan.memory_api as memory_api

    memory_api._memory = None



def test_first_run_api_is_safe_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("WANGCHUAN_HOME", str(tmp_path))
    _reset_memory_singleton()

    from wangchuan import Memory, recall, status

    expected_db = tmp_path / ".index" / "index.sqlite"

    first = Memory()
    second = Memory()

    assert first.db_path == str(expected_db)
    assert second.db_path == str(expected_db)
    assert isinstance(recall("火星坐标紫外线协议", limit=3), list)
    assert recall("火星坐标紫外线协议", limit=3) == []

    status_payload = status()
    assert isinstance(status_payload, dict)
    assert "message" in status_payload



def test_first_remember_creates_index_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("WANGCHUAN_HOME", str(tmp_path))
    _reset_memory_singleton()

    from wangchuan import Memory, remember

    memory = Memory()
    expected_db = Path(memory.db_path)

    assert not expected_db.exists()

    written = remember("用户第一次运行时会自动初始化数据库", importance=0.8, tags=["first-run"])

    assert written["success"] is True
    assert expected_db == tmp_path / ".index" / "index.sqlite"
    assert expected_db.exists()

    _reset_memory_singleton()
    reopened = Memory()
    assert reopened.db_path == str(expected_db)
    assert reopened.status()["memories"] >= 1
