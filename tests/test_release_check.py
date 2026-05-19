from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]



def _copy_clean_repo(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    shutil.copytree(
        REPO_ROOT,
        target,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            "__pycache__",
            ".index",
            "state",
            "*.pyc",
        ),
    )
    return target



def _run_release_check(repo_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/release_check.py"],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        timeout=60,
    )



def _parse_report(stdout: str):
    marker = "\nOVERALL "
    json_part = stdout.split(marker, 1)[0]
    return json.loads(json_part)



def _check_map(report):
    return {item["name"]: item for item in report}



def test_release_check_passes_on_clean_tree(tmp_path):
    repo = _copy_clean_repo(tmp_path)

    cp = _run_release_check(repo)

    assert cp.returncode == 0, cp.stdout + cp.stderr
    report = _parse_report(cp.stdout)
    checks = _check_map(report)
    assert checks["forbidden_files"]["ok"] is True
    assert checks["import_smoke"]["ok"] is True



def test_release_check_fails_when_env_file_exists(tmp_path):
    repo = _copy_clean_repo(tmp_path)
    (repo / ".env").write_text("DUMMY=1\n", encoding="utf-8")

    cp = _run_release_check(repo)

    assert cp.returncode == 2
    report = _parse_report(cp.stdout)
    checks = _check_map(report)
    assert checks["forbidden_files"]["ok"] is False
    assert any(item == ".env" for item in checks["forbidden_files"]["items"])



def test_release_check_fails_on_fake_key_signature(tmp_path):
    repo = _copy_clean_repo(tmp_path)
    sample = repo / "docs" / "fake_secret.md"
    fake_key = "sk-" + "12345678901234567890ABCDEF"
    sample.write_text(f"credential = '{fake_key}'\n", encoding="utf-8")

    cp = _run_release_check(repo)

    assert cp.returncode == 2
    report = _parse_report(cp.stdout)
    checks = _check_map(report)
    sample_rel = sample.relative_to(repo).as_posix()
    assert checks["precise_secret_signatures"]["ok"] is False
    assert any(sample_rel in item for item in checks["precise_secret_signatures"]["items"])



def test_release_check_fails_on_runtime_index_directory(tmp_path):
    repo = _copy_clean_repo(tmp_path)
    runtime_db = repo / ".index" / "index.sqlite"
    runtime_db.parent.mkdir(parents=True, exist_ok=True)
    runtime_db.write_text("runtime", encoding="utf-8")

    cp = _run_release_check(repo)

    assert cp.returncode == 2
    report = _parse_report(cp.stdout)
    checks = _check_map(report)
    assert checks["forbidden_files"]["ok"] is False
    assert any(item == ".index" for item in checks["forbidden_files"]["items"])
    assert any(item == ".index/index.sqlite" for item in checks["forbidden_files"]["items"])
