from __future__ import annotations

"""
意识层工具结果桥接兼容入口 / 实现承载层入口

说明：
- 当前正式职责名入口优先看 tiangong.consciousness.tool_bridge
- 本文件保留是为了兼容旧的导入路径 wangchuan.v3.consciousness.tool_bridge
- 不要再把本文件当成新的工具结果桥接入口继续扩散使用
"""

from wangchuan._adapters.consciousness_adapter import get_consciousness_tool_bridge

_TB = get_consciousness_tool_bridge()
ToolExecutionResult = _TB[0]
detect_tool_success = _TB[1] if _TB[1] else lambda x: x
run_tool_with_consciousness = _TB[2] if _TB[2] else lambda tool, **kw: {"status": "stub"}
summarize_tool_payload = lambda x: x

__all__ = [
    "ToolExecutionResult",
    "summarize_tool_payload",
    "detect_tool_success",
    "run_tool_with_consciousness",
]
