"""
运行态统一接口（兼容转发层）

目标：
- 兼容现有从 wangchuan.runtime_state 导入的调用方
- 把运行态职责逐步收口到 tiangong.runtime.energy / tiangong.runtime.truth 这类职责目录
- 避免 UI / API / hook / workspace 各自 new 一个状态对象再各自解释

阅读顺序建议：
- 本模块历史上承担运行态职责名入口；当前查运行态职责，优先看 tiangong.runtime.energy 与 tiangong.runtime.truth
- 本模块暂保留为兼容层，避免一次性大迁移打崩调用方
- 新的运行态能力优先从这里暴露；未来以职责目录为主，不再继续扩散到 wangchuan 目录

L4 解耦状态（2026-05-18）：
- energy 能力已走 adapter：wangchuan._adapters.runtime_adapter
- truth 能力暂保留直连 import（后续逐步替换）
"""

from typing import Any, Dict

from wangchuan._adapters.runtime_adapter import (
    get_energy_state as _adapter_get_energy_state,
)

# ---- energy 能力：通过 adapter 提供（不直连 tiangong.runtime.energy） ----


def get_runtime_energy_state() -> Dict[str, Any]:
    """获取运行时能量状态。

    已通过 L4 adapter 提供，不再直接 import tiangong.runtime.energy。
    """
    return _adapter_get_energy_state()


def charge_runtime_energy(amount: float = None, reason: str = "interaction") -> Dict[str, Any]:
    """兼容桩 — 实际能力在 L4 (tiangong.runtime.energy)。

    优先通过 protocol dispatch 调用，fallback 返回空。
    """
    try:
        from wangchuan._protocol import LayerRequest, dispatch
        resp = dispatch(LayerRequest(
            layer="liqi",
            operation="charge_runtime_energy",
            payload={"amount": amount, "reason": reason},
        ))
        if resp.ok:
            return resp.data
    except ImportError:
        pass
    except Exception:
        pass
    return {}


def consume_runtime_energy(action_type: str = "interaction") -> Dict[str, Any]:
    """兼容桩 — 同 charge_runtime_energy。"""
    try:
        from wangchuan._protocol import LayerRequest, dispatch
        resp = dispatch(LayerRequest(
            layer="liqi",
            operation="consume_runtime_energy",
            payload={"action_type": action_type},
        ))
        if resp.ok:
            return resp.data
    except ImportError:
        pass
    except Exception:
        pass
    return {}


# ---- truth 能力：暂保留直连（后续通过 truth_adapter 替换） ----

# ---- truth 能力：可选加载，无 L4 时静默降级 ----
try:
    from tiangong.runtime.truth import (
        RUNTIME_TRUTH_SCHEMA,
        get_runtime_truth, get_session_truth, get_task_truth,
        sync_task_checkpoint, start_task, advance_task_phase,
        bind_memory_refs, record_tool_execution, mark_runtime_mode, record_recovery_checkpoint,
    )
    from tiangong.runtime.truth_schema import (
        RuntimeTruthSchemaModel, ActiveToolState, RecoveryState,
    )
except ImportError:
    RUNTIME_TRUTH_SCHEMA = {}
    RuntimeTruthSchemaModel = object
    ActiveToolState = object
    RecoveryState = object
    def _stub(*a, **kw): return {}
    get_runtime_truth = get_session_truth = get_task_truth = _stub
    sync_task_checkpoint = start_task = advance_task_phase = _stub
    bind_memory_refs = record_tool_execution = mark_runtime_mode = _stub
    record_recovery_checkpoint = _stub


__all__ = [
    "get_runtime_energy_state",
    "charge_runtime_energy",
    "consume_runtime_energy",
    "RUNTIME_TRUTH_SCHEMA",
    "RuntimeTruthSchemaModel",
    "ActiveToolState",
    "RecoveryState",
    "get_runtime_truth",
    "get_session_truth",
    "get_task_truth",
    "sync_task_checkpoint",
    "start_task",
    "advance_task_phase",
    "bind_memory_refs",
    "record_tool_execution",
    "mark_runtime_mode",
    "record_recovery_checkpoint",
]
