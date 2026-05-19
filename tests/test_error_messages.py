from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


def _run_cli_with_home(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["WANGCHUAN_HOME"] = str(home)
    env["PYTHONPATH"] = str(SRC_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "wangchuan", *args],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_cli_reports_clear_configuration_error_without_traceback(tmp_path):
    bad_home = tmp_path / "not-a-directory"
    bad_home.write_text("file", encoding="utf-8")

    cp = _run_cli_with_home(bad_home, "status", "--json")

    assert cp.returncode == 2
    assert "Traceback" not in cp.stderr
    assert "Traceback" not in cp.stdout
    payload = json.loads(cp.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "configuration_error"
    assert "WANGCHUAN_HOME" in payload["message"]
    assert "not a directory" in payload["message"]


def test_mcp_missing_extra_has_install_hint():
    from wangchuan import mcp_server

    err = mcp_server._missing_mcp_dependency_error()
    assert "pip install" in str(err)
    assert ".[mcp]" in str(err)
