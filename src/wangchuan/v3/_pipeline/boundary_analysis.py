"""
边界分析函数 — 从 pipeline_v3.py 和 degraded_recall.py 中提取的共享逻辑

包含三个纯数据函数：
- _derive_primary_evidence_boundary: 判断主要证据来源
- _build_joint_gating_status: 判断联合门控状态
- _assess_cross_topic_risk: 评估跨主题风险

这些函数原本在 pipeline_v3.py (作为 @staticmethod/@classmethod) 和
degraded_recall.py (作为独立函数) 中重复定义，现在统一到此处。
"""

from typing import Dict, List

from .boundary_gating import is_raw_evidence_item


def derive_primary_evidence_boundary(
    memory_layer: Dict | None,
    history_support: Dict | None,
    query_preference_profile: Dict | None = None,
    resonance_decision_view: Dict | None = None,
) -> Dict[str, object]:
    """判断主要证据来源。

    从 memory_layer / history_support / query_preference_profile / resonance_decision_view
    综合判断当前 recall 的主要证据来源和规则。
    """
    memory_layer = memory_layer or {}
    history_support = history_support or {}
    query_preference_profile = query_preference_profile or {}
    resonance_decision_view = resonance_decision_view or {}

    route = str(memory_layer.get("route") or "default")
    memory_items = list(memory_layer.get("items", []) or [])
    scope_route = str(query_preference_profile.get("scope_route") or "memory")
    decision_summary = dict(resonance_decision_view.get("summary") or {})
    primary_role = str(decision_summary.get("primary_role") or "")
    primary_kind = str(decision_summary.get("primary_kind") or "")
    history_support_items = int(history_support.get("support_items") or 0)
    raw_evidence_items = sum(1 for item in memory_items if is_raw_evidence_item(item))

    if scope_route == "resource" and not memory_items:
        primary_source = "resource_layer"
        history_support_only = False
        memory_context_allowed = False
        rule = "resource_scope_has_priority_memory_can_only_support"
    elif scope_route == "skill":
        primary_source = "skill_layer"
        history_support_only = False
        memory_context_allowed = False
        rule = "skill_scope_has_priority_memory_can_only_support"
    elif route == "raw" and memory_items:
        primary_source = "memory_layer"
        history_support_only = False
        memory_context_allowed = True
        rule = "raw_route_prefers_raw_evidence_resonance_cannot_override"
    elif memory_items:
        primary_source = "memory_layer"
        history_support_only = False
        memory_context_allowed = True
        rule = "history_can_support_but_must_not_override_memory_layer"
    elif history_support_items > 0:
        primary_source = "history_support"
        history_support_only = True
        memory_context_allowed = False
        rule = "history_support_only_when_memory_layer_is_empty"
    else:
        primary_source = "no_memory"
        history_support_only = False
        memory_context_allowed = False
        rule = "no_memory_available"

    return {
        "route": route,
        "scope_route": scope_route,
        "primary_source": primary_source,
        "decision_primary_role": primary_role,
        "decision_primary_kind": primary_kind,
        "memory_items": len(memory_items),
        "raw_evidence_items": raw_evidence_items,
        "history_support_items": history_support_items,
        "history_support_only": history_support_only,
        "memory_context_allowed": memory_context_allowed,
        "rule": rule,
    }


def build_joint_gating_status(
    memory_layer: Dict[str, object] | None,
    query_preference_profile: Dict[str, object] | None,
    history_support: Dict[str, object] | None,
    primary_evidence_boundary: Dict[str, object] | None,
    resonance_decision_view: Dict[str, object] | None,
) -> Dict[str, object]:
    """判断联合门控状态。

    综合 scope_route / memory_route / primary_source / resonance decision
    判断当前 recall 是否通过联合门控。
    """
    memory_layer = memory_layer or {}
    query_preference_profile = query_preference_profile or {}
    history_support = history_support or {}
    primary_evidence_boundary = primary_evidence_boundary or {}
    resonance_decision_view = resonance_decision_view or {}

    scope_route = str(query_preference_profile.get("scope_route") or "memory")
    memory_route = str(memory_layer.get("route") or "default")
    summary = dict(resonance_decision_view.get("summary") or {})
    primary_role = str(summary.get("primary_role") or "")
    primary_kind = str(summary.get("primary_kind") or "")
    primary_source = str(primary_evidence_boundary.get("primary_source") or "")
    memory_items = list(memory_layer.get("items", []) or [])
    raw_evidence_items = sum(1 for item in memory_items if is_raw_evidence_item(item))
    history_support_items = int(history_support.get("support_items") or 0)

    status = "ok"
    failure_category = ""
    allowed_primary_roles: List[str] = []

    if scope_route == "resource":
        mode = "resource_scope_protected"
        allowed_primary_roles = ["primary_resource"]
        if primary_role not in allowed_primary_roles:
            status = "violation"
            failure_category = "scope_preempted_by_memory"
    elif scope_route == "skill":
        mode = "skill_scope_protected"
        allowed_primary_roles = ["primary_skill"]
        if primary_role not in allowed_primary_roles:
            status = "violation"
            failure_category = "scope_preempted_by_memory"
    elif memory_route == "raw":
        mode = "raw_evidence_only"
        allowed_primary_roles = ["memory_evidence", "no_memory"]
        if primary_role and primary_role not in allowed_primary_roles:
            status = "violation"
            failure_category = "raw_polluted_by_resonance"
        elif memory_items and raw_evidence_items == 0:
            status = "violation"
            failure_category = "raw_route_without_raw_evidence"
    elif primary_source == "history_support" and memory_items:
        mode = "history_support_only"
        status = "violation"
        failure_category = "history_overrode_memory"
    elif primary_role in {"memory_pattern", "memory_linked", "memory_seed", "memory_pattern_guarded"}:
        mode = "memory_led_resonance"
        allowed_primary_roles = ["memory_pattern", "memory_linked", "memory_seed", "memory_pattern_guarded", "memory_evidence"]
    elif primary_role == "memory_evidence":
        mode = "evidence_only_memory"
        allowed_primary_roles = ["memory_evidence", "no_memory"]
    else:
        mode = "no_memory" if primary_role in {"", "no_memory"} else "memory_led_resonance"

    return {
        "scope_route": scope_route,
        "memory_route": memory_route,
        "mode": mode,
        "status": status,
        "classification": failure_category or mode,
        "failure_category": failure_category,
        "allowed_primary_roles": allowed_primary_roles,
        "actual_primary_role": primary_role,
        "actual_primary_kind": primary_kind,
        "primary_source": primary_source,
        "memory_items": len(memory_items),
        "raw_evidence_items": raw_evidence_items,
        "history_support_items": history_support_items,
        "rule": str(primary_evidence_boundary.get("rule") or ""),
    }


def assess_cross_topic_risk(
    query: str,
    memory_layer: Dict | None,
    query_preference_profile: Dict | None,
) -> Dict[str, object]:
    """评估跨主题风险。

    基于 recall 的 subject_domain 分布和查询长度判断是否存在跨主题混淆风险。
    """
    memory_layer = memory_layer or {}
    query_preference_profile = query_preference_profile or {}
    memory_items = list(memory_layer.get("items", []) or [])

    if not memory_items:
        return {"risk": False, "signal": "no_memory_items", "recalled_domains": []}

    recalled_domains = []
    for item in memory_items:
        domain = str(item.get("subject_domain") or "").strip().lower()
        if domain:
            recalled_domains.append(domain)

    if not recalled_domains:
        return {"risk": False, "signal": "no_domains_in_items", "recalled_domains": []}

    unique_domains = set(recalled_domains)
    risk = False
    signal = "single_domain"

    if len(unique_domains) > 1:
        risk = True
        signal = f"mixed_domains:{','.join(sorted(unique_domains))}"

    query_len = len(query.strip())
    if query_len <= 6 and memory_items and not risk:
        risk = True
        signal = f"short_query_recall:{','.join(sorted(unique_domains))}"

    return {
        "risk": risk,
        "signal": signal,
        "recalled_domains": recalled_domains,
    }


# ---------------------------------------------------------------------------
# 便捷别名（保持与旧代码的命名兼容）
# ---------------------------------------------------------------------------
_derive_primary_evidence_boundary = derive_primary_evidence_boundary
_build_joint_gating_status = build_joint_gating_status
_assess_cross_topic_risk = assess_cross_topic_risk
