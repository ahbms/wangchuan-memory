"""
Context 组装模块 — 从 consciousness 上下文推导执行策略、唤醒包、响应策略

从 WangchuanPipeline 中提取的模块，依赖 FormatBlocks、QueryProfiler。
包含工具函数：_safe_read_text, _extract_bullets_from_markdown, _derive_vitality_state 等。
"""

import math
import re
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from typing import Dict, List
from urllib.parse import quote

from wangchuan.paths import workspace_root

from .format_blocks import FormatBlocks
from .query_profiler import QueryProfiler


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def extract_bullets_from_markdown(text: str, heading: str, limit: int = 5) -> List[str]:
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


def parse_datetime_maybe(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            return parsed.astimezone().replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def derive_vitality_state(item: Dict[str, object]) -> Dict[str, object]:
    quality = clamp01(float(item.get("quality_score") or item.get("confidence") or 0.0))
    confidence = clamp01(float(item.get("confidence") or 0.0))
    hotness = clamp01(float(item.get("hotness_score") or 0.0))

    try:
        trigger_count = max(0, int(item.get("trigger_count") or 0))
    except Exception:
        trigger_count = 0
    trigger_signal = clamp01(math.log1p(trigger_count) / math.log(6)) if trigger_count > 0 else 0.0

    recency_probe = (
        item.get("last_recall")
        or item.get("last_confirmed_at")
        or item.get("updated_at")
        or item.get("created_at")
    )
    recency_dt = parse_datetime_maybe(recency_probe)
    days_since = None
    recency_score = 0.0
    if recency_dt is not None:
        days_since = max(0.0, (datetime.now() - recency_dt).total_seconds() / 86400.0)
        recency_score = clamp01(math.exp(-days_since / 30.0))

    lifecycle = str(item.get("lifecycle") or "").strip().lower()
    lifecycle_factor = {
        "active": 0.12,
        "accepted": 0.08,
        "candidate": 0.04,
        "aging": -0.18,
        "archived": -0.36,
        "superseded": -0.55,
        "removed": -0.7,
    }.get(lifecycle, 0.0)

    vitality = (
        quality * 0.34
        + confidence * 0.18
        + hotness * 0.20
        + trigger_signal * 0.12
        + recency_score * 0.16
        + lifecycle_factor
    )
    vitality = clamp01(vitality)
    is_dormant = vitality < 0.05

    return {
        "vitality": round(vitality, 6),
        "is_dormant": bool(is_dormant),
        "last_activated": str(item.get("last_recall") or item.get("last_confirmed_at") or ""),
        "vitality_inputs": {
            "quality_score": round(quality, 6),
            "confidence": round(confidence, 6),
            "hotness_score": round(hotness, 6),
            "trigger_signal": round(trigger_signal, 6),
            "recency_score": round(recency_score, 6),
            "days_since_activation": None if days_since is None else round(days_since, 3),
            "lifecycle": lifecycle or "unknown",
        },
    }


def build_memory_context_uri(item: Dict[str, object]) -> str:
    memory_id = str(item.get("memory_id") or "").strip()
    if memory_id:
        return f"memory://wangchuan/id/{quote(memory_id, safe='._-')}"

    source_anchor = str(item.get("source_anchor") or "").strip()
    if source_anchor:
        return f"memory://wangchuan/anchor/{quote(source_anchor)}"

    turn_signature = str(item.get("turn_signature") or "").strip()
    if turn_signature:
        return f"memory://wangchuan/turn/{quote(turn_signature, safe='._-')}"

    return ""


def normalize_memory_item_explain(item: Dict) -> Dict:
    shaped = dict(item)
    raw_explain = shaped.get("explain") or shaped.get("recall_explain")
    if not isinstance(raw_explain, dict) or not raw_explain:
        return shaped

    explain = dict(raw_explain)
    if "final_score" not in explain and explain.get("final_rank_score") is not None:
        explain["final_score"] = explain.get("final_rank_score")
    if "raw_penalty" not in explain:
        explain["raw_penalty"] = explain.get("raw_conversation_penalty", 0.0)
    if shaped.get("ranking_score") is not None and "pipeline_ranking_score" not in explain:
        explain["pipeline_ranking_score"] = shaped.get("ranking_score")

    shaped["explain"] = explain
    shaped["recall_explain"] = dict(explain)
    if explain.get("summary"):
        shaped["explain_summary"] = str(explain.get("summary"))
    return shaped


def shape_memory_items_for_output(items: List[Dict]) -> List[Dict]:
    if not items:
        return []
    shaped_items: List[Dict] = []
    for item in items:
        shaped = normalize_memory_item_explain(item)
        shaped.setdefault("context_type", "memory")
        context_uri = shaped.get("context_uri") or build_memory_context_uri(shaped)
        if context_uri:
            shaped["context_uri"] = context_uri
        vitality_state = derive_vitality_state(shaped)
        shaped.update(vitality_state)
        shaped_items.append(shaped)
    return shaped_items


# ---------------------------------------------------------------------------
# ContextAssembler 类
# ---------------------------------------------------------------------------

class ContextAssembler:
    """Context 组装与策略器"""

    def __init__(self, consciousness=None, assemble_engine=None):
        self._consciousness = consciousness
        self._assemble_engine = assemble_engine

    def _score_wakeup_scar_item(self, query: str, scar: str) -> float:
        profile = QueryProfiler.build_query_preference_profile(query)
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
        scar_candidates = extract_bullets_from_markdown(memory_md, "认知精华（伤疤）", limit=12)
        if not scar_candidates:
            scar_candidates = extract_bullets_from_markdown(memory_md, "铁律", limit=12)
        ranked_scars = []
        for scar in scar_candidates:
            ranked_scars.append((self._score_wakeup_scar_item(query, scar), scar))
        ranked_scars.sort(key=lambda pair: pair[0], reverse=True)
        selected = [scar for _, scar in ranked_scars[:limit] if scar]
        if selected:
            return selected
        return scar_candidates[:limit]

    def build_wakeup_pack(self, query: str, session_id: str = None) -> str:
        workspace = workspace_root()
        user_md = safe_read_text(workspace / "USER.md")
        memory_md = safe_read_text(workspace / "MEMORY.md")
        profile = QueryProfiler.build_query_preference_profile(query)

        try:
            from wangchuan.runtime_state import get_runtime_energy_state
            runtime = get_runtime_energy_state() or {}
        except Exception:
            runtime = {}

        preferred_name = ""
        match = re.search(r"What to call them:\*\*\s*(.+)", user_md)
        if match:
            preferred_name = match.group(1).strip()
        else:
            match = re.search(r"称呼[:：]\*\*\s*(.+)", memory_md)
            if match:
                preferred_name = match.group(1).strip()

        user_prefs = extract_bullets_from_markdown(user_md, "沟通风格偏好", limit=4)
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

    def derive_execution_guidance(self, consciousness_context: str) -> Dict:
        hints = FormatBlocks.extract_block_items(consciousness_context, "decision_hints")
        rules = FormatBlocks.extract_block_items(consciousness_context, "self_state")

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

    def build_response_strategy(self, consciousness_context: str) -> str:
        guidance = self.derive_execution_guidance(consciousness_context)
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
    def summarize_history_support(assembly) -> Dict[str, object]:
        """摘要 history assembly 支持度"""
        if assembly is None:
            return {
                "recent_messages": 0,
                "history_recall": 0,
                "recalled_context": 0,
                "support_items": 0,
                "should_override_memory": False,
                "boundary": "history_support_only",
            }

        if isinstance(assembly, dict):
            formatted = str(assembly.get("formatted_context", "") or "")
            recent_items = FormatBlocks.extract_block_items(formatted, "recent_messages")
            history_items = FormatBlocks.extract_block_items(formatted, "history_recall")
            context_items = FormatBlocks.extract_block_items(formatted, "recalled_context")
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
    def compact_assembly_for_short_followup(assembly):
        """短追问时压缩 assembly"""
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
