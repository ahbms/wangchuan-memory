"""
Recall 候选构建模块 — 构建多阶段召回候选（seed → resonance → pattern → graph_edge）

从 WangchuanPipeline 中提取的模块，依赖 MemoryRanker、QueryProfiler、FormatBlocks。
Boundary gating 逻辑已迁移到 boundary_gating.py。
fusion 和 decision_view 逻辑已分别迁移到 fusion.py 和 decision_view.py。
"""

import re
import time
import logging
from collections import Counter
from typing import Dict, List, Optional
from typing import Dict, List

from .query_profiler import QueryProfiler
from .memory_ranker import MemoryRanker
from .format_blocks import FormatBlocks
from .context_assembler import shape_memory_items_for_output
from .boundary_gating import (
    is_raw_evidence_item as _is_raw_evidence_item,
    enforce_joint_gating_memory_boundary as _enforce_joint_gating_memory_boundary,
)


# ---------------------------------------------------------------------------
# RecallEngine 类
# ---------------------------------------------------------------------------

class RecallEngine:
    """Recall 候选构建器"""

    def __init__(self, memory_api=None, db_path: str = ""):
        self._memory_api = memory_api
        self.db_path = db_path

    # ----- Static / classmethod helpers -----

    @staticmethod
    def is_raw_evidence_item(item: Dict[str, object]) -> bool:
        """判断是否为原始证据项（委托给 boundary_gating 模块）"""
        return _is_raw_evidence_item(item)

    @staticmethod
    def derive_preference_seed_queries(query: str, topic_tokens: List[str]) -> List[str]:
        """从 topic_tokens 派生种子查询"""
        text = str(query or "")
        seeds: List[str] = []

        def add(seed: str) -> None:
            if seed and seed not in seeds:
                seeds.append(seed)

        if any(token in {"偏好", "用户", "称呼", "沟通", "回复风格", "分段回复", "少确认", "关键节点汇报", "透明黑盒", "任务板", "实施路线图", "路线图"} for token in topic_tokens):
            add("偏好")
            add("用户")

        if any(kw in text for kw in ["实施路线图", "路线图", "任务板"]):
            add("实施任务板")
            add("任务板")
            add("方案")
        if any(kw in text for kw in ["少确认", "关键节点汇报", "透明黑盒"]):
            add("透明黑盒")
            add("少确认")
            add("关键节点汇报")
        if any(kw in text for kw in ["分段回复", "回复风格", "一条消息一个重点"]):
            add("分段回复")
            add("回复风格")
            add("一条消息一个重点")
        if any(kw in text for kw in ["markdown", "文档扩散", "零散 Markdown"]):
            add("任务板")
            add("Markdown")

        return seeds

    @staticmethod
    def pattern_suppression_profile(item: Dict[str, object]) -> Dict[str, object]:
        """构建模式抑制 profile"""
        status = str(item.get("status") or "candidate")
        support_count = int(item.get("support_count") or 0)
        counter_evidence_count = int(item.get("counter_evidence_count") or 0)
        base_score = float(item.get("pattern_score") or 0.0)

        suppression_penalty = min(0.72, counter_evidence_count * 0.22)
        reason = "pattern_candidate"
        role = "memory_pattern"
        candidate_kind = "pattern_candidate"
        allow_primary = True
        route_state = "open"
        primary_cap = 1
        score_bias = 1.35

        if status == "contested":
            allow_primary = False
            route_state = "guarded"
            candidate_kind = "pattern_guarded_candidate"
            role = "memory_pattern_guarded"
            reason = "pattern_candidate_contested_suppressed"
            primary_cap = 0
            score_bias = max(-0.38, 0.22 - suppression_penalty)
        elif status == "weak":
            allow_primary = False
            route_state = "blocked"
            candidate_kind = "pattern_weak_candidate"
            role = "memory_pattern_blocked"
            reason = "pattern_candidate_weak_blocked"
            primary_cap = 0
            score_bias = -0.82 if support_count >= 2 else -1.15

        effective_score = round(base_score + score_bias - suppression_penalty, 6)
        return {
            "candidate_kind": candidate_kind,
            "role": role,
            "why_selected": reason,
            "allow_primary": allow_primary,
            "route_state": route_state,
            "suppression_penalty": round(suppression_penalty, 6),
            "effective_score": effective_score,
            "primary_cap": primary_cap,
        }

    # ----- Candidate builders -----

    @classmethod
    def build_seed_candidates(
        cls,
        query: str,
        memory_items: List[Dict],
        query_preference_profile: Dict[str, object],
        limit: int = 5,
    ) -> List[Dict[str, object]]:
        """构建种子候选"""
        query_text = str(query or "").strip().lower()
        topic_tokens = [str(token).strip().lower() for token in list(query_preference_profile.get("topic_tokens", []) or []) if token]
        preferred_domains = {str(v).strip().lower() for v in list(query_preference_profile.get("preferred_domains", []) or []) if v}
        preferred_types = {str(v).strip().lower() for v in list(query_preference_profile.get("preferred_types", []) or []) if v}

        seeds: List[Dict[str, object]] = []
        seen = set()

        for index, item in enumerate(memory_items or []):
            memory_id = str(item.get("memory_id") or "").strip()
            dedupe_key = str(item.get("dedupe_key") or memory_id or item.get("context_uri") or f"idx-{index}").strip().lower()
            if not dedupe_key or dedupe_key in seen:
                continue

            content = str(item.get("content") or "")
            lowered_content = content.lower()
            score = float(item.get("ranking_score") or item.get("score") or 0.0)
            seed_type = "semantic"
            reasons: List[str] = []
            match_terms: List[str] = []

            matched_topic_tokens = [token for token in topic_tokens if token and token in lowered_content]
            direct_hit = bool(matched_topic_tokens or (query_text and query_text in lowered_content))
            is_dormant = bool(item.get("is_dormant"))
            vitality = float(item.get("vitality") or 0.0)
            if is_dormant and not direct_hit:
                continue

            if matched_topic_tokens:
                seed_type = "entity"
                score += 0.16 * min(len(matched_topic_tokens), 3)
                reasons.append("topic_token_match")
                match_terms.extend(matched_topic_tokens)

            item_domain = str(item.get("subject_domain") or "").strip().lower()
            if item_domain and item_domain in preferred_domains:
                score += 0.12
                reasons.append(f"domain={item_domain}")

            item_type = str(item.get("memory_type") or "").strip().lower()
            if item_type and item_type in preferred_types:
                score += 0.1
                reasons.append(f"type={item_type}")

            if item.get("hot_memory_candidate"):
                score += 0.06
                reasons.append("hot_memory_candidate")

            if item.get("user_explicit"):
                score += 0.05
                reasons.append("user_explicit")

            if item.get("source_anchor"):
                score += 0.03
                reasons.append("anchored")

            if not matched_topic_tokens and query_text and query_text in lowered_content:
                score += 0.08
                reasons.append("direct_query_substring")

            awakened_from_dormant = False
            if is_dormant and direct_hit:
                awakened_from_dormant = True
                score += 0.07
                reasons.append("dormant_direct_hit")

            if not reasons:
                reasons.append("ranking_seed")

            seen.add(dedupe_key)
            seeds.append({
                "memory_id": item.get("memory_id"),
                "context_uri": item.get("context_uri"),
                "seed_type": seed_type,
                "seed_score": round(score, 6),
                "seed_reason": ", ".join(reasons[:3]),
                "query_match_terms": match_terms[:4],
                "direct_hit": direct_hit,
                "vitality": round(vitality, 6),
                "is_dormant": is_dormant,
                "awakened_from_dormant": awakened_from_dormant,
                "memory_type": item.get("memory_type"),
                "subject_domain": item.get("subject_domain"),
                "source_layer": item.get("source_layer"),
                "content_preview": content[:120],
            })

        seeds.sort(key=lambda row: float(row.get("seed_score") or 0.0), reverse=True)
        return seeds[:limit]

    @classmethod
    def build_resonance_candidates(
        cls,
        seeds: List[Dict[str, object]],
        memory_items: List[Dict],
        limit: int = 5,
    ) -> List[Dict[str, object]]:
        """构建共鸣候选"""
        if not seeds or not memory_items:
            return []

        seed_index = {
            str(seed.get("memory_id") or ""): seed
            for seed in (seeds or [])
            if seed.get("memory_id") not in (None, "")
        }
        seed_topic_terms = {
            str(term).strip().lower()
            for seed in (seeds or [])
            for term in list(seed.get("query_match_terms", []) or [])
            if term
        }

        resonance_rows: List[Dict[str, object]] = []
        seen = set()

        for item in memory_items or []:
            memory_id = str(item.get("memory_id") or "").strip()
            if not memory_id or memory_id in seed_index:
                continue
            if bool(item.get("is_dormant")):
                continue

            content = str(item.get("content") or "")
            lowered_content = content.lower()
            item_type = str(item.get("memory_type") or "").strip().lower()
            item_domain = str(item.get("subject_domain") or "").strip().lower()
            item_conflict_group = str(item.get("conflict_group") or "").strip().lower()
            item_dedupe_key = str(item.get("dedupe_key") or "").strip().lower()

            score = 0.0
            matched_seed_ids: List[str] = []
            resonance_reasons: List[str] = []
            matched_terms: List[str] = []

            for seed in seeds:
                seed_id = str(seed.get("memory_id") or "").strip()
                if bool(seed.get("is_dormant")):
                    continue
                seed_type = str(seed.get("memory_type") or "").strip().lower()
                seed_domain = str(seed.get("subject_domain") or "").strip().lower()
                seed_terms = [str(term).strip().lower() for term in list(seed.get("query_match_terms", []) or []) if term]

                matched = False
                if seed_type and item_type and seed_type == item_type:
                    score += 0.22
                    resonance_reasons.append(f"type={item_type}")
                    matched = True
                if seed_domain and item_domain and seed_domain == item_domain:
                    score += 0.18
                    resonance_reasons.append(f"domain={item_domain}")
                    matched = True
                if item_conflict_group and item_conflict_group == str(seed.get("memory_type") or "").strip().lower():
                    score += 0.08
                    resonance_reasons.append("conflict_group_hint")
                    matched = True
                if item_dedupe_key and item_dedupe_key == str(seed.get("memory_id") or "").strip().lower():
                    score += 0.05
                    resonance_reasons.append("dedupe_link")
                    matched = True

                overlapping_terms = [term for term in seed_terms if term and term in lowered_content]
                if overlapping_terms:
                    score += 0.12 * min(len(overlapping_terms), 2)
                    matched_terms.extend(overlapping_terms)
                    resonance_reasons.append("shared_terms")
                    matched = True

                if matched and seed_id:
                    matched_seed_ids.append(seed_id)

            if not matched_seed_ids and seed_topic_terms:
                fallback_terms = [term for term in seed_topic_terms if term in lowered_content]
                if fallback_terms:
                    score += 0.14
                    matched_terms.extend(fallback_terms)
                    resonance_reasons.append("topic_fallback")

            if item.get("hot_memory_candidate"):
                score += 0.04
            if item.get("source_anchor"):
                score += 0.03

            if score <= 0:
                continue

            unique_key = memory_id or str(item.get("context_uri") or "").strip().lower()
            if not unique_key or unique_key in seen:
                continue
            seen.add(unique_key)

            resonance_rows.append({
                "memory_id": item.get("memory_id"),
                "context_uri": item.get("context_uri"),
                "resonance_score": round(score, 6),
                "matched_seed_ids": matched_seed_ids[:3],
                "resonance_reason": ", ".join(dict.fromkeys(resonance_reasons))[:160],
                "query_match_terms": list(dict.fromkeys(matched_terms))[:4],
                "vitality": round(float(item.get("vitality") or 0.0), 6),
                "is_dormant": bool(item.get("is_dormant")),
                "memory_type": item.get("memory_type"),
                "subject_domain": item.get("subject_domain"),
                "source_layer": item.get("source_layer"),
                "content_preview": content[:120],
            })

        resonance_rows.sort(key=lambda row: float(row.get("resonance_score") or 0.0), reverse=True)
        return resonance_rows[:limit]

    @classmethod
    def build_pattern_candidates(
        cls,
        query: str,
        candidate_memory_items: List[Dict],
        resonance_candidates: List[Dict[str, object]],
        limit: int = 3,
    ) -> List[Dict[str, object]]:
        """构建模式候选"""
        if not candidate_memory_items:
            return []

        resonance_index = {
            str(item.get("memory_id") or ""): item
            for item in (resonance_candidates or [])
            if item.get("memory_id") not in (None, "")
        }

        groups: Dict[tuple[str, str], List[Dict]] = {}
        for item in candidate_memory_items:
            if bool(item.get("is_dormant")):
                continue
            memory_type = str(item.get("memory_type") or "").strip().lower()
            subject_domain = str(item.get("subject_domain") or "").strip().lower()
            if not memory_type or not subject_domain:
                continue
            groups.setdefault((memory_type, subject_domain), []).append(item)

        pattern_rows: List[Dict[str, object]] = []
        query_text = str(query or "").strip()
        query_terms = {
            token.strip().lower()
            for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", query_text)
            if token and len(token.strip()) >= 2
        }

        for (memory_type, subject_domain), items in groups.items():
            if len(items) < 2:
                continue

            ordered_items = sorted(
                items,
                key=lambda item: float(
                    resonance_index.get(str(item.get("memory_id") or ""), {}).get("resonance_score")
                    or item.get("ranking_score")
                    or item.get("score")
                    or 0.0
                ),
                reverse=True,
            )
            source_items = ordered_items[: min(4, len(ordered_items))]
            source_ids = [str(item.get("memory_id") or "") for item in source_items if item.get("memory_id") not in (None, "")]
            if len(source_ids) < 2:
                continue

            token_counter: Counter[str] = Counter()
            for item in source_items:
                text = str(item.get("content") or "")
                for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", text):
                    normalized = token.strip().lower()
                    if len(normalized) <= 1:
                        continue
                    if normalized in {"用户", "规则", "偏好", "记忆", "变更"}:
                        continue
                    token_counter[normalized] += 1

            top_terms = [term for term, _count in token_counter.most_common(4)]
            if not top_terms:
                top_terms = [memory_type, subject_domain]

            primary_scope_terms = top_terms[:2]
            scope_constraints: List[str] = []
            if query_terms:
                scope_constraints.extend(list(query_terms)[:2])
            for item in source_items:
                anchor = str(item.get("source_anchor") or "")
                if "workspace://USER.md" in anchor and "用户档案" not in scope_constraints:
                    scope_constraints.append("用户档案")
                if str(item.get("source_layer") or "") == "scar" and "scar层记忆" not in scope_constraints:
                    scope_constraints.append("scar层记忆")
            pattern_scope = " + ".join(dict.fromkeys(primary_scope_terms + scope_constraints))[:160]

            source_content_pool = [str(item.get("content") or "") for item in source_items]
            counter_evidence = 0
            counter_examples: List[str] = []
            for (other_type, other_domain), other_items in groups.items():
                if (other_type, other_domain) == (memory_type, subject_domain):
                    continue
                if other_domain != subject_domain:
                    continue
                for other in other_items:
                    other_text = str(other.get("content") or "")
                    lowered_other = other_text.lower()
                    overlap_terms = [term for term in primary_scope_terms if term and term in lowered_other]
                    if overlap_terms:
                        counter_evidence += 1
                        counter_examples.append(other_text[:120])
                        if counter_evidence >= 3:
                            break
                if counter_evidence >= 3:
                    break

            support_count = len(source_ids)
            avg_confidence = round(
                sum(float(item.get("confidence") or 0.0) for item in source_items) / max(1, support_count),
                6,
            )
            max_quality = round(max(float(item.get("quality_score") or item.get("confidence") or 0.0) for item in source_items), 6)
            avg_vitality = round(
                sum(float(item.get("vitality") or 0.0) for item in source_items) / max(1, support_count),
                6,
            )
            content = f"围绕「{query_text}」的 {memory_type}/{subject_domain} 规律候选：{pattern_scope}"
            evidence_preview = [str(item.get("content") or "")[:120] for item in source_items[:3]]

            contradiction_penalty = min(0.24, counter_evidence * 0.08)
            pattern_score = round(
                avg_vitality * 0.4
                + avg_confidence * 0.25
                + max_quality * 0.2
                + min(0.15, support_count * 0.05)
                - contradiction_penalty,
                6,
            )

            if support_count >= 3 and counter_evidence == 0:
                status = "candidate"
            elif counter_evidence > 0:
                status = "contested"
            else:
                status = "weak"

            pattern_rows.append({
                "pattern_id": f"pattern-candidate:{memory_type}:{subject_domain}:{'-'.join(source_ids[:2])}",
                "content": content,
                "pattern_scope": pattern_scope,
                "source_nodes": source_ids,
                "support_count": support_count,
                "counter_evidence_count": counter_evidence,
                "counter_evidence_preview": counter_examples[:3],
                "avg_confidence": avg_confidence,
                "max_quality_score": max_quality,
                "avg_vitality": avg_vitality,
                "dominant_memory_type": memory_type,
                "dominant_domain": subject_domain,
                "status": status,
                "pattern_score": pattern_score,
                "evidence_preview": evidence_preview,
            })

        for row in pattern_rows:
            suppression = cls.pattern_suppression_profile(row)
            row["suppression_penalty"] = suppression.get("suppression_penalty")
            row["allow_primary"] = suppression.get("allow_primary")
            row["route_state"] = suppression.get("route_state")
            row["suppression_reason"] = suppression.get("why_selected")

        pattern_rows.sort(key=lambda row: float(row.get("pattern_score") or 0.0), reverse=True)
        return pattern_rows[:limit]

    def build_graph_edge_resonance_candidates(
        self,
        query: str,
        seeds: List[Dict[str, object]],
        limit: int = 3,
    ) -> List[Dict[str, object]]:
        """图边共鸣候选"""
        if not query:
            return []

        query_text = str(query or "").strip()
        if not query_text:
            return []

        seed_terms = {
            str(term).strip().lower()
            for seed in (seeds or [])
            for term in list(seed.get("query_match_terms", []) or [])
            if term
        }
        query_terms = {
            token.strip().lower()
            for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", query_text)
            if token and len(token.strip()) >= 2
        }
        segmented_query_terms = {
            token.strip().lower()
            for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", query_text)
            if token and len(token.strip()) >= 2
        }
        query_terms.update(segmented_query_terms)
        if not seed_terms:
            seed_terms = set(query_terms)
        search_terms = [term for term in list(seed_terms | query_terms) if term]
        if not search_terms:
            search_terms = [query_text.lower()]

        candidates: List[Dict[str, object]] = []
        seen = set()

        try:
            import sqlite3
            from wangchuan.db_utils import get_connection

            with get_connection(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                node_map: Dict[str, object] = {}
                for term in search_terms[:6]:
                    like_rows = conn.execute(
                        """
                        SELECT node_id, node_type, name, COALESCE(description, '') AS description, COALESCE(content, '') AS content, COALESCE(pagerank_score, 0) AS pagerank_score
                        FROM gm_nodes
                        WHERE name LIKE ? OR description LIKE ? OR content LIKE ?
                        ORDER BY pagerank_score DESC, id DESC
                        LIMIT 5
                        """,
                        (f"%{term}%", f"%{term}%", f"%{term}%"),
                    ).fetchall()
                    for row in like_rows:
                        node_map[str(row["node_id"])] = row
                node_rows = sorted(
                    node_map.values(),
                    key=lambda row: float(row["pagerank_score"] or 0.0),
                    reverse=True,
                )[:5]

                for node in node_rows:
                    source_node_id = str(node["node_id"] or "")
                    if not source_node_id:
                        continue
                    source_name = str(node["name"] or "")
                    source_pagerank = float(node["pagerank_score"] or 0.0)

                    edge_rows = conn.execute(
                        """
                        SELECT
                            e.edge_id,
                            e.edge_type,
                            COALESCE(e.weight, 0.0) AS weight,
                            e.source_node_id,
                            e.target_node_id,
                            src.name AS source_name,
                            src.node_type AS source_type,
                            tgt.name AS target_name,
                            tgt.node_type AS target_type,
                            COALESCE(src.pagerank_score, 0.0) AS source_pagerank,
                            COALESCE(tgt.pagerank_score, 0.0) AS target_pagerank
                        FROM gm_edges e
                        JOIN gm_nodes src ON src.node_id = e.source_node_id
                        JOIN gm_nodes tgt ON tgt.node_id = e.target_node_id
                        WHERE e.source_node_id = ? OR e.target_node_id = ?
                        ORDER BY e.weight DESC, e.id DESC
                        LIMIT 12
                        """,
                        (source_node_id, source_node_id),
                    ).fetchall()

                    for edge in edge_rows:
                        edge_source_id = str(edge["source_node_id"] or "")
                        edge_target_id = str(edge["target_node_id"] or "")
                        neighbor_node_id = edge_target_id if edge_source_id == source_node_id else edge_source_id
                        neighbor_name = str(edge["target_name"] or "") if edge_source_id == source_node_id else str(edge["source_name"] or "")
                        neighbor_type = str(edge["target_type"] or "") if edge_source_id == source_node_id else str(edge["source_type"] or "")
                        neighbor_pagerank = float(edge["target_pagerank"] or 0.0) if edge_source_id == source_node_id else float(edge["source_pagerank"] or 0.0)
                        edge_type = str(edge["edge_type"] or "")
                        edge_weight = float(edge["weight"] or 0.0)

                        if not neighbor_node_id or neighbor_node_id == source_node_id:
                            continue

                        unique_key = f"{source_node_id}:{neighbor_node_id}:{edge_type}".lower()
                        if unique_key in seen:
                            continue

                        lowered_neighbor = neighbor_name.lower()
                        matched_terms = [term for term in search_terms if term and term in lowered_neighbor]
                        matched_seed_ids = [
                            str(seed.get("memory_id") or "")
                            for seed in (seeds or [])
                            if any(term in str(seed.get("content_preview") or "").lower() or term in str(seed.get("query_match_terms") or "").lower() for term in matched_terms)
                        ]

                        score = edge_weight * 0.65 + source_pagerank * 0.2 + neighbor_pagerank * 0.15
                        if matched_terms:
                            score += min(len(matched_terms) * 0.08, 0.16)

                        graph_vitality = round(
                            max(
                                0.08,
                                max(0.0, min(1.0, edge_weight * 0.5 + source_pagerank * 0.25 + neighbor_pagerank * 0.25)),
                            ),
                            6,
                        )

                        seen.add(unique_key)
                        candidates.append({
                            "memory_id": None,
                            "node_id": neighbor_node_id,
                            "context_uri": f"graph://gm_nodes/{neighbor_node_id}",
                            "resonance_source": "graph_edge",
                            "resonance_score": round(score, 6),
                            "matched_seed_ids": [item for item in matched_seed_ids if item][:3],
                            "resonance_reason": f"graph_edge:{edge_type}",
                            "query_match_terms": matched_terms[:4],
                            "vitality": graph_vitality,
                            "is_dormant": False,
                            "awakened_from_dormant": False,
                            "memory_type": None,
                            "subject_domain": "graph",
                            "source_layer": "graph",
                            "content_preview": neighbor_name[:120],
                            "graph_path": {
                                "source_node_id": source_node_id,
                                "source_name": source_name,
                                "edge_id": str(edge["edge_id"] or ""),
                                "edge_type": edge_type,
                                "edge_weight": round(edge_weight, 6),
                                "target_node_id": neighbor_node_id,
                                "target_name": neighbor_name,
                                "target_type": neighbor_type,
                            },
                        })

        except Exception:
            return []

        candidates.sort(key=lambda row: float(row.get("resonance_score") or 0.0), reverse=True)
        return candidates[:limit]

    # ----- Boundary gating (已迁移到 boundary_gating.py) -----

    @classmethod
    def enforce_joint_gating_memory_boundary(cls, memory_layer: Dict[str, object]) -> Dict[str, object]:
        """强制联合 gating boundary（委托给 boundary_gating 模块）"""
        return _enforce_joint_gating_memory_boundary(memory_layer)

    # ----- Primary recall method -----

    def recall_memory_layer(self, query: str, top_k: int = 5) -> Dict:
        """构建 memory layer（路由 + 召回 + 排序 + boundary）"""
        route = QueryProfiler.memory_route(query)
        profile = QueryProfiler.build_query_preference_profile(query)
        premise_challenge = bool(profile.get("premise_challenge")) if isinstance(profile, dict) else False
        preferred_domains = list(profile.get("preferred_domains", [])) if isinstance(profile, dict) else []
        topic_tokens = list(profile.get("topic_tokens", [])) if isinstance(profile, dict) else []

        if route == "raw":
            items = self._memory_api.recall_raw(query, limit=top_k * 2)
            reader = "memory_api.recall_raw"
        elif route == "scar":
            items = self._memory_api.recall_scars(query, limit=top_k * 2)
            scar_items = items
            if any(domain in {"user", "rule"} for domain in preferred_domains):
                mixed_items = self._memory_api.recall(query, limit=top_k * 2)
                preference_seed_items = []
                for seed in self.derive_preference_seed_queries(query, topic_tokens):
                    preference_seed_items.extend(self._memory_api.recall(seed, limit=max(2, top_k // 2)))
                items = scar_items + mixed_items + preference_seed_items
                reader = "memory_api.recall_scars+recall+preference_seed"
            else:
                items = scar_items
                reader = "memory_api.recall_scars"
        else:
            items = self._memory_api.recall(query, limit=top_k * 2)
            reader = "memory_api.recall"

        ranked_pool = MemoryRanker.rank(query, route, items)
        if premise_challenge and route != "raw":
            ranked_pool = []
        ranked_items = shape_memory_items_for_output(ranked_pool[:top_k])
        candidate_items = shape_memory_items_for_output(ranked_pool[: max(top_k * 2, top_k)])
        metadata_summary = {
            "source_layers": sorted({item.get("source_layer", "") for item in ranked_items if item.get("source_layer")}),
            "memory_types": sorted({item.get("memory_type", "") for item in ranked_items if item.get("memory_type")}),
            "subject_domains": sorted({item.get("subject_domain", "") for item in ranked_items if item.get("subject_domain")}),
            "evidence_levels": sorted({item.get("evidence_level", "") for item in ranked_items if item.get("evidence_level")}),
            "lifecycle": sorted({item.get("lifecycle", "") for item in ranked_items if item.get("lifecycle")}),
            "promotion_states": sorted({item.get("promotion_state", "") for item in ranked_items if item.get("promotion_state")}),
            "recall_source_types": sorted({item.get("recall_source_type", "") for item in ranked_items if item.get("recall_source_type")}),
            "schema_versions": sorted({item.get("schema_version", "") for item in ranked_items if item.get("schema_version")}),
            "premise_challenge": premise_challenge,
        }
        for key in ["source_layer", "memory_type", "subject_domain", "evidence_level"]:
            metadata_summary.setdefault(f"{key}s", metadata_summary.get(f"{key}s", []))
        memory_layer = {
            "route": route,
            "reader": reader,
            "structured": reader in {"memory_api.recall", "memory_api.recall_raw", "memory_api.recall_scars"},
            "items": ranked_items,
            "candidate_items": candidate_items,
            "block": FormatBlocks.format_memory_recall_block(ranked_items, route),
            "metadata_summary": metadata_summary,
        }
        return self.enforce_joint_gating_memory_boundary(memory_layer)


# ---------------------------------------------------------------------------
# Recall Result Assembly — recall() 中段逻辑的独立提取
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def build_recall_candidates(
    query: str,
    memory_layer: Dict,
    query_preference_profile: Dict[str, object],
    retrieve_top_k: int,
    pipeline,
) -> Dict[str, object]:
    """构建召回候选（seed → resonance → pattern → graph_edge → fusion → decision_view）

    Returns:
        dict 包含 seed_candidates, resonance_candidates, graph_edge_resonance_candidates,
        pattern_candidates, fusion_candidates, resonance_decision_view,
        resource_items, skill_items
    """
    candidate_memory_items = list(memory_layer.get('candidate_items', []) or memory_layer.get('items', []) or [])
    scope_route = str(query_preference_profile.get('scope_route') or 'memory')
    raw_evidence_only_mode = scope_route == 'memory' and str(memory_layer.get('route') or '') == 'raw'

    if raw_evidence_only_mode:
        seed_candidates = []
        resonance_candidates = []
        graph_edge_resonance_candidates = []
        pattern_candidates = []
    else:
        seed_candidates = pipeline._build_seed_candidates(
            query,
            candidate_memory_items,
            query_preference_profile,
            limit=min(5, retrieve_top_k or 5),
        )
        resonance_candidates = pipeline._build_resonance_candidates(
            seed_candidates,
            candidate_memory_items,
            limit=min(5, retrieve_top_k or 5),
        )
        graph_edge_resonance_candidates = pipeline._build_graph_edge_resonance_candidates(
            query,
            seed_candidates,
            limit=min(3, retrieve_top_k or 3),
        )
        resonance_candidates = sorted(
            list(resonance_candidates) + list(graph_edge_resonance_candidates),
            key=lambda row: float(row.get('resonance_score') or 0.0),
            reverse=True,
        )[: max(5, min(8, retrieve_top_k + 2))]
        pattern_candidates = pipeline._build_pattern_candidates(
            query,
            candidate_memory_items,
            resonance_candidates,
            limit=min(3, retrieve_top_k or 3),
        )

    resource_items = (
        pipeline._probe_resource_items(query, limit=min(3, retrieve_top_k or 3))
        if query_preference_profile.get('scope_route') == 'resource'
        else []
    )
    skill_items = (
        pipeline._probe_skill_items(query, limit=min(3, retrieve_top_k or 3))
        if query_preference_profile.get('scope_route') == 'skill'
        else []
    )

    fusion_candidates = pipeline._build_fusion_candidates(
        query_preference_profile,
        list(memory_layer.get('items', []) or []),
        resource_items,
        skill_items,
        seed_candidates,
        resonance_candidates,
        pattern_candidates,
        limit=max(5, min(8, retrieve_top_k + 2)),
    )
    resonance_decision_view = pipeline._build_resonance_decision_view(
        query,
        query_preference_profile,
        fusion_candidates,
        list(memory_layer.get('items', []) or []),
        seed_candidates,
        resonance_candidates,
        pattern_candidates,
        resource_items,
        skill_items,
    )
    return {
        'seed_candidates': seed_candidates,
        'resonance_candidates': resonance_candidates,
        'graph_edge_resonance_candidates': graph_edge_resonance_candidates,
        'pattern_candidates': pattern_candidates,
        'fusion_candidates': fusion_candidates,
        'resonance_decision_view': resonance_decision_view,
        'resource_items': resource_items,
        'skill_items': skill_items,
    }


def build_recall_result(
    query: str,
    session_id: str,
    started_at: float,
    memory_layer: Dict,
    assembly,
    query_preference_profile: Dict[str, object],
    node_ids: List[str],
    results: list,
    retrieval_debug: Dict,
    short_followup_mode: bool,
    retrieve_top_k: int,
    consciousness_context: str,
    wakeup_pack: str,
    response_strategy: str,
    execution_guidance: Dict,
    history_support: Dict,
    prompt_sections: Dict,
    candidates: Dict[str, object],
    pipeline,
) -> Dict[str, object]:
    """组装完整的 recall result payload

    Args:
        candidates: build_recall_candidates 的返回值
        pipeline: WangchuanPipeline 实例（用于访问辅助方法）

    Returns:
        完整的 recall payload dict
    """
    scope_route = str(query_preference_profile.get('scope_route') or 'memory')
    memory_items_list = list(memory_layer.get('items', []) or [])

    primary_evidence_boundary = pipeline._derive_primary_evidence_boundary(
        memory_layer, history_support, query_preference_profile,
        candidates['resonance_decision_view'],
    )
    joint_gating = pipeline._build_joint_gating_status(
        memory_layer, query_preference_profile, history_support,
        primary_evidence_boundary, candidates['resonance_decision_view'],
    )
    cross_topic_risk = pipeline._assess_cross_topic_risk(
        query, memory_layer, query_preference_profile,
    )

    decision_context_block = pipeline._format_resonance_decision_block(candidates['resonance_decision_view'])
    resource_recall_block = pipeline._format_resource_recall_block(candidates['resource_items'])
    skill_recall_block = pipeline._format_skill_recall_block(candidates['skill_items'])
    memory_recall_block = memory_layer.get('block', '')
    history_search_index = pipeline.history_search_index_status()

    if scope_route == 'resource':
        scope_context_block = resource_recall_block or memory_recall_block
    elif scope_route == 'skill':
        scope_context_block = skill_recall_block or memory_recall_block
    else:
        scope_context_block = memory_recall_block

    stable_prefix = "\n\n".join(part for part in [
        wakeup_pack,
        consciousness_context,
        response_strategy,
        decision_context_block,
        scope_context_block,
        getattr(assembly, 'stable_prefix', ''),
    ] if part)
    dynamic_suffix = "\n\n".join(part for part in [
        getattr(assembly, 'dynamic_suffix', ''),
    ] if part)
    context_parts = [part for part in [stable_prefix, dynamic_suffix] if part]
    final_context = "\n\n".join(context_parts)

    assembled_ids = node_ids[:3]
    recall_metrics = pipeline._observability.capture_recall_metrics(
        session_id=session_id,
        query=query,
        stable_prefix=stable_prefix,
        dynamic_suffix=dynamic_suffix,
        final_context=final_context,
        extra={
            'memory_route': memory_layer.get('route'),
            'scope_route': query_preference_profile.get('scope_route', 'memory'),
            'context_route': query_preference_profile.get('context_route', 'default'),
            'selected_sections': ','.join(prompt_sections.get('selected_sections', []) or []),
            'memory_items': len(memory_items_list),
            'history_support_items': history_support.get('support_items', 0),
            'decision_primary_role': candidates['resonance_decision_view'].get('summary', {}).get('primary_role', ''),
            'decision_primary_kind': candidates['resonance_decision_view'].get('summary', {}).get('primary_kind', ''),
            'decision_block_len': len(decision_context_block or ''),
            'scope_context_block_len': len(scope_context_block or ''),
            'retrieved_nodes': len(node_ids),
            'assembled_nodes': len(assembled_ids),
            'elapsed_ms': round(max((time.time() - started_at) * 1000, 0.0), 2),
        },
    )

    runtime_health = pipeline._record_runtime_health(
        session_id=session_id,
        recall_metrics=recall_metrics,
        resonance_decision_view=candidates['resonance_decision_view'],
        memory_route=str(memory_layer.get('route') or ''),
        scope_route=str(query_preference_profile.get('scope_route', 'memory')),
        degraded_runtime=None,
    )

    runtime_view = pipeline._observability.read_session_runtime_view(session_id)
    if runtime_view.get('status') == 'ok':
        runtime_snapshot = dict(runtime_view)
        runtime_snapshot.update({
            key: runtime_health.get(key)
            for key in [
                'current_mode', 'last_success_ts', 'success_rate', 'p95',
                'backlog', 'last_degrade_reason', 'consecutive_failures',
                'recovered_from_stage', 'degrade_stage', 'fallback_mode',
            ]
        })
        recall_metrics['session_runtime'] = runtime_snapshot
        pipeline._observability.state_store.append_metric(session_id, 'session_runtime_metrics', runtime_snapshot)

    return {
        'context': final_context,
        'stable_prefix': stable_prefix,
        'dynamic_suffix': dynamic_suffix,
        'recall_metrics': recall_metrics,
        'wakeup_pack': wakeup_pack,
        'consciousness_context': consciousness_context,
        'response_strategy': response_strategy,
        'execution_guidance': execution_guidance,
        'query_preference_profile': query_preference_profile,
        'scope_route': query_preference_profile.get('scope_route', 'memory'),
        'scope_route_profile': query_preference_profile.get('scope_route_profile', {}),
        'decision_context_block': decision_context_block,
        'scope_context_block': scope_context_block,
        'memory_recall_block': memory_recall_block,
        'resource_recall_block': resource_recall_block,
        'skill_recall_block': skill_recall_block,
        'seed_candidates': candidates['seed_candidates'],
        'resonance_candidates': candidates['resonance_candidates'],
        'graph_edge_resonance_candidates': candidates['graph_edge_resonance_candidates'],
        'pattern_candidates': candidates['pattern_candidates'],
        'fusion_candidates': candidates['fusion_candidates'],
        'resonance_decision_view': candidates['resonance_decision_view'],
        'resource_items': candidates['resource_items'],
        'skill_items': candidates['skill_items'],
        'context_route': query_preference_profile.get('context_route', 'default'),
        'selected_sections': prompt_sections.get('selected_sections', []),
        'memory_route': memory_layer.get('route'),
        'memory_reader': memory_layer.get('reader'),
        'memory_structured': memory_layer.get('structured', False),
        'memory_items': memory_items_list,
        'memory_metadata_summary': memory_layer.get('metadata_summary', {}),
        'history_support': history_support,
        'history_search_index': history_search_index,
        'primary_evidence_boundary': primary_evidence_boundary,
        'joint_gating': joint_gating,
        'cross_topic_risk': cross_topic_risk,
        'runtime_health': runtime_health,
        'retrieval_debug': retrieval_debug,
        'nodes': [
            {'node_id': r.node_id, 'name': r.name, 'type': r.node_type,
             'score': r.score, 'sources': r.sources}
            for r in results
        ],
        'assembly': assembly,
    }
