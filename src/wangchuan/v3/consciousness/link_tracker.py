from __future__ import annotations

"""
意识层规则关联跟踪兼容入口 / 实现承载层入口

说明：
- 当前正式职责名入口优先看 tiangong.consciousness.link_tracker
- 本文件保留是为了兼容旧的导入路径 wangchuan.v3.consciousness.link_tracker
- 不要再把本文件当成新的 link_tracker 入口继续扩散使用
"""

from wangchuan._adapters.consciousness_adapter import (
    get_rule_links_path,
    get_link_tracker_cls,
)

RULE_LINKS_PATH = get_rule_links_path() or ""
RuleLinkTracker = get_link_tracker_cls()

__all__ = ["RULE_LINKS_PATH", "RuleLinkTracker"]
