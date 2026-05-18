#!/usr/bin/env python3
"""Generic layer consumer contract for standalone-capable Tiangong layers.

Goal:
- let selected layers expose one stable consumer-facing contract
- make in-process use look like future service use
- keep layer-specific internals out of the default public contract
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LayerError:
    code: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class LayerRequest:
    layer: str
    operation: str
    trace_id: str = ""
    session_id: str = "default"
    payload: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer": self.layer,
            "operation": self.operation,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "payload": self.payload,
            "metadata": self.metadata,
        }


@dataclass
class LayerCapability:
    layer: str
    version: str
    operations: List[str] = field(default_factory=list)
    stable: List[str] = field(default_factory=list)
    preview: List[str] = field(default_factory=list)
    internal_only: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer": self.layer,
            "version": self.version,
            "operations": self.operations,
            "stable": self.stable,
            "preview": self.preview,
            "internal_only": self.internal_only,
            "notes": self.notes,
        }


@dataclass
class LayerHealth:
    layer: str
    version: str
    status: str
    ok: bool
    checks: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer": self.layer,
            "version": self.version,
            "status": self.status,
            "ok": self.ok,
            "checks": self.checks,
            "notes": self.notes,
        }


@dataclass
class LayerResponse:
    layer: str
    version: str
    operation: str
    ok: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[LayerError] = None
    trace_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer": self.layer,
            "version": self.version,
            "operation": self.operation,
            "ok": self.ok,
            "data": self.data,
            "error": self.error.to_dict() if self.error else None,
            "trace_id": self.trace_id,
            "metadata": self.metadata,
        }
