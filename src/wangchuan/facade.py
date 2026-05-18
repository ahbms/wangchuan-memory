#!/usr/bin/env python3
"""WangChuan consumer-facing facade.

This facade is the first standalone-consumer layer surface for L2.
It wraps current public WangChuan capabilities behind one explicit contract,
without exposing `v3.*` or other internal carriers as the default entry.
"""

from __future__ import annotations

from typing import Any, Dict, List

from wangchuan._protocol.layer_contract import (
    LayerCapability,
    LayerError,
    LayerHealth,
    LayerRequest,
    LayerResponse,
)

from .migrations import MigrationManager
from .paths import data_root, default_db_path, state_root, workspace_root

VERSION = "3.0.0"
LAYER_NAME = "wangchuan"

_STABLE_OPERATIONS = [
    "remember",
    "recall",
    "recall_raw",
    "recall_scars",
    "status",
    "healthcheck",
    "task_resume",
    "paths",
]

_PREVIEW_OPERATIONS = [
    "cleanup",
    "question_like_rule_audit",
    "question_like_rule_cleanup",
    "canonical_repair",
]

_INTERNAL_ONLY = [
    "wangchuan.v3.*",
    "memory_api.py",
    "recall_service.py",
]


def _memory_api_exports() -> Dict[str, Any]:
    from .memory_api import Memory, recall, recall_raw, recall_scars, status, task_resume

    return {
        "Memory": Memory,
        "recall": recall,
        "recall_raw": recall_raw,
        "recall_scars": recall_scars,
        "status": status,
        "task_resume": task_resume,
    }


def _healthcheck_impl() -> Dict[str, Any]:
    exports = _memory_api_exports()
    memory = exports["Memory"]()
    return memory.user_healthcheck()


def _paths_payload() -> Dict[str, str]:
    return {
        "workspace_root": str(workspace_root()),
        "data_root": str(data_root()),
        "state_root": str(state_root()),
        "db_path": str(default_db_path()),
    }


def _ensure_schema_ready() -> Dict[str, Any]:
    db_path = default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    state_root().mkdir(parents=True, exist_ok=True)
    mm = MigrationManager(str(db_path))
    executed = mm.run_migrations()
    return {
        "db_path": str(db_path),
        "executed": executed,
        "status": mm.status(),
    }


def capabilities() -> LayerCapability:
    return LayerCapability(
        layer=LAYER_NAME,
        version=VERSION,
        operations=[*_STABLE_OPERATIONS, *_PREVIEW_OPERATIONS],
        stable=list(_STABLE_OPERATIONS),
        preview=list(_PREVIEW_OPERATIONS),
        internal_only=list(_INTERNAL_ONLY),
        notes=[
            "Standalone-consumer first surface for WangChuan.",
            "Default public consumers should not import v3.* directly.",
        ],
    )


def version() -> str:
    return VERSION


def health() -> LayerHealth:
    checks: Dict[str, Any] = {
        "db_path": str(default_db_path()),
        "db_ready": default_db_path().exists(),
        "state_root": str(state_root()),
    }
    status = "healthy"
    ok = True
    notes: List[str] = []
    try:
        payload = _healthcheck_impl()
        checks["user_healthcheck_status"] = payload.get("status")
        checks["summary"] = payload.get("summary")
        ok = payload.get("status") in {"healthy", "ok", "needs_review"}
        if not ok:
            status = payload.get("status") or "degraded"
            notes.append("Underlying WangChuan user_healthcheck reported non-healthy status.")
    except Exception as exc:
        ok = False
        status = "error"
        notes.append(str(exc))

    return LayerHealth(
        layer=LAYER_NAME,
        version=VERSION,
        status=status,
        ok=ok,
        checks=checks,
        notes=notes,
    )


def invoke(request: LayerRequest) -> LayerResponse:
    if request.layer != LAYER_NAME:
        return LayerResponse(
            layer=LAYER_NAME,
            version=VERSION,
            operation=request.operation,
            ok=False,
            error=LayerError(
                code="layer_mismatch",
                message=f"expected layer={LAYER_NAME}, got {request.layer}",
            ),
            trace_id=request.trace_id,
        )

    payload = request.payload or {}
    exports = _memory_api_exports()

    try:
        op = request.operation
        if op == "remember":
            content = payload.get("content")
            if not content:
                raise ValueError("missing required payload.content")
            init_info = _ensure_schema_ready()
            memory = exports["Memory"]()
            data = memory.remember(
                content,
                importance=payload.get("importance", 0.6),
                tags=payload.get("tags"),
                metadata=payload.get("metadata"),
            )
            if isinstance(data, dict):
                data.setdefault("schema_init", init_info)
        elif op == "recall":
            query = payload.get("query")
            if not query:
                raise ValueError("missing required payload.query")
            memory = exports["Memory"]()
            data = {"items": memory.recall(query, limit=payload.get("limit", 5))}
        elif op == "recall_raw":
            query = payload.get("query")
            if not query:
                raise ValueError("missing required payload.query")
            memory = exports["Memory"]()
            data = {"items": memory.recall_raw(query, limit=payload.get("limit", 5))}
        elif op == "recall_scars":
            query = payload.get("query")
            if not query:
                raise ValueError("missing required payload.query")
            memory = exports["Memory"]()
            data = {"items": memory.recall_scars(query, limit=payload.get("limit", 5))}
        elif op == "status":
            memory = exports["Memory"]()
            data = memory.status()
        elif op == "task_resume":
            memory = exports["Memory"]()
            data = memory.task_resume(board_path=payload.get("board_path"))
        elif op == "healthcheck":
            data = health().to_dict()
        elif op == "paths":
            data = _paths_payload()
        elif op == "capabilities":
            data = capabilities().to_dict()
        elif op == "version":
            data = {"version": VERSION}
        else:
            return LayerResponse(
                layer=LAYER_NAME,
                version=VERSION,
                operation=op,
                ok=False,
                error=LayerError(
                    code="unsupported_operation",
                    message=f"unsupported operation: {op}",
                    details={"supported": [*_STABLE_OPERATIONS, "capabilities", "version"]},
                ),
                trace_id=request.trace_id,
            )
    except Exception as exc:
        return LayerResponse(
            layer=LAYER_NAME,
            version=VERSION,
            operation=request.operation,
            ok=False,
            error=LayerError(
                code="invoke_failed",
                message=str(exc),
            ),
            trace_id=request.trace_id,
        )

    return LayerResponse(
        layer=LAYER_NAME,
        version=VERSION,
        operation=request.operation,
        ok=True,
        data=data,
        trace_id=request.trace_id,
        metadata={
            "stable_operations": list(_STABLE_OPERATIONS),
        },
    )
