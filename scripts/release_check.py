#!/usr/bin/env python3
"""Release safety check for wangchuan-memory.

Checks before publishing:
- no forbidden/runtime files
- no cache files
- no common secret signatures or literal secret assignments
- no machine-local absolute paths
- no old tiangong.wangchuan public refs
- no broken internal doc links
- no direct tiangong imports outside optional adapters
- AST syntax check
- import smoke test
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "wangchuan"
checks: list[tuple[str, bool, str, list[str]]] = []


def check(name: str, ok: bool, detail: str = "", items: list[str] | None = None) -> None:
    checks.append((name, ok, detail, items or []))


def cleanup_cache() -> None:
    for p in ROOT.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    for p in ROOT.rglob(".pytest_cache"):
        shutil.rmtree(p, ignore_errors=True)
    for p in ROOT.rglob("*.pyc"):
        p.unlink(missing_ok=True)


def iter_text_files(*patterns: str) -> list[Path]:
    out: list[Path] = []
    for pattern in patterns:
        out.extend(ROOT.rglob(pattern))
    return [
        p for p in sorted(set(out))
        if ".git" not in p.parts and "__pycache__" not in p.parts and p.name != "release_check.py"
    ]


def main() -> int:
    cleanup_cache()

    check("structure", ROOT.exists() and SRC.exists(), f"root={ROOT}, src={SRC}")

    forbidden: list[str] = []
    for pattern in [".env", "*.pem", "*.key", "*.p12", "*.pfx", "*.sqlite", "*.db", "*.log", "*.bak", "*.pyc"]:
        forbidden.extend(str(p.relative_to(ROOT)) for p in ROOT.rglob(pattern) if ".git" not in p.parts)
    for runtime_dir in [".index", ".wangchuan", "state"]:
        forbidden.extend(str(p.relative_to(ROOT)) for p in ROOT.rglob(runtime_dir) if ".git" not in p.parts)
    for runtime_file in ["benchmark_results.json"]:
        forbidden.extend(str(p.relative_to(ROOT)) for p in ROOT.rglob(runtime_file) if ".git" not in p.parts)
    check("forbidden_files", not forbidden, f"{len(forbidden)} found", forbidden[:50])

    cache = [str(p.relative_to(ROOT)) for p in list(ROOT.rglob("__pycache__")) + list(ROOT.rglob(".pytest_cache"))]
    check("cache_dirs", not cache, f"{len(cache)} found", cache[:50])

    precise_secret_patterns = [
        r"edd966",
        r"812f04b8d3f4aa9a5107704f12f2ca6f",
        r"MGZhN2UxZGMyYjY3MTNiNmFjNjJkNGNh",
        r"sk-[A-Za-z0-9]{20,}",
        r"sk-ant-[A-Za-z0-9_-]{20,}",
        r"gh[pousr]_[A-Za-z0-9_]{30,}",
        r"AKIA[0-9A-Z]{16}",
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    ]
    precise_hits: list[str] = []
    for p in iter_text_files("*.py", "*.md", "*.json", "*.toml", "*.env*"):
        text = p.read_text(errors="ignore")
        for pattern in precise_secret_patterns:
            for m in re.finditer(pattern, text):
                line = text.count("\n", 0, m.start()) + 1
                precise_hits.append(f"{p.relative_to(ROOT)}:{line}:{pattern}")
    check("precise_secret_signatures", not precise_hits, f"{len(precise_hits)} found", precise_hits[:50])

    literal_hits: list[str] = []
    literal_re = re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*=\s*[\"']([^\"']{12,})[\"']")
    for p in iter_text_files("*.py", "*.json", "*.toml", "*.env*"):
        text = p.read_text(errors="ignore")
        for m in literal_re.finditer(text):
            value = m.group(2).lower()
            nearby = text[max(0, m.start() - 100):m.start() + 160]
            if any(marker in value for marker in ["your_", "<your", "placeholder", "example", "change-me", "changeme"]):
                continue
            if "os.getenv" in nearby or "environ.get" in nearby:
                continue
            line = text.count("\n", 0, m.start()) + 1
            literal_hits.append(f"{p.relative_to(ROOT)}:{line}:{m.group(0)[:120]}")
    check("literal_secret_assignments", not literal_hits, f"{len(literal_hits)} found", literal_hits[:50])

    abs_hits: list[str] = []
    for p in iter_text_files("*.py", "*.md", "*.json", "*.toml"):
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if re.search(r"/(root|home/ahbms|Users)/", line):
                abs_hits.append(f"{p.relative_to(ROOT)}:{i}:{line[:160]}")
    check("absolute_local_paths", not abs_hits, f"{len(abs_hits)} found", abs_hits[:50])

    old_refs: list[str] = []
    doc_refs: list[str] = []
    for p in iter_text_files("*.py", "*.md", "*.toml", "*.json"):
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if "tiangong.wangchuan" in line or "python3 -m tiangong.wangchuan" in line:
                old_refs.append(f"{p.relative_to(ROOT)}:{i}:{line[:160]}")
            if ("../../" in line or "../../../" in line or "deploy/docker" in line) and "../../docs/" not in line:
                doc_refs.append(f"{p.relative_to(ROOT)}:{i}:{line[:160]}")
    check("old_package_refs", not old_refs, f"{len(old_refs)} found", old_refs[:50])
    check("internal_doc_links", not doc_refs, f"{len(doc_refs)} found", doc_refs[:50])

    bad_imports: list[str] = []
    for p in SRC.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if re.match(r"\s*(from|import)\s+tiangong\.", line):
                if "_adapters" in p.parts:
                    continue
                if p.name == "runtime_state.py" and "tiangong.runtime" in line:
                    continue
                bad_imports.append(f"{p.relative_to(ROOT)}:{i}:{line.strip()}")
    check("direct_tiangong_imports", not bad_imports, f"{len(bad_imports)} found", bad_imports[:50])

    syntax_errors: list[str] = []
    for p in SRC.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            ast.parse(p.read_text(errors="ignore"), filename=str(p))
        except Exception as exc:
            syntax_errors.append(f"{p.relative_to(ROOT)}:{exc}")
    check("ast_syntax", not syntax_errors, f"{len(syntax_errors)} syntax errors", syntax_errors[:50])

    code = """
import warnings
warnings.filterwarnings('ignore')
import wangchuan
from wangchuan import Memory, remember, recall, recall_raw, recall_scars, status, healthcheck, task_resume
from wangchuan.facade import version
m = Memory()
print(version())
print(type(m).__name__)
print(callable(remember), callable(recall), callable(recall_raw), callable(recall_scars))
print(callable(status), callable(healthcheck), callable(task_resume))
"""
    cp = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        timeout=30,
    )
    check("import_smoke", cp.returncode == 0, f"rc={cp.returncode}", (cp.stdout + cp.stderr).splitlines()[:50])

    cleanup_cache()

    result = [
        {"name": name, "ok": ok, "detail": detail, "items": items}
        for name, ok, detail, items in checks
    ]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    passed = all(ok for _, ok, _, _ in checks)
    print("OVERALL", "PASS" if passed else "FAIL")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
