"""
Memory 排序评分模块 — 对召回的 memory items 进行打分、去重、平衡采样、排序

从 WangchuanPipeline 中提取的模块，依赖 QueryProfiler。
"""

import re
from typing import Dict, List

from .query_profiler import QueryProfiler


class MemoryRanker:
    """Memory 排序与评分器"""

    @staticmethod
    def memory_ranking_profile(query: str, route: str) -> Dict[str, object]:
        """从 query preference profile 转换为排序 profile"""
        profile = QueryProfiler.build_query_preference_profile(query)
        ranking_profile = {
            "query_text": str(profile.get("text", "") or ""),
            "context_route": str(profile.get("context_route", "default") or "default"),
            "premise_challenge": bool(profile.get("premise_challenge")),
            "preferred_layers": list(profile.get("preferred_layers", [])),
            "preferred_types": list(profile.get("preferred_types", [])),
            "preferred_domains": list(profile.get("preferred_domains", [])),
            "preferred_evidence": list(profile.get("preferred_evidence", [])),
            "topic_tokens": list(profile.get("topic_tokens", [])),
        }
        if route == "raw" and "raw" not in ranking_profile["preferred_layers"]:
            ranking_profile["preferred_layers"] = ["raw"] + ranking_profile["preferred_layers"]
        elif route == "scar" and "scar" not in ranking_profile["preferred_layers"]:
            ranking_profile["preferred_layers"] = ["scar"] + ranking_profile["preferred_layers"]
        return ranking_profile

    @staticmethod
    def has_checkpoint_query_intent(ranking_profile: Dict[str, object]) -> bool:
        """检测 checkpoint/handoff 意图"""
        context_route = str(ranking_profile.get("context_route") or "default").strip().lower()
        if context_route in {"checkpoint", "handoff"}:
            return True

        query_text = str(ranking_profile.get("query_text") or "").strip().lower()
        checkpoint_intent_tokens = {
            "下一步", "接下来", "checkpoint", "检查点", "任务状态", "当前步骤", "下一动作", "待做", "blocker", "阻塞",
            "交接", "handoff", "恢复", "resume", "续上", "接着干", "接上次", "从上次继续", "先做什么", "哪一步", "刚刚那个", "哪块", "主线", "那个呢", "先哪个",
        }
        if any(token.lower() in query_text for token in checkpoint_intent_tokens):
            return True

        short_ambiguous_queries = {
            "那这个呢", "那先那个呢", "先哪个", "先哪个呢", "哪个先", "这条主线呢"
        }
        if query_text in short_ambiguous_queries:
            return True

        if len(query_text) <= 8 and any(token in query_text for token in ["那个", "这个", "哪块", "哪步"]) and any(token in query_text for token in ["呢", "先", "接"]):
            return True

        correction_tokens = ["不是那个", "不对", "不是", "我说的是", "拉回", "别管"]
        if any(token in query_text for token in correction_tokens) and any(token in query_text for token in ["哪类", "哪块", "先哪个", "第一步", "恢复"]):
            return True

        return False

    @staticmethod
    def score_memory_item(item: Dict, ranking_profile: Dict) -> float:
        """对单个 memory item 打分"""
        base_score = float(item.get("score") or 0.0)
        bonus = 0.0

        preferred_layers = ranking_profile.get("preferred_layers", [])
        preferred_types = ranking_profile.get("preferred_types", [])
        preferred_domains = ranking_profile.get("preferred_domains", [])
        preferred_evidence = ranking_profile.get("preferred_evidence", [])
        topic_tokens = [str(token).lower() for token in ranking_profile.get("topic_tokens", []) if token]

        if item.get("source_layer") in preferred_layers:
            layer_index = preferred_layers.index(item.get("source_layer"))
            bonus += max(0.18, 0.34 - layer_index * 0.08)
        if item.get("memory_type") in preferred_types:
            type_index = preferred_types.index(item.get("memory_type"))
            bonus += max(0.1, 0.22 - type_index * 0.05)
        if item.get("subject_domain") in preferred_domains:
            domain_index = preferred_domains.index(item.get("subject_domain"))
            bonus += max(0.08, 0.2 - domain_index * 0.04)
        if item.get("evidence_level") in preferred_evidence:
            evidence_index = preferred_evidence.index(item.get("evidence_level"))
            bonus += max(0.08, 0.18 - evidence_index * 0.04)

        if item.get("user_explicit"):
            bonus += 0.08
        if item.get("source_anchor"):
            bonus += 0.05
        if item.get("turn_signature"):
            bonus += 0.04
        if item.get("promotion_reason"):
            bonus += 0.03
        if item.get("is_test_data"):
            bonus -= 0.45
        if not item.get("hot_memory_candidate") and item.get("source_layer") != "raw":
            bonus -= 0.08

        content = str(item.get("content", ""))
        lowered_content = content.lower()
        if len(content) > 320:
            bonus -= 0.05
        if topic_tokens:
            matched = [token for token in topic_tokens if token in lowered_content]
            bonus += min(len(matched) * 0.06, 0.18)

        item_type = str(item.get("memory_type") or "").strip().lower()
        if item_type in {"checkpoint", "handoff"}:
            has_checkpoint_intent = MemoryRanker.has_checkpoint_query_intent(ranking_profile)
            if not has_checkpoint_intent:
                bonus -= 0.28

        # 低质量/模板污染 preference 旧记忆降权
        if item.get("memory_type") == "preference":
            junk_markers = [
                "→ preference",
                "欢/偏好/倾向",
                "用户喜欢欢/偏好/倾向",
                "这样我就能记住你的口味偏好了",
            ]
            if any(marker.lower() in lowered_content for marker in junk_markers):
                bonus -= 0.38

        # 用户偏好类 query 下，conversation 命中只作为兜底证据
        if item.get("memory_type") == "conversation":
            if "user" in preferred_domains or "preference" in preferred_types:
                bonus -= 0.42
                if item.get("source_layer") == "raw":
                    bonus -= 0.08
                if len(content) > 220:
                    bonus -= 0.06

        return base_score + bonus

    @staticmethod
    def dedupe_memory_items(items: List[Dict]) -> List[Dict]:
        """去重"""
        deduped: List[Dict] = []
        seen_keys = set()

        for item in items:
            dedupe_key = str(item.get("dedupe_key") or "").strip().lower()
            conflict_group = str(item.get("conflict_group") or "").strip().lower()
            content_key = re.sub(r"\s+", "", str(item.get("content") or "").strip().lower())[:160]

            unique_key = dedupe_key or (f"{conflict_group}:{content_key}" if conflict_group and content_key else content_key)
            if not unique_key:
                unique_key = str(item.get("memory_id") or "")

            if unique_key in seen_keys:
                continue
            seen_keys.add(unique_key)
            deduped.append(item)

        return deduped

    @staticmethod
    def is_low_quality_preference(item: Dict) -> bool:
        """过滤低质量偏好项"""
        if item.get("memory_type") != "preference":
            return False
        lowered_content = str(item.get("content", "")).strip().lower()
        junk_markers = [
            "→ preference",
            "欢/偏好/倾向",
            "用户喜欢欢/偏好/倾向",
            "这样我就能记住你的口味偏好了",
        ]
        return any(marker.lower() in lowered_content for marker in junk_markers)

    @staticmethod
    def apply_memory_type_balance(items: List[Dict], ranking_profile: Dict, top_k: int | None = None) -> List[Dict]:
        """按类型平衡采样"""
        if not items:
            return items

        effective_top_k = top_k or len(items)
        preferred_types = list(ranking_profile.get("preferred_types", []) or [])
        if not preferred_types:
            filtered = [item for item in items if not MemoryRanker.is_low_quality_preference(item)]
            return (filtered or items)[:effective_top_k]

        clean_items = [item for item in items if not MemoryRanker.is_low_quality_preference(item)]
        candidate_items = clean_items or items

        selected: List[Dict] = []
        used_ids = set()

        # 类型配额保底：每个偏好类型先挑 1 条
        for memory_type in preferred_types:
            for item in candidate_items:
                item_id = item.get("memory_id")
                if item_id in used_ids:
                    continue
                if item.get("memory_type") == memory_type:
                    selected.append(item)
                    used_ids.add(item_id)
                    break
                # preference 配额允许 identity 兜底
                if memory_type == "preference" and item.get("memory_type") == "identity":
                    selected.append(item)
                    used_ids.add(item_id)
                    break
            if len(selected) >= effective_top_k:
                return selected[:effective_top_k]

        for item in candidate_items:
            item_id = item.get("memory_id")
            if item_id in used_ids:
                continue
            selected.append(item)
            used_ids.add(item_id)
            if len(selected) >= effective_top_k:
                break

        return (selected or candidate_items)[:effective_top_k]

    @classmethod
    def rank(cls, query: str, route: str, items: List[Dict]) -> List[Dict]:
        """综合排序入口：profile → score → dedupe → balance → output"""
        ranking_profile = cls.memory_ranking_profile(query, route)
        ranked_items = []
        for item in items:
            new_item = dict(item)
            new_item["ranking_score"] = cls.score_memory_item(new_item, ranking_profile)
            ranked_items.append(new_item)
        ranked_items.sort(key=lambda item: item.get("ranking_score", 0.0), reverse=True)
        deduped = cls.dedupe_memory_items(ranked_items)
        filtered = [
            item
            for item in deduped
            if not (
                str(item.get("memory_type") or "").strip().lower() in {"checkpoint", "handoff"}
                and str(ranking_profile.get("context_route") or "default").strip().lower() == "default"
                and not cls.has_checkpoint_query_intent(ranking_profile)
            )
        ]
        return cls.apply_memory_type_balance(filtered, ranking_profile)
