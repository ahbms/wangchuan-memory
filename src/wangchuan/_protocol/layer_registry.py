#!/usr/bin/env python3
"""
天工层注册表 — 层间调用的唯一枢纽

目标：
- 让层之间不直接 import，只通过 protocol 通信
- 同一进程内：dispatch() 本地路由
- 未来跨进程：保留扩展点，替换 transport 即可

用法:
    from wangchuan._protocol import LayerRequest, LayerResponse, register, dispatch

    # 注册（各层启动时调用）
    LayerRegistry.register("wangchuan", version="3.0.0", handler=my_handler)

    # 调用
    resp = LayerRegistry.dispatch(LayerRequest(
        layer="consciousness",
        operation="inject_state",
        payload={"session_id": "abc"}
    ))
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .layer_contract import LayerCapability, LayerError, LayerRequest, LayerResponse

logger = logging.getLogger(__name__)

# Handler type: receives a LayerRequest, returns a LayerResponse
LayerHandler = Callable[[LayerRequest], LayerResponse]

# Default fallback for unregistered layers
_UNKNOWN_HANDLER_RESPONSE = {
    "ok": False,
    "version": "0.0.0",
    "data": {},
    "error": LayerError(
        code="LAYER_NOT_FOUND",
        message="请求的层未在注册表中注册",
    ),
}


@dataclass
class RegisteredLayer:
    """已注册的层元信息 + 调用句柄"""
    name: str
    version: str
    handler: Optional[LayerHandler] = None
    capability: Optional[LayerCapability] = None
    status: str = "active"  # active / degraded / offline
    metadata: Dict[str, Any] = field(default_factory=dict)


class _LayerRegistry:
    """
    层注册表（线程安全单例）
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._layers: Dict[str, RegisteredLayer] = {}
        self._initialized = False

    # ---- 注册 ----

    def register(
        self,
        name: str,
        version: str,
        handler: Optional[LayerHandler] = None,
        capability: Optional[LayerCapability] = None,
        status: str = "active",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """注册一个层。

        Args:
            name: 层名（如 "wangchuan", "consciousness"）
            version: 语义版本号
            handler: 请求处理函数，接收 LayerRequest → 返回 LayerResponse
            capability: 该层的稳定能力清单
            status: 状态（active / degraded / offline）
            metadata: 附加元信息
        """
        if not name or not name.strip():
            raise ValueError("层名不能为空")
        norm_name = name.strip().lower()

        with self._lock:
            prev = self._layers.get(norm_name)
            if prev and prev.handler and handler:
                logger.warning(
                    "层 [%s] 重复注册: 旧版本 %s → 新版本 %s",
                    norm_name, prev.version, version,
                )

            registered = RegisteredLayer(
                name=norm_name,
                version=version,
                handler=handler,
                capability=capability,
                status=status,
                metadata=metadata or {},
            )
            self._layers[norm_name] = registered
            self._initialized = True
            logger.info("层 [%s] v%s 注册成功, 状态: %s", norm_name, version, status)

    def unregister(self, name: str) -> bool:
        """注销一个层。"""
        norm_name = name.strip().lower()
        with self._lock:
            if norm_name in self._layers:
                del self._layers[norm_name]
                logger.info("层 [%s] 已注销", norm_name)
                return True
            return False

    # ---- 查询 ----

    def get(self, name: str) -> Optional[RegisteredLayer]:
        """获取层的注册信息。"""
        norm_name = name.strip().lower()
        with self._lock:
            return self._layers.get(norm_name)

    def list_layers(self) -> Dict[str, RegisteredLayer]:
        """返回所有已注册层的快照。"""
        with self._lock:
            return dict(self._layers)

    def discover(self) -> List[Dict[str, Any]]:
        """返回所有已注册层的摘要列表（供外部发现用）。"""
        with self._lock:
            result = []
            for name, rl in self._layers.items():
                ops = []
                if rl.capability:
                    ops = rl.capability.stable
                result.append({
                    "name": rl.name,
                    "version": rl.version,
                    "status": rl.status,
                    "operations": ops,
                })
            return sorted(result, key=lambda x: x["name"])

    def is_registered(self, name: str) -> bool:
        """检查层是否已注册。"""
        norm_name = name.strip().lower()
        with self._lock:
            return norm_name in self._layers

    # ---- 调用分发 ----

    def dispatch(self, request: LayerRequest) -> LayerResponse:
        """将请求分发到目标层的 handler。

        这是层间调用的唯一入口：
        - 注册了的层 → 调用其 handler
        - 未注册 → 返回 LAYER_NOT_FOUND 错误
        - handler 异常 → 返回 HANDLER_ERROR 错误

        Args:
            request: 层请求，包含 target layer/operation/payload

        Returns:
            LayerResponse: 始终返回有效响应（即使出错）
        """
        target = request.layer.strip().lower()
        trace_id = request.trace_id or ""

        if not target:
            return LayerResponse(
                layer="",
                version="",
                operation=request.operation,
                ok=False,
                error=LayerError(code="INVALID_TARGET", message="目标层名为空"),
                trace_id=trace_id,
            )

        registered = self.get(target)
        if not registered:
            return LayerResponse(
                layer=target,
                version="",
                operation=request.operation,
                ok=False,
                error=LayerError(
                    code="LAYER_NOT_FOUND",
                    message=f"层 '{target}' 未在注册表中注册。已注册层: {[l['name'] for l in self.discover()]}",
                ),
                trace_id=trace_id,
            )

        if registered.handler is None:
            return LayerResponse(
                layer=target,
                version=registered.version,
                operation=request.operation,
                ok=False,
                error=LayerError(
                    code="HANDLER_NOT_SET",
                    message=f"层 '{target}' 已注册但未绑定 handler",
                ),
                trace_id=trace_id,
            )

        try:
            response = registered.handler(request)
            if not isinstance(response, LayerResponse):
                raise TypeError(
                    f"handler 返回类型错误: 期望 LayerResponse, 得到 {type(response).__name__}"
                )
            return response
        except Exception as exc:
            logger.error("层 [%s] handler 调用异常: %s", target, exc, exc_info=True)
            return LayerResponse(
                layer=target,
                version=registered.version,
                operation=request.operation,
                ok=False,
                error=LayerError(
                    code="HANDLER_ERROR",
                    message=f"handler 抛出异常: {exc}",
                ),
                trace_id=trace_id,
            )

    # ---- 生命周期 ----

    def reset(self) -> None:
        """清空所有注册（测试 / 重启用）。"""
        with self._lock:
            self._layers.clear()
            self._initialized = False
            logger.info("注册表已清空")

    @property
    def initialized(self) -> bool:
        return self._initialized


# =============================================
# 模块级单例（标准用法）
# =============================================

_registry = _LayerRegistry()


def register(
    name: str,
    version: str,
    handler: Optional[LayerHandler] = None,
    capability: Optional[LayerCapability] = None,
    status: str = "active",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """注册一个层（模块级快捷函数）。"""
    _registry.register(
        name=name,
        version=version,
        handler=handler,
        capability=capability,
        status=status,
        metadata=metadata,
    )


def dispatch(request: LayerRequest) -> LayerResponse:
    """分发一个层请求（模块级快捷函数）。"""
    return _registry.dispatch(request)


def discover() -> List[Dict[str, Any]]:
    """发现所有已注册层（模块级快捷函数）。"""
    return _registry.discover()


def get_layer(name: str) -> Optional[RegisteredLayer]:
    """获取指定层的注册信息。"""
    return _registry.get(name)


def is_registered(name: str) -> bool:
    """检查层是否已注册。"""
    return _registry.is_registered(name)


def list_layers() -> Dict[str, RegisteredLayer]:
    """获取所有已注册层。"""
    return _registry.list_layers()


def unregister(name: str) -> bool:
    """注销一个层。"""
    return _registry.unregister(name)


def reset_for_testing() -> None:
    """测试用：清空注册表。"""
    _registry.reset()


__all__ = [
    "LayerHandler",
    "RegisteredLayer",
    "_LayerRegistry",
    "dispatch",
    "discover",
    "get_layer",
    "list_layers",
    "register",
    "reset_for_testing",
    "unregister",
]
