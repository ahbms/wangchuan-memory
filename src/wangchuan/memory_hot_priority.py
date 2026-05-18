from __future__ import annotations

"""WangChuan hot-memory heuristics helpers.

这一层承接 memory_api 中与 hot-memory 候选判定相关的低风险纯逻辑：
- 文本归一化
- canonical key 生成
- 热度优先级打分
- hot-memory candidate 判定
- quality / hotness 默认分数计算

约束：
- 不改写记忆真值协议
- 仍由调用方（Memory）提供 tags/metadata 常量与辅助归一化
- 优先保持与 memory_api 现有打分口径一致
"""

from typing import Any, Dict, List
import re


def normalize_hot_memory_text(content: str) -> str:
    text = re.sub(r"\s+", " ", str(content or "").strip())
    return text.strip(" -•\t")


def canonical_hot_memory_key(content: str) -> str:
    text = normalize_hot_memory_text(content).lower()
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text


def hot_memory_priority(memory_obj: Any, text: str, tags: List[str] | None = None, metadata: Dict[str, Any] | None = None) -> int:
    metadata = dict(metadata or {})
    normalized_tags = memory_obj._normalize_tags(tags or metadata.get("tags") or [])
    lowered_tags = {t.lower() for t in normalized_tags}
    normalized_text = normalize_hot_memory_text(text)
    score = 0
    if metadata.get("user_explicit") or lowered_tags & {"user", "preference", "identity", "profile", "habit"}:
        score += 5
    if lowered_tags & {"rule", "lesson"} or any(token in normalized_text for token in ["必须", "禁止", "不要", "默认", "规则", "教训", "经验"]):
        score += 4
    if metadata.get("promotion_reason"):
        score += 2
    if metadata.get("source_anchor") or metadata.get("turn_signature"):
        score += 1
    if len(normalized_text) <= 72:
        score += 2
    elif len(normalized_text) <= 120:
        score += 1
    if any(token in normalized_text for token in ["测试", "回归", "通过", "py_compile", "pipeline", "日志", "收口"]):
        score -= 4
    return score


def _priority_metadata(user_explicit: bool, source_anchor: str, turn_signature: str, promotion_reason: str) -> Dict[str, Any]:
    return {
        "user_explicit": user_explicit,
        "source_anchor": source_anchor,
        "turn_signature": turn_signature,
        "promotion_reason": promotion_reason,
    }


def compute_hot_memory_candidate(
    memory_obj: Any,
    text: str,
    normalized_tags: List[str] | None = None,
    *,
    source_layer: str,
    is_test_data: bool,
    user_explicit: bool,
    promotion_reason: str,
    source_anchor: str,
    turn_signature: str,
) -> bool:
    normalized_tags = list(normalized_tags or [])
    lowered_tags = {t.lower() for t in normalized_tags}
    return (
        not is_test_data
        and source_layer not in {"raw", "candidate"}
        and not (lowered_tags & memory_obj.HOT_MEMORY_BLOCK_TAGS)
        and len(normalize_hot_memory_text(text)) <= memory_obj.HOT_MEMORY_MAX_TEXT_LENGTH
        and (
            bool(lowered_tags & memory_obj.HOT_MEMORY_ALLOWED_TAGS)
            or any(hint in text for hint in memory_obj.WRITE_GATE_ALLOW_HINTS)
        )
        and hot_memory_priority(
            memory_obj,
            text,
            normalized_tags,
            _priority_metadata(user_explicit, source_anchor, turn_signature, promotion_reason),
        ) >= 4
    )


def compute_quality_score(
    memory_obj: Any,
    text: str,
    normalized_tags: List[str] | None = None,
    *,
    user_explicit: bool,
    promotion_reason: str,
    source_anchor: str,
    turn_signature: str,
) -> float:
    priority = hot_memory_priority(
        memory_obj,
        text,
        list(normalized_tags or []),
        _priority_metadata(user_explicit, source_anchor, turn_signature, promotion_reason),
    )
    return max(0.0, min(1.0, 0.35 + min(max(priority, 0), 8) * 0.08))


def compute_hotness_score(*, hot_memory_candidate: bool, user_explicit: bool, promotion_reason: str) -> float:
    return max(
        0.0,
        min(
            1.0,
            0.2
            + (0.45 if hot_memory_candidate else 0.0)
            + (0.2 if user_explicit else 0.0)
            + (0.1 if promotion_reason else 0.0),
        ),
    )
