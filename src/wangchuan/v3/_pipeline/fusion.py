"""
融合模块 — 综合多源候选

从 RecallEngine 中提取的职责，负责将种子、共振、模式、资源、技能候选融合为统一排名。
"""

from typing import Dict, List


class FusionBuilder:
    """融合候选构建器"""

    @classmethod
    def build_fusion_candidates(
        cls,
        query_preference_profile: Dict[str, object],
        memory_items: List[Dict],
        resource_items: List[Dict[str, object]],
        skill_items: List[Dict[str, object]],
        seed_candidates: List[Dict[str, object]],
        resonance_candidates: List[Dict[str, object]],
        pattern_candidates: List[Dict[str, object]],
        limit: int = 8,
    ) -> List[Dict[str, object]]:
        """融合候选（综合多源）"""
        from .recall_engine import RecallEngine

        scope_route = str(query_preference_profile.get("scope_route") or "memory")
        rows: List[Dict[str, object]] = []

        def add_row(kind: str, score: float, role: str, payload: Dict[str, object]) -> None:
            rows.append({
                "candidate_kind": kind,
                "fusion_score": round(float(score), 6),
                "decision_role": role,
                **dict(payload or {}),
            })

        if scope_route == "resource":
            for item in resource_items[:5]:
                add_row(
                    "resource",
                    float(item.get("score") or 0.0) + 1.0,
                    "primary_resource",
                    {
                        "context_uri": item.get("context_uri"),
                        "title": item.get("title"),
                        "path": item.get("path"),
                        "why_selected": "scope_route=resource",
                    },
                )
        elif scope_route == "skill":
            for item in skill_items[:5]:
                add_row(
                    "skill",
                    float(item.get("score") or 0.0) + 1.0,
                    "primary_skill",
                    {
                        "context_uri": item.get("context_uri"),
                        "name": item.get("name"),
                        "path": item.get("path"),
                        "why_selected": "scope_route=skill",
                    },
                )
        else:
            for item in pattern_candidates[:3]:
                suppression = RecallEngine.pattern_suppression_profile(item)
                add_row(
                    str(suppression.get("candidate_kind") or "pattern_candidate"),
                    float(suppression.get("effective_score") or 0.0),
                    str(suppression.get("role") or "memory_pattern"),
                    {
                        "pattern_id": item.get("pattern_id"),
                        "content": item.get("content"),
                        "context_uri": f"pattern://candidate/{item.get('pattern_id')}",
                        "status": item.get("status"),
                        "support_count": item.get("support_count"),
                        "counter_evidence_count": item.get("counter_evidence_count"),
                        "suppression_penalty": suppression.get("suppression_penalty"),
                        "allow_primary": suppression.get("allow_primary"),
                        "route_state": suppression.get("route_state"),
                        "why_selected": suppression.get("why_selected"),
                    },
                )

            for item in resonance_candidates[:4]:
                source = str(item.get("resonance_source") or "memory_pool")
                add_row(
                    "resonance_candidate",
                    float(item.get("resonance_score") or 0.0) + (1.15 if source == "graph_edge" else 0.55),
                    "memory_linked",
                    {
                        "context_uri": item.get("context_uri"),
                        "memory_id": item.get("memory_id"),
                        "content_preview": item.get("content_preview"),
                        "resonance_reason": item.get("resonance_reason"),
                        "resonance_source": source,
                        "why_selected": "graph_edge" if source == "graph_edge" else "resonance_candidate",
                    },
                )

            for item in seed_candidates[:3]:
                add_row(
                    "seed_candidate",
                    float(item.get("seed_score") or 0.0) - 0.35,
                    "memory_seed",
                    {
                        "context_uri": item.get("context_uri"),
                        "memory_id": item.get("memory_id"),
                        "content_preview": item.get("content_preview"),
                        "seed_reason": item.get("seed_reason"),
                        "why_selected": "seed_candidate",
                    },
                )

            for item in memory_items[:3]:
                add_row(
                    "memory_item",
                    float(item.get("ranking_score") or item.get("score") or 0.0),
                    "memory_evidence",
                    {
                        "context_uri": item.get("context_uri"),
                        "memory_id": item.get("memory_id"),
                        "content_preview": str(item.get("content") or "")[:120],
                        "memory_type": item.get("memory_type"),
                        "subject_domain": item.get("subject_domain"),
                        "why_selected": "pure_memory",
                    },
                )

        deduped: List[Dict[str, object]] = []
        seen = set()
        for row in sorted(rows, key=lambda item: float(item.get("fusion_score") or 0.0), reverse=True):
            unique_key = str(
                row.get("context_uri")
                or row.get("pattern_id")
                or row.get("memory_id")
                or row.get("title")
                or row.get("name")
            ).strip().lower()
            if not unique_key or unique_key in seen:
                continue
            seen.add(unique_key)
            deduped.append(row)
            if len(deduped) >= limit:
                break
        return deduped
