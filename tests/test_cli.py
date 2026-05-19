from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"



def _run_cli(tmp_path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["WANGCHUAN_HOME"] = str(tmp_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(SRC_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "wangchuan", *args],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )



def test_cli_help_has_no_warnings(tmp_path):
    cp = _run_cli(tmp_path, "--help")

    assert cp.returncode == 0, cp.stderr
    assert "WangChuan public CLI" in cp.stdout
    assert cp.stderr == ""



def test_cli_paths_and_status_json(tmp_path):
    paths_cp = _run_cli(tmp_path, "paths", "--json")
    assert paths_cp.returncode == 0, paths_cp.stderr
    assert paths_cp.stderr == ""
    paths_payload = json.loads(paths_cp.stdout)
    assert paths_payload["workspace_root"] == str(tmp_path)
    assert paths_payload["db_path"].endswith(".index/index.sqlite")

    status_cp = _run_cli(tmp_path, "status", "--json")
    assert status_cp.returncode == 0, status_cp.stderr
    assert status_cp.stderr == ""
    status_payload = json.loads(status_cp.stdout)
    assert isinstance(status_payload, dict)
    assert "message" in status_payload
    assert "memories" in status_payload



def test_cli_remember_and_recall_json(tmp_path):
    remember_cp = _run_cli(
        tmp_path,
        "remember",
        "用户偏好简洁回复",
        "--importance",
        "0.9",
        "--tag",
        "preference",
        "--json",
    )
    assert remember_cp.returncode == 0, remember_cp.stderr
    assert remember_cp.stderr == ""
    remember_payload = json.loads(remember_cp.stdout)
    assert remember_payload["success"] is True

    recall_cp = _run_cli(tmp_path, "recall", "简洁回复", "--limit", "3", "--json")
    assert recall_cp.returncode == 0, recall_cp.stderr
    assert recall_cp.stderr == ""
    recall_payload = json.loads(recall_cp.stdout)
    assert isinstance(recall_payload, list)
    assert recall_payload
    assert any(item["content"] == "用户偏好简洁回复" for item in recall_payload)
    assert all("recall_explain" in item for item in recall_payload)
