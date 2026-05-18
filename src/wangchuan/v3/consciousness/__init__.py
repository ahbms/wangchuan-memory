#!/usr/bin/env python3
"""
意识闭环引擎兼容入口 / 实现承载层入口

说明：
- 当前 consciousness 的职责名入口已经上移到 `tiangong.consciousness.engine`
- 本包当前属于 v3 consciousness 的兼容入口 / 实现承载层入口
- 如果你在找“意识中枢应该从哪里开始读”，优先看 `tiangong.consciousness.engine`
- 当前主 recall 主链依然优先看 `wangchuan.recall_service`
- 为避免包级循环导入，这里不再做 engine / injector 的 eager import，而改为 lazy export
- 本包只保留兼容壳与实现承载层职责，不再承载新的 consciousness 对外职责入口
- 不要把本包继续当成 consciousness 对外的长期主入口
"""

from wangchuan._adapters.consciousness_adapter import get_group_awareness_cls

GroupAwareness = get_group_awareness_cls()

__all__ = ["ConsciousnessEngine", "ConsciousnessInjector", "GroupAwareness"]


def __getattr__(name):
    if name == "ConsciousnessEngine":
        from .engine import ConsciousnessEngine
        return ConsciousnessEngine
    if name == "ConsciousnessInjector":
        from .injector import ConsciousnessInjector
        return ConsciousnessInjector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
