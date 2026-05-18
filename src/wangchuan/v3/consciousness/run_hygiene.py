#!/usr/bin/env python3
"""
意识维护脚本（兼容入口 / 实现承载层入口）

职责：
- 兼容现有从 wangchuan/v3/consciousness/run_hygiene.py 触发的调用方
- 当前本脚本转发到 tiangong.consciousness.hygiene 的职责名入口
- 这是 consciousness 维护动作的可执行脚本入口，而不是职责名入口本身

边界：
- recall 主链职责名入口优先看 wangchuan.recall_service
- 意识维护职责名入口当前优先看 tiangong.consciousness.hygiene
- 本脚本位于 v3/consciousness 下，表示它属于当前实现承载层兼容入口
- 不要把本脚本当成主 recall 入口，也不要把它和 legacy memory 链混为一谈
- 如果你在找“意识维护应该从哪里开始读”，先看 tiangong.consciousness.hygiene，再回到本脚本看可执行封装
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wangchuan._adapters.consciousness_adapter import get_consciousness_tool_bridge

# adapt: run_hygiene → consciousness tool_bridge fallback存在


def main():
    result = maintain_consciousness_hygiene()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
