from __future__ import annotations

"""
意识层身份桥接兼容入口 / 实现承载层入口

说明：
- 当前正式职责名入口优先看 tiangong.consciousness.identity_adapter
- 本文件保留是为了兼容旧的导入路径 wangchuan.v3.consciousness.identity_adapter
- 不要再把本文件当成新的 identity_adapter 入口继续扩散使用
"""

from wangchuan._adapters.consciousness_adapter import get_identity_adapter_cls

IdentityAdapter = get_identity_adapter_cls()

__all__ = ["IdentityAdapter"]
