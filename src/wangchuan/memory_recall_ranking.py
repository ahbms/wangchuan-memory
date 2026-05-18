from __future__ import annotations

"""WangChuan recall ranking / text-match helpers.

这一层承接 memory_api 中与 recall 排序、文本匹配、解释构造相关的低风险纯逻辑：
- source / trace / type / layer priority
- duplicate keeper sort key
- recall rank score / explain
- recall text normalization / matching
- recall result sort key

约束：
- 不改写 recall 主链查询与数据读取协议
- 仍由调用方（Memory）提供 parse/coerce 与类级常量
- 优先保持与 memory_api 现有评分口径一致
"""

from typing import Any, Dict, List
import re
import sqlite3

try:
    from wangchuan.memory_rules import (
        classify_short_query_meta_noise,
        looks_like_questionish_rule_noise,
    )
except ImportError:
    from wangchuan.memory_rules import classify_short_query_meta_noise, looks_like_questionish_rule_noise

try:
    from wangchuan.memory_recall_query import query_looks_like_user_profile
except ImportError:
    from wangchuan.memory_recall_query import query_looks_like_user_profile


def source_session_priority(session_id: str) -> int:
    normalized = str(session_id or "").strip().lower()
    if normalized == "default":
        return 3
    if normalized and normalized != "cli":
        return 2
    if not normalized:
        return 1
    return 0


def trace_completeness(source_anchor: Any, source_session: Any, turn_signature: Any) -> int:
    return sum(1 for value in (source_anchor, source_session, turn_signature) if str(value or "").strip())


def memory_type_priority(memory_type: str) -> int:
    normalized = str(memory_type or "").strip().lower()
    if normalized in {"rule", "correction"}:
        return 4
    if normalized in {"lesson", "decision", "preference"}:
        return 3
    if normalized in {"memory", "conversation"}:
        return 2
    if normalized in {"emotional", "emotion"}:
        return 0
    return 1


def source_layer_priority(source_layer: str) -> int:
    normalized = str(source_layer or "").strip().lower()
    if normalized == "scar":
        return 3
    if normalized == "raw":
        return 2
    if normalized:
        return 1
    return 0


def subject_domain_priority(subject_domain: str) -> int:
    normalized = str(subject_domain or "").strip().lower()
    if normalized == "rule":
        return 4
    if normalized in {"code", "ops"}:
        return 3
    if normalized == "user":
        return 2
    if normalized == "general":
        return 1
    return 0


def _safe_row_value(row: sqlite3.Row, key: str, default: Any = "") -> Any:
    try:
        return row[key]
    except Exception:
        return default


def duplicate_memory_sort_key(memory_obj: Any, row: sqlite3.Row) -> tuple:
    source_session = str(_safe_row_value(row, "source_session") or "").strip()
    session_priority = source_session_priority(source_session)
    trace_score = trace_completeness(
        _safe_row_value(row, "source_anchor"),
        _safe_row_value(row, "source_session"),
        _safe_row_value(row, "turn_signature"),
    )
    memory_type_rank = memory_type_priority(str(_safe_row_value(row, "memory_type") or ""))
    source_layer_rank = source_layer_priority(str(_safe_row_value(row, "source_layer") or ""))
    subject_domain_rank = subject_domain_priority(str(_safe_row_value(row, "subject_domain") or ""))
    try:
        quality_score = float(_safe_row_value(row, "quality_score") or 0.0)
    except Exception:
        quality_score = 0.0
    try:
        hotness_score = float(_safe_row_value(row, "hotness_score") or 0.0)
    except Exception:
        hotness_score = 0.0

    confirmed_dt = memory_obj._parse_iso_dt(_safe_row_value(row, "last_confirmed_at"))
    created_dt = memory_obj._parse_iso_dt(_safe_row_value(row, "created_at"))
    confirmed_rank = confirmed_dt.isoformat() if confirmed_dt else ""
    created_rank = created_dt.isoformat() if created_dt else ""

    return (
        memory_type_rank,
        subject_domain_rank,
        session_priority,
        trace_score,
        source_layer_rank,
        quality_score,
        hotness_score,
        confirmed_rank,
        created_rank,
        int(_safe_row_value(row, "id", 0) or 0),
    )


def _canonical_bonus(memory_obj: Any, item: Dict[str, Any], memory_type: str, source_layer: str) -> float:
    canonical_bonus = 0.0
    if (
        memory_obj._coerce_bool(item.get("user_explicit"))
        and memory_type in {"preference", "rule"}
        and source_layer == "scar"
        and str(item.get("promotion_state") or "").strip().lower() in {"promoted", "accepted", "recalled"}
    ):
        canonical_bonus = 0.08
    if memory_type == "fact" and source_layer == "scar" and memory_obj._coerce_bool(item.get("user_explicit")):
        canonical_bonus = max(canonical_bonus, 0.05)
    return canonical_bonus


def _schema_bonus_parts(item: Dict[str, Any], memory_type: str, source_layer: str) -> tuple[float, float, float, float]:
    session_bonus = {0: -0.05, 1: 0.0, 2: 0.06, 3: 0.1}.get(
        source_session_priority(item.get("source_session") or ""),
        0.0,
    )
    trace_bonus = trace_completeness(
        item.get("source_anchor"),
        item.get("source_session"),
        item.get("turn_signature"),
    ) * 0.04
    layer_bonus = {0: 0.0, 1: 0.01, 2: 0.03, 3: 0.06}.get(
        source_layer_priority(source_layer),
        0.0,
    )
    type_bonus = {0: -0.04, 1: 0.0, 2: 0.01, 3: 0.03, 4: 0.05}.get(
        memory_type_priority(memory_type),
        0.0,
    )
    return session_bonus, trace_bonus, layer_bonus, type_bonus


def _short_query_meta_noise_penalty(query_text: str, subject_domain: str, content_text: str, memory_type: str = "") -> tuple[float, str]:
    compact_query = compact_recall_match_text(query_text)
    if not compact_query or len(compact_query) > 12:
        return 0.0, ""
    noise_kind = classify_short_query_meta_noise(content_text)
    if not noise_kind:
        return 0.0, ""
    penalty = {
        "tool_ack": 2.0,
        "positive_feedback_reflection": 1.45,
        "continue_transcript_echo": 2.15,
        "continue_emotional_echo": 1.7,
        "explainability_emotional_echo": 1.7,
        "correction_emotional_echo": 1.85,
        "model_meta_notice": 1.2,
    }.get(noise_kind, 0.0)
    if noise_kind == "correction_emotional_echo" and str(memory_type or "").strip().lower() == "emotional":
        penalty = 0.0
    if noise_kind in {"continue_emotional_echo", "explainability_emotional_echo"} and str(subject_domain or "").strip().lower() == "rule":
        penalty = max(0.0, penalty - 0.7)
    return penalty, noise_kind


def recall_rank_score(memory_obj: Any, item: Dict[str, Any]) -> float:
    quality_score = memory_obj._coerce_float(item.get("quality_score"), memory_obj._coerce_float(item.get("score"), 0.0))
    importance = memory_obj._coerce_float(item.get("importance"), 0.0)
    hotness_score = memory_obj._coerce_float(item.get("hotness_score"), 0.0)
    lifecycle = str(item.get("lifecycle") or "").strip().lower()
    lifecycle_penalty = {"aging": 0.08, "archived": 0.3}.get(lifecycle, 0.0)
    text_support = max(0.0, memory_obj._coerce_float(item.get("recall_text_match_score"), 0.0))
    support_scale = 0.35 + min(0.65, text_support * 0.65)

    memory_type = str(item.get("memory_type") or "").strip().lower()
    content_text = str(item.get("content") or "").strip()
    source_layer = str(item.get("source_layer") or "").strip().lower()
    canonical_bonus = _canonical_bonus(memory_obj, item, memory_type, source_layer)
    session_bonus, trace_bonus, layer_bonus, type_bonus = _schema_bonus_parts(item, memory_type, source_layer)

    weak_match_penalty = 0.0
    if lifecycle == "aging" and text_support < 0.35:
        weak_match_penalty += 0.08
    if memory_type == "lesson" and text_support < 0.6:
        weak_match_penalty += 0.22
    if memory_type == "conversation" and text_support < 0.35:
        weak_match_penalty += 0.22
    if source_layer == "raw" and text_support < 0.45:
        weak_match_penalty += 0.18
    if content_text.startswith("情感事件:") and text_support < 0.45:
        weak_match_penalty += 0.35
    if memory_type == "rule" and source_layer == "scar" and looks_like_questionish_rule_noise(content_text):
        weak_match_penalty += 0.42

    lowered_query = str(item.get("_recall_query") or "").strip().lower()
    query_looks_like_profile = query_looks_like_user_profile(memory_obj, lowered_query)
    subject_domain = str(item.get("subject_domain") or "").strip().lower()
    if query_looks_like_profile:
        if memory_type == "conversation":
            weak_match_penalty += 0.32
        if memory_type == "lesson" and text_support < 0.35:
            weak_match_penalty += 0.24
            if subject_domain in {"code", "general"}:
                weak_match_penalty += 0.08
        if subject_domain == "general" and text_support < 0.75:
            weak_match_penalty += 0.18

    short_query_meta_noise_penalty, _ = _short_query_meta_noise_penalty(
        lowered_query,
        subject_domain,
        content_text,
        memory_type,
    )
    weak_match_penalty += short_query_meta_noise_penalty

    blended_bonus = (importance * 0.25 + hotness_score * 0.15 + session_bonus + trace_bonus + layer_bonus + type_bonus + canonical_bonus) * support_scale

    return round(
        quality_score
        + blended_bonus
        - lifecycle_penalty
        - weak_match_penalty,
        6,
    )


def build_recall_explain(memory_obj: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    text_match = round(memory_obj._coerce_float(item.get("recall_text_match_score"), 0.0), 6)
    lifecycle = str(item.get("lifecycle") or "").strip().lower()
    lifecycle_penalty = round({"aging": 0.08, "archived": 0.3}.get(lifecycle, 0.0), 6)

    memory_type = str(item.get("memory_type") or "").strip().lower()
    source_layer = str(item.get("source_layer") or "").strip().lower()
    content_text = str(item.get("content") or "").strip()
    canonical_bonus = _canonical_bonus(memory_obj, item, memory_type, source_layer)
    session_bonus, trace_bonus, layer_bonus, type_bonus = _schema_bonus_parts(item, memory_type, source_layer)
    schema_bonus = round(session_bonus + trace_bonus + layer_bonus + type_bonus, 6)

    raw_conversation_penalty = 0.0
    if memory_type == "conversation" and text_match < 0.35:
        raw_conversation_penalty += 0.22
    if source_layer == "raw" and text_match < 0.45:
        raw_conversation_penalty += 0.18
    if content_text.startswith("情感事件:") and text_match < 0.45:
        raw_conversation_penalty += 0.35
    question_noise_penalty = 0.0
    if memory_type == "rule" and source_layer == "scar" and looks_like_questionish_rule_noise(content_text):
        question_noise_penalty += 0.42

    lowered_query = str(item.get("_recall_query") or "").strip().lower()
    query_looks_like_profile = query_looks_like_user_profile(memory_obj, lowered_query)
    subject_domain = str(item.get("subject_domain") or "").strip().lower()
    profile_noise_penalty = 0.0
    if query_looks_like_profile:
        if memory_type == "conversation":
            profile_noise_penalty += 0.32
        if memory_type == "lesson" and text_match < 0.35:
            profile_noise_penalty += 0.24
            if subject_domain in {"code", "general"}:
                profile_noise_penalty += 0.08
        if subject_domain == "general" and text_match < 0.75:
            profile_noise_penalty += 0.18

    short_query_noise_penalty = 0.0
    short_query_noise_kind = ""
    short_query_noise_penalty, short_query_noise_kind = _short_query_meta_noise_penalty(
        lowered_query,
        subject_domain,
        content_text,
        memory_type,
    )

    raw_conversation_penalty = round(raw_conversation_penalty, 6)
    profile_noise_penalty = round(profile_noise_penalty, 6)
    question_noise_penalty = round(question_noise_penalty, 6)
    short_query_noise_penalty = round(short_query_noise_penalty, 6)

    final_rank_score = round(memory_obj._coerce_float(item.get("recall_rank_score"), recall_rank_score(memory_obj, item)), 6)

    summary_parts = []
    if text_match >= 1.2:
        summary_parts.append("文本强命中")
    elif text_match > 0:
        summary_parts.append("文本弱命中")
    if canonical_bonus > 0:
        summary_parts.append("canonical 加成")
    if schema_bonus > 0.08:
        summary_parts.append("schema 加成")
    if lifecycle_penalty > 0:
        summary_parts.append("生命周期惩罚")
    if raw_conversation_penalty > 0:
        summary_parts.append("raw/conversation 惩罚")
    if profile_noise_penalty > 0:
        summary_parts.append("user-profile 噪音惩罚")
    if question_noise_penalty > 0:
        summary_parts.append("问句型规则噪音惩罚")
    if short_query_noise_penalty > 0:
        summary_parts.append("短 query 元噪音惩罚")
    if not summary_parts:
        summary_parts.append("基础质量分主导")

    factor_hits = {
        "text_match": text_match > 0,
        "schema_bonus": schema_bonus > 0,
        "lifecycle_penalty": lifecycle_penalty > 0,
        "raw_conversation_penalty": raw_conversation_penalty > 0,
        "profile_noise_penalty": profile_noise_penalty > 0,
        "question_noise_penalty": question_noise_penalty > 0,
        "short_query_noise_penalty": short_query_noise_penalty > 0,
        "canonical_bonus": canonical_bonus > 0,
    }
    matched_factors = [name for name, hit in factor_hits.items() if hit]

    return {
        "explain_version": "recall_explain_v1",
        "final_rank_score": final_rank_score,
        "text_match": text_match,
        "schema_bonus": schema_bonus,
        "lifecycle_penalty": lifecycle_penalty,
        "raw_conversation_penalty": raw_conversation_penalty,
        "profile_noise_penalty": profile_noise_penalty,
        "question_noise_penalty": question_noise_penalty,
        "short_query_noise_penalty": short_query_noise_penalty,
        "short_query_noise_kind": short_query_noise_kind,
        "canonical_bonus": round(canonical_bonus, 6),
        "factor_hits": factor_hits,
        "matched_factors": matched_factors,
        "summary": " + ".join(summary_parts),
    }


def normalize_recall_match_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).lower()


def compact_recall_match_text(text: Any) -> str:
    normalized = normalize_recall_match_text(text)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)


def recall_token_weight(memory_obj: Any, token: str) -> float:
    normalized = compact_recall_match_text(token)
    if len(normalized) < 2:
        return 0.0
    if normalized in memory_obj.RECALL_LOW_SIGNAL_TOKENS:
        return 0.12
    if len(normalized) >= 6:
        return 1.0
    if len(normalized) >= 4:
        return 0.85
    return 0.6


def char_bigram_overlap_score(source: str, target: str) -> float:
    source_text = compact_recall_match_text(source)
    target_text = compact_recall_match_text(target)
    if not source_text or not target_text:
        return 0.0

    def grams(text: str):
        if len(text) < 2:
            return {text}
        return {text[i:i + 2] for i in range(len(text) - 1)}

    source_grams = grams(source_text)
    target_grams = grams(target_text)
    if not source_grams or not target_grams:
        return 0.0
    overlap = len(source_grams & target_grams)
    if overlap == 0:
        return 0.0
    return round((2 * overlap) / (len(source_grams) + len(target_grams)), 6)


def honorific_alias_bonus(source: str, target: str) -> float:
    source_text = compact_recall_match_text(source)
    target_text = compact_recall_match_text(target)
    if len(source_text) != 2 or len(target_text) < 2:
        return 0.0
    if source_text[1] not in {"哥", "姐", "总", "叔", "姨"}:
        return 0.0
    surname = source_text[0]
    if re.search(rf"{re.escape(surname)}[\u4e00-\u9fff]", target_text):
        return 0.32
    return 0.0


def recall_text_match_score(memory_obj: Any, content: Any, normalized_query: str, keyword_tokens: List[str]) -> float:
    content_text = normalize_recall_match_text(content)
    query_text = normalize_recall_match_text(normalized_query)
    if not content_text or not query_text:
        return 0.0

    score = 0.0
    if query_text and query_text in content_text:
        score += 1.4

    compact_content = compact_recall_match_text(content_text)
    compact_query = compact_recall_match_text(query_text)
    if compact_query and compact_query in compact_content:
        score += 0.9

    bigram_overlap = char_bigram_overlap_score(compact_query, compact_content)
    if bigram_overlap > 0:
        score += min(1.1, bigram_overlap * 1.35)

    alias_bonus = honorific_alias_bonus(compact_query, compact_content)
    if alias_bonus > 0:
        score += alias_bonus

    weighted_tokens = []
    seen = set()
    for token in keyword_tokens or []:
        normalized_token = normalize_recall_match_text(token)
        compact_token = compact_recall_match_text(token)
        if len(compact_token) < 2 or compact_token in seen:
            continue
        seen.add(compact_token)
        weight = recall_token_weight(memory_obj, token)
        if weight <= 0:
            continue
        weighted_tokens.append((normalized_token, compact_token, weight))

    if weighted_tokens:
        total_weight = sum(weight for _, _, weight in weighted_tokens)
        matched_weight = 0.0
        strong_match = False
        for normalized_token, compact_token, weight in weighted_tokens:
            matched = normalized_token in content_text or compact_token in compact_content
            if matched:
                matched_weight += weight
                if weight >= 0.6:
                    strong_match = True
        if matched_weight > 0 and total_weight > 0:
            coverage = matched_weight / total_weight
            score += coverage * 0.95
            score += min(0.2, matched_weight * 0.08)
            if strong_match and coverage >= 0.85:
                score += 0.18

    return round(score, 6)


def recall_result_sort_key(memory_obj: Any, item: Dict[str, Any]) -> tuple:
    confirmed_dt = memory_obj._parse_iso_dt(item.get("last_confirmed_at"))
    created_dt = memory_obj._parse_iso_dt(item.get("created_at"))
    confirmed_rank = confirmed_dt.isoformat() if confirmed_dt else ""
    created_rank = created_dt.isoformat() if created_dt else ""
    final_rank = memory_obj._coerce_float(item.get("recall_rank_score"), recall_rank_score(memory_obj, item))
    text_rank = memory_obj._coerce_float(item.get("recall_text_match_score"), 0.0)
    return (
        final_rank,
        text_rank,
        source_session_priority(item.get("source_session") or ""),
        trace_completeness(item.get("source_anchor"), item.get("source_session"), item.get("turn_signature")),
        source_layer_priority(item.get("source_layer") or ""),
        memory_type_priority(item.get("memory_type") or ""),
        confirmed_rank,
        created_rank,
        int(item.get("memory_id") or 0),
    )
