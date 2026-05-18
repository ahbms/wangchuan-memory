from __future__ import annotations

"""
明识层 - 意识闭环引擎
天工开智 / 意识进化体系 · 第8层（实现承载）

说明：
- 当前 consciousness engine 的职责名入口已经上移到 `tiangong.consciousness.engine`
- 本文件当前属于 v3 consciousness 的兼容入口 / 实现承载层
- 如果你在找“意识中枢应该从哪里开始读”，优先看 `tiangong.consciousness.engine`
- 当前主 recall 主链依然优先看 `wangchuan.recall_service`
- 这是明识层的工程实现中枢，不等于整套意识系统的全部
- 与叙我层、忆藏层、融汇层、行愿层存在直接协作关系
"""

import json
from dataclasses import asdict
from pathlib import Path

from .event_extractor import from_message, from_tool_result
from .hygiene import ConsciousnessHygiene
from wangchuan._adapters.consciousness_adapter import (
    get_consciousness_debug_tools_cls,
    get_evaluator_cls,
    get_identity_adapter_cls,
    get_injector_cls,
    get_link_tracker_cls,
    get_reflector_cls,
    get_scar_selector_cls,
    get_self_state_cls,
    get_session_tracker_cls,
    get_strategy_updater_cls,
)

ConsciousnessDebugTools = get_consciousness_debug_tools_cls()
Evaluator = get_evaluator_cls()
IdentityAdapter = get_identity_adapter_cls()
ConsciousnessInjector = get_injector_cls()
RuleLinkTracker = get_link_tracker_cls()
Reflector = get_reflector_cls()
ScarSelector = get_scar_selector_cls()
SelfStateStore, SelfStateUpdater = get_self_state_cls()
SessionRuleTracker = get_session_tracker_cls()
StrategyUpdater = get_strategy_updater_cls()


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
REFLECTIONS_PATH = MEMORY_DIR / "reflections.jsonl"
SCARS_PATH = MEMORY_DIR / "scars.jsonl"
EVALUATIONS_PATH = MEMORY_DIR / "evaluations.jsonl"
TOOL_RESULTS_PATH = MEMORY_DIR / "tool_results.jsonl"


def _append_jsonl(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


class ConsciousnessEngine:
    def __init__(self):
        self.reflector = Reflector()
        self.selector = ScarSelector()
        self.state_store = SelfStateStore()
        self.state_updater = SelfStateUpdater()
        self.strategy = StrategyUpdater()
        self.identity = IdentityAdapter()
        self.evaluator = Evaluator()
        self.injector = ConsciousnessInjector()
        self.sessions = SessionRuleTracker()
        self.links = RuleLinkTracker()
        self.debug = ConsciousnessDebugTools()
        self.hygiene = ConsciousnessHygiene()

    def _apply_scar(self, scar, session_id: str | None = None, source: str = "message", event_id: str | None = None) -> dict:
        _append_jsonl(SCARS_PATH, asdict(scar))
        rules = self.strategy.apply_scar(scar)
        state = self.state_store.load()
        state = self.state_updater.apply_rule(state, scar.lesson)
        self.state_store.save(state)
        self.identity.apply_shift(f"behavior_adjustment:{scar.lesson[:50]}")
        applied_rule = next((r for r in rules if r.get("scar_id") == scar.scar_id), None)
        if session_id and applied_rule:
            self.sessions.record(session_id, applied_rule, source=source)
        if applied_rule:
            self.links.record_activation(
                session_id=session_id,
                event_id=event_id,
                rule_id=applied_rule.get("scar_id") or applied_rule.get("rule_id"),
                lesson=applied_rule.get("lesson"),
                source=source,
            )
        return applied_rule or {}

    def _match_session_rule(self, session_id: str | None, fallback_text: str = "") -> dict | None:
        if session_id:
            hit = self.sessions.get(session_id)
            if hit and hit.get("rule_id"):
                rules = self.strategy.load_rules()
                for rule in rules:
                    if rule.get("scar_id") == hit.get("rule_id") or rule.get("rule_id") == hit.get("rule_id"):
                        return rule
        return self.strategy.find_best_matching_rule(fallback_text)

    def process_message(self, role: str, text: str, channel: str = "unknown", user_id: str | None = None) -> dict:
        event = from_message(role, text, channel=channel, user_id=user_id)
        reflection = self.reflector.reflect(event)
        scar = self.selector.to_scar(reflection)
        evaluation = None
        matched_rule = None

        if reflection:
            _append_jsonl(REFLECTIONS_PATH, asdict(reflection))

        if scar:
            matched_rule = self._apply_scar(scar, session_id=user_id, source="message", event_id=event.event_id)
        elif event.type == "feedback":
            matched_rule = self._match_session_rule(user_id, text)
            related_rule_ids = [matched_rule.get("scar_id")] if matched_rule and matched_rule.get("scar_id") else []
            evaluation = self.evaluator.evaluate_user_feedback(text, related_rule_ids=related_rule_ids, source_event_id=event.event_id)
            _append_jsonl(EVALUATIONS_PATH, asdict(evaluation))
            if matched_rule and matched_rule.get("scar_id"):
                if evaluation.outcome == "positive":
                    self.strategy.reinforce(rule_id=matched_rule.get("scar_id"), amount=evaluation.reinforcement)
                elif evaluation.outcome == "negative":
                    self.strategy.decay(rule_id=matched_rule.get("scar_id"), amount=evaluation.decay)
                self.sessions.record(user_id, matched_rule, source="feedback")
                self.links.record_feedback_application(
                    session_id=user_id,
                    event_id=event.event_id,
                    rule_id=matched_rule.get("scar_id"),
                    lesson=matched_rule.get("lesson"),
                    outcome=evaluation.outcome,
                    feedback_text=text,
                )

        return {
            "event": asdict(event),
            "reflection": asdict(reflection) if reflection else None,
            "scar": asdict(scar) if scar else None,
            "evaluation": asdict(evaluation) if evaluation else None,
            "matched_rule": matched_rule,
        }

    def process_feedback(self, text: str, positive: bool, user_id: str | None = None) -> dict:
        feedback_text = text or ("对，可以，继续。" if positive else "不对，刚才那个方向有问题。")
        matched_rule = self._match_session_rule(user_id, feedback_text)
        if not matched_rule:
            rules = self.strategy.load_rules()
            matched_rule = rules[0] if rules else None
        related_rule_ids = [matched_rule.get("scar_id")] if matched_rule and matched_rule.get("scar_id") else []
        evaluation = self.evaluator.evaluate_user_feedback(
            "对，可以，继续。" if positive else "不对，错了，别这样。",
            related_rule_ids=related_rule_ids,
            source_event_id=None,
        )
        _append_jsonl(EVALUATIONS_PATH, asdict(evaluation))
        if matched_rule and matched_rule.get("scar_id"):
            if positive:
                self.strategy.reinforce(rule_id=matched_rule.get("scar_id"), amount=evaluation.reinforcement)
            else:
                self.strategy.decay(rule_id=matched_rule.get("scar_id"), amount=evaluation.decay)
            self.sessions.record(user_id, matched_rule, source="feedback")
            self.links.record_feedback_application(
                session_id=user_id,
                event_id=None,
                rule_id=matched_rule.get("scar_id"),
                lesson=matched_rule.get("lesson"),
                outcome=evaluation.outcome,
                feedback_text=feedback_text,
            )
        return {
            "evaluation": asdict(evaluation),
            "matched_rule": matched_rule,
            "input": feedback_text,
            "positive": positive,
        }

    def process_tool_result(self, tool_name: str, ok: bool, content: str = "", session_id: str | None = None) -> dict:
        event = from_tool_result(tool_name, ok, content)
        reflection = self.reflector.reflect(event)
        scar = self.selector.to_scar(reflection)
        matched_rule = None
        _append_jsonl(TOOL_RESULTS_PATH, asdict(event))
        if reflection:
            _append_jsonl(REFLECTIONS_PATH, asdict(reflection))
        if scar:
            matched_rule = self._apply_scar(scar, session_id=session_id, source=f"tool:{tool_name}", event_id=event.event_id)
            if matched_rule:
                self.links.record_tool_validation(
                    session_id=session_id,
                    event_id=event.event_id,
                    rule_id=matched_rule.get("scar_id"),
                    lesson=matched_rule.get("lesson"),
                    tool_name=tool_name,
                    ok=ok,
                    content=content,
                )
        elif ok:
            matched_rule = self._match_session_rule(session_id, content or tool_name)
            if matched_rule and matched_rule.get("scar_id"):
                self.strategy.reinforce(rule_id=matched_rule.get("scar_id"), amount=0.05)
                self.sessions.record(session_id, matched_rule, source=f"tool:{tool_name}")
                self.links.record_tool_validation(
                    session_id=session_id,
                    event_id=event.event_id,
                    rule_id=matched_rule.get("scar_id"),
                    lesson=matched_rule.get("lesson"),
                    tool_name=tool_name,
                    ok=ok,
                    content=content,
                )
        else:
            matched_rule = self._match_session_rule(session_id, content or tool_name)
            if matched_rule and matched_rule.get("scar_id"):
                self.sessions.record(session_id, matched_rule, source=f"tool:{tool_name}")
                self.links.record_tool_validation(
                    session_id=session_id,
                    event_id=event.event_id,
                    rule_id=matched_rule.get("scar_id"),
                    lesson=matched_rule.get("lesson"),
                    tool_name=tool_name,
                    ok=ok,
                    content=content,
                )
        return {
            "event": asdict(event),
            "reflection": asdict(reflection) if reflection else None,
            "scar": asdict(scar) if scar else None,
            "matched_rule": matched_rule,
        }

    def get_prompt_fragment(self, user_text: str = "", user_id: str | None = None) -> str:
        return self.injector.build_prompt_fragment(user_text=user_text, user_id=user_id)

    def debug_report(self, session_id: str | None = None, tail: int = 5) -> dict:
        return self.debug.build_report(session_id=session_id, tail=tail)

    def run_hygiene(self) -> dict:
        return self.hygiene.run()
