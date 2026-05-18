from __future__ import annotations

"""
意识层自我状态兼容入口 / 实现承载层入口

说明：
- 当前正式职责名入口优先看 tiangong.consciousness.self_state
- 本文件保留是为了兼容旧的导入路径 wangchuan.v3.consciousness.self_state
- 不要再把本文件当成新的 self_state 入口继续扩散使用
"""

from wangchuan._adapters.consciousness_adapter import (
    get_self_state_cls,
    get_self_state_path,
)

SelfStateStore, SelfStateUpdater = get_self_state_cls()
STATE_PATH = get_self_state_path() or ""
SelfState = None
DEFAULT_STATE = {}

__all__ = [
    "SelfState",
    "STATE_PATH",
    "DEFAULT_STATE",
    "SelfStateStore",
    "SelfStateUpdater",
]
