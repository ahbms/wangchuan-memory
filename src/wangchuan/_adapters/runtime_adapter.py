#!/usr/bin/env python3
"""
忘川 → L4 利器（runtime）适配器

封装忘川对 tiangong.runtime.energy 的所有调用面。

目标：
- 优先通过 protocol.dispatch() 调用 L4
- fallback：懒加载本地 import（同一进程内兼容）
- 让忘川不直接 import tiangong.runtime.*
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_HAVE_RUNTIME = False
_runtime_cache: Dict[str, Any] = {}


def _check_runtime_availability() -> bool:
    """检查 L4 runtime 是否可用（协议层或本地）。"""
    global _HAVE_RUNTIME
    if _HAVE_RUNTIME:
        return True
    try:
        from wangchuan._protocol import LayerRequest, dispatch, get_layer

        if get_layer("liqi") is not None:
            _HAVE_RUNTIME = True
            return True
    except ImportError:
        pass
    except Exception:
        pass
    return False


def get_energy_state() -> Dict[str, Any]:
    """获取运行时能量状态。

    返回与 tiangong.runtime.energy.get_runtime_energy_state() 相同的 dict 结构。

    优先级：
    1. protocol.dispatch → L4 handler
    2. 本地懒加载 import（同一进程）
    """
    # 尝试 1: protocol dispatch
    try:
        from wangchuan._protocol import LayerRequest, dispatch

        resp = dispatch(
            LayerRequest(
                layer="liqi",
                operation="get_energy_state",
                payload={},
            )
        )
        if resp.ok:
            return resp.data.get("state", {"enabled": False})
        logger.debug("L4 protocol dispatch 未就绪，回退本地: %s", resp.error)
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("L4 dispatch 异常，回退本地: %s", exc)

    # 尝试 2: 本地懒加载
    return _local_get_energy_state()


def _local_get_energy_state() -> Dict[str, Any]:
    """本地回退：直接 import tiangong.runtime.energy。"""
    if _runtime_cache.get("energy_state_noop"):
        return {"enabled": False, "state_label": "noop", "state": "noop", "source": "adapter_fallback_noop"}

    try:
        from tiangong.runtime.energy import get_runtime_energy_state

        result = get_runtime_energy_state()
        if isinstance(result, dict):
            result["source"] = "adapter_fallback_local"
            return result
        return {"enabled": False, "source": "adapter_fallback_invalid"}
    except ImportError:
        _runtime_cache["energy_state_noop"] = True
        return {"enabled": False, "state_label": "noop", "state": "noop", "source": "adapter_fallback_noop"}
    except Exception as exc:
        logger.warning("L4 runtime 获取失败: %s", exc)
        return {"enabled": False, "state_label": "error", "state": "error", "source": "adapter_fallback_error"}


__all__ = ["get_energy_state"]
