from __future__ import annotations

"""
意识层行为策略兼容入口 / 实现承载层入口

说明：
- 当前正式职责名入口优先看 tiangong.consciousness.strategy
- 本文件保留是为了兼容旧的导入路径 wangchuan.v3.consciousness.strategy_updater
- 不要再把本文件当成新的 strategy 入口继续扩散使用
"""

from wangchuan._adapters.consciousness_adapter import (
    get_rules_path,
    get_strategy_updater_cls,
)

RULES_PATH = get_rules_path() or ""
StrategyUpdater = get_strategy_updater_cls()
Scar = None  # 降级: 无 L6 时不可用

__all__ = ["Scar", "RULES_PATH", "StrategyUpdater"]
