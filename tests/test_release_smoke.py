from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_first_run_remember_recall(tmp_path, monkeypatch):
    monkeypatch.setenv("WANGCHUAN_HOME", str(tmp_path))
    from wangchuan import Memory

    m = Memory()
    assert not Path(m.db_path).exists()

    written = m.remember("release smoke memory: user likes iced americano", importance=0.7, tags=["smoke"])
    assert written["success"] is True
    assert Path(m.db_path).exists()

    recalled = m.recall("iced americano", limit=3)
    assert isinstance(recalled, list)
    assert len(recalled) >= 1
    assert "iced americano" in recalled[0]["content"]


def test_public_imports():
    import wangchuan
    from wangchuan import Memory, healthcheck
    from wangchuan.facade import version

    assert wangchuan.__version__
    assert version() == "3.0.0"
    assert Memory.__name__ == "Memory"
    assert callable(healthcheck)


def test_cli_status_json(tmp_path):
    env = os.environ.copy()
    env["WANGCHUAN_HOME"] = str(tmp_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    cp = subprocess.run(
        [sys.executable, "-m", "wangchuan", "status", "--json"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert "message" in payload
    assert "memories" in payload
