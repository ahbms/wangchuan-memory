from __future__ import annotations

"""WangChuan LLM extraction helpers.

这一层承接 memory_api.extract_with_llm 中的低风险结构化提取逻辑：
- 组装 extraction prompt
- 调用 OpenAI / Anthropic 客户端
- 从模型返回中解析 JSON 数组

目标：
- 不改变 Memory 公共签名
- 保持现有错误口径与默认 fallback 语义
"""

import json
import re
from typing import Any, Dict, List


def _build_extraction_prompts(text: str, max_items: int) -> Dict[str, str]:
    return {
        "preference": f"""从以下文本中提取用户偏好信息。每条偏好需要包含：
- content: 偏好描述
- importance: 重要性 0-1
- tags: 标签列表

返回 JSON 数组格式，最多 {max_items} 条：
文本：{text}""",
        "fact": f"""从以下文本中提取事实信息。每条事实需要包含：
- content: 事实描述
- importance: 重要性 0-1
- tags: 标签列表

返回 JSON 数组格式，最多 {max_items} 条：
文本：{text}""",
        "rule": f"""从以下文本中提取规则/规范信息。每条规则需要包含：
- content: 规则描述
- importance: 重要性 0-1
- tags: 标签列表

返回 JSON 数组格式，最多 {max_items} 条：
文本：{text}""",
    }


def _parse_llm_extraction_items(content: str) -> List[Dict[str, Any]]:
    json_match = re.search(r"\[.*\]", str(content or ""), re.DOTALL)
    if json_match:
        return json.loads(json_match.group())
    return [{"error": "Failed to parse LLM response"}]


def extract_with_llm(
    memory_obj: Any,
    text: str,
    extraction_type: str = "preference",
    max_items: int = 5,
) -> List[Dict[str, Any]]:
    client_type = memory_obj._get_llm_client()
    if not client_type:
        return [{"error": "No LLM API key configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY"}]

    extraction_prompts = _build_extraction_prompts(text, max_items)
    prompt = extraction_prompts.get(extraction_type, extraction_prompts["preference"])

    try:
        if client_type == "openai":
            import openai

            response = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000,
            )
            content = response.choices[0].message.content
        else:
            response = client_type.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.content[0].text

        return _parse_llm_extraction_items(content)
    except Exception as e:
        return [{"error": str(e)}]
