#!/usr/bin/env python3
"""
意识层身份追踪兼容入口 / 实现承载层入口

说明：
- 当前正式职责名入口优先看 tiangong.consciousness.identity
- 本文件保留是为了兼容旧的导入路径 wangchuan.v3.identity_tracker
- 不要再把本文件当成新的意识层入口继续扩散使用
"""

from wangchuan._adapters.consciousness_adapter import get_identity_tracker as _get_identity_tracker

IdentityTracker = type("_StubIdentityTracker", (), {})  # 实际由 get_identity_tracker() 创建

__all__ = ["IdentityTracker"]


if __name__ == "__main__":
    tracker = IdentityTracker()
    path = tracker.snapshot()
    print(f"📸 快照保存: {path}")
    print(f"历史版本: {len(tracker.get_history())} 个")
