from __future__ import annotations

"""WangChuan hot-memory sync helpers.

低风险拆分目标：
- 抽离 MEMORY.md 热记忆同步逻辑
- 保留 Memory._sync_to_memory_md 公开/内部方法签名不变
- 不改变 hot memory curator 的过滤、去重与排序口径
"""

from datetime import datetime
import re
from typing import Any, List

try:
    from wangchuan.paths import hot_memory_md_path
except ImportError:
    from wangchuan.paths import hot_memory_md_path


def sync_to_memory_md(memory_obj: Any, content: str, tags: List[str]):
    """同步重要记忆到独立运行态 hot-memory markdown。"""
    memory_md = hot_memory_md_path()

    text = memory_obj._normalize_hot_memory_text(content)
    normalized_tags = memory_obj._normalize_tags(tags)
    lowered_tags = {t.lower() for t in normalized_tags}
    metadata = memory_obj._build_memory_metadata(text, normalized_tags)

    if not text:
        return
    if lowered_tags & memory_obj.HOT_MEMORY_BLOCK_TAGS:
        return
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in memory_obj.HOT_MEMORY_BLOCK_PATTERNS):
        return
    if len(text) > memory_obj.HOT_MEMORY_MAX_TEXT_LENGTH:
        return

    memory_signal = (
        bool(lowered_tags & memory_obj.HOT_MEMORY_ALLOWED_TAGS)
        or any(hint in text for hint in memory_obj.WRITE_GATE_ALLOW_HINTS)
    )
    if not memory_signal:
        return
    if memory_obj._hot_memory_priority(text, normalized_tags, metadata) < 4:
        return

    entry = f"- {text}"
    if normalized_tags:
        entry += " " + " ".join(f"[{t}]" for t in normalized_tags)

    try:
        memory_md.parent.mkdir(parents=True, exist_ok=True)
        existing = memory_md.read_text(encoding="utf-8") if memory_md.exists() else "# 长期记忆\n"
        lines = existing.splitlines()
        header = []
        body = []
        in_hot_sync = False
        for line in lines:
            stripped = line.strip()
            if stripped == "## 忘川同步记忆":
                in_hot_sync = True
                continue
            if in_hot_sync:
                if stripped.startswith("## ") and stripped != "## 忘川同步记忆":
                    in_hot_sync = False
                else:
                    continue
            if not in_hot_sync:
                if line.startswith("#") or not stripped:
                    header.append(line)
                elif line.lstrip().startswith("- "):
                    body.append(line)
                else:
                    header.append(line)

        existing_entries = []
        seen_keys = set()
        for line in body:
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            key = memory_obj._canonical_hot_memory_key(stripped[2:])
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            existing_entries.append(stripped)

        new_key = memory_obj._canonical_hot_memory_key(text)
        if not new_key or new_key in seen_keys:
            return
        existing_entries.append(entry)

        ranked_entries = sorted(
            existing_entries,
            key=lambda item: (
                memory_obj._hot_memory_priority(
                    item[2:],
                    re.findall(r"\[([^\]]+)\]", item),
                    memory_obj._build_memory_metadata(item[2:], re.findall(r"\[([^\]]+)\]", item)),
                ),
                -len(memory_obj._normalize_hot_memory_text(item[2:])),
            ),
            reverse=True,
        )
        ranked_entries = ranked_entries[: memory_obj.HOT_MEMORY_MAX_ITEMS]

        base = "\n".join(header).rstrip() or "# 长期记忆"
        sync_lines = ["", "## 忘川同步记忆", f"最后同步: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
        sync_lines.extend(ranked_entries)
        memory_md.write_text(base + "\n" + "\n".join(sync_lines).rstrip() + "\n", encoding="utf-8")
    except Exception:
        pass
