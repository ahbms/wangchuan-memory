#!/usr/bin/env python3
"""
意识层目标管理兼容入口 / 实现承载层入口

说明：
- 当前正式职责名入口优先看 tiangong.consciousness.goals
- 本文件保留是为了兼容旧的导入路径 wangchuan.v3.goal_manager
- 不要再把本文件当成新的目标系统入口继续扩散使用
"""

from wangchuan._adapters.consciousness_adapter import get_goal_types

Goal, GoalManager, GoalPriority, GoalStatus, SubTask = get_goal_types()

__all__ = ["GoalManager", "Goal", "SubTask", "GoalStatus", "GoalPriority"]


if __name__ == "__main__":
    manager = GoalManager()
    summary = manager.get_summary()
    print("🎯 行愿层 · 目标管理器")
    print(f"  总目标: {summary['total']}")
    print(f"  进行中: {summary['active']}")
    print(f"  已完成: {summary['completed']}")
    for g in summary["active_goals"]:
        print(f"  → [{g['priority']}] {g['title']} ({g['progress']:.0%})")
