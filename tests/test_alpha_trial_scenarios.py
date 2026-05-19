from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


def _env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["WANGCHUAN_HOME"] = str(tmp_path)
    env["PYTHONPATH"] = str(SRC_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def test_alpha_trial_scenario_python_api_agent(tmp_path):
    cp = subprocess.run(
        [sys.executable, "examples/basic_memory.py"],
        cwd=REPO_ROOT,
        env=_env(tmp_path / "python-agent"),
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "recall_count" in cp.stdout


def test_alpha_trial_scenario_cli_agent(tmp_path):
    cp = subprocess.run(
        ["bash", "examples/cli_demo.sh"],
        cwd=REPO_ROOT,
        env=_env(tmp_path / "cli-agent"),
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "User prefers concise CLI output." in cp.stdout


def test_alpha_trial_scenario_release_agent(tmp_path):
    cp = subprocess.run(
        [sys.executable, "scripts/release_check.py"],
        cwd=REPO_ROOT,
        env=_env(tmp_path / "release-agent"),
        text=True,
        capture_output=True,
        timeout=60,
    )
    # The active dev tree may contain runtime artifacts, but the gate must report structured results.
    assert cp.returncode in {0, 2}
    assert "OVERALL" in cp.stdout
    assert "forbidden_files" in cp.stdout
