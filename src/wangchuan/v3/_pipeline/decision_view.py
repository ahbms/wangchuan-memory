"""
决策视图模块 — 构建共鸣决策视图

从 RecallEngine 中提取的职责，负责将融合候选组织为决策视图。
"""

from typing import Dict, List


class DecisionViewBuilder:
    """构建共鸣决策视图"""

    @classmethod
    def build_resonance_decision_view(
        cls,
        query: str,
        query_preference_profile: Dict[str, object],
        fusion_candidates: List[Dict[str, object]],
        memory_items: List[Dict],
        seed_candidates: List[Dict[str, object]],
        resonance_candidates: List[Dict[str, object]],
        pattern_candidates: List[Dict[str, object]],
        resource_items: List[Dict[str, object]],
        skill_items: List[Dict[str, object]],
    ) -> Dict[str, object]:
        """构建共鸣决策视图"""
        from .recall_engine import RecallEngine

        scope_route = str(query_preference_profile.get("scope_route") or "memory")
        primary = next(
            (
                item for item in fusion_candidates
                if str(item.get("decision_role") or "") not in {"memory_pattern_guarded", "memory_pattern_blocked"}
                and bool(item.get("allow_primary", True))
            ),
            fusion_candidates[0] if fusion_candidates else {},
        )

        primary_candidates = []
        supporting_candidates = []
        evidence_candidates = []
        pattern_rows = []
        suppressed_pattern_count = 0
        blocked_pattern_count = 0

        for item in fusion_candidates:
            role = str(item.get("decision_role") or "")
            if role in {"primary_resource", "primary_skill", "memory_pattern"} and bool(item.get("allow_primary", True)) and len(primary_candidates) < 3:
                primary_candidates.append(item)
            elif role in {"memory_linked", "memory_seed", "memory_pattern_guarded"} and len(supporting_candidates) < 5:
                supporting_candidates.append(item)
            elif role == "memory_evidence" and len(evidence_candidates) < 4:
                evidence_candidates.append(item)
            if role == "memory_pattern_guarded":
                suppressed_pattern_count += 1
            elif role == "memory_pattern_blocked":
                blocked_pattern_count += 1

        for item in pattern_candidates[:3]:
            suppression = RecallEngine.pattern_suppression_profile(item)
            pattern_rows.append({
                "pattern_id": item.get("pattern_id"),
                "status": item.get("status"),
                "pattern_scope": item.get("pattern_scope"),
                "support_count": item.get("support_count"),
                "counter_evidence_count": item.get("counter_evidence_count"),
                "suppression_penalty": suppression.get("suppression_penalty"),
                "allow_primary": suppression.get("allow_primary"),
                "route_state": suppression.get("route_state"),
            })

        suppression_summary = {
            "suppressed_pattern_count": suppressed_pattern_count,
            "blocked_pattern_count": blocked_pattern_count,
            "open_pattern_count": sum(1 for row in pattern_rows if row.get("allow_primary") is True),
        }

        return {
            "query": query,
            "scope_route": scope_route,
            "summary": {
                "primary_kind": primary.get("candidate_kind", ""),
                "primary_role": primary.get("decision_role", ""),
                "primary_reason": primary.get("why_selected", ""),
                "fusion_count": len(fusion_candidates or []),
                "seed_count": len(seed_candidates or []),
                "resonance_count": len(resonance_candidates or []),
                "pattern_count": len(pattern_candidates or []),
                "suppressed_pattern_count": suppressed_pattern_count,
                "blocked_pattern_count": blocked_pattern_count,
                "resource_count": len(resource_items or []),
                "skill_count": len(skill_items or []),
                "memory_count": len(memory_items or []),
            },
            "pattern_suppression_summary": suppression_summary,
            "primary_candidates": primary_candidates,
            "supporting_candidates": supporting_candidates,
            "evidence_candidates": evidence_candidates,
            "pattern_candidates": pattern_rows,
        }
