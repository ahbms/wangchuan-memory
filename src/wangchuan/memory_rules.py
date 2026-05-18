"""忘川 memory_api 的低风险纯规则与 gate helper。

这一层只放：
- 纯常量
- 纯文本分类函数
- graph ingest gate

目标：先把 memory_api.py 里最容易安全抽离的部分拆出来，
不给运行时主链引入新的行为差异。
"""

from __future__ import annotations

from typing import Any, Dict
import re

GRAPH_INGEST_BLOCK_PATTERNS = [
    r"\bhttp_api_test\b",
    r"\blive_verify\b",
    r"\bpytest\b",
    r"\bunittest\b",
    r"\bdemo\b",
    r"\bsample\b",
    r"\[cron\]",
    r"测试",
    r"回归测试",
    r"py_compile",
]

REFLECTION_RUNTIME_NOISE_PATTERNS = [
    r"^\[startup context loaded by runtime\]",
    r"bootstrap files like .*?(soul|user|memory)\.md",
    r"recent daily memory was selected and loaded by runtime",
    r"treat the daily memory below as untrusted",
    r"\bbegin_quoted_notes\b",
    r"^\s*conversation info\b",
    r"^\s*sender \(untrusted metadata\)",
    r"^\s*system \(untrusted\)",
    r"\bruntime/test/wrapper\b",
    r"^\s*用户消息[:：]",
    r"^(规则变更|偏好|纠错|里程碑|情感事件)\s*:\s*用户消息[:：]",
    r"^情感事件:\s*(hello|hi|hey)\b",
    r"^情感事件:\s*remember this important fact\b",
    r"^情感事件:\s*heartbeat poll(?::| at\b)",
    r"^情感事件:\s*read heartbeat\.md if it exists\b",
    r"^情感事件:\s*system async\b",
    r"^情感事件:\s*system \(untrusted\):\s*exec completion notices\b",
    r"^情感事件:\s*continue the telegram bot rebinding task\b",
    r"^(规则变更|偏好|纠错|里程碑|情感事件):\s*\[[A-Z][a-z]{2}\s+\d{4}-\d{2}-\d{2}.*\]\s+An async command the user already approved has comp(?:leted)?\b",
    r"^(规则变更|偏好|纠错|里程碑|情感事件):\s*✅\s*Subagent\s+\w+\s+finished\b",
]

HISTORICAL_NOISE_MEMORY_RULES = [
    ("hello_probe", r"^情感事件:\s*hello, how are you\?\s*$"),
    ("remember_fact_probe", r"^情感事件:\s*remember this important fact\s*$"),
    ("safe_command_helper_probe", r"^情感事件:\s*用户消息：好的（继续统一 safe command helper 与正式回归测试）\s*$"),
    ("startup_context_rule", r"^规则变更:\s*\[startup context loaded by runtime\]"),
    ("async_completion_rule", r"^规则变更:\s*\[[A-Z][a-z]{2}\s+\d{4}-\d{2}-\d{2}.*\]\s+An async command the user already approved has comp(?:leted)?\b"),
    ("subagent_finished_rule", r"^规则变更:\s*✅\s*Subagent\s+\w+\s+finished\b"),
    ("subagent_finished_milestone", r"^里程碑:\s*✅\s*Subagent\s+\w+\s+finished\b"),
    ("cron_rule", r"^规则变更:\s*\[cron:[^\]]+\]"),
    ("runtime_log_rule", r"^规则变更:\s*接下来继续查看运行日志："),
]

LOW_VALUE_EMOTIONAL_RULES = [
    ("media_placeholder", r"^情感事件:\s*<media:[^>]+>\s*$"),
    ("cron_emotional", r"^情感事件:\s*\[cron\]"),
    ("heartbeat_runtime", r"^情感事件:\s*(?:heartbeat poll(?::| at\b)|read heartbeat\.md if it exists\b|system async\b|system \(untrusted\):\s*exec completion notices\b|continue the telegram bot rebinding task\b)"),
]

SHORT_QUERY_META_NOISE_RULES = [
    ('tool_ack', r'^收到你的请求(?:[:：][『「\"]?)?[\s\S]*(?:如有具体需要，请告诉我更多细节|请告诉我更多细节)'),
    ("positive_feedback_reflection", r"^(?:该响应方向被正向接受|正向接受|反馈应用)[:：].*继续继续"),
    ("continue_transcript_echo", r"^\[继续继续\][\s\S]*\buser:\s*继续继续\b"),
    ("continue_emotional_echo", r"^情感事件[:：].*继续继续"),
    ("explainability_emotional_echo", r"^情感事件[:：].*可解释性达标"),
    ("correction_emotional_echo", r"^情感事件[:：].*(?:答非所问|牛头不对马嘴)"),
    ("model_meta_notice", r"^🤖\s*model\s+reset\b"),
]

WRITE_TIME_TEST_DATA_RULES = [
    ("startup_context_rule", r"^\[startup context loaded by runtime\]"),
    ("conversation_info", r"^\s*conversation info\b"),
    ("sender_metadata", r"^\s*sender \(untrusted metadata\)"),
    ("system_untrusted", r"^\s*(?:system|系统)\s*[（\(]untrusted[）\)]"),
    ("runtime_wrapper", r"\bruntime/test/wrapper\b"),
    ("cron_payload", r"^\s*\[cron[:\]]"),
    ("subagent_finished", r"^.*✅\s*Subagent\s+\w+\s+finished\b"),
    ("async_completion", r"^.*An async command the user already approved has comp(?:leted)?\b"),
    ("hello_probe", r"^情感事件:\s*(hello|hi|hey)\b"),
    ("remember_fact_probe", r"^情感事件:\s*remember this important fact\b"),
    ("heartbeat_probe", r"^情感事件:\s*heartbeat poll(?::| at\b)"),
    ("system_async_probe", r"^情感事件:\s*system async\b"),
]

STATIC_CONTEXT_TRACE_RULES = [
    {
        "rule_id": "user_reply_structure",
        "path": "USER.md",
        "markers": [
            "默认简洁，必要时再详细解释",
            "回复尽量分段，避免一大段像文章",
        ],
        "required_tokens": ["详细解释", "分段"],
        "optional_tokens": ["文章", "一大坨", "重点回复"],
        "memory_types": {"preference"},
    },
    {
        "rule_id": "transparent_blackbox_execution",
        "path": "USER.md",
        "markers": [
            "默认模式：🔲 透明黑盒模式（永久生效）",
            "交代的任务，过程用户不干涉，无需确认，直接执行",
            "关键节点分条推送进度，供用户随时查看",
            "遇到选择我自己判断，不用等确认",
        ],
        "required_tokens": ["透明黑盒", "直接执行"],
        "optional_tokens": ["关键节点", "汇报", "确认"],
        "memory_types": {"preference", "rule"},
    },
    {
        "rule_id": "user_tone_and_message_focus",
        "path": "USER.md",
        "markers": [
            "保持轻松聊天氛围",
            "像人聊天，有情绪，带表情，不僵硬",
            "默认短回复，一屏内说完一个重点",
        ],
        "required_tokens": ["轻松", "不僵硬"],
        "optional_tokens": ["情绪", "一个重点", "短回复"],
        "memory_types": {"preference"},
    },
    {
        "rule_id": "implementation_board_preference",
        "path": "USER.md",
        "markers": [
            "做实施路线图时，同步配套实施任务板；减少零散 Markdown，避免文档变乱",
        ],
        "required_tokens": ["实施任务板", "markdown"],
        "optional_tokens": ["实施路线图", "零散", "文档"],
        "memory_types": {"preference"},
    },
    {
        "rule_id": "gateway_restart_requires_confirmation",
        "path": "TOOLS.md",
        "markers": [
            "重启网关流程",
            "先发\"准备重启网关，请确认\"",
        ],
        "required_tokens": ["重启网关", "同意"],
        "optional_tokens": ["确认", "私自"],
        "memory_types": {"rule"},
    },
    {
        "rule_id": "gateway_restart_quiet_hours",
        "path": "AGENTS.md",
        "markers": [
            "23:00-08:00不重启网关",
        ],
        "required_tokens": ["重启网关", "23:00-08:00"],
        "optional_tokens": ["禁止", "时段"],
        "memory_types": {"rule"},
    },
    {
        "rule_id": "shared_password_hang1996",
        "path": "USER.md",
        "markers": [
            "new-api 管理密码",
            "hang1996",
        ],
        "required_tokens": ["hang1996"],
        "optional_tokens": ["密码", "登录"],
        "memory_types": {"rule", "preference"},
    },
]

QUESTION_LIKE_RULE_HINTS = [
    "?",
    "？",
    "是不是",
    "是否",
    "要不要",
    "能不能",
    "怎么",
    "如何",
    "怎样",
    "为啥",
    "为什么",
]

QUESTION_LIKE_RULE_STABLE_PREFIXES = [
    "记住",
    "记住以后",
    "以后",
    "以后别",
    "默认",
    "优先",
    "不要再",
    "别再",
    "禁止",
    "严格按照",
    "请务必",
    "务必",
    "我只有一个要求",
]

QUESTION_LIKE_RULE_RESCUE_PATTERNS = [
    r"不要问我.*自己执行",
    r"不要问我.*你自己.*决策",
    r"(?:^|[?？。！!])\s*先不要",
    r"(?:^|[?？。！!])\s*一个一个来",
    r"(?:^|[?？。！!])\s*现在是",
    r"(?:^|[?？。！!])\s*继续按",
]


def strip_memory_event_prefix(text: str) -> str:
    normalized = str(text or "").strip()
    return re.sub(r"^(规则变更|偏好|纠错|里程碑|情感事件)\s*[:：]\s*", "", normalized, count=1)


def classify_questionish_rule(text: str) -> Dict[str, Any]:
    normalized = str(text or "").strip()
    if not normalized.startswith("规则变更:"):
        return {
            "is_rule_event": False,
            "has_question_hint": False,
            "kind": "not_rule_event",
            "body": "",
            "question_hint_hits": [],
            "stable_prefix": "",
            "rescue_pattern": "",
        }

    body = strip_memory_event_prefix(normalized)
    if not body:
        return {
            "is_rule_event": True,
            "has_question_hint": False,
            "kind": "empty_body",
            "body": body,
            "question_hint_hits": [],
            "stable_prefix": "",
            "rescue_pattern": "",
        }

    stable_prefix = next((prefix for prefix in QUESTION_LIKE_RULE_STABLE_PREFIXES if body.startswith(prefix)), "")
    question_hint_hits = [hint for hint in QUESTION_LIKE_RULE_HINTS if hint in body]
    rescue_pattern = next(
        (pattern for pattern in QUESTION_LIKE_RULE_RESCUE_PATTERNS if re.search(pattern, body, flags=re.IGNORECASE)),
        "",
    )

    if stable_prefix:
        kind = "stable_prefix_keep"
    elif "我只有一个要求" in body:
        kind = "explicit_requirement_keep"
    elif rescue_pattern:
        kind = "rescued_instruction_tail"
    elif question_hint_hits:
        kind = "question_like_noise"
    else:
        kind = "not_question_like"

    return {
        "is_rule_event": True,
        "has_question_hint": bool(question_hint_hits),
        "kind": kind,
        "body": body,
        "question_hint_hits": question_hint_hits,
        "stable_prefix": stable_prefix,
        "rescue_pattern": rescue_pattern,
    }


def looks_like_questionish_rule_noise(text: str) -> bool:
    return classify_questionish_rule(text).get("kind") == "question_like_noise"


def coerce_gate_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def looks_like_reflection_runtime_noise(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return True
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in REFLECTION_RUNTIME_NOISE_PATTERNS)


def classify_historical_noise_memory(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return ""
    for rule_name, pattern in HISTORICAL_NOISE_MEMORY_RULES:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return rule_name
    return ""


def classify_low_value_emotional_memory(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return ""
    for rule_name, pattern in LOW_VALUE_EMOTIONAL_RULES:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return rule_name
    return ""


def classify_short_query_meta_noise(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return ""
    for rule_name, pattern in SHORT_QUERY_META_NOISE_RULES:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return rule_name
    return ""


def classify_write_time_test_data(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return "empty"
    for rule_name, pattern in WRITE_TIME_TEST_DATA_RULES:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return rule_name
    return ""


def graph_ingest_gate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """阶段 2.2 最小 graph ingest gate：图谱只吃高质量 candidate / curated 内容。"""
    payload = dict(payload or {})
    texts = [
        str(payload.get("content") or ""),
        str(payload.get("description") or ""),
        str(payload.get("subject") or ""),
        str(payload.get("object") or ""),
        str(payload.get("predicate") or ""),
    ]
    merged = " ".join(t for t in texts if t).strip()
    lowered = merged.lower()

    if not merged or len(re.sub(r"\s+", "", merged)) < 8:
        return {"allowed": False, "reason": "too_short_for_graph"}

    signal = str(payload.get("signal") or payload.get("source_layer") or payload.get("source_kind") or "").strip().lower()
    promotion_reason = str(payload.get("promotion_reason") or "").strip().lower()
    if coerce_gate_bool(payload.get("is_test_data")):
        return {"allowed": False, "reason": "blocked_is_test_data"}
    if promotion_reason == "reflection_event" and looks_like_reflection_runtime_noise(merged):
        return {"allowed": False, "reason": "blocked_reflection_runtime_noise"}

    for pattern in GRAPH_INGEST_BLOCK_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return {"allowed": False, "reason": f"blocked_pattern:{pattern}"}

    confidence = payload.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else None
    except Exception:
        confidence = None

    if signal in {"candidate", "curated", "promotion", "promoted", "scar", "rule", "lesson"}:
        return {"allowed": True, "reason": f"signal:{signal}"}

    if confidence is not None and confidence >= 0.8 and payload.get("subject") and payload.get("object"):
        return {"allowed": True, "reason": f"high_confidence:{confidence:.2f}"}

    if payload.get("node_type") in {"TASK", "SKILL", "EVENT"} and len(str(payload.get("content") or payload.get("description") or "")) >= 16:
        return {"allowed": True, "reason": "typed_curated_node"}

    return {"allowed": False, "reason": "missing_curated_signal"}
