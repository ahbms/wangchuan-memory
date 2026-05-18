from __future__ import annotations

"""
明识层 - 事件提取器
天工开智 / 意识进化体系 · 第8层（实现承载）

说明：
- 当前 consciousness event_extractor 的职责名入口已经上移到 `tiangong.consciousness.event_extractor`
- 本文件当前属于 v3 consciousness 的兼容入口 / 实现承载层
- 如果你在找“意识系统如何把原始消息和工具结果整理成可反思事件”，优先看 `tiangong.consciousness.event_extractor`
- 当前主 recall 主链依然优先看 `wangchuan.recall_service`
- 它负责把“发生了什么”整理成结构化事件，而不直接决定“学到了什么”
"""

import uuid
import re
from datetime import datetime

from wangchuan._adapters.consciousness_adapter import get_consciousness_schemas

Event, _, _, _ = get_consciousness_schemas()


CORRECTION_MARKERS = ["不对", "错了", "不是这个意思", "别这样", "不要", "应该", "修正", "纠正"]
POSITIVE_FEEDBACK_MARKERS = ["对", "可以", "明白", "好", "继续", "就这样"]
ERROR_MARKERS = ["error", "失败", "报错", "异常", "exception"]

_TOOL_RESULT_NOISE_PATTERNS = [
    r"plugin hooks?\(",
    r"before_message_write",
    r"message_sent",
    r"queued messages while agent was busy",
    r"system \(untrusted\)",
    r"conversation info",
    r"sender \(untrusted metadata\)",
    r"sender metadata",
    r"request envelope",
    r"\bmessage_id\b",
    r"\bsender_id\b",
    r"\bchat_id\b",
    r"\bassistant_text\b",
    r"\btoolresult\b",
    r"\btool: exec\b",
    r"\btool: process\b",
    r"\brate_limited\b",
    r"\bnext_request_at\b",
    r"\bretry_after_ms\b",
    r"\bvalidation_error\b",
    r"\bstdout=",
    r"\bstderr=",
    r"an async command the user already approved has comp(?:leted)?\b",
    r"exact completion details:",
    r"do not run the command again\.",
    r"\bsubagent\s+\w+\s+finished\b",
]


def _looks_like_correction(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    for marker in CORRECTION_MARKERS:
        if marker in text:
            return True
    # 单字“别”只在明确祈使场景下才算纠错，避免把“区别”误判成 correction
    if re.search(r"(?:^|[，。！？\s])别(?:这样|再|老|总|一下|默认|直接|急|乱|瞎|动|搞)?", text):
        return True
    return False


def is_noisy_tool_result(text: str) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in _TOOL_RESULT_NOISE_PATTERNS)


def make_event(
    *,
    event_type: str,
    source: str,
    summary: str,
    content: str = "",
    importance: float = 0.3,
    tags=None,
    metadata=None,
) -> Event:
    return Event(
        event_id=str(uuid.uuid4()),
        ts=datetime.now().astimezone().isoformat(),
        type=event_type,
        source=source,
        summary=(summary or "").strip()[:200],
        content=(content or "").strip(),
        importance=importance,
        tags=tags or [],
        metadata=metadata or {},
    )


def classify_user_text(text: str) -> str:
    text = (text or "").strip()
    if _looks_like_correction(text):
        return "correction"
    if any(m in text for m in POSITIVE_FEEDBACK_MARKERS) and len(text) <= 20:
        return "feedback"
    if any(m.lower() in text.lower() for m in ERROR_MARKERS):
        return "error"
    return "conversation"


def from_message(role: str, text: str, channel: str = "unknown", user_id: str | None = None) -> Event:
    role = (role or "").lower()
    event_type = classify_user_text(text) if role == "user" else "conversation"
    importance = 0.9 if event_type == "correction" else 0.55 if event_type in ("feedback", "error") else 0.35
    return make_event(
        event_type=event_type,
        source=f"{channel}:{role}",
        summary=(text or "")[:80],
        content=text,
        importance=importance,
        tags=[f"role:{role}", f"event:{event_type}"],
        metadata={"user_id": user_id, "role": role},
    )


def from_tool_result(tool_name: str, ok: bool, content: str = "") -> Event:
    noisy = is_noisy_tool_result(content)
    return make_event(
        event_type="success" if ok else "error",
        source=f"tool:{tool_name}",
        summary=f"{tool_name} {'ok' if ok else 'failed'}",
        content=content,
        importance=0.2 if noisy else (0.65 if not ok else 0.4),
        tags=[tool_name, "tool_result", *( ["noisy_tool_result"] if noisy else [] )],
        metadata={"noisy": noisy},
    )
