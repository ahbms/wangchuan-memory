from __future__ import annotations

"""
意识反馈评估器

说明：
- 当前 consciousness evaluator 的职责名入口已经上移到 `tiangong.consciousness.evaluator`
- 本文件当前属于 v3 consciousness 的兼容入口 / 实现承载层
- 如果你在找“意识系统怎么判断用户反馈是正向/负向/中性”，优先看 `tiangong.consciousness.evaluator`
- 当前主 recall 主链依然优先看 `wangchuan.recall_service`
"""

import uuid

from wangchuan._adapters.consciousness_adapter import get_consciousness_schemas

_, _, _, Evaluation = get_consciousness_schemas()


class Evaluator:
    def evaluate_user_feedback(self, text: str, related_rule_ids=None, source_event_id=None) -> Evaluation:
        text = (text or "").strip()

        if any(k in text for k in ["对", "可以", "明白", "好", "继续"]):
            return Evaluation(
                evaluation_id=str(uuid.uuid4()),
                target="response",
                outcome="positive",
                reason="用户正向接受",
                reinforcement=0.15,
                related_rule_ids=related_rule_ids or [],
                source_event_id=source_event_id,
            )

        if any(k in text for k in ["不对", "错了", "不是", "别这样"]):
            return Evaluation(
                evaluation_id=str(uuid.uuid4()),
                target="response",
                outcome="negative",
                reason="用户纠正或否定",
                decay=0.20,
                related_rule_ids=related_rule_ids or [],
                source_event_id=source_event_id,
            )

        return Evaluation(
            evaluation_id=str(uuid.uuid4()),
            target="response",
            outcome="neutral",
            reason="反馈不明显",
            related_rule_ids=related_rule_ids or [],
            source_event_id=source_event_id,
        )
