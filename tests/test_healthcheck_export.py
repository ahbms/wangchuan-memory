from __future__ import annotations

from pathlib import Path



def _reset_memory_singleton() -> None:
    import wangchuan.memory_api as memory_api

    memory_api._memory = None



def test_healthcheck_callable_is_exported_and_returns_schema_status(tmp_path, monkeypatch):
    monkeypatch.setenv("WANGCHUAN_HOME", str(tmp_path))
    _reset_memory_singleton()

    from wangchuan import healthcheck, remember, status

    written = remember("healthcheck export smoke", importance=0.8, tags=["healthcheck"])
    assert written["success"] is True

    health = healthcheck()
    current_status = status()

    assert isinstance(health, dict)
    assert health["status"] in {"healthy", "degraded", "risky"}
    assert health["migration_status"]["current_version"] == "001_baseline"
    assert health["migration_status"]["meta_schema_version"] == "001_baseline"
    assert health["checks"]["schema_version_is_visible"]["ok"] is True
    assert health["checks"]["schema_version_matches_meta"]["ok"] is True
    assert current_status["foundation"]["schema_version"] == "001_baseline"
    assert current_status["foundation"]["schema_meta_version"] == "001_baseline"
    assert current_status["migration_status"]["version_matches_meta"] is True
    assert "schema=001_baseline" in current_status["message"]
