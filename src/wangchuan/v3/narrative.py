#!/usr/bin/env python3
"""
意识层叙事兼容入口 / 实现承载层入口

说明：
- 当前正式职责名入口优先看 tiangong.consciousness.narrative
- 本文件保留是为了兼容旧的导入路径 wangchuan.v3.narrative
- 不要再把本文件当成新的叙事系统入口继续扩散使用
"""

from wangchuan._adapters.consciousness_adapter import (
    get_narrative_builder as _get_nb,
    get_timeline_event_type as _get_tev_type,
)

NarrativeBuilder = _get_nb
TimelineEvent = _get_tev_type

__all__ = ["NarrativeBuilder", "TimelineEvent"]


if __name__ == "__main__":
    print("📖 叙我层 · 时间线叙事构建器")
    print("=" * 50)

    builder = NarrativeBuilder()
    narrative = builder.build()

    lines = narrative.split("\n")
    events_count = sum(1 for l in lines if l.startswith("⭐") or l.startswith("•") or l.startswith("·"))
    print(f"生成完成: {events_count} 个事件")
    print(f"输出文件: {builder.output_path}")
