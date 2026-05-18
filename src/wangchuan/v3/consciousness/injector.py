from __future__ import annotations

"""
融汇层 - 轻量接入器 / Prompt 注入器
天工开智 / 意识进化体系 · 第4层（实现承载）

说明：
- 当前 consciousness injector 的职责名入口已经上移到 `tiangong.consciousness.injector`
- 本文件当前属于 v3 consciousness 的兼容入口 / 实现承载层
- 如果你在找“意识状态怎么注入上下文”，优先看 `tiangong.consciousness.injector`
- 当前主 recall 主链依然优先看 `wangchuan.recall_service`
- 相比 `global_workspace.py` 的控制平面版本，这里更偏运行时接入器实现
"""

from wangchuan._adapters.consciousness_adapter import (
    get_self_state_cls,
    get_strategy_updater_cls,
)

SelfStateStore, SelfStateUpdater = get_self_state_cls()
StrategyUpdater = get_strategy_updater_cls()
from ..group_awareness import GroupAwareness


class ConsciousnessInjector:
    def __init__(self):
        self.group_awareness = GroupAwareness()
        self.state_store = SelfStateStore()
        self.state_updater = SelfStateUpdater()

    @staticmethod
    def _contains_any(text: str, tokens: list[str]) -> bool:
        return any(token in text for token in tokens)

    def _score_rule_for_guidance(self, rule: dict, guidance) -> float:
        base = float(rule.get("strength", 0.0))
        if not guidance:
            return base

        lesson = (rule.get("lesson") or "").strip()
        mode = getattr(guidance, "mode", "") or ""
        inferred_state = getattr(guidance, "inferred_state", "") or ""
        score = base

        explanation_tokens = ["先解释", "解释", "澄清", "对齐", "先确认", "聊聊", "铺垫"]
        execution_block_tokens = ["不要默认直接执行", "不要一上来就执行", "别一上来就执行", "先不要"]
        collaborative_tokens = [
            "继续", "推进", "执行", "直接", "少废话", "放行", "授权", "绿灯",
            "目标已经对齐", "目标对齐", "不重复铺垫", "并肩推进", "反复确认", "短确认",
            "继续往前", "优先推进", "确认后推进",
        ]
        reserved_tokens = ["克制", "简短", "少打扰", "先确认"]
        direct_tokens = ["短路径", "直接", "尽快", "立刻"]

        if mode == "supportive":
            if self._contains_any(lesson, explanation_tokens + execution_block_tokens):
                score += 0.35
            if self._contains_any(lesson, ["马上做", "立刻做"]) and not self._contains_any(lesson, execution_block_tokens):
                score -= 0.15
        elif mode == "collaborative":
            if self._contains_any(lesson, collaborative_tokens):
                score += 0.34
            if self._contains_any(lesson, explanation_tokens):
                score -= 0.10
            if self._contains_any(lesson, execution_block_tokens):
                score -= 0.16
        elif mode == "reserved":
            if self._contains_any(lesson, reserved_tokens):
                score += 0.25
        elif mode == "direct":
            if self._contains_any(lesson, direct_tokens):
                score += 0.2

        if inferred_state == "frustrated" and self._contains_any(lesson, ["解释", "澄清", "对齐"]):
            score += 0.1
        if inferred_state == "affirmative":
            if self._contains_any(lesson, collaborative_tokens):
                score += 0.14
            if self._contains_any(lesson, execution_block_tokens):
                score -= 0.06
        return round(score, 3)

    def _select_rules(self, guidance=None, limit: int = 3) -> list[dict]:
        rules = StrategyUpdater().load_rules()
        if not rules:
            return []
        ranked = sorted(
            rules,
            key=lambda r: (self._score_rule_for_guidance(r, guidance), r.get("updated_at", "")),
            reverse=True,
        )
        return ranked[:limit]

    def _build_decision_hints(self, guidance, rules: list[dict]) -> list[str]:
        hints: list[str] = []
        rule_texts = [(r.get("lesson") or "").strip() for r in rules if (r.get("lesson") or "").strip()]
        top_rule = rule_texts[0] if rule_texts else ""

        if guidance:
            mode = getattr(guidance, "mode", "") or ""
            rationale = getattr(guidance, "rationale", "") or ""

            if "暂停" in rationale or "等待" in rationale:
                hints.append("strategy_bias=hold")
                hints.append("opening_move=ack_and_pause")
                hints.append("action_policy=pause_and_wait")
            elif "直接给结论" in rationale or "立即回答" in rationale:
                hints.append("strategy_bias=direct_answer")
                hints.append("opening_move=answer_immediately")
                hints.append("reply_shape=direct")
            elif getattr(guidance, "should_push_forward", False):
                hints.append("strategy_bias=push_forward")
            elif getattr(guidance, "should_soften_tone", False):
                hints.append("strategy_bias=stabilize_and_align")

            if getattr(guidance, "should_be_brief", False):
                hints.append("reply_shape=brief")

            if mode == "reserved" and "opening_move=ack_and_pause" not in hints:
                hints.append("strategy_bias=hold")
                hints.append("opening_move=ack_and_pause")
            elif mode == "direct" and "opening_move=answer_immediately" not in hints and ("直接给结论" in rationale or "立即回答" in rationale):
                hints.append("strategy_bias=direct_answer")
                hints.append("opening_move=answer_immediately")

        if "opening_move=ack_and_pause" not in hints and "opening_move=answer_immediately" not in hints:
            if any(token in top_rule for token in ["先解释", "解释", "澄清", "对齐"]):
                hints.append("opening_move=ack_then_explain")
            elif any(token in top_rule for token in ["继续", "推进", "授权推进", "少废话", "并肩推进"]):
                hints.append("opening_move=continue_without_repadding")

        if any(token in top_rule for token in ["反复确认", "短确认", "目标已经对齐", "目标对齐"]):
            hints.append("confirmation_policy=minimize_repeat_confirmation")

        if not hints:
            hints.append("strategy_bias=neutral")
        return hints[:4]

    def build_prompt_fragment(self, user_text: str = "", user_id: str | None = None) -> str:
        state = self.state_store.load()
        state = self.state_updater.decay_social_residue(state)
        guidance = self.group_awareness.get_guidance(user_text, user_id=user_id) if user_text else None
        rules = self._select_rules(guidance=guidance, limit=3)
        decision_hints = self._build_decision_hints(guidance, rules)

        if guidance:
            state = self.state_updater.apply_social_guidance(state, guidance)
        state = self.state_updater.sync_top_rules(state, [r.get("lesson") or "" for r in rules], limit=5)

        self.state_store.save(state)

        lines = [
            "<self_state>",
            f"mode={state.mode}",
            f"initiative_level={state.initiative_level}",
            f"caution_level={state.caution_level}",
            f"confidence_level={state.confidence_level}",
            f"execution_bias={state.execution_bias}",
            f"social_mode={state.social_mode}",
            "top_active_rules:",
        ]

        if not rules:
            lines.append("- none")
        else:
            for r in rules:
                lines.append(f"- {r['lesson']}")

        lines.append("</self_state>")

        if guidance:
            lines.extend([
                "<social_guidance>",
                f"mode={guidance.mode}",
                f"inferred_state={guidance.inferred_state}",
                f"should_push_forward={str(guidance.should_push_forward).lower()}",
                f"should_soften_tone={str(guidance.should_soften_tone).lower()}",
                f"should_be_brief={str(guidance.should_be_brief).lower()}",
                f"rationale={guidance.rationale}",
                "</social_guidance>",
            ])

        lines.extend([
            "<decision_hints>",
            *[f"- {hint}" for hint in decision_hints],
            "</decision_hints>",
        ])

        return "\n".join(lines)
