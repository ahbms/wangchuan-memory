from __future__ import annotations

"""WangChuan LLM client lazy-init helpers.

这一层承接 memory_api._get_llm_client 中的低风险懒加载逻辑：
- 根据环境变量探测 API key
- 优先尝试 OpenAI
- fallback 到 Anthropic
- 缓存到 memory_obj._llm_client
"""

from typing import Any
import os


def get_llm_client(memory_obj: Any):
    """获取 LLM 客户端（懒加载）。"""
    if memory_obj._llm_client is not None:
        return memory_obj._llm_client

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import openai

        openai.api_key = api_key
        memory_obj._llm_client = "openai"
        return memory_obj._llm_client
    except ImportError:
        pass

    try:
        import anthropic

        memory_obj._llm_client = anthropic.Anthropic(api_key=api_key)
        return memory_obj._llm_client
    except ImportError:
        pass

    return None
