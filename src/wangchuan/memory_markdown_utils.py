from __future__ import annotations

"""WangChuan markdown parsing helpers.

这一层承接 memory_api / diagnostics 中与任务板解析相关的低风险 helper：
- 提取 markdown section
- 提取 bullet / numbered items
- 提取 label:value 文本值
"""

from typing import List
import re


def extract_markdown_section(text: str, heading: str) -> str:
    lines = (text or "").splitlines()
    heading_pattern = re.compile(rf"^(#{'{'}2,6{'}'})\s+{re.escape(heading)}\s*$")

    start_idx = -1
    start_level = 0
    for idx, line in enumerate(lines):
        match = heading_pattern.match(line.strip())
        if match:
            start_idx = idx + 1
            start_level = len(match.group(1))
            break

    if start_idx < 0:
        return ""

    end_idx = len(lines)
    for idx in range(start_idx, len(lines)):
        stripped = lines[idx].strip()
        match = re.match(r"^(#{2,6})\s+", stripped)
        if match and len(match.group(1)) <= start_level:
            end_idx = idx
            break

    return "\n".join(lines[start_idx:end_idx]).strip()


def extract_bullet_items(text: str, heading: str, limit: int = 8) -> List[str]:
    body = extract_markdown_section(text, heading)
    if not body:
        return []
    items: List[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith('- ') or stripped.startswith('* '):
            items.append(stripped[2:].strip())
        elif re.match(r"^\d+\.\s+", stripped):
            items.append(re.sub(r"^\d+\.\s+", "", stripped))
        if len(items) >= limit:
            break
    return items


def extract_label_value(text: str, label: str) -> str:
    pattern = rf"{re.escape(label)}\s*[：:]\s*(.+)"
    match = re.search(pattern, text or "")
    return match.group(1).strip() if match else ""
