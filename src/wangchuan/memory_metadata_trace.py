from __future__ import annotations

"""WangChuan memory metadata inference / trace enrich helpers.

这一层承接 memory_api 中 metadata 推断与 trace 补全相关的低风险纯逻辑：
- 从 content/source_layer 推断 memory metadata
- 为缺失 trace 的条目补全 source_anchor/source_session/turn_signature/provenance

约束：
- 不改写主 recall / remember 持久化协议
- 仍由调用方（Memory）提供 noise/tracing/hot-memory helper 能力
- 优先保持与 memory_api 现有推断口径一致
"""

from datetime import datetime
from typing import Any, Dict
import re


RULE_HINTS = [
    "规则变更",
    "发布规则",
    "铁律",
    "禁止",
    "必须",
    "不要",
    "先不要",
    "要求",
    "默认",
    "优先",
    "继续推进",
    "路线图",
    "任务板",
]
IMPLICIT_RULE_DOMAIN_HINTS = [
    "先回复收到",
    "再回复完成",
    "分段回复",
    "一步一步",
    "Telegram 主通道",
    "Telegram 当主线",
    "QQ 备用",
    "QQ 当备用",
    "Discord 暂不启用",
]
USER_PREFERENCE_HINTS = [
    "用户长期偏好",
    "用户偏好",
    "用户喜欢",
    "用户不吃",
    "用户讨厌",
    "用户习惯",
]
USER_FACT_HINTS = [
    "用户叫",
    "用户是",
    "用户在",
    "用户家",
    "项目代号",
    "账号是",
]
EXEC_RULE_HINTS = [
    "透明黑盒模式",
    "开始执行",
    "继续执行",
    "按你的建议",
    "按你说的",
    "依次执行",
    "顺序执行",
    "顺序全部执行",
    "每小时汇报一次",
    "今天不做别的",
    "先接悬赏任务",
    "可解释性达标",
    "把记忆搞好了",
]
CORRECTION_HINTS = [
    "答非所问",
    "牛头不对马嘴",
    "识别错了",
    "存不存在你好好检查",
    "说错了",
]
OPS_HINTS = [
    "网关",
    "gateway",
    "部署",
    "服务",
    "日志",
    "systemctl",
    "端口",
    "qqbot",
    "cloudflare",
    "restart",
    "重启",
    "openclaw admin",
    "sub2api",
    "配置文件",
    "oauth",
    "token",
    "bottoken",
    "apikey",
    "api key",
    "域名",
    "中转",
    "反代",
    "cf",
    "cpa",
    "cliproxyapi",
    "new-api",
    "运行中",
    "轮询",
    "节点",
    "上线",
    "vertex ai",
    "telegrambot",
]
CODE_HINTS = [
    "代码",
    "python",
    "测试",
    "模块",
    "函数",
    "架构",
    "bug",
    "回归",
    "schema",
    "trace",
    "recall",
    "write_gate",
    "pipeline",
    "脚本",
    "embedding",
    "向量",
    "语义搜索",
    "表名",
    "框架",
    "子智能体",
    "orchestrator",
    "分层",
    "记忆引擎",
    "github.com",
    "天心",
    "忘川",
    "百工",
    "利器",
    "明察",
    "力行",
    "璇玑",
    "日新",
    "天工开智",
    "九九归一",
    "多而合一",
    "生生不息",
    "环环相扣",
    "版本号",
    "开源仓库",
]
USER_HINTS = [
    "用户",
    "称呼",
    "喜欢",
    "偏好",
    "不吃",
    "账号",
    "项目代号",
    "家在",
]


USER_PREFERENCE_SHARD_PATTERN = re.compile(
    r"(?:爱喝|欢喝|厌喝|喜欢喝|喜欢).{0,8}冰美式|(?:偏好称呼|称呼为)",
    flags=re.IGNORECASE,
)
VERSION_RELEASE_PATTERN = re.compile(
    r"\bv\d+(?:\.\d+){0,2}\b.*(?:发布|版本号)|(?:发布|正式发布).{0,24}\bv\d+(?:\.\d+){0,2}\b",
    flags=re.IGNORECASE,
)


def _contains_any(text: str, hints: list[str]) -> bool:
    return any(hint in text for hint in hints)


def _looks_like_implicit_user_preference(text: str) -> bool:
    return bool(USER_PREFERENCE_SHARD_PATTERN.search(text or ""))


def _looks_like_code_release_note(text: str, lowered: str) -> bool:
    return bool(VERSION_RELEASE_PATTERN.search(text or "")) or (
        "正式发布" in (text or "") and bool(re.search(r"\bv\d+(?:\.\d+){0,2}\b", lowered or ""))
    )


def infer_memory_metadata(memory_obj: Any, content: str, source_layer: str) -> Dict[str, Any]:
    text = content or ""
    stripped_text = text.strip()
    lowered = text.lower()
    bracketed_transcript = stripped_text.startswith("[") and "user:" in lowered
    implicit_user_preference = _looks_like_implicit_user_preference(text)
    implicit_code_release = _looks_like_code_release_note(text, lowered)
    memory_type = "conversation" if source_layer == "raw" else "lesson"
    evidence_level = "raw" if source_layer == "raw" else "summarized"

    if bracketed_transcript:
        memory_type = "conversation"
    elif _contains_any(text, ["纠错:"]) or _contains_any(text, CORRECTION_HINTS):
        memory_type = "correction"
    elif _contains_any(text, RULE_HINTS) or _contains_any(text, EXEC_RULE_HINTS):
        memory_type = "rule"
    elif _contains_any(text, ["情感事件:"]):
        memory_type = "emotional"
    elif _contains_any(text, ["决定", "方案", "结论"]):
        memory_type = "decision"
    elif _contains_any(text, USER_PREFERENCE_HINTS) or implicit_user_preference:
        memory_type = "preference"
    elif _contains_any(text, USER_FACT_HINTS):
        memory_type = "fact"
    elif _contains_any(lowered, ["教训", "踩坑", "经验", "修复", "解决方法"]):
        memory_type = "lesson"

    subject_domain = "general"
    if bracketed_transcript or (source_layer == "raw" and memory_type == "conversation"):
        if any(token in lowered for token in ["cloudflare", "sub2api", "cpa", "cliproxyapi", "域名", "反代"]):
            subject_domain = "ops"
        elif any(token in lowered for token in ["可解释性", "trace", "recall", "写入链路", "记忆引擎", "修复忘川"]):
            subject_domain = "code"
        else:
            subject_domain = "general"
    elif memory_type in {"rule", "decision", "correction"} or _contains_any(text, RULE_HINTS) or _contains_any(text, EXEC_RULE_HINTS) or _contains_any(text, CORRECTION_HINTS):
        subject_domain = "rule"
    elif _contains_any(text, USER_PREFERENCE_HINTS) or _contains_any(text, USER_FACT_HINTS) or implicit_user_preference:
        subject_domain = "user"
    elif _contains_any(lowered, OPS_HINTS):
        subject_domain = "ops"
    elif _contains_any(lowered, CODE_HINTS) or implicit_code_release:
        subject_domain = "code"
    elif _contains_any(text, IMPLICIT_RULE_DOMAIN_HINTS) or (text.startswith("里程碑:") and "回复" in text):
        subject_domain = "rule"
    elif _contains_any(text, USER_HINTS):
        subject_domain = "user"

    source_anchor_match = re.search(r"来源:\s*([^\n]+)", text)
    source_anchor = source_anchor_match.group(1).strip() if source_anchor_match else ""
    turn_match = re.search(r"\bturn_signature=([^\s|]+)|\bturn[:=]\s*([^\s|]+)", text, flags=re.IGNORECASE)
    turn_signature = next((g.strip() for g in (turn_match.groups() if turn_match else []) if g), "")
    session_match = re.search(r"\bsource_session=([^\s|]+)", text, flags=re.IGNORECASE)
    source_session = session_match.group(1).strip() if session_match else ""
    user_explicit = (
        memory_type in {"preference", "rule", "lesson"}
        or any(token in text for token in ["用户", "记住", "长期", "约定"])
        or implicit_user_preference
    )
    is_test_data = any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in memory_obj.RECALL_NOISE_PATTERNS)
    promotion_match = re.search(r"\bpromotion_reason=([^\n|]+)", text, flags=re.IGNORECASE)
    promotion_reason = promotion_match.group(1).strip() if promotion_match else ""
    dedupe_match = re.search(r"\bdedupe_key=([^\n|]+)", text, flags=re.IGNORECASE)
    dedupe_key = dedupe_match.group(1).strip() if dedupe_match else (turn_signature or memory_obj._canonical_hot_memory_key(text)[:96])
    conflict_match = re.search(r"\bconflict_group=([^\n|]+)", text, flags=re.IGNORECASE)
    conflict_group = conflict_match.group(1).strip() if conflict_match else memory_type
    lifecycle_match = re.search(r"\blifecycle=([^\n|]+)", text, flags=re.IGNORECASE)
    lifecycle = lifecycle_match.group(1).strip() if lifecycle_match else ("raw" if source_layer == "raw" else "active")
    promotion_state_match = re.search(r"\bpromotion_state=([^\n|]+)", text, flags=re.IGNORECASE)
    promotion_state = promotion_state_match.group(1).strip() if promotion_state_match else ("promoted" if promotion_reason else ("captured" if source_layer == "raw" else "accepted"))
    recall_source_type_match = re.search(r"\brecall_source_type=([^\n|]+)", text, flags=re.IGNORECASE)
    recall_source_type = recall_source_type_match.group(1).strip() if recall_source_type_match else source_layer
    hot_memory_candidate = (not is_test_data) and source_layer not in {"raw", "candidate"} and memory_type in {"preference", "rule", "lesson", "decision", "memory"}
    quality_score = 0.92 if user_explicit else (0.84 if memory_type in {"rule", "lesson", "decision", "preference"} else 0.68)
    hotness_score = 0.82 if hot_memory_candidate else 0.35
    provenance = source_anchor or source_session or source_layer or "memory"
    last_confirmed_at = datetime.now().isoformat(timespec="seconds")

    return {
        "source_layer": source_layer,
        "source_anchor": source_anchor,
        "source_session": source_session,
        "turn_signature": turn_signature,
        "memory_type": memory_type,
        "subject_domain": subject_domain,
        "evidence_level": evidence_level,
        "user_explicit": user_explicit,
        "is_test_data": is_test_data,
        "promotion_reason": promotion_reason,
        "hot_memory_candidate": hot_memory_candidate,
        "provenance": provenance,
        "lifecycle": lifecycle,
        "dedupe_key": dedupe_key,
        "conflict_group": conflict_group,
        "quality_score": round(float(quality_score), 3),
        "promotion_state": promotion_state,
        "last_confirmed_at": last_confirmed_at,
        "hotness_score": round(float(hotness_score), 3),
        "recall_source_type": recall_source_type,
    }


def enrich_missing_trace_metadata(memory_obj: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    enriched = dict(item or {})
    if not enriched:
        return enriched

    if enriched.get("source_anchor") and enriched.get("turn_signature") and enriched.get("source_session"):
        return enriched

    traces = []
    if str(enriched.get("promotion_reason") or "").strip().lower() == "reflection_event":
        traces.append(memory_obj._lookup_message_trace(
            str(enriched.get("content") or ""),
            created_at=str(enriched.get("created_at") or enriched.get("last_confirmed_at") or ""),
        ))
    traces.append(memory_obj._lookup_static_context_trace(
        str(enriched.get("content") or ""),
        memory_type=str(enriched.get("memory_type") or ""),
    ))
    traces.append(memory_obj._lookup_related_memory_trace(
        str(enriched.get("content") or ""),
        memory_type=str(enriched.get("memory_type") or ""),
        exclude_memory_id=enriched.get("memory_id"),
    ))

    for trace in traces:
        if not trace:
            continue
        for key in ("source_anchor", "source_session", "turn_signature", "provenance"):
            if not enriched.get(key) and trace.get(key):
                enriched[key] = trace.get(key)
        if enriched.get("source_anchor") and enriched.get("source_session") and enriched.get("turn_signature"):
            break

    if enriched.get("source_anchor") and not enriched.get("provenance"):
        enriched["provenance"] = enriched["source_anchor"]
    return enriched
