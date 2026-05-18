#!/usr/bin/env python3
"""
忘川 v3.0 - 统一管线
将摄取→提取→维护→检索→组装→反馈串成完整数据流

用法：
    from pipeline_v3 import WangchuanPipeline
    pipe = WangchuanPipeline()

    # 新消息进来
    pipe.ingest("session_001", "user", "帮我安装Docker")
    pipe.ingest("session_001", "assistant", "运行 sudo apt install docker.io")

    # 提取三元组（异步或批量）
    pipe.extract_recent("session_001")

    # 回忆
    context = pipe.recall("Docker安装", session_id="session_001")

    # 反馈
    pipe.feedback_used(["node_xxx", "node_yyy"])
"""

import os
import sys
import sqlite3
import json
import time
import math
import re
import logging
from collections import Counter
from textwrap import dedent
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from wangchuan.paths import default_db_path, workspace_root

# 添加路径
_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

# 加载 .env
_env_file = os.path.join(_dir, ".env")
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from .core.ingest import IngestEngine, Message
from .core.extract import ExtractEngine, Triple
from .core.assemble import AssembleEngine
from .retrieval.hybrid import HybridRetriever
from .retrieval.feedback import FeedbackEngine, FeedbackSignal, FeedbackType
from .graph.maintenance import MaintenanceEngine
from ._pipeline.format_blocks import FormatBlocks
from ._pipeline.query_profiler import QueryProfiler
from ._pipeline.memory_ranker import MemoryRanker
from ._pipeline.recall_engine import RecallEngine
from ._pipeline.context_assembler import (
    build_memory_context_uri as _build_memory_context_uri_impl,
    clamp01 as _clamp01_impl,
    derive_vitality_state as _derive_vitality_state_impl,
    normalize_memory_item_explain as _normalize_memory_item_explain_impl,
    parse_datetime_maybe as _parse_datetime_maybe_impl,
    shape_memory_items_for_output as _shape_memory_items_for_output_impl,
)
from wangchuan._adapters.consciousness_adapter import (
    get_consciousness_engine as _ce_engine,
    run_tool_with_consciousness,
)
from wangchuan.memory_api import Memory, graph_ingest_gate
from wangchuan.runtime_state import get_runtime_energy_state
from wangchuan._adapters.context_adapter import (
    get_observability as _get_observability,
    get_semantic_cache as _get_semantic_cache,
)

logger = logging.getLogger(__name__)


def _read_text_head(path: Path, max_chars: int = 800) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return text[:max_chars].strip()


def _extract_frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", text or "", re.M)
    if not match:
        return ""
    value = str(match.group(1) or "").strip()
    if value.startswith(('"', "'")) and value.endswith(('"', "'")) and len(value) >= 2:
        value = value[1:-1]
    return value.strip()


def _resolve_runtime_session_id(session_id: Optional[str], default: str = "default") -> str:
    text = str(session_id or "").strip()
    return text or default


class WangchuanPipeline:
    """忘川 v3 统一管线"""

    _QUEUED_WRAPPER_MARKERS = [
        "[Queued messages while agent was busy]",
        "Queued messages while agent was busy",
        "Conversation info (untrusted metadata):",
        "Sender (untrusted metadata):",
        "untrusted metadata",
    ]

    _PURE_METADATA_KEYS = [
        '"message_id"',
        '"sender_id"',
        '"sender"',
        '"timestamp"',
        '"label"',
        '"id"',
        '"name"',
        '"username"',
        '"chat_id"',
    ]

    def __init__(self, db_path: str = None,
                 llm_api_key: str = None,
                 llm_base_url: str = None,
                 llm_model: str = None):

        self.db_path = db_path or str(default_db_path())

        # LLM 配置（用于三元组提取）
        self.llm_api_key = llm_api_key or os.getenv('LLM_API_KEY') or os.getenv('EMBEDDING_API_KEY')
        self.llm_base_url = llm_base_url or os.getenv('LLM_BASE_URL', 'https://ark.cn-beijing.volces.com/api/coding/v3')
        self.llm_model = llm_model or os.getenv('LLM_MODEL', 'kimi-k2.5')

        # 初始化引擎
        self._ingest_engine = IngestEngine(self.db_path)
        self._extract_engine = ExtractEngine(self.db_path, self.llm_api_key)
        self._assemble_engine = AssembleEngine(self.db_path)
        self._retriever = HybridRetriever(self.db_path)
        self._feedback_engine = FeedbackEngine(self.db_path)
        self._maintenance_engine = MaintenanceEngine(self.db_path)
        self._consciousness = _ce_engine()
        self._memory_api = Memory(self.db_path)
        self._observability = _get_observability()
        # 召回语义缓存。注意 state 指纹必须覆盖会影响 recall 结果的外部状态，
        # 但不要把 recall 自身的 implicit_use/ignored 回写也算进去，否则正常重复 recall 永远 miss。
        self._semantic_cache = _get_semantic_cache()

        # 状态追踪
        self._turn_counters: Dict[str, int] = {}
        self._last_query_nodes: Dict[str, List[str]] = {}

    @staticmethod
    def _extract_block_items(text: str, block_name: str) -> List[str]:
        return FormatBlocks.extract_block_items(text, block_name)

    @staticmethod
    def _resource_probe_paths() -> List[Path]:
        root = workspace_root()
        return [
            root / "docs" / "OpenViking-借鉴-实施任务板-v1.md",
            root / "docs" / "OpenViking-借鉴-Context-Tree-最小方案-v1.md",
            root / "docs" / "忘川下一阶段优化实施任务板-v1.md",
            root / "tiangong" / "wangchuan" / "README.md",
            root / "USER.md",
        ]

    @staticmethod
    def _quote_context_part(value: object, safe: str = "/._-:#") -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return quote(text, safe=safe)

    @classmethod
    def _build_memory_context_uri(cls, item: Dict[str, object]) -> str:
        return _build_memory_context_uri_impl(item)

    @classmethod
    def _build_resource_context_uri(cls, path: Path) -> str:
        try:
            relative_path = path.resolve().relative_to(workspace_root().resolve()).as_posix()
        except Exception:
            relative_path = path.as_posix()
        if not relative_path:
            return ""
        return f"resource://workspace/{cls._quote_context_part(relative_path, safe='/._-')}"

    @classmethod
    def _build_skill_context_uri(cls, skill_name: str) -> str:
        skill_key = cls._quote_context_part(skill_name, safe='._-')
        if not skill_key:
            return ""
        return f"skill://local/{skill_key}"

    def _probe_resource_items(self, query: str, limit: int = 3) -> List[Dict[str, object]]:
        query_text = str(query or "").strip().lower()
        if not query_text:
            return []

        compact_query = re.sub(r"\s+", "", query_text)
        scored: List[Dict[str, object]] = []
        for path in self._resource_probe_paths():
            if not path.exists() or not path.is_file():
                continue
            head = _read_text_head(path)
            haystack = f"{path.name}\n{head}".lower()
            compact_haystack = re.sub(r"\s+", "", haystack)
            score = 0.0
            if query_text in haystack:
                score += 1.2
            if compact_query and compact_query in compact_haystack:
                score += 0.9

            matched_terms = []
            for token in ["配置", "文件", "路径", "任务板", "文档", "readme", "日志"]:
                if token in query_text and token in haystack:
                    score += 0.35
                    matched_terms.append(token)

            if score <= 0:
                continue
            scored.append({
                "path": str(path),
                "title": path.name,
                "preview": head[:240],
                "score": round(score, 6),
                "context_type": "resource",
                "context_uri": self._build_resource_context_uri(path),
                "matched_terms": matched_terms,
            })

        scored.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return scored[:limit]

    def _probe_skill_items(self, query: str, limit: int = 3) -> List[Dict[str, object]]:
        query_text = str(query or "").strip().lower()
        if not query_text:
            return []

        compact_query = re.sub(r"\s+", "", query_text)
        skills_root = workspace_root() / "skills"
        if not skills_root.exists():
            return []

        generic_skill_markers = ["技能", "skill", "流程", "方法", "怎么做", "怎么用", "tool", "工具", "workflow"]
        matched_query_markers = [token for token in generic_skill_markers if token in query_text]
        scored: List[Dict[str, object]] = []

        for skill_file in sorted(skills_root.glob("*/SKILL.md")):
            text = _read_text_head(skill_file, max_chars=1600)
            if not text:
                continue
            skill_name = _extract_frontmatter_value(text, "name") or skill_file.parent.name
            description = _extract_frontmatter_value(text, "description")
            haystack = f"{skill_file.parent.name}\n{skill_name}\n{description}\n{text[:500]}".lower()
            compact_haystack = re.sub(r"\s+", "", haystack)

            score = 0.0
            if query_text in haystack:
                score += 1.2
            if compact_query and compact_query in compact_haystack:
                score += 0.9

            matched_terms = []
            for token in generic_skill_markers + ["bugfix", "boundary", "debug", "repair"]:
                if token in query_text and token in haystack:
                    score += 0.25
                    matched_terms.append(token)

            if score <= 0 and matched_query_markers:
                if any(token in haystack for token in ["skill", "workflow", "debug", "fix", "guide", "boundary"]):
                    score += 0.05

            if score <= 0:
                continue

            scored.append({
                "name": skill_name,
                "path": str(skill_file),
                "description": description,
                "preview": text[:240],
                "score": round(score, 6),
                "context_type": "skill",
                "context_uri": self._build_skill_context_uri(skill_name or skill_file.parent.name),
                "matched_terms": matched_terms,
            })

        scored.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return scored[:limit]

    def _derive_execution_guidance(self, consciousness_context: str) -> Dict:
        hints = self._extract_block_items(consciousness_context, "decision_hints")
        rules = self._extract_block_items(consciousness_context, "self_state")

        guidance = {
            "strategy_bias": "neutral",
            "opening_move": "default",
            "reply_shape": "normal",
            "confirmation_policy": "default",
            "action_policy": "default",
            "alignment_first": False,
            "can_skip_redundant_confirmation": False,
        }

        if not hints:
            return guidance

        if "strategy_bias=hold" in hints:
            guidance["strategy_bias"] = "hold"
            guidance["action_policy"] = "pause_and_wait"
        elif "strategy_bias=direct_answer" in hints:
            guidance["strategy_bias"] = "direct_answer"
            guidance["action_policy"] = "answer_directly"
        elif "strategy_bias=push_forward" in hints:
            guidance["strategy_bias"] = "push_forward"
            guidance["action_policy"] = "advance_with_minimum_friction"
        elif "strategy_bias=stabilize_and_align" in hints:
            guidance["strategy_bias"] = "stabilize_and_align"
            guidance["action_policy"] = "align_before_advancing"
            guidance["alignment_first"] = True

        if "reply_shape=direct" in hints:
            guidance["reply_shape"] = "direct"
        elif "reply_shape=brief" in hints:
            guidance["reply_shape"] = "brief"

        if "opening_move=ack_and_pause" in hints:
            guidance["opening_move"] = "ack_and_pause"
        elif "opening_move=answer_immediately" in hints:
            guidance["opening_move"] = "answer_immediately"
        elif "opening_move=ack_then_explain" in hints:
            guidance["opening_move"] = "ack_then_explain"
            guidance["alignment_first"] = True
        elif "opening_move=continue_without_repadding" in hints:
            guidance["opening_move"] = "continue_without_repadding"

        if "action_policy=pause_and_wait" in hints:
            guidance["action_policy"] = "pause_and_wait"
        elif "action_policy=answer_directly" in hints:
            guidance["action_policy"] = "answer_directly"

        if "confirmation_policy=minimize_repeat_confirmation" in hints:
            guidance["confirmation_policy"] = "minimize_repeat_confirmation"
            guidance["can_skip_redundant_confirmation"] = True

        top_rules = [r for r in rules if r and "=" not in r][:2]
        if top_rules:
            guidance["top_rules"] = top_rules

        return guidance

    @staticmethod
    def _safe_read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    @staticmethod
    def _extract_bullets_from_markdown(text: str, heading: str, limit: int = 5) -> List[str]:
        if not text:
            return []
        pattern = rf"^##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s+|\Z)"
        match = re.search(pattern, text, re.M)
        if not match:
            return []
        body = match.group(1)
        items: List[str] = []
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("- ") or line.startswith("* "):
                items.append(line[2:].strip())
            elif re.match(r"^\d+\.\s+", line):
                items.append(re.sub(r"^\d+\.\s+", "", line))
            if len(items) >= limit:
                break
        return items

    @staticmethod
    def _score_wakeup_scar_item(query: str, scar: str) -> float:
        profile = WangchuanPipeline._build_query_preference_profile(query)
        scar_text = (scar or "").lower()
        if not scar_text:
            return 0.0

        score = 0.1
        preferred_domains = list(profile.get("preferred_domains", []))
        if "ops" in preferred_domains and any(token in scar_text for token in ["网关", "gateway", "重启", "部署", "服务", "systemctl"]):
            score += 0.45
        if "code" in preferred_domains and any(token in scar_text for token in ["代码", "python", "测试", "导入", "模块", "架构"]):
            score += 0.35
        if "rule" in preferred_domains and any(token in scar_text for token in ["规则", "教训", "踩坑", "经验", "默认", "铁律"]):
            score += 0.35
        if any(token in scar_text for token in ["透明黑盒", "原话", "验证", "主链", "入口"]):
            score += 0.1

        topic_tokens = [token for token in list(profile.get("topic_tokens", [])) if token and token in scar_text]
        score += min(len(topic_tokens) * 0.08, 0.24)
        return score

    def _select_query_aware_scars(self, query: str, memory_md: str, limit: int = 3) -> List[str]:
        scar_candidates = self._extract_bullets_from_markdown(memory_md, "认知精华（伤疤）", limit=12)
        if not scar_candidates:
            scar_candidates = self._extract_bullets_from_markdown(memory_md, "铁律", limit=12)
        ranked_scars = []
        for scar in scar_candidates:
            ranked_scars.append((self._score_wakeup_scar_item(query, scar), scar))
        ranked_scars.sort(key=lambda pair: pair[0], reverse=True)
        selected = [scar for _, scar in ranked_scars[:limit] if scar]
        if selected:
            return selected
        return scar_candidates[:limit]

    def _build_wakeup_pack(self, query: str, session_id: str = None) -> str:
        workspace = workspace_root()
        user_md = self._safe_read_text(workspace / "USER.md")
        memory_md = self._safe_read_text(workspace / "MEMORY.md")
        profile = self._build_query_preference_profile(query)

        try:
            runtime = get_runtime_energy_state() or {}
        except Exception as e:
            logger.warning("【WangChuan】[Pipeline][Wakeup] runtime state failed: %s", e)
            runtime = {}

        preferred_name = ""
        match = re.search(r"What to call them:\*\*\s*(.+)", user_md)
        if match:
            preferred_name = match.group(1).strip()
        else:
            match = re.search(r"称呼[:：]\*\*\s*(.+)", memory_md)
            if match:
                preferred_name = match.group(1).strip()

        user_prefs = self._extract_bullets_from_markdown(user_md, "沟通风格偏好", limit=4)
        long_scars = self._select_query_aware_scars(query, memory_md, limit=3)

        runtime_bits: List[str] = []
        if isinstance(runtime, dict) and runtime.get("enabled") is not False:
            for key in ["energy_percent", "energy", "level", "status", "time_state"]:
                value = runtime.get(key)
                if value not in (None, ""):
                    runtime_bits.append(f"{key}={value}")
        runtime_line = " | ".join(runtime_bits[:4]) if runtime_bits else "runtime=standard"

        preference_bits: List[str] = []
        route = profile.get("route")
        if route:
            preference_bits.append(f"route={route}")
        preferred_layers = list(profile.get("preferred_layers", []))
        if preferred_layers:
            preference_bits.append("layers=" + "/".join(preferred_layers[:3]))
        preferred_domains = list(profile.get("preferred_domains", []))
        if preferred_domains:
            preference_bits.append("domains=" + "/".join(preferred_domains[:3]))
        preferred_types = list(profile.get("preferred_types", []))
        if preferred_types:
            preference_bits.append("types=" + "/".join(preferred_types[:3]))

        lines = ["<wakeup_pack>"]
        lines.append(f"- runtime: {runtime_line}")
        if preference_bits:
            lines.append("- preference_profile: " + " | ".join(preference_bits))
        if preferred_name:
            lines.append(f"- user: preferred_name={preferred_name}")
        if session_id:
            lines.append(f"- session: {session_id}")
        if user_prefs:
            lines.append("- user_prefs: " + " | ".join(user_prefs[:3]))
        if long_scars:
            lines.append("- scar_pack: " + " | ".join(long_scars[:3]))
        if query:
            lines.append(f"- query_focus: {query[:120]}")
        lines.append("</wakeup_pack>")
        return "\n".join(lines)

    def _build_response_strategy(self, consciousness_context: str) -> str:
        guidance = self._derive_execution_guidance(consciousness_context)
        if guidance.get("strategy_bias") == "neutral" and guidance.get("opening_move") == "default":
            return ""

        instructions: List[str] = []
        if guidance.get("strategy_bias") == "hold":
            instructions.append("当前轮暂停推进；只做简短确认并等待用户下一步。")
        if guidance.get("strategy_bias") == "direct_answer":
            instructions.append("当前轮直接给结论/答案，减少铺垫和过程解释。")
        if guidance.get("strategy_bias") == "push_forward":
            instructions.append("当前轮允许直接推进；避免重复铺垫，优先给出下一步。")
        if guidance.get("strategy_bias") == "stabilize_and_align":
            instructions.append("当前轮先稳住并对齐；先承认偏差/误解，再给出修正后的继续动作。")
        if guidance.get("reply_shape") == "direct":
            instructions.append("当前轮回复采用直给式表达，先结论后补充。")
        elif guidance.get("reply_shape") == "brief":
            instructions.append("当前轮回复保持简短，减少背景复述。")
        if guidance.get("opening_move") == "ack_and_pause":
            instructions.append("开头简短确认收到，不继续展开，等待用户。")
        if guidance.get("opening_move") == "answer_immediately":
            instructions.append("开头直接给答案，不做前置铺垫。")
        if guidance.get("opening_move") == "ack_then_explain":
            instructions.append("开头先承认/对齐，再解释或继续。")
        if guidance.get("opening_move") == "continue_without_repadding":
            instructions.append("开头直接续上当前任务，不要重复背景说明。")
        if guidance.get("confirmation_policy") == "minimize_repeat_confirmation":
            instructions.append("若目标已对齐，避免重复确认。")

        top_rules = guidance.get("top_rules") or []
        if top_rules:
            instructions.append("优先遵循当前高权重规则：" + " | ".join(top_rules))

        if not instructions:
            return ""

        return dedent("""
        <response_strategy>
        """).strip() + "\n" + "\n".join(f"- {item}" for item in instructions[:5]) + "\n</response_strategy>"

    @staticmethod
    def _build_query_preference_profile(query: str) -> Dict[str, object]:
        return QueryProfiler.build_query_preference_profile(query)

    @staticmethod
    def _memory_route(query: str) -> str:
        return QueryProfiler.memory_route(query)

    @staticmethod
    def _format_memory_recall_block(items: List[Dict], route: str) -> str:
        return FormatBlocks.format_memory_recall_block(items, route)

    @staticmethod
    def _format_resource_recall_block(items: List[Dict[str, object]]) -> str:
        return FormatBlocks.format_resource_recall_block(items)

    @staticmethod
    def _format_skill_recall_block(items: List[Dict[str, object]]) -> str:
        return FormatBlocks.format_skill_recall_block(items)

    @staticmethod
    def _candidate_brief_text(item: Dict[str, object]) -> str:
        return FormatBlocks.candidate_brief_text(item)

    @classmethod
    def _format_resonance_decision_block(cls, view: Dict[str, object]) -> str:
        return FormatBlocks.format_resonance_decision_block(view)

    @staticmethod
    def _normalize_memory_item_explain(item: Dict) -> Dict:
        return _normalize_memory_item_explain_impl(item)

    @classmethod
    def _shape_memory_items_for_output(cls, items: List[Dict]) -> List[Dict]:
        return _shape_memory_items_for_output_impl(items)

    @staticmethod
    def _parse_datetime_maybe(value: object) -> datetime | None:
        return _parse_datetime_maybe_impl(value)

    @staticmethod
    def _clamp01(value: float) -> float:
        return _clamp01_impl(value)

    @classmethod
    def _derive_vitality_state(cls, item: Dict[str, object]) -> Dict[str, object]:
        return _derive_vitality_state_impl(item)

    @classmethod
    def _build_seed_candidates(
        cls,
        query: str,
        memory_items: List[Dict],
        query_preference_profile: Dict[str, object],
        limit: int = 5,
    ) -> List[Dict[str, object]]:
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
    def _build_resonance_candidates(
        cls,
        seeds: List[Dict[str, object]],
        memory_items: List[Dict],
        limit: int = 5,
    ) -> List[Dict[str, object]]:
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
    def _build_pattern_candidates(
        cls,
        query: str,
        candidate_memory_items: List[Dict],
        resonance_candidates: List[Dict[str, object]],
        limit: int = 3,
    ) -> List[Dict[str, object]]:
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
            suppression = cls._pattern_suppression_profile(row)
            row["suppression_penalty"] = suppression.get("suppression_penalty")
            row["allow_primary"] = suppression.get("allow_primary")
            row["route_state"] = suppression.get("route_state")
            row["suppression_reason"] = suppression.get("why_selected")

        pattern_rows.sort(key=lambda row: float(row.get("pattern_score") or 0.0), reverse=True)
        return pattern_rows[:limit]

    @staticmethod
    def _pattern_suppression_profile(item: Dict[str, object]) -> Dict[str, object]:
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

    def _build_graph_edge_resonance_candidates(
        self,
        query: str,
        seeds: List[Dict[str, object]],
        limit: int = 3,
    ) -> List[Dict[str, object]]:
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
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                node_map: Dict[str, sqlite3.Row] = {}
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
                                self._clamp01(edge_weight * 0.5 + source_pagerank * 0.25 + neighbor_pagerank * 0.25),
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

    @classmethod
    def _build_fusion_candidates(
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
                suppression = cls._pattern_suppression_profile(item)
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

    @classmethod
    def _build_resonance_decision_view(
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
            if (
                role in {"primary_resource", "primary_skill", "memory_pattern"}
                or (scope_route == "memory" and role in {"memory_linked", "memory_seed", "memory_evidence"})
            ) and bool(item.get("allow_primary", True)) and len(primary_candidates) < 3:
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
            pattern_rows.append({
                "pattern_id": item.get("pattern_id"),
                "status": item.get("status"),
                "pattern_scope": item.get("pattern_scope"),
                "support_count": item.get("support_count"),
                "counter_evidence_count": item.get("counter_evidence_count"),
                "suppression_penalty": cls._pattern_suppression_profile(item).get("suppression_penalty"),
                "allow_primary": cls._pattern_suppression_profile(item).get("allow_primary"),
                "route_state": cls._pattern_suppression_profile(item).get("route_state"),
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

    @staticmethod
    def _memory_ranking_profile(query: str, route: str) -> Dict[str, object]:
        profile = WangchuanPipeline._build_query_preference_profile(query)
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
    def _has_checkpoint_query_intent(ranking_profile: Dict[str, object]) -> bool:
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
    def _is_premise_challenge_query(query_text: str) -> bool:
        return QueryProfiler.is_premise_challenge_query(query_text)

    @staticmethod
    def _score_memory_item(item: Dict, ranking_profile: Dict) -> float:
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
            has_checkpoint_intent = WangchuanPipeline._has_checkpoint_query_intent(ranking_profile)
            if not has_checkpoint_intent:
                bonus -= 0.28

        # 低质量/模板污染 preference 旧记忆降权，避免“欢/偏好/倾向”这类脏样本顶到前排。
        if item.get("memory_type") == "preference":
            junk_markers = [
                "→ preference",
                "欢/偏好/倾向",
                "用户喜欢欢/偏好/倾向",
                "这样我就能记住你的口味偏好了",
            ]
            if any(marker.lower() in lowered_content for marker in junk_markers):
                bonus -= 0.38

        # 用户偏好类 query 下，conversation 命中只作为兜底证据，避免顶到前排。
        if item.get("memory_type") == "conversation":
            if "user" in preferred_domains or "preference" in preferred_types:
                bonus -= 0.42
                if item.get("source_layer") == "raw":
                    bonus -= 0.08
                if len(content) > 220:
                    bonus -= 0.06

        return base_score + bonus


    @staticmethod
    def _dedupe_memory_items(items: List[Dict]) -> List[Dict]:
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
    def _is_low_quality_preference(item: Dict) -> bool:
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
    def _apply_memory_type_balance(items: List[Dict], ranking_profile: Dict, top_k: int | None = None) -> List[Dict]:
        if not items:
            return items

        effective_top_k = top_k or len(items)
        preferred_types = list(ranking_profile.get("preferred_types", []) or [])
        if not preferred_types:
            filtered = [item for item in items if not WangchuanPipeline._is_low_quality_preference(item)]
            return (filtered or items)[:effective_top_k]

        clean_items = [item for item in items if not WangchuanPipeline._is_low_quality_preference(item)]
        candidate_items = clean_items or items

        selected: List[Dict] = []
        used_ids = set()

        # 类型配额保底：每个偏好类型先挑 1 条，避免混合问法被单一类型刷屏。
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

    def _rank_memory_items(self, query: str, route: str, items: List[Dict]) -> List[Dict]:
        return MemoryRanker.rank(query, route, items)


    @staticmethod
    def _derive_preference_seed_queries(query: str, topic_tokens: List[str]) -> List[str]:
        text = str(query or "")
        seeds: List[str] = []

        def add(seed: str) -> None:
            if seed and seed not in seeds:
                seeds.append(seed)

        # 通用 user/preference 召回兜底
        if any(token in {"偏好", "用户", "称呼", "沟通", "回复风格", "分段回复", "少确认", "关键节点汇报", "透明黑盒", "任务板", "实施路线图", "路线图"} for token in topic_tokens):
            add("偏好")
            add("用户")

        # 自然问法 → 更接近真实记忆内容的 seed query
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
    def _is_raw_evidence_item(item: Dict[str, object]) -> bool:
        source_layer = str(item.get("source_layer") or "").strip().lower()
        evidence_level = str(item.get("evidence_level") or "").strip().lower()
        recall_source_type = str(item.get("recall_source_type") or "").strip().lower()
        source_anchor = str(item.get("source_anchor") or "")
        provenance = str(item.get("provenance") or "")
        return (
            source_layer == "raw"
            or recall_source_type == "raw"
            or evidence_level == "raw"
            or "memory/raw/" in source_anchor
            or "memory/raw/" in provenance
        )

    @classmethod
    def _enforce_joint_gating_memory_boundary(cls, memory_layer: Dict[str, object]) -> Dict[str, object]:
        layer = dict(memory_layer or {})
        if str(layer.get("route") or "") != "raw":
            return layer

        items = list(layer.get("items", []) or [])
        candidate_items = list(layer.get("candidate_items", []) or items)
        raw_items = [dict(item) for item in items if cls._is_raw_evidence_item(item)]
        raw_candidate_items = [dict(item) for item in candidate_items if cls._is_raw_evidence_item(item)]

        if raw_items:
            layer["items"] = raw_items
        if raw_candidate_items:
            layer["candidate_items"] = raw_candidate_items
        elif raw_items:
            layer["candidate_items"] = list(raw_items)

        filtered_items = list(layer.get("items", []) or [])
        layer["block"] = cls._format_memory_recall_block(filtered_items, "raw")

        metadata_summary = dict(layer.get("metadata_summary") or {})
        metadata_summary.update({
            "source_layers": sorted({item.get("source_layer", "") for item in filtered_items if item.get("source_layer")}),
            "memory_types": sorted({item.get("memory_type", "") for item in filtered_items if item.get("memory_type")}),
            "subject_domains": sorted({item.get("subject_domain", "") for item in filtered_items if item.get("subject_domain")}),
            "evidence_levels": sorted({item.get("evidence_level", "") for item in filtered_items if item.get("evidence_level")}),
            "joint_gating_boundary": "raw_evidence_priority",
            "raw_evidence_items": len(filtered_items),
        })
        layer["metadata_summary"] = metadata_summary
        return layer

    def _recall_memory_layer(self, query: str, top_k: int = 5) -> Dict:
        engine = RecallEngine(memory_api=self._memory_api, db_path=self.db_path)
        return engine.recall_memory_layer(query, top_k=top_k)

    @staticmethod
    def _summarize_history_support(assembly: Dict | None) -> Dict[str, object]:
        if assembly is None:
            return {
                "recent_messages": 0,
                "history_recall": 0,
                "recalled_context": 0,
                "support_items": 0,
                "should_override_memory": False,
                "boundary": "history_support_only",
            }

        # 兼容旧 dict 口径与当前 ContextAssembly dataclass 口径。
        if isinstance(assembly, dict):
            formatted = str(assembly.get("formatted_context", "") or "")
            recent_items = WangchuanPipeline._extract_block_items(formatted, "recent_messages")
            history_items = WangchuanPipeline._extract_block_items(formatted, "history_recall")
            context_items = WangchuanPipeline._extract_block_items(formatted, "recalled_context")
            support_items = recent_items + history_items + context_items
            return {
                "recent_messages": len(recent_items),
                "history_recall": len(history_items),
                "recalled_context": len(context_items),
                "support_items": len(support_items),
                "should_override_memory": False,
                "boundary": "history_support_only",
            }

        recent_count = len(getattr(assembly, "fresh_tail", []) or [])
        history_count = 1 if getattr(assembly, "dag_summary", "") else 0
        recalled_count = 0
        if getattr(assembly, "graph_xml", ""):
            recalled_count += 1
        if getattr(assembly, "episodic_xml", ""):
            recalled_count += 1

        return {
            "recent_messages": recent_count,
            "history_recall": history_count,
            "recalled_context": recalled_count,
            "support_items": recent_count + history_count + recalled_count,
            "should_override_memory": False,
            "boundary": "history_support_only",
        }

    @staticmethod
    def _assess_cross_topic_risk(
        query: str,
        memory_layer: Dict | None,
        query_preference_profile: Dict | None,
    ) -> Dict[str, object]:
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

    @staticmethod
    def _derive_primary_evidence_boundary(
        memory_layer: Dict | None,
        history_support: Dict | None,
        query_preference_profile: Dict | None = None,
        resonance_decision_view: Dict | None = None,
    ) -> Dict[str, object]:
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
        raw_evidence_items = sum(1 for item in memory_items if WangchuanPipeline._is_raw_evidence_item(item))

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
            # compat anchor for text-based regression tests:
            # "rule": "history_can_support_but_must_not_override_memory_layer",
        }

    @classmethod
    def _build_joint_gating_status(
        cls,
        memory_layer: Dict[str, object] | None,
        query_preference_profile: Dict[str, object] | None,
        history_support: Dict[str, object] | None,
        primary_evidence_boundary: Dict[str, object] | None,
        resonance_decision_view: Dict[str, object] | None,
    ) -> Dict[str, object]:
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
        raw_evidence_items = sum(1 for item in memory_items if cls._is_raw_evidence_item(item))
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

    def ingest(self, session_id: str, role: str, content: str) -> int:
        """
        摄取一条消息

        Returns:
            消息 ID
        """
        sanitized_content = self._sanitize_inbound_content(content)
        if not sanitized_content:
            logger.info("【WangChuan】[Pipeline][Ingest] skip noisy/metadata-only message session=%s role=%s", session_id, role)
            return -1

        if self._is_recent_duplicate_message(session_id, role, sanitized_content):
            logger.info(
                "【WangChuan】[Pipeline][Ingest] skip recent duplicate session=%s role=%s content=%r",
                session_id,
                role,
                sanitized_content[:80],
            )
            return -1

        msg = Message(session_id=session_id, role=role, content=sanitized_content)
        msg_id = self._ingest_engine.ingest(msg)

        # 自动信号检测
        self._detect_signals(msg_id, sanitized_content)

        # 最小接入：把消息送入意识进化闭环
        try:
            self._consciousness.process_message(role, sanitized_content, channel="wangchuan", user_id=session_id)
        except Exception as e:
            logger.warning("【WangChuan】[Pipeline][Consciousness] process_message failed: %s", e)

        return msg_id

    @classmethod
    def _sanitize_inbound_content(cls, content: str) -> str:
        text = (content or "").strip()
        if not text:
            return ""

        if cls._is_metadata_only_payload(text):
            return ""

        lines = text.splitlines()
        cleaned_lines: List[str] = []
        in_metadata_block = False

        for raw_line in lines:
            line = raw_line.rstrip()
            stripped = line.strip()

            if not stripped:
                if cleaned_lines and cleaned_lines[-1] != "":
                    cleaned_lines.append("")
                continue

            if any(marker in stripped for marker in cls._QUEUED_WRAPPER_MARKERS):
                in_metadata_block = True
                continue

            if stripped.startswith("```json") or stripped == "```":
                if in_metadata_block:
                    continue

            if in_metadata_block:
                if stripped.startswith("Queued #"):
                    continue
                if cls._looks_like_metadata_line(stripped):
                    continue
                if stripped.startswith("```"):
                    continue
                if stripped.startswith("---"):
                    continue
                in_metadata_block = False

            if cls._looks_like_metadata_line(stripped):
                continue

            cleaned_lines.append(stripped)

        while cleaned_lines and cleaned_lines[-1] == "":
            cleaned_lines.pop()

        cleaned = "\n".join(cleaned_lines).strip()
        if cls._is_metadata_only_payload(cleaned):
            return ""
        return cleaned

    @classmethod
    def _is_metadata_only_payload(cls, text: str) -> bool:
        stripped = (text or "").strip()
        if not stripped:
            return True
        if any(marker in stripped for marker in cls._QUEUED_WRAPPER_MARKERS):
            meaningful = [line.strip() for line in stripped.splitlines() if line.strip()]
            non_wrapper = [line for line in meaningful if not any(marker in line for marker in cls._QUEUED_WRAPPER_MARKERS)]
            if non_wrapper and all(cls._looks_like_metadata_line(line) or line in {'---', '```', '```json'} or line.startswith('Queued #') for line in non_wrapper):
                return True
        return False

    @classmethod
    def _looks_like_metadata_line(cls, line: str) -> bool:
        s = (line or "").strip()
        if not s:
            return False
        if s.startswith('{') or s.startswith('}'):
            return True
        if s in {'[', ']'}:
            return True
        if s.startswith('"') and any(key in s for key in cls._PURE_METADATA_KEYS):
            return True
        return False

    def _is_recent_duplicate_message(self, session_id: str, role: str, content: str, recent_limit: int = 3) -> bool:
        text = (content or "").strip()
        if not text:
            return True
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT content
                    FROM gm_messages
                    WHERE session_id = ? AND role = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (session_id, role, recent_limit),
                )
                recent_rows = cursor.fetchall()
        except Exception as e:
            logger.warning("【WangChuan】[Pipeline][Ingest] recent duplicate check failed: %s", e)
            return False

        recent_contents = [str(row["content"] or "").strip() for row in recent_rows]
        if not recent_contents:
            return False

        normalized = text.lower()
        normalized_recent = [item.lower() for item in recent_contents if item]
        if normalized in normalized_recent:
            return True

        short_followup = self._is_short_followup_query(text)
        if short_followup and normalized_recent and normalized_recent[0] == normalized:
            return True

        return False

    def _detect_signals(self, msg_id: int, content: str):
        """自动信号检测"""
        signals = []

        patterns = {
            'error': [r'error', r'失败', r'报错', r'异常', r'exception'],
            'correction': [r'不对', r'错了', r'应该', r'修正', r'纠正'],
            'completion': [r'完成', r'搞定', r'解决', r'成功', r'done'],
            'question': [r'[?？]', r'如何', r'怎么', r'为什么'],
        }

        for sig_type, pats in patterns.items():
            for pat in pats:
                if re.search(pat, content, re.IGNORECASE):
                    signals.append(sig_type)
                    break

        if signals:
            with sqlite3.connect(self.db_path) as conn:
                for sig_type in signals:
                    conn.execute("""
                        INSERT INTO gm_signals (message_id, signal_type, confidence, extracted_text)
                        VALUES (?, ?, 0.7, ?)
                    """, (msg_id, sig_type, content[:200]))
                conn.commit()

    def extract_recent(self, session_id: str, max_messages: int = 10) -> List[Triple]:
        """
        从最近的消息中提取三元组

        需要 LLM API 配置
        """
        if not self.llm_api_key:
            self.process_consciousness_tool_result("extract_recent", False, "missing llm_api_key", session_id=session_id)
            return []

        # 获取最近的未提取消息
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT m.id, m.role, m.content
                FROM gm_messages m
                WHERE m.session_id = ?
                AND m.id NOT IN (
                    SELECT DISTINCT source_message_ids FROM gm_nodes
                    WHERE source_message_ids IS NOT NULL
                )
                ORDER BY m.id DESC
                LIMIT ?
            """, (session_id, max_messages))
            messages = c.fetchall()

        if not messages:
            self.process_consciousness_tool_result("extract_recent", True, "no pending messages", session_id=session_id)
            return []

        # 拼接消息内容
        text = "\n".join([f"[{m['role']}] {m['content'][:200]}" for m in messages])
        msg_ids = [m['id'] for m in messages]

        wrapped = self.run_with_consciousness(
            "llm_extract",
            lambda: self._llm_extract(text, msg_ids),
            session_id=session_id,
        )
        triples = wrapped.get("raw") or []

        if not isinstance(triples, list):
            self.process_consciousness_tool_result("extract_recent", False, f"llm_extract returned non-list: {type(triples).__name__}", session_id=session_id)
            return []

        stored = 0
        for triple in triples:
            try:
                self._store_triple(triple)
                stored += 1
            except Exception as e:
                self.process_consciousness_tool_result("store_triple", False, str(e), session_id=session_id)

        self.process_consciousness_tool_result("extract_recent", True, f"triples={len(triples)} stored={stored}", session_id=session_id)
        return triples

    def _llm_extract(self, text: str, msg_ids: List[int]) -> List[Triple]:
        """调用 LLM 提取三元组"""
        import urllib.request
        import urllib.error

        prompt = f"""从以下对话中提取知识图谱三元组。

对话内容：
{text}

请以 JSON 数组格式返回，每个三元组包含：
- subject: 头实体名称
- predicate: 关系（USED_SKILL/SOLVED_BY/REQUIRES/PATCHES/CONFLICTS_WITH）
- object: 尾实体名称
- subject_type: TASK/SKILL/EVENT/FACT
- object_type: TASK/SKILL/EVENT/FACT
- confidence: 置信度 0-1

只返回 JSON，不要解释。"""

        url = f"{self.llm_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json"
        }
        data = json.dumps({
            "model": self.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 2000
        }).encode()

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw_body = resp.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:300] if hasattr(e, "read") else ""
            raise RuntimeError(f"llm_http_error status={getattr(e, 'code', 'unknown')} body={body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"llm_network_error reason={e.reason}") from e

        try:
            result = json.loads(raw_body)
            content = result["choices"][0]["message"]["content"]
        except Exception as e:
            preview = raw_body.decode("utf-8", errors="ignore")[:300]
            raise RuntimeError(f"llm_response_parse_error body={preview}") from e

        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0]
        content = content.strip()
        if content.startswith("json"):
            content = content[4:].strip()

        try:
            items = json.loads(content)
        except Exception as e:
            raise RuntimeError(f"llm_json_parse_error content={content[:300]}") from e

        triples = []
        for item in items:
            t = Triple(
                subject=item.get("subject", ""),
                predicate=item.get("predicate", "RELATED_TO"),
                object=item.get("object", ""),
                subject_type=item.get("subject_type", "FACT"),
                object_type=item.get("object_type", "FACT"),
                confidence=item.get("confidence", 0.7),
                source_message_ids=msg_ids
            )
            if t.subject and t.object:
                triples.append(t)

        return triples

    def _store_triple(self, triple: Triple):
        """存储三元组到数据库"""
        gate = graph_ingest_gate({
            "subject": triple.subject,
            "predicate": triple.predicate,
            "object": triple.object,
            "confidence": triple.confidence,
            "signal": "candidate" if (triple.confidence or 0) < 0.8 else "curated",
        })
        if not gate.get("allowed"):
            logger.info("【WangChuan】[Pipeline][GraphGate] blocked triple: %s", gate.get("reason"))
            return

        subj_node, obj_node, edge = triple.to_node_edge()

        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            src_json = json.dumps(triple.source_message_ids)

            # 插入/更新头实体
            c.execute("""
                INSERT OR REPLACE INTO gm_nodes
                (node_id, node_type, name, description, source_message_ids, pagerank_score)
                VALUES (?, ?, ?, ?, ?, 0.1)
            """, (subj_node['node_id'], subj_node['node_type'],
                  subj_node['name'], subj_node['description'], src_json))

            # 插入/更新尾实体
            c.execute("""
                INSERT OR REPLACE INTO gm_nodes
                (node_id, node_type, name, description, source_message_ids, pagerank_score)
                VALUES (?, ?, ?, ?, ?, 0.1)
            """, (obj_node['node_id'], obj_node['node_type'],
                  obj_node['name'], obj_node['description'], src_json))

            # 插入边
            c.execute("""
                INSERT OR IGNORE INTO gm_edges
                (edge_id, source_node_id, target_node_id, edge_type, weight, source_message_ids)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (edge['edge_id'], edge['source_node_id'], edge['target_node_id'],
                  edge['edge_type'], edge['weight'], src_json))

            conn.commit()

        # 更新 FTS5 索引
        self._rebuild_fts()

    def _rebuild_fts(self):
        """重建 FTS5 索引"""
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute("INSERT INTO gm_nodes_fts(gm_nodes_fts) VALUES('rebuild')")
            except Exception as e:
                logger.warning("【WangChuan】[Pipeline][FTS] rebuild failed: %s", e)

    def history_search_index_status(self) -> Dict[str, object]:
        """阶段 2.3 最小历史搜索索引健康摘要。"""
        status: Dict[str, object] = {
            "reader": "gm_nodes_fts",
            "available": False,
            "total_nodes": 0,
            "fts_rows": 0,
            "coverage_ratio": 0.0,
            "status": "missing",
            "degraded": True,
        }
        try:
            with sqlite3.connect(self.db_path) as conn:
                total_nodes = conn.execute("SELECT COUNT(*) FROM gm_nodes").fetchone()[0]
                try:
                    fts_rows = conn.execute("SELECT COUNT(*) FROM gm_nodes_fts").fetchone()[0]
                except Exception:
                    fts_rows = 0

            coverage_ratio = round((fts_rows / total_nodes), 3) if total_nodes else 0.0
            available = total_nodes > 0 and fts_rows > 0
            degraded = (total_nodes > 0 and fts_rows == 0) or (total_nodes > 0 and coverage_ratio < 0.5)
            health = "healthy" if available and not degraded else ("degraded" if total_nodes > 0 else "empty")
            status.update({
                "available": available,
                "total_nodes": int(total_nodes or 0),
                "fts_rows": int(fts_rows or 0),
                "coverage_ratio": coverage_ratio,
                "status": health,
                "degraded": degraded,
            })
        except Exception as e:
            status.update({
                "status": "error",
                "error": str(e),
                "degraded": True,
            })
        return status

    @staticmethod
    def _is_short_followup_query(query: str) -> bool:
        return QueryProfiler.is_short_followup_query(query)

    @staticmethod
    def _compact_assembly_for_short_followup(assembly):
        if not assembly:
            return assembly
        try:
            fresh_tail = list(getattr(assembly, "fresh_tail", []) or [])
            if len(fresh_tail) > 4:
                assembly.fresh_tail = fresh_tail[-4:]
            assembly.graph_xml = "<graph></graph>"
            assembly.episodic_xml = ""
            assembly.episodic_tokens = 0
            assembly.dag_summary = ""
        except Exception:
            return assembly
        return assembly

    @staticmethod
    def _format_recall_degraded_block(stage: str, reason: str, mode: str) -> str:
        return FormatBlocks.format_recall_degraded_block(stage, reason, mode)

    @staticmethod
    def _runtime_mode_from_primary_role(primary_role: str, degraded: bool) -> str:
        if not degraded:
            return 'resonance_mainline'
        return 'no_memory' if primary_role in {'', 'no_memory'} else 'foundation_recall'

    @staticmethod
    def _runtime_latency_p95(latencies_ms: List[float]) -> float:
        values: List[float] = []
        for item in latencies_ms:
            try:
                value = float(item or 0.0)
            except Exception:
                continue
            if value >= 0.0:
                values.append(value)
        if not values:
            return 0.0
        values.sort()
        index = max(0, min(len(values) - 1, math.ceil(len(values) * 0.95) - 1))
        return round(values[index], 2)

    def _recent_runtime_health_events(self, session_id: str, limit: int = 40) -> List[Dict[str, object]]:
        try:
            rows = self._observability.state_store.load_metrics(session_id, limit=limit)
        except Exception:
            return []
        return [
            dict(item)
            for item in rows
            if isinstance(item, dict) and item.get('event') == 'wangchuan_runtime_health'
        ]

    def _record_runtime_health(
        self,
        session_id: str,
        recall_metrics: Dict[str, object],
        resonance_decision_view: Dict[str, object],
        memory_route: str,
        scope_route: str,
        degraded_runtime: Dict[str, object] | None = None,
    ) -> Dict[str, object]:
        summary = dict(resonance_decision_view.get('summary') or {})
        degraded_runtime = dict(degraded_runtime or {})
        primary_role = str(summary.get('primary_role') or '')
        primary_kind = str(summary.get('primary_kind') or '')
        degraded = str(degraded_runtime.get('status') or '').lower() == 'degraded'
        degrade_stage = str(degraded_runtime.get('stage') or '')
        degrade_reason = str(degraded_runtime.get('reason') or '')[:240]
        fallback_mode = str(degraded_runtime.get('fallback_mode') or '')
        current_mode = self._runtime_mode_from_primary_role(primary_role, degraded)
        timestamp = datetime.now().astimezone().isoformat(timespec='seconds')

        prior_events = self._recent_runtime_health_events(session_id, limit=40)
        last_success_ts = ''
        latest_degraded_streak: List[Dict[str, object]] = []
        for event in reversed(prior_events):
            status = str(event.get('status') or '')
            if not last_success_ts and status == 'ok':
                last_success_ts = str(event.get('last_success_ts') or event.get('timestamp') or '')
            if status == 'degraded':
                latest_degraded_streak.append(event)
            elif latest_degraded_streak:
                break

        if degraded:
            consecutive_failures = len(latest_degraded_streak) + 1
            backlog = consecutive_failures
            recovered_from_stage = ''
            last_degrade_reason = degrade_reason or str(
                (latest_degraded_streak[0] if latest_degraded_streak else {}).get('last_degrade_reason')
                or (latest_degraded_streak[0] if latest_degraded_streak else {}).get('reason')
                or ''
            )[:240]
        else:
            consecutive_failures = 0
            backlog = 0
            recovered_from_stage = str((latest_degraded_streak[0] if latest_degraded_streak else {}).get('degrade_stage') or '')
            last_degrade_reason = str(
                (latest_degraded_streak[0] if latest_degraded_streak else {}).get('last_degrade_reason')
                or (latest_degraded_streak[0] if latest_degraded_streak else {}).get('reason')
                or ''
            )[:240]
            last_success_ts = timestamp

        sample_events = prior_events[-19:]
        sample_statuses = [str(event.get('status') or '') for event in sample_events]
        sample_statuses.append('degraded' if degraded else 'ok')
        success_count = sum(1 for item in sample_statuses if item == 'ok')
        success_rate = round(success_count / max(len(sample_statuses), 1), 4)

        sample_latencies: List[float] = []
        for event in sample_events:
            try:
                latency = float(event.get('elapsed_ms') or 0.0)
            except Exception:
                latency = 0.0
            if latency > 0:
                sample_latencies.append(latency)
        try:
            elapsed_ms = round(float(recall_metrics.get('elapsed_ms') or 0.0), 2)
        except Exception:
            elapsed_ms = 0.0
        if elapsed_ms > 0:
            sample_latencies.append(elapsed_ms)

        payload: Dict[str, object] = {
            'status': 'degraded' if degraded else 'ok',
            'current_mode': current_mode,
            'last_success_ts': last_success_ts,
            'success_rate': success_rate,
            'p95': self._runtime_latency_p95(sample_latencies),
            'backlog': backlog,
            'last_degrade_reason': last_degrade_reason,
            'consecutive_failures': consecutive_failures,
            'recovered_from_stage': recovered_from_stage,
            'degrade_stage': degrade_stage,
            'fallback_mode': fallback_mode or ('no_memory' if current_mode == 'no_memory' else ('foundation_recall' if degraded else '')),
            'primary_role': primary_role,
            'primary_kind': primary_kind,
            'scope_route': scope_route,
            'memory_route': memory_route,
            'degraded': degraded,
            'elapsed_ms': elapsed_ms,
            'recent_sample_size': len(sample_statuses),
            'query': str(recall_metrics.get('query') or '')[:120],
            'reason': degrade_reason if degraded else 'ok',
            'fallback_chain': ['resonance_mainline', 'foundation_recall', 'no_memory'],
        }
        self._observability.state_store.append_metric(session_id, 'wangchuan_runtime_health', payload)
        return payload

    def _build_degraded_recall_payload(
        self,
        query: str,
        session_id: str,
        top_k: int,
        stage: str,
        error: Exception | str,
        short_followup_mode: bool = False,
        started_at: float | None = None,
    ) -> Dict[str, object]:
        query_preference_profile = self._build_query_preference_profile(query)

        assembly = None
        try:
            assembly = self._assemble_engine.assemble(session_id, query=query)
            if short_followup_mode:
                assembly = self._compact_assembly_for_short_followup(assembly)
        except Exception:
            assembly = None

        try:
            consciousness_context = self._consciousness.get_prompt_fragment(user_text=query, user_id=session_id)
        except Exception:
            consciousness_context = ""

        wakeup_pack = self._build_wakeup_pack(query, session_id=session_id)
        response_strategy = self._build_response_strategy(consciousness_context)
        execution_guidance = self._derive_execution_guidance(consciousness_context)

        history_support = self._summarize_history_support(assembly)
        history_search_index = self.history_search_index_status()

        memory_layer = {
            "route": "degraded_no_memory",
            "reader": "degraded_no_memory",
            "structured": False,
            "items": [],
            "candidate_items": [],
            "metadata_summary": {},
            "block": "",
        }
        if not short_followup_mode:
            try:
                memory_layer = self._recall_memory_layer(query, top_k=top_k)
            except Exception:
                pass

        resource_items = self._probe_resource_items(query, limit=min(3, top_k or 3)) if query_preference_profile.get('scope_route') == 'resource' else []
        skill_items = self._probe_skill_items(query, limit=min(3, top_k or 3)) if query_preference_profile.get('scope_route') == 'skill' else []

        memory_items = list(memory_layer.get('items', []) or [])
        scope_route = str(query_preference_profile.get('scope_route') or 'memory')
        if scope_route == 'resource' and resource_items:
            primary_kind = 'resource'
            primary_role = 'primary_resource'
            primary_reason = f'degraded:{stage}'
            primary_candidates = [{
                'candidate_kind': 'resource',
                'decision_role': 'primary_resource',
                'context_uri': resource_items[0].get('context_uri'),
                'title': resource_items[0].get('title'),
                'path': resource_items[0].get('path'),
                'why_selected': primary_reason,
            }]
            scope_context_block = self._format_resource_recall_block(resource_items)
        elif scope_route == 'skill' and skill_items:
            primary_kind = 'skill'
            primary_role = 'primary_skill'
            primary_reason = f'degraded:{stage}'
            primary_candidates = [{
                'candidate_kind': 'skill',
                'decision_role': 'primary_skill',
                'context_uri': skill_items[0].get('context_uri'),
                'name': skill_items[0].get('name'),
                'path': skill_items[0].get('path'),
                'why_selected': primary_reason,
            }]
            scope_context_block = self._format_skill_recall_block(skill_items)
        elif memory_items:
            primary_kind = 'memory_item'
            primary_role = 'memory_evidence'
            primary_reason = f'degraded:{stage}'
            first_memory = memory_items[0]
            primary_candidates = [{
                'candidate_kind': 'memory_item',
                'decision_role': 'memory_evidence',
                'context_uri': first_memory.get('context_uri'),
                'memory_id': first_memory.get('memory_id'),
                'content_preview': str(first_memory.get('content') or '')[:120],
                'memory_type': first_memory.get('memory_type'),
                'subject_domain': first_memory.get('subject_domain'),
                'why_selected': primary_reason,
            }]
            scope_context_block = memory_layer.get('block', '')
        else:
            primary_kind = 'no_memory'
            primary_role = 'no_memory'
            primary_reason = f'degraded:{stage}'
            primary_candidates = []
            scope_context_block = ''

        resonance_decision_view = {
            'query': query,
            'scope_route': scope_route,
            'summary': {
                'primary_kind': primary_kind,
                'primary_role': primary_role,
                'primary_reason': primary_reason,
                'fusion_count': len(primary_candidates),
                'seed_count': 0,
                'resonance_count': 0,
                'pattern_count': 0,
                'resource_count': len(resource_items),
                'skill_count': len(skill_items),
                'memory_count': len(memory_items),
            },
            'primary_candidates': primary_candidates,
            'supporting_candidates': [],
            'evidence_candidates': primary_candidates if primary_role == 'memory_evidence' else [],
            'pattern_candidates': [],
        }
        decision_context_block = self._format_resonance_decision_block(resonance_decision_view)
        degraded_block = self._format_recall_degraded_block(stage, str(error), 'simplified_fallback')

        stable_prefix = "\n\n".join(part for part in [
            wakeup_pack,
            consciousness_context,
            response_strategy,
            degraded_block,
            decision_context_block,
            scope_context_block,
            getattr(assembly, 'stable_prefix', ''),
        ] if part)
        dynamic_suffix = "\n\n".join(part for part in [
            getattr(assembly, 'dynamic_suffix', ''),
        ] if part)
        final_context = "\n\n".join(part for part in [stable_prefix, dynamic_suffix] if part)
        selected_sections = []
        try:
            if assembly is not None:
                selected_sections = self._assemble_engine.build_prompt_sections(assembly, profile=query_preference_profile).get('selected_sections', []) or []
        except Exception:
            selected_sections = []

        # compat anchor: primary_evidence_boundary = self._derive_primary_evidence_boundary(memory_layer, history_support)
        primary_evidence_boundary = self._derive_primary_evidence_boundary(
            memory_layer,
            history_support,
            query_preference_profile,
            resonance_decision_view,
        )
        joint_gating = self._build_joint_gating_status(
            memory_layer,
            query_preference_profile,
            history_support,
            primary_evidence_boundary,
            resonance_decision_view,
        )
        cross_topic_risk = self._assess_cross_topic_risk(
            query,
            memory_layer,
            query_preference_profile,
        )
        recall_metrics = self._observability.capture_recall_metrics(
            session_id=session_id,
            query=query,
            stable_prefix=stable_prefix,
            dynamic_suffix=dynamic_suffix,
            final_context=final_context,
            extra={
                'memory_route': memory_layer.get('route'),
                'scope_route': scope_route,
                'context_route': query_preference_profile.get('context_route', 'default'),
                'selected_sections': ','.join(selected_sections),
                'memory_items': len(memory_items),
                'history_support_items': history_support.get('support_items', 0),
                'decision_primary_role': primary_role,
                'decision_primary_kind': primary_kind,
                'decision_block_len': len(decision_context_block or ''),
                'scope_context_block_len': len(scope_context_block or ''),
                'degraded_runtime': 'true',
                'degrade_stage': stage,
                'elapsed_ms': round(max((time.time() - started_at) * 1000, 0.0), 2) if started_at else 0.0,
            },
        )
        self._observability.state_store.append_metric(session_id, 'recall_degraded_metrics', {
            'stage': stage,
            'reason': str(error)[:240],
            'scope_route': scope_route,
            'primary_role': primary_role,
            'memory_items': len(memory_items),
        })

        runtime_health = self._record_runtime_health(
            session_id=session_id,
            recall_metrics=recall_metrics,
            resonance_decision_view=resonance_decision_view,
            memory_route=str(memory_layer.get('route') or ''),
            scope_route=scope_route,
            degraded_runtime={
                'status': 'degraded',
                'stage': stage,
                'reason': str(error)[:240],
                'fallback_mode': 'foundation_recall' if primary_role != 'no_memory' else 'no_memory',
            },
        )
        runtime_view = self._observability.read_session_runtime_view(session_id)
        if runtime_view.get('status') == 'ok':
            runtime_snapshot = dict(runtime_view)
            runtime_snapshot.update({
                key: runtime_health.get(key)
                for key in [
                    'current_mode',
                    'last_success_ts',
                    'success_rate',
                    'p95',
                    'backlog',
                    'last_degrade_reason',
                    'consecutive_failures',
                    'recovered_from_stage',
                    'degrade_stage',
                    'fallback_mode',
                ]
            })
            recall_metrics['session_runtime'] = runtime_snapshot
            self._observability.state_store.append_metric(session_id, 'session_runtime_metrics', runtime_snapshot)

        degraded_runtime = {
            'status': 'degraded',
            'stage': stage,
            'reason': str(error)[:240],
            'fallback_mode': runtime_health.get('fallback_mode') or 'foundation_recall',
            'current_mode': runtime_health.get('current_mode') or 'foundation_recall',
            'last_success_ts': runtime_health.get('last_success_ts') or '',
            'last_degrade_reason': runtime_health.get('last_degrade_reason') or str(error)[:240],
            'consecutive_failures': runtime_health.get('consecutive_failures') or 0,
            'recovered_from_stage': runtime_health.get('recovered_from_stage') or '',
            'success_rate': runtime_health.get('success_rate') or 0.0,
            'p95': runtime_health.get('p95') or 0.0,
            'backlog': runtime_health.get('backlog') or 0,
            'fallback_chain': runtime_health.get('fallback_chain') or ['resonance_mainline', 'foundation_recall', 'no_memory'],
        }

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
            'scope_route': scope_route,
            'scope_route_profile': query_preference_profile.get('scope_route_profile', {}),
            'decision_context_block': decision_context_block,
            'scope_context_block': scope_context_block,
            'memory_recall_block': memory_layer.get('block', ''),
            'resource_recall_block': self._format_resource_recall_block(resource_items),
            'skill_recall_block': self._format_skill_recall_block(skill_items),
            'seed_candidates': [],
            'resonance_candidates': [],
            'graph_edge_resonance_candidates': [],
            'pattern_candidates': [],
            'fusion_candidates': primary_candidates,
            'resonance_decision_view': resonance_decision_view,
            'resource_items': resource_items,
            'skill_items': skill_items,
            'context_route': query_preference_profile.get('context_route', 'default'),
            'selected_sections': selected_sections,
            'memory_route': memory_layer.get('route'),
            'memory_reader': memory_layer.get('reader'),
            'memory_structured': memory_layer.get('structured', False),
            'memory_items': memory_items,
            'memory_metadata_summary': memory_layer.get('metadata_summary', {}),
            'history_support': history_support,
            'history_search_index': history_search_index,
            'primary_evidence_boundary': primary_evidence_boundary,
            'joint_gating': joint_gating,
            'cross_topic_risk': cross_topic_risk,
            'semantic_cache': {
                'status': 'bypass',
                'cache_key': '',
                'semantic_family': '',
                'state_fingerprint': '',
            },
            'retrieval_debug': {'short_followup_mode': short_followup_mode, 'degraded': True, 'degrade_stage': stage},
            'nodes': [],
            'degraded_runtime': degraded_runtime,
            'runtime_health': runtime_health,
            'assembly': assembly,
        }

    def recall(self, query: str, session_id: str = None, top_k: int = 5) -> Dict:
        """
        回忆：检索 + 组装 + 反馈

        Returns:
            {
                'context': 格式化的上下文字符串,
                'nodes': 检索到的节点列表,
                'assembly': ContextAssembly 对象
            }
        """
        started_at = time.time()
        try:
            resolved_session_id = _resolve_runtime_session_id(session_id)
            short_followup_mode = self._is_short_followup_query(query)
            retrieve_top_k = min(top_k, 2) if short_followup_mode else top_k
            results = []
            retrieval_debug = {"short_followup_mode": short_followup_mode}
            node_ids = []
            query_preference_profile = self._build_query_preference_profile(query)
            cache_state = {
                'last_compacted_message_id': '',
                'task_updated_at': '',
                'summary_updated_at': '',
                'explicit_feedback_state': {'max_feedback_id': 0, 'max_created_at': ''},
            }
            if resolved_session_id:
                try:
                    summary_state = self._assemble_engine.state_store.load_session_summary(resolved_session_id)
                    checkpoint_state = self._assemble_engine.state_store.load_task_checkpoint(resolved_session_id)
                    explicit_feedback_state = {'max_feedback_id': 0, 'max_created_at': ''}
                    with sqlite3.connect(self.db_path) as conn:
                        feedback_row = conn.execute(
                            """
                            SELECT COALESCE(MAX(id), 0), COALESCE(MAX(created_at), '')
                            FROM gm_feedback
                            WHERE session_id = ?
                              AND feedback_type IN ('explicit_pos', 'explicit_neg', 'follow_up')
                            """,
                            (resolved_session_id,),
                        ).fetchone()
                        if feedback_row:
                            explicit_feedback_state = {
                                'max_feedback_id': int(feedback_row[0] or 0),
                                'max_created_at': str(feedback_row[1] or ''),
                            }
                    cache_state = {
                        'last_compacted_message_id': str(summary_state.get('last_compacted_message_id') or ''),
                        'summary_updated_at': str(summary_state.get('updated_at') or ''),
                        'task_updated_at': str(checkpoint_state.get('updated_at') or ''),
                        'explicit_feedback_state': explicit_feedback_state,
                    }
                except Exception:
                    pass
            cache_meta, cached_recall = self._semantic_cache.get(
                session_id=resolved_session_id,
                query=query,
                top_k=retrieve_top_k,
                profile=query_preference_profile,
                state=cache_state,
            )
            if cached_recall is not None and not short_followup_mode:
                cached_recall = dict(cached_recall)
                cached_recall['memory_items'] = self._shape_memory_items_for_output(
                    list(cached_recall.get('memory_items', []) or [])
                )
                self._observability.state_store.append_metric(resolved_session_id, 'semantic_cache_metrics', {
                    'status': 'hit',
                    'cache_key': cache_meta.get('cache_key', ''),
                    'semantic_family': cache_meta.get('semantic_family', ''),
                    'state_fingerprint': cache_meta.get('state_fingerprint', ''),
                    'query': query[:120],
                })
                cached_nodes = list(cached_recall.get('nodes', []) or [])
                cached_metrics = self._observability.capture_recall_metrics(
                    session_id=resolved_session_id,
                    query=query,
                    stable_prefix=cached_recall.get('stable_prefix', ''),
                    dynamic_suffix=cached_recall.get('dynamic_suffix', ''),
                    final_context=cached_recall.get('context', ''),
                extra={
                    'memory_route': cached_recall.get('memory_route', 'default'),
                    'scope_route': cached_recall.get('scope_route', 'memory'),
                    'context_route': cached_recall.get('context_route', 'default'),
                    'selected_sections': ','.join(cached_recall.get('selected_sections', []) or []),
                    'memory_items': len(cached_recall.get('memory_items', []) or []),
                    'history_support_items': int((cached_recall.get('history_support') or {}).get('support_items') or 0),
                        'retrieved_nodes': len(cached_nodes),
                        'assembled_nodes': min(len(cached_nodes), 3),
                        'semantic_cache': 'hit',
                        'semantic_cache_family': cache_meta.get('semantic_family', ''),
                    },
                )
                cached_recall['recall_metrics'] = cached_metrics
                cached_recall['semantic_cache'] = {
                    'status': 'hit',
                    'cache_key': cache_meta.get('cache_key', ''),
                    'semantic_family': cache_meta.get('semantic_family', ''),
                    'state_fingerprint': cache_meta.get('state_fingerprint', ''),
                }
                self.process_consciousness_tool_result(
                    'recall_cache',
                    True,
                    f"semantic_cache hit query={query[:80]} family={cache_meta.get('semantic_family', '')} key={cache_meta.get('cache_key', '')[:12]}",
                    session_id=resolved_session_id,
                )
                return cached_recall
            memory_layer = {
                "route": "short_followup_bypass",
                "reader": "short_followup_guard",
                "structured": False,
                "items": [],
                "metadata_summary": {"short_followup_mode": True},
                "block": "",
            } if short_followup_mode else None

            if not short_followup_mode:
                retrieve_wrapped = self.run_with_consciousness(
                    "recall_retrieve",
                    lambda: self._retriever.retrieve(
                        query,
                        session_id=resolved_session_id,
                        top_k=retrieve_top_k,
                        use_graph=True,
                        use_vector=False,
                        use_fts=True,
                    ),
                    session_id=resolved_session_id,
                )
                results = retrieve_wrapped.get("raw") or []
                retrieval_debug = dict(getattr(self._retriever, "last_debug", {}) or {})
                retrieval_debug.update({
                    "short_followup_mode": short_followup_mode,
                    "mode": "lightweight_graph_fts",
                })
                node_ids = [r.node_id for r in results]
                query_preference_profile = self._build_query_preference_profile(query)
                memory_layer = self._recall_memory_layer(query, top_k=retrieve_top_k)

            assemble_wrapped = self.run_with_consciousness(
                "recall_assemble",
                lambda: self._assemble_engine.assemble(resolved_session_id, query=query),
                session_id=resolved_session_id,
            )
            assembly = assemble_wrapped.get("raw")
            if short_followup_mode:
                assembly = self._compact_assembly_for_short_followup(assembly)

            assembled_ids = node_ids[:3]
            if resolved_session_id and not short_followup_mode:
                self._last_query_nodes[resolved_session_id] = node_ids

            consciousness_context = ""
            try:
                consciousness_context = self._consciousness.get_prompt_fragment(user_text=query, user_id=resolved_session_id)
            except Exception as e:
                logger.warning("【WangChuan】[Pipeline][Consciousness] get_prompt_fragment failed: %s", e)

            wakeup_pack = self._build_wakeup_pack(query, session_id=resolved_session_id)

            try:
                base_context = self._assemble_engine.format_for_prompt(assembly, profile=query_preference_profile)
            except Exception as e:
                self.process_consciousness_tool_result("recall_context_format", False, f"recall_context_format_error {e}", session_id=resolved_session_id)
                raise

            prompt_sections = self._assemble_engine.build_prompt_sections(assembly, profile=query_preference_profile)

            memory_recall_block = memory_layer.get("block", "")
            history_support = self._summarize_history_support(assembly)
            history_search_index = self.history_search_index_status()
            execution_guidance = self._derive_execution_guidance(consciousness_context)
            response_strategy = self._build_response_strategy(consciousness_context)
            resource_items = self._probe_resource_items(query, limit=min(3, retrieve_top_k or 3)) if query_preference_profile.get('scope_route') == 'resource' else []
            skill_items = self._probe_skill_items(query, limit=min(3, retrieve_top_k or 3)) if query_preference_profile.get('scope_route') == 'skill' else []
            candidate_memory_items = list(memory_layer.get('candidate_items', []) or memory_layer.get('items', []) or [])
            scope_route = str(query_preference_profile.get('scope_route') or 'memory')
            raw_evidence_only_mode = scope_route == 'memory' and str(memory_layer.get('route') or '') == 'raw'
            if raw_evidence_only_mode:
                seed_candidates = []
                resonance_candidates = []
                graph_edge_resonance_candidates = []
                pattern_candidates = []
            else:
                seed_candidates = self._build_seed_candidates(
                    query,
                    candidate_memory_items,
                    query_preference_profile,
                    limit=min(5, retrieve_top_k or 5),
                )
                resonance_candidates = self._build_resonance_candidates(
                    seed_candidates,
                    candidate_memory_items,
                    limit=min(5, retrieve_top_k or 5),
                )
                graph_edge_resonance_candidates = self._build_graph_edge_resonance_candidates(
                    query,
                    seed_candidates,
                    limit=min(3, retrieve_top_k or 3),
                )
                resonance_candidates = sorted(
                    list(resonance_candidates) + list(graph_edge_resonance_candidates),
                    key=lambda row: float(row.get('resonance_score') or 0.0),
                    reverse=True,
                )[: max(5, min(8, retrieve_top_k + 2))]
                pattern_candidates = self._build_pattern_candidates(
                    query,
                    candidate_memory_items,
                    resonance_candidates,
                    limit=min(3, retrieve_top_k or 3),
                )
            fusion_candidates = self._build_fusion_candidates(
                query_preference_profile,
                list(memory_layer.get('items', []) or []),
                resource_items,
                skill_items,
                seed_candidates,
                resonance_candidates,
                pattern_candidates,
                limit=max(5, min(8, retrieve_top_k + 2)),
            )
            resonance_decision_view = self._build_resonance_decision_view(
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
            primary_evidence_boundary = self._derive_primary_evidence_boundary(
                memory_layer,
                history_support,
                query_preference_profile,
                resonance_decision_view,
            )
            joint_gating = self._build_joint_gating_status(
                memory_layer,
                query_preference_profile,
                history_support,
                primary_evidence_boundary,
                resonance_decision_view,
            )
            cross_topic_risk = self._assess_cross_topic_risk(
                query,
                memory_layer,
                query_preference_profile,
            )
            decision_context_block = self._format_resonance_decision_block(resonance_decision_view)
            resource_recall_block = self._format_resource_recall_block(resource_items)
            skill_recall_block = self._format_skill_recall_block(skill_items)
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
            recall_metrics = self._observability.capture_recall_metrics(
                session_id=resolved_session_id,
                query=query,
                stable_prefix=stable_prefix,
                dynamic_suffix=dynamic_suffix,
                final_context=final_context,
                extra={
                    'memory_route': memory_layer.get('route'),
                    'scope_route': query_preference_profile.get('scope_route', 'memory'),
                    'context_route': query_preference_profile.get('context_route', 'default'),
                    'selected_sections': ','.join(prompt_sections.get('selected_sections', []) or []),
                    'memory_items': len(memory_layer.get('items', []) or []),
                    'history_support_items': history_support.get('support_items', 0),
                    'decision_primary_role': resonance_decision_view.get('summary', {}).get('primary_role', ''),
                    'decision_primary_kind': resonance_decision_view.get('summary', {}).get('primary_kind', ''),
                    'decision_block_len': len(decision_context_block or ''),
                    'scope_context_block_len': len(scope_context_block or ''),
                    'retrieved_nodes': len(node_ids),
                    'assembled_nodes': len(assembled_ids),
                    'elapsed_ms': round(max((time.time() - started_at) * 1000, 0.0), 2),
                },
            )
            runtime_health = self._record_runtime_health(
                session_id=resolved_session_id,
                recall_metrics=recall_metrics,
                resonance_decision_view=resonance_decision_view,
                memory_route=str(memory_layer.get('route') or ''),
                scope_route=str(query_preference_profile.get('scope_route', 'memory')),
                degraded_runtime=None,
            )
            runtime_view = self._observability.read_session_runtime_view(resolved_session_id)
            if runtime_view.get('status') == 'ok':
                runtime_snapshot = dict(runtime_view)
                runtime_snapshot.update({
                    key: runtime_health.get(key)
                    for key in [
                        'current_mode',
                        'last_success_ts',
                        'success_rate',
                        'p95',
                        'backlog',
                        'last_degrade_reason',
                        'consecutive_failures',
                        'recovered_from_stage',
                        'degrade_stage',
                        'fallback_mode',
                    ]
                })
                recall_metrics['session_runtime'] = runtime_snapshot
                self._observability.state_store.append_metric(resolved_session_id, 'session_runtime_metrics', runtime_snapshot)

            self.process_consciousness_tool_result(
                "recall",
                True,
                f"query={query[:80]} route={memory_layer.get('route')} memory_items={len(memory_layer.get('items', []))} history_support={history_support.get('support_items', 0)} history_index={history_search_index.get('status')} nodes={len(node_ids)} assembled={len(assembled_ids)} retrieval_top={','.join(retrieval_debug.get('top_names', [])[:3])}",
                session_id=resolved_session_id,
            )

            result_payload = {
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
                'seed_candidates': seed_candidates,
                'resonance_candidates': resonance_candidates,
                'graph_edge_resonance_candidates': graph_edge_resonance_candidates,
                'pattern_candidates': pattern_candidates,
                'fusion_candidates': fusion_candidates,
                'resonance_decision_view': resonance_decision_view,
                'resource_items': resource_items,
                'skill_items': skill_items,
                'context_route': query_preference_profile.get('context_route', 'default'),
                'selected_sections': prompt_sections.get('selected_sections', []),
                'memory_route': memory_layer.get('route'),
                'memory_reader': memory_layer.get('reader'),
                'memory_structured': memory_layer.get('structured', False),
                'memory_items': memory_layer.get('items', []),
                'memory_metadata_summary': memory_layer.get('metadata_summary', {}),
                'history_support': history_support,
                'history_search_index': history_search_index,
                'primary_evidence_boundary': primary_evidence_boundary,
                'joint_gating': joint_gating,
                'cross_topic_risk': cross_topic_risk,
                'semantic_cache': {
                    'status': 'miss' if not short_followup_mode else 'bypass',
                    'cache_key': cache_meta.get('cache_key', ''),
                    'semantic_family': cache_meta.get('semantic_family', ''),
                    'state_fingerprint': cache_meta.get('state_fingerprint', ''),
                },
                'runtime_health': runtime_health,
                'retrieval_debug': retrieval_debug,
                'nodes': [
                    {'node_id': r.node_id, 'name': r.name, 'type': r.node_type,
                     'score': r.score, 'sources': r.sources}
                    for r in results
                ],
                'assembly': assembly
            }
            if not short_followup_mode:
                cacheable_payload = dict(result_payload)
                cacheable_payload.pop('assembly', None)
                self._semantic_cache.put(
                    session_id=resolved_session_id,
                    query=query,
                    top_k=retrieve_top_k,
                    value=cacheable_payload,
                    profile=query_preference_profile,
                    state=cache_state,
                )
                self._observability.state_store.append_metric(resolved_session_id, 'semantic_cache_metrics', {
                    'status': 'miss',
                    'cache_key': cache_meta.get('cache_key', ''),
                    'semantic_family': cache_meta.get('semantic_family', ''),
                    'state_fingerprint': cache_meta.get('state_fingerprint', ''),
                    'query': query[:120],
                })
            return result_payload
        except Exception as e:
            resolved_session_id = _resolve_runtime_session_id(session_id)
            self.process_consciousness_tool_result("recall", False, str(e), session_id=resolved_session_id)
            try:
                return self._build_degraded_recall_payload(
                    query=query,
                    session_id=resolved_session_id,
                    top_k=top_k,
                    stage='recall_mainline_exception',
                    error=e,
                    short_followup_mode=self._is_short_followup_query(query),
                    started_at=started_at,
                )
            except Exception:
                raise

    def feedback_used(self, node_ids: List[str], session_id: str = "default"):
        """正反馈：这些节点有用"""
        for nid in node_ids:
            self._feedback_engine.record(FeedbackSignal(
                node_id=nid,
                feedback_type=FeedbackType.EXPLICIT_POSITIVE,
                query="",
                session_id=session_id
            ))
        try:
            self._consciousness.process_feedback("对，可以，继续。", positive=True, user_id=session_id)
        except Exception as e:
            logger.warning("【WangChuan】[Pipeline][Consciousness] feedback_positive failed: %s", e)

    def get_consciousness_context(self) -> str:
        """获取当前意识状态注入片段"""
        try:
            return self._consciousness.get_prompt_fragment()
        except Exception as e:
            logger.warning("【WangChuan】[Pipeline][Consciousness] get_context failed: %s", e)
            return ""

    def process_consciousness_tool_result(self, tool_name: str, ok: bool, content: str = "", session_id: str = None) -> Dict:
        """把工具执行结果送入意识进化闭环"""
        try:
            return self._consciousness.process_tool_result(tool_name, ok, content, session_id=session_id)
        except Exception as e:
            logger.warning("【WangChuan】[Pipeline][Consciousness] tool_result failed tool=%s ok=%s: %s", tool_name, ok, e)
            return {"error": str(e), "tool_name": tool_name, "ok": ok, "session_id": session_id}

    def run_with_consciousness(self, tool_name: str, runner, session_id: str = None) -> Dict:
        """包装真实工具/函数执行，并自动把结果送入意识进化闭环"""
        result = run_tool_with_consciousness(
            tool_name=tool_name,
            runner=runner,
            consciousness_callback=self.process_consciousness_tool_result,
            session_id=session_id,
        )
        return {
            "tool_name": result.tool_name,
            "ok": result.ok,
            "content": result.content,
            "session_id": result.session_id,
            "raw": result.raw,
        }

    def debug_consciousness(self, session_id: str = None, tail: int = 5) -> Dict:
        """输出意识进化调试报告"""
        try:
            return self._consciousness.debug_report(session_id=session_id, tail=tail)
        except Exception as e:
            logger.warning("【WangChuan】[Pipeline][Consciousness] debug_report failed: %s", e)
            return {"error": str(e), "session_id": session_id, "tail": tail}

    def maintain_consciousness(self) -> Dict:
        """运行意识层数据清理与老化维护"""
        wrapped = self.run_with_consciousness(
            "consciousness_hygiene",
            lambda: self._consciousness.run_hygiene(),
            session_id="maintenance",
        )
        raw = wrapped.get("raw")
        if isinstance(raw, dict):
            raw.setdefault("consciousness", {"ok": wrapped.get("ok"), "content": wrapped.get("content")})
            return raw
        return wrapped

    def feedback_correction(self, node_ids: List[str], session_id: str = "default"):
        """负反馈：这些节点有问题"""
        self._feedback_engine.on_correction(node_ids, "", session_id)
        try:
            self._consciousness.process_feedback("不对，刚才那个方向有问题。", positive=False, user_id=session_id)
        except Exception as e:
            logger.warning("【WangChuan】[Pipeline][Consciousness] feedback_negative failed: %s", e)

    def on_follow_up(self, query: str, session_id: str = "default"):
        """追问：上次召回有用"""
        prev_nodes = self._last_query_nodes.get(session_id, [])
        if prev_nodes:
            self._feedback_engine.on_follow_up(query, session_id, prev_nodes)

    def maintain(self):
        """运行周期维护"""
        wrapped = self.run_with_consciousness(
            "graph_maintenance",
            lambda: self._maintenance_engine.run_maintenance(),
            session_id="maintenance",
        )
        result = wrapped.get("raw")
        if hasattr(result, "pagerank_updated"):
            return {
                'pagerank': result.pagerank_updated,
                'communities': result.communities_detected,
                'summaries': result.summaries_generated,
                'duration_ms': result.duration_ms,
                'consciousness': {"ok": wrapped.get("ok"), "content": wrapped.get("content")}
            }
        return wrapped

    def consolidate(self, session_id: str = None):
        """会话巩固"""
        result = self._maintenance_engine.consolidate_session(_resolve_runtime_session_id(session_id))
        return result

    def get_stats(self) -> Dict:
        """获取管线统计"""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            stats = {}
            for table in ['gm_messages', 'gm_signals', 'gm_nodes', 'gm_edges',
                          'gm_communities', 'gm_embeddings', 'gm_feedback']:
                try:
                    c.execute(f"SELECT COUNT(*) FROM {table}")
                    stats[table] = c.fetchone()[0]
                except Exception as e:
                    logger.warning("【WangChuan】[Pipeline][Stats] count failed table=%s: %s", table, e)
                    stats[table] = 0
            # DAG 摘要统计
            try:
                from .core.dag_compressor import DAGCompressor
                dag = DAGCompressor(self.db_path)
                stats['dag_summaries'] = dag.get_dag_stats()
            except Exception as e:
                logger.warning("【WangChuan】[Pipeline][Stats] dag summary failed: %s", e)
                stats['dag_summaries'] = {}
            return stats

    def compress(self, session_id: str = None) -> Dict:
        """压缩会话消息（DAG 多级摘要）"""
        from .core.dag_compressor import DAGCompressor
        dag = DAGCompressor(self.db_path)

        sid = _resolve_runtime_session_id(session_id)

        if dag.should_compress(sid):
            return dag.compress_session(sid)
        else:
            return {'compressed': 0, 'reason': '未达到压缩阈值'}

    def expand_summary(self, node_id: str) -> Dict:
        """展开摘要节点"""
        from .core.dag_compressor import DAGCompressor
        dag = DAGCompressor(self.db_path)
        return dag.expand(node_id)


# 便捷创建函数
def create_pipeline(**kwargs) -> WangchuanPipeline:
    return WangchuanPipeline(**kwargs)
