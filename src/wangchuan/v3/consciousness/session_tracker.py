from __future__ import annotations

"""
意识层会话规则跟踪兼容入口 / 实现承载层入口

说明：
- 当前正式职责名入口优先看 tiangong.consciousness.session_tracker
- 本文件保留是为了兼容旧的导入路径 wangchuan.v3.consciousness.session_tracker
- 不要再把本文件当成新的 session_tracker 入口继续扩散使用
"""

from wangchuan._adapters.consciousness_adapter import (
    get_session_rule_hits_path,
    get_session_tracker_cls,
)

SESSION_RULE_HITS_PATH = get_session_rule_hits_path() or ""
SessionRuleTracker = get_session_tracker_cls()

__all__ = ["SESSION_RULE_HITS_PATH", "SessionRuleTracker"]
