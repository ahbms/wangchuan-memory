"""WangChuan canonical public facade.

Use `wangchuan` as the stable external import path, and
`python3 -m wangchuan` as the matching CLI facade.

Stable public facade exposed from this root package:
- Functional memory API: remember / recall / recall_raw / recall_scars / status / healthcheck / task_resume
- Python object API: Memory / WangchuanPipeline
- Utility helper: paths

Layering:
1. Stable public facade: this package root and `python3 -m wangchuan`
2. Advanced operations: power-user CLI subcommands plus `scripts/wangchuan/debug_recall.py` and `scripts/wangchuan/primary_healthcheck.py`
3. Compat: `wangchuan.compat` for explicit legacy fallback only
4. Internal implementation: `wangchuan.v3.*`, `memory_api.py`, `recall_service.py`, and related internals

Advanced helpers may still resolve here for backward compatibility, but they
are intentionally excluded from `__all__` and `dir()` so the visible public
surface stays small and consistent.

Important: keep this module import-light.
Do not eagerly import memory_api / recall_service here, otherwise runtime façade
imports can re-enter `wangchuan.memory_api` during package
initialization and create circular imports.
"""

from .paths import data_root, default_db_path, state_root, workspace_root
from .facade import capabilities as facade_capabilities, health as facade_health, invoke as facade_invoke, version as facade_version

__version__ = "3.0.0"

_STABLE_FUNCTION_EXPORTS = [
    "remember",
    "recall",
    "recall_raw",
    "recall_scars",
    "status",
    "healthcheck",
    "task_resume",
]

_STABLE_OBJECT_EXPORTS = [
    "Memory",
    "WangchuanPipeline",
]

_STABLE_UTILITY_EXPORTS = [
    "paths",
    "facade_invoke",
    "facade_health",
    "facade_capabilities",
    "facade_version",
]

_STABLE_PUBLIC_EXPORTS = [
    *_STABLE_OBJECT_EXPORTS,
    *_STABLE_FUNCTION_EXPORTS,
    *_STABLE_UTILITY_EXPORTS,
]

_STABLE_MEMORY_API_EXPORT_NAMES = {
    "Memory",
    "remember",
    "recall",
    "recall_raw",
    "recall_scars",
    "status",
    "task_resume",
}

_RECALL_SERVICE_EXPORT_NAMES = {"WangchuanPipeline"}


def _memory_api_exports():
    from .memory_api import (
        Memory,
        get_memory,
        recall,
        recall_raw,
        recall_scars,
        cleanup_question_like_rule_noise,
        audit_question_like_rules,
        recall_at,
        merge,
        history,
        get_supersession_chain,
        rollback,
        remember_rule,
        remember_lesson,
        get_user_memories,
        find_by_tag,
        memory_healthcheck,
        consolidate,
        agent_tools,
        remember,
        status,
        task_resume,
    )
    return {
        "Memory": Memory,
        "get_memory": get_memory,
        "recall": recall,
        "recall_raw": recall_raw,
        "recall_scars": recall_scars,
        "cleanup_question_like_rule_noise": cleanup_question_like_rule_noise,
        "audit_question_like_rules": audit_question_like_rules,
        "recall_at": recall_at,
        "merge": merge,
        "history": history,
        "get_supersession_chain": get_supersession_chain,
        "rollback": rollback,
        "remember_rule": remember_rule,
        "remember_lesson": remember_lesson,
        "get_user_memories": get_user_memories,
        "find_by_tag": find_by_tag,
        "memory_healthcheck": memory_healthcheck,
        "consolidate": consolidate,
        "agent_tools": agent_tools,
        "remember": remember,
        "status": status,
        "task_resume": task_resume,
    }


def _recall_service_exports():
    from .recall_service import WangchuanPipeline
    return {
        "WangchuanPipeline": WangchuanPipeline,
    }


def __getattr__(name):
    if name in _STABLE_MEMORY_API_EXPORT_NAMES:
        exports = _memory_api_exports()
        value = exports[name]
        globals()[name] = value
        return value
    if name in _RECALL_SERVICE_EXPORT_NAMES:
        exports = _recall_service_exports()
        value = exports[name]
        globals()[name] = value
        return value
    raise AttributeError(name)


def __dir__():
    return sorted(
        {
            "__all__",
            "__doc__",
            "__file__",
            "__getattr__",
            "__dir__",
            "__loader__",
            "__name__",
            "__package__",
            "__path__",
            "__spec__",
            *__all__,
        }
    )


def recent(limit: int = 10):
    return __getattr__("get_memory")().recent(limit)


def healthcheck():
    return __getattr__("get_memory")().user_healthcheck()


def cleanup(dry_run: bool = True):
    return __getattr__("get_memory")().cleanup_historical_noise(dry_run=dry_run)


def task_resume(board_path: str | None = None):
    return __getattr__("get_memory")().task_resume(board_path)


def paths():
    return {
        "workspace_root": str(workspace_root()),
        "data_root": str(data_root()),
        "state_root": str(state_root()),
        "db_path": str(default_db_path()),
    }


__all__ = _STABLE_PUBLIC_EXPORTS
