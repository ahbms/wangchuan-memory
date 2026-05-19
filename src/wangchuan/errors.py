from __future__ import annotations

from pathlib import Path


class WangChuanConfigurationError(RuntimeError):
    """User-facing configuration error with a concise fix hint."""


def assert_runtime_paths_are_usable(db_path: str) -> None:
    """Validate runtime database path before deep SQLite calls.

    This catches common first-run misconfiguration such as setting
    WANGCHUAN_HOME to a file instead of a directory. Raising one concise
    configuration error prevents a long traceback from leaking to CLI users.
    """
    path = Path(db_path).expanduser().resolve()
    parent = path.parent
    blockers = [candidate for candidate in [parent, *parent.parents] if candidate.exists() and not candidate.is_dir()]
    if blockers:
        blocker = blockers[0]
        raise WangChuanConfigurationError(
            "WangChuan database path is not usable: "
            f"'{blocker}' exists but is not a directory. "
            "Set WANGCHUAN_HOME to a directory, or set WANGCHUAN_DB_PATH to a writable .sqlite file."
        )
