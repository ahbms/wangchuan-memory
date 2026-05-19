from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


def _env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["WANGCHUAN_HOME"] = str(tmp_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(SRC_ROOT)
    return env


def test_basic_memory_example_runs_and_explains(tmp_path):
    cp = subprocess.run(
        [sys.executable, "examples/basic_memory.py"],
        cwd=str(REPO_ROOT),
        env=_env(tmp_path),
        text=True,
        capture_output=True,
        timeout=60,
    )

    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert cp.stderr == ""
    payload = json.loads(cp.stdout)
    assert payload["preference_written"] is True
    assert payload["fact_written"] is True
    assert payload["recall_count"] >= 2
    assert any("concise" in content for content in payload["top_contents"])
    assert payload["explain_samples"]


def test_cli_demo_runs_from_source_checkout(tmp_path):
    cp = subprocess.run(
        ["bash", "examples/cli_demo.sh"],
        cwd=str(REPO_ROOT),
        env=_env(tmp_path),
        text=True,
        capture_output=True,
        timeout=60,
    )

    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert cp.stderr == ""
    assert "User prefers concise CLI output." in cp.stdout
    assert "WangChuan stores data in SQLite." in cp.stdout
    assert '"message"' in cp.stdout
