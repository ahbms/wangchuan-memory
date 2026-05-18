from __future__ import annotations

"""WangChuan schema index / overview helpers.

这一层承接 memory_api 中围绕 memory_schema_index 的低风险读层逻辑：
- backfill 到 sqlite derived index
- index 状态摘要
- 结构化 overview 读层
- maintenance/update 链同步 sidecar / index

约束：
- 不改写 sidecar JSON 协议
- 仍由调用方（Memory）提供 ensure/read/infer/upsert helper
- 优先保持与 memory_api 现有统计口径一致
"""

from datetime import datetime
from typing import Any, Dict, List


RULE_HINTS = [
    "规则变更", "铁律", "禁止", "必须", "不要", "先不要", "要求", "默认", "优先", "继续推进", "路线图", "任务板"
]
USER_PREFERENCE_HINTS = [
    "用户长期偏好", "用户偏好", "用户喜欢", "用户不吃", "用户讨厌", "用户习惯"
]
USER_FACT_HINTS = [
    "用户叫", "用户是", "用户在", "用户家", "项目代号", "账号是"
]
EXEC_RULE_HINTS = [
    "透明黑盒模式", "开始执行", "继续执行", "按你的建议", "按你说的", "依次执行", "顺序执行", "顺序全部执行", "每小时汇报一次", "今天不做别的", "先接悬赏任务", "可解释性达标", "把记忆搞好了"
]
CORRECTION_HINTS = [
    "答非所问", "牛头不对马嘴", "识别错了", "存不存在你好好检查", "说错了"
]
OPS_HINTS = [
    "网关", "gateway", "部署", "服务", "日志", "systemctl", "端口", "qqbot", "cloudflare", "restart", "重启", "openclaw admin", "sub2api",
    "配置文件", "oauth", "token", "bottoken", "apikey", "api key", "域名", "中转", "反代", "cf", "cpa", "cliproxyapi", "new-api", "运行中", "轮询", "节点", "上线", "vertex ai", "telegrambot"
]
CODE_HINTS = [
    "代码", "python", "测试", "模块", "函数", "架构", "bug", "回归", "schema", "trace", "recall", "write_gate", "pipeline", "脚本",
    "embedding", "向量", "语义搜索", "表名", "框架", "子智能体", "orchestrator", "分层", "记忆引擎", "github.com", "天心", "忘川", "百工", "利器", "明察", "力行", "璇玑", "日新"
]
USER_HINTS = [
    "用户", "称呼", "喜欢", "偏好", "不吃", "账号", "项目代号", "家在"
]


LEGACY_TYPE_TO_MEMORY_TYPE = {
    "identity": "fact",
    "skill": "fact",
    "aversion": "preference",
    "preference": "preference",
    "habit": "preference",
    "instruction": "rule",
    "strategy": "decision",
    "technical": "lesson",
    "knowledge": "fact",
    "milestone": "lesson",
    "status": "lesson",
    "session": "conversation",
    "event": "lesson",
    "extracted": "fact",
    "user": "fact",
    "user_defined": "fact",
    "fact": "fact",
    "memory": "memory",
    "rule": "rule",
    "lesson": "lesson",
    "decision": "decision",
    "correction": "correction",
    "emotional": "emotional",
    "conversation": "conversation",
}

LEGACY_TYPE_TO_SUBJECT_DOMAIN = {
    "identity": "user",
    "skill": "user",
    "aversion": "user",
    "preference": "user",
    "habit": "user",
    "instruction": "rule",
    "strategy": "rule",
    "technical": "code",
    "knowledge": "code",
    "milestone": "general",
    "status": "general",
    "session": "general",
    "event": "general",
    "extracted": "general",
    "user": "user",
    "user_defined": "user",
    "fact": "general",
    "memory": "general",
    "rule": "rule",
    "lesson": "general",
    "decision": "rule",
    "correction": "general",
    "emotional": "general",
    "conversation": "general",
}


def _normalize_legacy_type(value: Any) -> str:
    return str(value or "").strip().lower()


def _contains_any(text: str, hints) -> bool:
    return any(h in text for h in hints)


def _refine_memory_type(content: str, current: str, source_layer: str) -> str:
    text = str(content or "")
    current = str(current or "").strip().lower()
    source_layer = str(source_layer or "").strip().lower()

    if source_layer == "raw":
        return current or "conversation"
    if "纠错:" in text or _contains_any(text, CORRECTION_HINTS):
        return "correction"
    if _contains_any(text, RULE_HINTS) or _contains_any(text, EXEC_RULE_HINTS):
        return "rule"
    if _contains_any(text, USER_PREFERENCE_HINTS):
        return "preference"
    if _contains_any(text, USER_FACT_HINTS):
        return "fact"
    if "情感事件:" in text and current in {"", "lesson", "memory", "conversation", "emotional"}:
        return "emotional"
    if current in {"correction", "emotional", "rule", "decision", "preference", "fact"}:
        return current
    if _contains_any(text, ["决定", "方案", "结论"]):
        return "decision"
    if _contains_any(text, ["用户喜欢", "用户偏好", "用户不吃", "用户讨厌", "用户习惯"]):
        return "preference"
    if _contains_any(text, ["用户叫", "用户是", "用户在", "用户家", "项目代号", "账号是"]):
        return "fact"
    return current or "lesson"


def _refine_subject_domain(content: str, memory_type: str, current: str) -> str:
    text = str(content or "")
    lowered = text.lower()
    current = str(current or "").strip().lower()

    if memory_type in {"rule", "decision"} or _contains_any(text, RULE_HINTS) or _contains_any(text, EXEC_RULE_HINTS):
        return "rule"
    if _contains_any(text, USER_PREFERENCE_HINTS) or _contains_any(text, USER_FACT_HINTS):
        return "user"
    if _contains_any(lowered, OPS_HINTS):
        return "ops"
    if _contains_any(lowered, CODE_HINTS):
        return "code"
    if current in {"rule", "ops", "code"}:
        return current
    if _contains_any(text, USER_HINTS):
        return "user"
    if current in {"user", "general"}:
        return current
    return "general"


def backfill_memory_schema_index(memory_obj: Any) -> Dict[str, Any]:
    memory_obj._ensure_memory_schema_index_table()
    conn = memory_obj._conn()
    try:
        rows = conn.execute(
            "SELECT id, content, confidence, importance, trigger_count, last_recall FROM memories ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()

    indexed = 0
    inferred = 0
    repaired_existing = 0
    repaired_sidecar_fields = 0
    for memory_id, content, confidence, importance, trigger_count, last_recall in rows:
        schema = memory_obj._read_memory_schema(memory_id)
        legacy_type = None
        try:
            conn_legacy = memory_obj._conn()
            row = conn_legacy.execute("SELECT type FROM memories WHERE id = ?", (memory_id,)).fetchone()
            legacy_type = row[0] if row else None
        finally:
            conn_legacy.close()
        inferred_payload = memory_obj._infer_memory_metadata(str(content or ""), str((schema or {}).get("source_layer") or "mixed") or "mixed")
        if schema:
            payload = dict(schema)
            for key in (
                "source_layer",
                "source_anchor",
                "source_session",
                "turn_signature",
                "memory_type",
                "user_explicit",
                "is_test_data",
                "promotion_reason",
                "hot_memory_candidate",
                "provenance",
                "lifecycle",
                "dedupe_key",
                "conflict_group",
                "quality_score",
                "evidence_level",
                "promotion_state",
                "last_confirmed_at",
                "hotness_score",
                "recall_source_type",
                "subject_domain",
                "content_preview",
            ):
                if payload.get(key) in (None, "", []):
                    payload[key] = inferred_payload.get(key)
            legacy_key = _normalize_legacy_type(legacy_type)
            if legacy_key:
                if payload.get("memory_type") in (None, "", "memory") and LEGACY_TYPE_TO_MEMORY_TYPE.get(legacy_key):
                    payload["memory_type"] = LEGACY_TYPE_TO_MEMORY_TYPE[legacy_key]
                if payload.get("subject_domain") in (None, "", "general") and LEGACY_TYPE_TO_SUBJECT_DOMAIN.get(legacy_key):
                    payload["subject_domain"] = LEGACY_TYPE_TO_SUBJECT_DOMAIN[legacy_key]
            repaired_existing += 1
        else:
            payload = dict(inferred_payload)
            payload["memory_id"] = memory_id
            payload["schema_version"] = "phase2.1-derived-backfill-v1"
            inferred += 1
        payload.setdefault("memory_id", memory_id)
        legacy_key = _normalize_legacy_type(legacy_type)
        if legacy_key:
            if payload.get("memory_type") in (None, "", "memory") and LEGACY_TYPE_TO_MEMORY_TYPE.get(legacy_key):
                payload["memory_type"] = LEGACY_TYPE_TO_MEMORY_TYPE[legacy_key]
            if payload.get("subject_domain") in (None, "", "general") and LEGACY_TYPE_TO_SUBJECT_DOMAIN.get(legacy_key):
                payload["subject_domain"] = LEGACY_TYPE_TO_SUBJECT_DOMAIN[legacy_key]
        payload["memory_type"] = _refine_memory_type(
            str(content or ""),
            str(payload.get("memory_type") or ""),
            str(payload.get("source_layer") or "mixed")
        )
        if payload.get("content_preview") in (None, ""):
            payload["content_preview"] = str(content or "")[:160]
        payload["subject_domain"] = _refine_subject_domain(
            str(content or ""),
            str(payload.get("memory_type") or ""),
            str(payload.get("subject_domain") or "")
        )
        if schema:
            sidecar_updates = {}
            for key in ("memory_type", "subject_domain", "content_preview"):
                if payload.get(key) not in (None, "", []) and payload.get(key) != schema.get(key):
                    sidecar_updates[key] = payload.get(key)
            if sidecar_updates:
                memory_obj._update_memory_schema_fields(memory_id, sidecar_updates)
                repaired_sidecar_fields += 1
        if confidence is not None:
            payload["confidence"] = confidence
        if importance is not None:
            payload["importance"] = importance
        if trigger_count is not None:
            payload["trigger_count"] = trigger_count
        if last_recall is not None:
            payload["last_recall"] = last_recall
        payload.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
        memory_obj._upsert_memory_schema_index(payload)
        indexed += 1

    return {
        "success": True,
        "indexed": indexed,
        "inferred": inferred,
        "repaired_existing": repaired_existing,
        "repaired_sidecar_fields": repaired_sidecar_fields,
        "sidecar_records": max(0, indexed - inferred),
        "table": "memory_schema_index",
    }


def memory_schema_index_status(memory_obj: Any) -> Dict[str, Any]:
    memory_obj._ensure_memory_schema_index_table()
    conn = memory_obj._conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM memory_schema_index").fetchone()[0]
        promoted = conn.execute("SELECT COUNT(*) FROM memory_schema_index WHERE promotion_state IN ('promoted', 'accepted', 'recalled')").fetchone()[0]
        sidecar_backed = conn.execute("SELECT COUNT(*) FROM memory_schema_index WHERE schema_version = 'phase2.1-sidecar-v1'").fetchone()[0]
        derived_backfill = conn.execute("SELECT COUNT(*) FROM memory_schema_index WHERE schema_version = 'phase2.1-derived-backfill-v1'").fetchone()[0]
        removed = conn.execute("SELECT COUNT(*) FROM memory_schema_index WHERE COALESCE(removed_at, '') != ''").fetchone()[0]
    finally:
        conn.close()
    return {
        "table": "memory_schema_index",
        "total": int(total),
        "promoted_like": int(promoted),
        "sidecar_backed": int(sidecar_backed),
        "derived_backfill": int(derived_backfill),
        "removed": int(removed),
    }


def structured_memory_overview(memory_obj: Any) -> Dict[str, Any]:
    memory_obj._ensure_memory_schema_index_table()
    conn = memory_obj._conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM memory_schema_index").fetchone()[0]
        rows_by_type = conn.execute(
            "SELECT COALESCE(memory_type, 'unknown'), COUNT(*) FROM memory_schema_index WHERE COALESCE(removed_at, '') = '' GROUP BY COALESCE(memory_type, 'unknown') ORDER BY COUNT(*) DESC"
        ).fetchall()
        rows_by_lifecycle = conn.execute(
            "SELECT COALESCE(lifecycle, 'unknown'), COUNT(*) FROM memory_schema_index WHERE COALESCE(removed_at, '') = '' GROUP BY COALESCE(lifecycle, 'unknown') ORDER BY COUNT(*) DESC"
        ).fetchall()
        rows_by_promotion = conn.execute(
            "SELECT COALESCE(promotion_state, 'unknown'), COUNT(*) FROM memory_schema_index WHERE COALESCE(removed_at, '') = '' GROUP BY COALESCE(promotion_state, 'unknown') ORDER BY COUNT(*) DESC"
        ).fetchall()
        rows_by_source = conn.execute(
            "SELECT COALESCE(recall_source_type, 'unknown'), COUNT(*) FROM memory_schema_index WHERE COALESCE(removed_at, '') = '' GROUP BY COALESCE(recall_source_type, 'unknown') ORDER BY COUNT(*) DESC"
        ).fetchall()
        high_quality = conn.execute(
            "SELECT COUNT(*) FROM memory_schema_index WHERE COALESCE(removed_at, '') = '' AND quality_score IS NOT NULL AND quality_score >= 0.8"
        ).fetchone()[0]
        hot_candidates = conn.execute(
            "SELECT COUNT(*) FROM memory_schema_index WHERE COALESCE(removed_at, '') = '' AND hot_memory_candidate = 1"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "reader": "memory_schema_index",
        "total": int(total),
        "by_memory_type": {str(k): int(v) for k, v in rows_by_type},
        "by_lifecycle": {str(k): int(v) for k, v in rows_by_lifecycle},
        "by_promotion_state": {str(k): int(v) for k, v in rows_by_promotion},
        "by_recall_source_type": {str(k): int(v) for k, v in rows_by_source},
        "high_quality": int(high_quality),
        "hot_candidates": int(hot_candidates),
    }


def _score_user_canonical_candidate(
    row: tuple,
    *,
    patterns: List[str],
    preferred_types: set[str],
    preferred_domains: set[str],
) -> tuple[float, List[str]]:
    (
        memory_id,
        content,
        memory_type,
        subject_domain,
        promotion_state,
        lifecycle,
        quality_score,
        user_explicit,
        hot_memory_candidate,
        source_anchor,
        turn_signature,
        valid_until,
        superseded_by,
        last_confirmed_at,
        updated_at,
    ) = row

    text = str(content or "")
    lowered = text.lower()
    matched_patterns: List[str] = []
    score = 0.0

    for pattern in patterns:
        needle = str(pattern or "").replace("%", "").strip().lower()
        if needle and needle in lowered:
            matched_patterns.append(needle)
            score += 1.0

    if memory_type in preferred_types:
        score += 1.2
    if subject_domain in preferred_domains:
        score += 0.9
    if user_explicit:
        score += 0.7
    if promotion_state in {"promoted", "accepted", "recalled"}:
        score += 0.5
    if lifecycle in {"active", "aging", "accepted", "candidate"}:
        score += 0.3
    if hot_memory_candidate:
        score += 0.2

    try:
        score += min(1.0, max(0.0, float(quality_score or 0.0)))
    except Exception:
        pass

    if text.startswith("用户"):
        score += 0.3
    if text.startswith("情感事件:"):
        score -= 0.8
    if text.startswith("规则变更:"):
        score -= 0.25
    if superseded_by not in (None, "", 0, "0") or lifecycle == "superseded":
        score -= 5.0
    if valid_until not in (None, ""):
        score -= 0.5
    if not source_anchor and not turn_signature:
        score -= 0.1
    if last_confirmed_at not in (None, ""):
        score += 0.05
    if updated_at not in (None, ""):
        score += 0.05

    return score, matched_patterns


def _build_user_canonical_candidate_payload(row: tuple, score: float, matched_patterns: List[str]) -> Dict[str, Any]:
    (
        memory_id,
        content,
        memory_type,
        subject_domain,
        promotion_state,
        lifecycle,
        quality_score,
        user_explicit,
        hot_memory_candidate,
        source_anchor,
        turn_signature,
        valid_until,
        superseded_by,
        last_confirmed_at,
        updated_at,
    ) = row

    return {
        "memory_id": int(memory_id),
        "content": str(content or ""),
        "memory_type": str(memory_type or ""),
        "subject_domain": str(subject_domain or ""),
        "promotion_state": str(promotion_state or ""),
        "lifecycle": str(lifecycle or ""),
        "quality_score": round(float(quality_score or 0.0), 3),
        "user_explicit": bool(user_explicit),
        "hot_memory_candidate": bool(hot_memory_candidate),
        "source_anchor": str(source_anchor or ""),
        "turn_signature": str(turn_signature or ""),
        "valid_until": str(valid_until or ""),
        "superseded_by": str(superseded_by or ""),
        "last_confirmed_at": str(last_confirmed_at or ""),
        "updated_at": str(updated_at or ""),
        "matched_patterns": matched_patterns,
        "score": round(float(score), 3),
    }


def _classify_user_canonical_slot(candidates: List[Dict[str, Any]]) -> tuple[str, str]:
    if not candidates:
        return "needs_review", "no_candidate_match"

    best = candidates[0]
    best_score = float(best.get("score") or 0.0)
    if best_score < 4.5:
        return "needs_review", "top_candidate_score_too_low"

    if len(candidates) == 1:
        return "stable", "single_candidate"

    runner_up = candidates[1]
    runner_up_score = float(runner_up.get("score") or 0.0)
    score_gap = round(best_score - runner_up_score, 3)
    matched_gap = len(best.get("matched_patterns") or []) - len(runner_up.get("matched_patterns") or [])
    same_type = best.get("memory_type") == runner_up.get("memory_type")
    same_domain = best.get("subject_domain") == runner_up.get("subject_domain")

    if score_gap < 0.15:
        return "needs_review", "runner_up_too_close"

    if same_type and same_domain and best_score >= 6.0 and runner_up_score >= 5.8 and score_gap <= 1.1:
        return "contended", "multiple_high_confidence_candidates"

    if score_gap < 0.75 and matched_gap <= 0:
        return "contended", "runner_up_close"

    if score_gap < 0.45 and matched_gap <= 1:
        return "contended", "pattern_advantage_is_thin"

    return "stable", "clear_winner"


def _build_user_canonical_repair_suggestions(
    spec: Dict[str, Any],
    best: Dict[str, Any] | None,
    candidates: List[Dict[str, Any]],
    status: str,
    status_reason: str,
) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []
    slot = str(spec.get("slot") or "")
    patterns = list(spec.get("patterns") or [])

    if status == "stable":
        return suggestions

    if not candidates:
        suggestions.append({
            "action": "seed_canonical_memory",
            "priority": "high",
            "reason": status_reason,
            "slot": slot,
            "note": "当前槽位没有命中候选，建议补一条明确、短句、canonical 的用户记忆。",
        })
        return suggestions

    if status == "needs_review" and status_reason == "top_candidate_score_too_low":
        suggestions.append({
            "action": "write_clearer_canonical_memory",
            "priority": "high",
            "reason": status_reason,
            "slot": slot,
            "target_memory_id": best.get("memory_id") if best else None,
            "note": "当前最高分候选仍偏弱，建议补一条更明确的 canonical 记忆，并减少模糊表述。",
        })

    if status in {"contended", "needs_review"} and best:
        runner_up = candidates[1] if len(candidates) > 1 else None
        if runner_up:
            suggestions.append({
                "action": "review_runner_up",
                "priority": "high" if status == "needs_review" else "medium",
                "reason": status_reason,
                "slot": slot,
                "winner_memory_id": best.get("memory_id"),
                "runner_up_memory_id": runner_up.get("memory_id"),
                "note": "检查次优候选是否应 supersede 到 winner，或是否其实代表另一个独立槽位。",
            })

        low_quality_alts = [
            cand for cand in candidates[1:4]
            if str(cand.get("subject_domain") or "") == "general" or not str(cand.get("memory_type") or "")
        ]
        if low_quality_alts:
            suggestions.append({
                "action": "cleanup_low_quality_alternatives",
                "priority": "medium",
                "reason": status_reason,
                "slot": slot,
                "candidate_ids": [cand.get("memory_id") for cand in low_quality_alts],
                "note": "次优候选里混入了 general/未结构化条目，建议清理或降权，避免假冲突。",
            })

    if status in {"contended", "needs_review"} and len(patterns) >= 2:
        suggestions.append({
            "action": "narrow_slot_patterns",
            "priority": "medium",
            "reason": status_reason,
            "slot": slot,
            "patterns": patterns,
            "note": "当前 pattern 可能过宽，建议收窄为更像该槽位真值的短语，减少跨槽串味。",
        })

    if status == "contended" and status_reason == "multiple_high_confidence_candidates":
        suggestions.append({
            "action": "manual_confirm_canonical",
            "priority": "medium",
            "reason": status_reason,
            "slot": slot,
            "winner_memory_id": best.get("memory_id") if best else None,
            "note": "多个高质量候选同时存在，建议人工确认 canonical 口径后再做 supersession。",
        })

    return suggestions


def _pick_user_canonical_slot(memory_obj: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
    memory_obj._ensure_memory_schema_index_table()
    patterns = list(spec.get("patterns") or [])
    if not patterns:
        return {
            "slot": spec.get("slot"),
            "label": spec.get("label"),
            "found": False,
            "status": "needs_review",
            "status_reason": "missing_patterns",
            "candidate_count": 0,
            "alternatives": [],
            "reason": "missing_patterns",
        }

    where_like = " OR ".join(["m.content LIKE ?" for _ in patterns])
    query = f"""
        SELECT
            m.id,
            m.content,
            COALESCE(msi.memory_type, ''),
            COALESCE(msi.subject_domain, ''),
            COALESCE(msi.promotion_state, ''),
            COALESCE(msi.lifecycle, ''),
            COALESCE(msi.quality_score, 0),
            COALESCE(msi.user_explicit, 0),
            COALESCE(msi.hot_memory_candidate, 0),
            COALESCE(msi.source_anchor, ''),
            COALESCE(msi.turn_signature, ''),
            COALESCE(msi.valid_until, ''),
            COALESCE(msi.superseded_by, ''),
            COALESCE(msi.last_confirmed_at, ''),
            COALESCE(msi.updated_at, '')
        FROM memories m
        LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
        WHERE COALESCE(msi.removed_at, '') = ''
          AND COALESCE(msi.lifecycle, '') != 'superseded'
          AND ({where_like})
        ORDER BY m.id DESC
        LIMIT 48
    """

    conn = memory_obj._conn()
    try:
        rows = conn.execute(query, tuple(patterns)).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "slot": spec.get("slot"),
            "label": spec.get("label"),
            "found": False,
            "status": "needs_review",
            "status_reason": "no_match",
            "candidate_count": 0,
            "alternatives": [],
            "reason": "no_match",
            "patterns": patterns,
        }

    preferred_types = {str(v) for v in (spec.get("preferred_types") or [])}
    preferred_domains = {str(v) for v in (spec.get("preferred_domains") or [])}
    scored_candidates: List[Dict[str, Any]] = []

    for row in rows:
        score, matched_patterns = _score_user_canonical_candidate(
            row,
            patterns=patterns,
            preferred_types=preferred_types,
            preferred_domains=preferred_domains,
        )
        scored_candidates.append(_build_user_canonical_candidate_payload(row, score, matched_patterns))

    scored_candidates.sort(
        key=lambda item: (
            float(item.get("score") or 0.0),
            len(item.get("matched_patterns") or []),
            float(item.get("quality_score") or 0.0),
            1 if item.get("user_explicit") else 0,
            int(item.get("memory_id") or 0),
        ),
        reverse=True,
    )

    if not scored_candidates:
        return {
            "slot": spec.get("slot"),
            "label": spec.get("label"),
            "found": False,
            "status": "needs_review",
            "status_reason": "scoring_failed",
            "candidate_count": 0,
            "alternatives": [],
            "reason": "scoring_failed",
            "patterns": patterns,
        }

    best = dict(scored_candidates[0])
    status, status_reason = _classify_user_canonical_slot(scored_candidates)
    runner_up = scored_candidates[1] if len(scored_candidates) > 1 else None
    repair_suggestions = _build_user_canonical_repair_suggestions(spec, best, scored_candidates, status, status_reason)
    best.update({
        "slot": spec.get("slot"),
        "label": spec.get("label"),
        "found": True,
        "status": status,
        "status_reason": status_reason,
        "candidate_count": len(scored_candidates),
        "score_gap_to_runner_up": round(float(best.get("score") or 0.0) - float((runner_up or {}).get("score") or 0.0), 3) if runner_up else None,
        "runner_up_memory_id": runner_up.get("memory_id") if runner_up else None,
        "runner_up_score": runner_up.get("score") if runner_up else None,
        "alternatives": [
            {
                "memory_id": cand.get("memory_id"),
                "content": cand.get("content"),
                "score": cand.get("score"),
                "matched_patterns": cand.get("matched_patterns"),
                "memory_type": cand.get("memory_type"),
                "subject_domain": cand.get("subject_domain"),
            }
            for cand in scored_candidates[1:4]
        ],
        "repair_suggestions": repair_suggestions,
        "patterns": patterns,
    })
    return best


def user_canonical_profile(memory_obj: Any) -> Dict[str, Any]:
    """用户核心画像的 curated truth view。"""
    slot_specs = [
        {
            "slot": "city",
            "label": "所在城市",
            "patterns": ["%石家庄人%", "%住在石家庄%", "%人在石家庄%"],
            "preferred_types": ["fact"],
            "preferred_domains": ["user"],
        },
        {
            "slot": "home_address",
            "label": "家庭地址",
            "patterns": ["%家在石家庄%", "%南高营%", "%家在%"],
            "preferred_types": ["fact"],
            "preferred_domains": ["user"],
        },
        {
            "slot": "work_address",
            "label": "公司地址",
            "patterns": ["%公司在%", "%盈伴商住大厦B座%"],
            "preferred_types": ["fact"],
            "preferred_domains": ["user"],
        },
        {
            "slot": "drink_preference",
            "label": "饮品偏好",
            "patterns": ["%喜欢喝冰美式咖啡%", "%喜欢冰美式%", "%冰美式咖啡%"],
            "preferred_types": ["preference"],
            "preferred_domains": ["user"],
        },
        {
            "slot": "honorific",
            "label": "偏好称呼",
            "patterns": ["%爱喝冰美式大人%", "%偏好称呼%", "%怎么称呼%"],
            "preferred_types": ["preference", "fact"],
            "preferred_domains": ["user"],
        },
        {
            "slot": "execution_mode",
            "label": "执行模式偏好",
            "patterns": ["%透明黑盒%", "%黑盒模式%"],
            "preferred_types": ["preference", "rule"],
            "preferred_domains": ["user", "rule"],
        },
        {
            "slot": "reply_concise",
            "label": "回答长度偏好",
            "patterns": ["%简洁的回答%", "%简洁回答%", "%简洁%"],
            "preferred_types": ["rule", "preference"],
            "preferred_domains": ["rule", "user"],
        },
        {
            "slot": "reply_segmented",
            "label": "回复结构偏好",
            "patterns": ["%分段回复%", "%一大条消息%"],
            "preferred_types": ["rule", "preference"],
            "preferred_domains": ["rule", "user"],
        },
        {
            "slot": "tone_style",
            "label": "聊天语气偏好",
            "patterns": ["%轻松自然%", "%不僵硬%", "%聊天语气%", "%一个重点%"],
            "preferred_types": ["preference", "rule"],
            "preferred_domains": ["user", "rule"],
        },
        {
            "slot": "primary_channel",
            "label": "主通道策略",
            "patterns": ["%Telegram 主通道%", "%Telegram 主线%", "%QQ 备用%", "%Discord 暂不启用%"],
            "preferred_types": ["decision", "rule"],
            "preferred_domains": ["rule"],
        },
    ]

    slots = {spec["slot"]: _pick_user_canonical_slot(memory_obj, spec) for spec in slot_specs}
    filled_slots = [slot for slot, item in slots.items() if item.get("found")]
    missing_slots = [slot for slot, item in slots.items() if not item.get("found")]
    stable_slots = [slot for slot, item in slots.items() if item.get("status") == "stable"]
    contended_slots = [slot for slot, item in slots.items() if item.get("status") == "contended"]
    review_slots = [slot for slot, item in slots.items() if item.get("status") == "needs_review"]
    repair_suggestions = []
    for slot_name, item in slots.items():
        for suggestion in item.get("repair_suggestions", []) or []:
            enriched = dict(suggestion)
            enriched.setdefault("slot", slot_name)
            repair_suggestions.append(enriched)

    profile_status = "stable"
    if review_slots or missing_slots:
        profile_status = "needs_review"
    elif contended_slots:
        profile_status = "contended"

    summary = (
        f"user canonical profile: {len(filled_slots)}/{len(slot_specs)} slots ready | "
        f"status={profile_status} | "
        f"filled={','.join(filled_slots) if filled_slots else 'none'}"
    )

    return {
        "reader": "memory_schema_index.user_canonical_profile",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": profile_status,
        "total_slots": len(slot_specs),
        "filled_slots": len(filled_slots),
        "missing_slots": missing_slots,
        "stable_slots": stable_slots,
        "contended_slots": contended_slots,
        "review_slots": review_slots,
        "status_counts": {
            "stable": len(stable_slots),
            "contended": len(contended_slots),
            "needs_review": len(review_slots),
        },
        "repair_suggestions": repair_suggestions,
        "slots": slots,
        "summary": summary,
    }


def sync_maintenance_updates(
    memory_obj: Any,
    memory_ids: List[Any],
    *,
    last_recall: str = None,
    trigger_delta: int = 0,
    importance: float = None,
    confidence: float = None,
    lifecycle: str = None,
    promotion_state: str = None,
    last_confirmed_at: str = None,
    hotness_score: float = None,
    remove: bool = False,
) -> int:
    """把 maintenance/update 链的字段变化同步到 schema sidecar。"""
    updated = 0
    now_iso = datetime.now().isoformat(timespec="seconds")
    for memory_id in memory_ids or []:
        existing = memory_obj._read_memory_schema(memory_id)
        updates: Dict[str, Any] = {}
        if last_recall is not None:
            updates["last_recall"] = last_recall
            updates["last_confirmed_at"] = last_confirmed_at or last_recall
        if trigger_delta:
            base_trigger = existing.get("trigger_count") or 0
            try:
                base_trigger = int(base_trigger)
            except Exception:
                base_trigger = 0
            updates["trigger_count"] = base_trigger + int(trigger_delta)
        if importance is not None:
            updates["importance"] = round(float(importance), 3)
        if confidence is not None:
            updates["confidence"] = round(float(confidence), 3)
        if lifecycle is not None:
            updates["lifecycle"] = lifecycle
        if promotion_state is not None:
            updates["promotion_state"] = promotion_state
        if hotness_score is not None:
            updates["hotness_score"] = round(float(hotness_score), 3)
        elif trigger_delta or last_recall is not None:
            base_hotness = existing.get("hotness_score")
            try:
                base_hotness = float(base_hotness)
            except Exception:
                base_hotness = 0.35
            updates["hotness_score"] = round(
                min(1.0, max(base_hotness, base_hotness + 0.08 * max(1, int(trigger_delta or 1)))),
                3,
            )
        if last_confirmed_at is not None:
            updates["last_confirmed_at"] = last_confirmed_at
        if remove:
            updates["lifecycle"] = lifecycle or "archived"
            updates["removed_at"] = now_iso
        memory_obj._update_memory_schema_fields(memory_id, updates, remove=remove)
        updated += 1
    return updated
