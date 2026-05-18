#!/usr/bin/env python3
"""
FTS 查询辅助工具。

把自然语言查询整理成 SQLite FTS5 更稳定的 MATCH 语句，避免诸如
`[foo]`、时间戳、系统元数据等带符号文本直接进入 MATCH 后触发
`fts5: syntax error near "["`。
"""

from __future__ import annotations

import re
from typing import List


TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_.:/-]+|[\u4e00-\u9fff]+")
SPLIT_PATTERN = re.compile(r"[\s\u3000,，。！？、；：:;\"'“”‘’【】（）()\[\]{}<>《》]+")


def tokenize_search_terms(query: str, min_len: int = 2, max_terms: int = 8) -> List[str]:
    """提取适合全文检索的安全 token。"""
    normalized = str(query or "").strip()
    if not normalized:
        return []

    tokens: List[str] = []
    seen = set()

    def add(token: str) -> None:
        token = str(token or "").strip()
        if len(token) < min_len:
            return
        if token not in seen:
            tokens.append(token)
            seen.add(token)

    raw_parts = SPLIT_PATTERN.split(normalized)
    for part in raw_parts:
        part = part.strip()
        if not part:
            continue
        for segment in TOKEN_PATTERN.findall(part):
            add(segment)
            if len(tokens) >= max_terms:
                return tokens[:max_terms]

    if not tokens:
        for segment in TOKEN_PATTERN.findall(normalized):
            add(segment)
            if len(tokens) >= max_terms:
                return tokens[:max_terms]

    return tokens[:max_terms]


def build_safe_fts_match_query(
    query: str,
    min_len: int = 2,
    max_terms: int = 8,
    joiner: str = "OR",
) -> str:
    """构造尽量稳健的 FTS5 MATCH 查询串。"""
    tokens = tokenize_search_terms(query, min_len=min_len, max_terms=max_terms)
    phrases = []
    for token in tokens:
        safe = token.replace('"', ' ').strip()
        if safe:
            phrases.append(f'"{safe}"')
    if not phrases:
        return ""
    operator = f" {joiner.strip().upper() or 'OR'} "
    return operator.join(phrases)
