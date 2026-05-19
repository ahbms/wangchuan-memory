from __future__ import annotations

from pathlib import Path


FAQ = Path(__file__).resolve().parents[1] / "docs" / "FAQ.md"


def test_faq_answers_required_questions():
    text = FAQ.read_text(encoding="utf-8")
    required_headings = [
        "## 忘川和向量数据库有什么区别？",
        "## 数据存在哪里？",
        "## 如何改数据库路径？",
        "## 是否需要 LLM？",
        "## 为什么 recall 返回空？",
        "## 什么是 recall_raw / recall_scars？",
        "## MCP 怎么配置？",
        "## 可以生产用吗？",
        "## 怎么备份？",
    ]
    for heading in required_headings:
        assert heading in text

    required_terms = [
        "$WANGCHUAN_HOME/.index/index.sqlite",
        "WANGCHUAN_HOME",
        "无需",
        "recall_raw",
        "recall_scars",
        "wangchuan-memory[mcp]",
        "python3 -m wangchuan.mcp_server",
        "3.0.0-alpha",
        "cp .index/index.sqlite",
    ]
    for term in required_terms:
        assert term in text
