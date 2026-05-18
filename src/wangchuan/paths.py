#!/usr/bin/env python3
"""Path helpers for WangChuan public/runtime surfaces.

These helpers keep the current OpenClaw workspace layout working while making
standalone / local-first runs less dependent on one hard-coded absolute path.
"""

from __future__ import annotations

import os
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
REPO_WORKSPACE_ROOT = PACKAGE_DIR.parents[1]


def _looks_like_repo_workspace(root: Path) -> bool:
    return (
        (root / "tiangong" / "wangchuan").exists()
        and (root / "scripts" / "wangchuan").exists()
    )


def workspace_root() -> Path:
    """Resolve the base workspace/home for WangChuan.

    Priority:
    1. WANGCHUAN_HOME
    2. OPENCLAW_WORKSPACE
    3. Repo workspace root when running inside the current source tree
    4. Current working directory for standalone/local-first runs
    """
    for key in ("WANGCHUAN_HOME", "OPENCLAW_WORKSPACE"):
        raw = os.getenv(key)
        if raw:
            return Path(raw).expanduser().resolve()

    if _looks_like_repo_workspace(REPO_WORKSPACE_ROOT):
        return REPO_WORKSPACE_ROOT

    return Path.cwd().resolve()


def data_root() -> Path:
    """Resolve the WangChuan data/code root.

    In the source repo this is `<workspace>/wangchuan`.
    In standalone mode it falls back to `<workspace>` unless overridden.
    """
    custom = os.getenv("WANGCHUAN_DATA_DIR")
    if custom:
        return Path(custom).expanduser().resolve()

    base = workspace_root()
    repo_style = base / "tiangong" / "wangchuan"
    if _looks_like_repo_workspace(base) and repo_style.exists():
        return repo_style

    return base


def default_db_path() -> Path:
    raw = os.getenv("WANGCHUAN_DB_PATH")
    if raw:
        return Path(raw).expanduser().resolve()
    return data_root() / ".index" / "index.sqlite"


def state_root() -> Path:
    raw = os.getenv("WANGCHUAN_STATE_DIR")
    if raw:
        return Path(raw).expanduser().resolve()

    base = workspace_root()
    return base / "state" / "wangchuan"


def hot_memory_md_path() -> Path:
    raw = os.getenv("WANGCHUAN_HOT_MEMORY_MD_PATH")
    if raw:
        return Path(raw).expanduser().resolve()
    return state_root() / "hot_memory" / "MEMORY.md"
