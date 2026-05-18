from __future__ import annotations

"""WangChuan recall query / temporal / noise helpers.

这一层承接 memory_api 中 recall 查询阶段的低风险纯逻辑：
- recall noise 判定
- temporal probe 归一化
- query keyword 扩展
- RRF 融合排序

约束：
- 不改写 recall 主链 SQL / 向量召回协议
- 仍由调用方（Memory）提供 alias hints / compact helper / noise patterns
- 优先保持与 memory_api 现有行为口径一致
"""

from datetime import datetime
from typing import Any, Dict, List, Set
import re


GENERIC_PROFILE_QUERY_HINTS = {
    "偏好", "用户", "称呼", "怎么称呼", "回复风格", "分段回复",
    "透明黑盒", "主通道", "telegram", "家在", "公司在",
}


def is_recall_noise(memory_obj: Any, content: str) -> bool:
    text = str(content or '').strip().lower()
    if not text:
        return True
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in memory_obj.RECALL_NOISE_PATTERNS)


def normalize_temporal_probe(value: Any, default_now: bool = False) -> str:
    if value in (None, ""):
        probe = datetime.now().isoformat(timespec="microseconds") if default_now else ""
    else:
        probe = str(value).strip()
    if not probe:
        return probe
    return probe.replace("T", " ")


def build_recall_keyword_tokens(memory_obj: Any, normalized_query: str) -> List[str]:
    normalized_query = str(normalized_query or "").strip()
    if not normalized_query:
        return []

    split_pattern = r"[\s,，。！？、:：;；()（）\[\]{}\-_/\\|]+|(?:和|与|及|并且|以及|还有|跟|或|或者)"
    keyword_tokens = [
        token.strip() for token in re.split(split_pattern, normalized_query)
        if token.strip() and len(token.strip()) >= 2
    ]

    expanded_tokens = []
    for token in keyword_tokens:
        expanded_tokens.append(token)
        compact_token = memory_obj._compact_recall_match_text(token)
        if compact_token.startswith("用户") and len(compact_token) > 2:
            expanded_tokens.append(compact_token[2:])
        for cue in ("冰美式", "不吃辣", "简洁", "张哥", "张三"):
            if cue in token and cue != token:
                expanded_tokens.append(cue)
        for cue, aliases in memory_obj.RECALL_QUERY_ALIAS_HINTS.items():
            if cue in compact_token or cue in normalized_query:
                expanded_tokens.extend(aliases)

    seen = set()
    return [
        token for token in expanded_tokens
        if not (memory_obj._compact_recall_match_text(token) in seen or seen.add(memory_obj._compact_recall_match_text(token)))
    ]


def build_dynamic_profile_query_tokens(memory_obj: Any, query: str) -> Set[str]:
    text = str(query or "").strip()
    if not text:
        return set(GENERIC_PROFILE_QUERY_HINTS)

    tokens: Set[str] = set(GENERIC_PROFILE_QUERY_HINTS)
    for token in build_recall_keyword_tokens(memory_obj, text):
        compact = memory_obj._compact_recall_match_text(token)
        if len(compact) >= 2:
            tokens.add(token)
            tokens.add(compact)
            for prefix in ("用户喜欢", "用户偏好", "用户叫", "喜欢喝", "爱喝", "欢喝", "厌喝", "喜欢", "家在", "公司在"):
                if compact.startswith(prefix) and len(compact) > len(prefix):
                    tokens.add(compact[len(prefix):])
    return {token for token in tokens if str(token).strip()}


def query_looks_like_user_profile(memory_obj: Any, query: str) -> bool:
    lowered_query = str(query or "").strip().lower()
    if not lowered_query:
        return False
    if any(hint in lowered_query for hint in GENERIC_PROFILE_QUERY_HINTS):
        return True
    compact_query = memory_obj._compact_recall_match_text(lowered_query)
    if re.fullmatch(r".[哥姐总叔姨]", compact_query):
        return True
    if compact_query.startswith("用户"):
        return True
    return False


def rrf_fusion(rows: List[tuple], normalized_query: str, keyword_tokens: List[str], k: int = 60) -> List[tuple]:
    if not rows or len(rows) <= 1:
        return rows

    score_map: Dict[int, float] = {}

    for rank, row in enumerate(rows):
        memory_id = int(row[0])
        confidence = float(row[2]) if row[2] else 0.0
        base_score = confidence * 10
        score_map[memory_id] = score_map.get(memory_id, 0) + 1.0 / (k + rank + 1) * (base_score * 0.3)

    if normalized_query:
        for rank, row in enumerate(rows):
            memory_id = int(row[0])
            content = str(row[1] or "").lower()
            query_lower = normalized_query.lower()

            exact_match = 1.0 if query_lower in content else 0.0
            prefix_match = 1.0 if content.startswith(query_lower[:5]) else 0.0

            match_score = (exact_match * 2.0 + prefix_match * 1.0) / (k + rank + 1)
            score_map[memory_id] = score_map.get(memory_id, 0) + match_score * 0.4

    if keyword_tokens:
        for rank, row in enumerate(rows):
            memory_id = int(row[0])
            content = str(row[1] or "").lower()

            matched = sum(1 for token in keyword_tokens if token.lower() in content)
            keyword_score = matched / len(keyword_tokens) if keyword_tokens else 0.0

            score_map[memory_id] = score_map.get(memory_id, 0) + keyword_score * 0.3 / (k + rank + 1)

    fused_rows = sorted(
        [(row, score_map.get(int(row[0]), 0)) for row in rows],
        key=lambda x: x[1],
        reverse=True,
    )

    return [row for row, _score in fused_rows]


def filter_recall_noise(memory_obj: Any, rows: List[tuple], limit: int) -> List[tuple]:
    filtered = [row for row in rows if not is_recall_noise(memory_obj, row[1] if len(row) > 1 else row[0])]
    if filtered:
        return filtered[:limit]
    return rows[:limit]
