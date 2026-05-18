#!/usr/bin/env python3
"""
忘川 → L5 明察（context）适配器

封装忘川对 tiangong.context 的所有调用面。

目标：
- 优先通过 protocol.dispatch() 调用 L5
- fallback：不依赖 L5 也能跑（降级为 no-op / 空结果）
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================
# 通用 fallback 桩
# =============================================

class _StubStateStore:
    """fallback 状态存储桩"""

    def __init__(self):
        # Keep compatibility with ContextSessionStateStore callers that scan
        # `<base_dir>/*/metrics.jsonl` for optional runtime observations.
        self.base_dir = Path.cwd() / "state" / "context"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def load_metrics(self, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        return []

    def append_metric(self, session_id: str, metric_name: str, payload: Dict[str, Any]) -> None:
        pass

    def load_session_summary(self, session_id: str) -> Dict[str, Any]:
        return {}

    def load_task_checkpoint(self, session_id: str) -> Dict[str, Any]:
        return {}

    def load_handoff_pack(self, session_id: str) -> Dict[str, Any]:
        return {}

    def handoff_resume_view(self, session_id: str) -> Dict[str, Any]:
        return {}


class _StubObservability:
    """fallback 可观测性桩"""

    def __init__(self):
        self.state_store = _StubStateStore()

    def capture_recall_metrics(self, session_id: str, **kwargs) -> Dict[str, Any]:
        return {"captured": False, "source": "stub"}

    def capture_pipeline_metrics(self, session_id: str, **kwargs) -> Dict[str, Any]:
        return {"captured": False, "source": "stub"}

    def capture_tool_execution(self, session_id: str, **kwargs) -> Dict[str, Any]:
        return {"captured": False, "source": "stub"}

    def capture_recovery_checkpoint(self, session_id: str, **kwargs) -> Dict[str, Any]:
        return {"captured": False, "source": "stub"}

    def read_session_store_tokens(self, session_key: str) -> Dict[str, Any]:
        return {"tokens": {}}

    def resolve_session_key(self, session_id: str, preferred_channel: str | None = None) -> Dict[str, Any]:
        return {"session_key": session_id}

    def read_session_runtime_view(self, session_id: str, preferred_channel: str | None = None) -> Dict[str, Any]:
        return {"session_id": session_id, "source": "stub"}

    def read_pseudo_ttft(self, session_key: str, sample_size: int = 5) -> Dict[str, Any]:
        return {}


class _StubSemanticCache:
    """fallback 语义缓存桩"""

    def __init__(self, max_size: int = 256, ttl_seconds: float = 120.0):
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds

    def clear(self) -> None:
        pass

    def stats(self) -> Dict[str, Any]:
        return {"size": 0, "max_size": self._max_size, "ttl_seconds": self._ttl_seconds}

    def get(self, query: str, **kwargs) -> Tuple[Optional[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
        return None, None

    def put(self, query: str, results: List[Dict[str, Any]], **kwargs) -> None:
        pass

    def describe_key(self, query: str, **kwargs) -> Dict[str, Any]:
        return {"query": query, "source": "stub"}


# =============================================
# 适配器主入口
# =============================================

_observability_instance = None
_semantic_cache_instance = None
_have_l5 = False


def _lazy_check_l5() -> bool:
    """检查 L5 是否可用。"""
    global _have_l5
    if _have_l5:
        return True
    try:
        # 尝试协议层
        from wangchuan._protocol import get_layer
        if get_layer("mingcha") is not None:
            _have_l5 = True
            return True
    except ImportError:
        pass
    except Exception:
        pass
    return False


def get_observability() -> Any:
    """获取 ContextObservability 实例。

    返回与 tiangong.context.observability.ContextObservability 兼容的对象。
    """
    global _observability_instance

    if _observability_instance is not None:
        return _observability_instance

    # 尝试 L5 真实实例
    if _lazy_check_l5():
        try:
            from tiangong.context.observability import ContextObservability as _RealObs
            _observability_instance = _RealObs()
            return _observability_instance
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("L5 observability 实例化失败: %s", exc)

    # fallback
    _observability_instance = _StubObservability()
    return _observability_instance


def get_semantic_cache(max_size: int = 256, ttl_seconds: float = 120.0) -> Any:
    """获取 RecallSemanticCache 实例。

    返回与 tiangong.context.semantic_cache.RecallSemanticCache 兼容的对象。
    """
    global _semantic_cache_instance

    if _semantic_cache_instance is not None:
        return _semantic_cache_instance

    # 尝试 L5 真实实例
    if _lazy_check_l5():
        try:
            from tiangong.context.semantic_cache import RecallSemanticCache as _RealCache
            _semantic_cache_instance = _RealCache(max_size=max_size, ttl_seconds=ttl_seconds)
            return _semantic_cache_instance
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("L5 semantic_cache 实例化失败: %s", exc)

    # fallback
    _semantic_cache_instance = _StubSemanticCache(max_size=max_size, ttl_seconds=ttl_seconds)
    return _semantic_cache_instance


def get_session_state_store() -> Any:
    """获取 ContextSessionStateStore 实例。

    返回与 tiangong.context.session_state.ContextSessionStateStore 兼容的对象。
    """
    if _lazy_check_l5():
        try:
            from tiangong.context.session_state import ContextSessionStateStore as _RealStore
            return _RealStore(_RealStore.default_workspace())
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("L5 session_state 实例化失败: %s", exc)

    return _StubStateStore()


__all__ = [
    "get_observability",
    "get_semantic_cache",
    "get_session_state_store",
    "ContextObservability",
    "RecallSemanticCache",
]
