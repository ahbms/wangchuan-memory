from __future__ import annotations

"""
明识层 - 反思提炼器
天工开智 / 意识进化体系 · 第8层（实现承载）

说明：
- 当前 consciousness reflector 的职责名入口已经上移到 `tiangong.consciousness.reflector`
- 本文件当前属于 v3 consciousness 的兼容入口 / 实现承载层
- 如果你在找“意识系统如何把事件提炼成可落地经验”，优先看 `tiangong.consciousness.reflector`
- 当前主 recall 主链依然优先看 `wangchuan.recall_service`
"""

import uuid

from wangchuan._adapters.consciousness_adapter import get_consciousness_schemas

Event, ProposedUpdate, Reflection, _ = get_consciousness_schemas()


class Reflector:
    def reflect(self, event: Event) -> Reflection | None:
        text = (event.content or event.summary or "").strip()
        if not text:
            return None

        if event.metadata.get("noisy") or "noisy_tool_result" in (event.tags or []):
            return None

        if event.type == "correction":
            return Reflection(
                reflection_id=str(uuid.uuid4()),
                event_id=event.event_id,
                lesson=text[:120],
                category="behavior",
                scope="global",
                confidence=0.9,
                actionability="high",
                proposed_updates=[
                    ProposedUpdate(
                        type="behavior_rule",
                        scope="global",
                        trigger="similar_user_interaction",
                        reason="用户明确纠正，说明当前行为模式需要修正",
                        confidence=0.9,
                        new_value=text[:120],
                        source_event_ids=[event.event_id],
                    )
                ],
            )

        if event.type == "error":
            return Reflection(
                reflection_id=str(uuid.uuid4()),
                event_id=event.event_id,
                lesson=f"失败后先验证再继续：{event.summary}",
                category="risk",
                scope="task_type",
                confidence=0.74,
                actionability="medium",
                proposed_updates=[
                    ProposedUpdate(
                        type="risk_adjustment",
                        scope="task_type",
                        trigger=event.source,
                        reason="失败说明当前路径不稳，需要提高验证强度",
                        confidence=0.74,
                        new_value={"verification_required": True},
                        source_event_ids=[event.event_id],
                    )
                ],
            )

        if event.type == "feedback":
            return Reflection(
                reflection_id=str(uuid.uuid4()),
                event_id=event.event_id,
                lesson=f"该响应方向被正向接受：{event.summary}",
                category="style",
                scope="session",
                confidence=0.62,
                actionability="medium",
                proposed_updates=[],
            )

        return None
