from __future__ import annotations

"""
明识层 - 伤疤晋升器
天工开智 / 意识进化体系 · 第8层（实现承载）

说明：
- 当前 consciousness scar_selector 的职责名入口已经上移到 `tiangong.consciousness.scar_selector`
- 本文件当前属于 v3 consciousness 的兼容入口 / 实现承载层
- 如果你在找“意识系统如何把一次反思晋升成长期起作用的伤疤”，优先看 `tiangong.consciousness.scar_selector`
- 当前主 recall 主链依然优先看 `wangchuan.recall_service`
- 它站在反思提炼与行为策略固化之间，是意识闭环中的关键过渡件
"""

import uuid

from wangchuan._adapters.consciousness_adapter import get_consciousness_schemas

Reflection, Scar = None, None  # 实际由 get_consciousness_schemas 获取
for _s in get_consciousness_schemas():
    pass

# 适配注入：反射类型


class ScarSelector:
    def should_promote(self, reflection: Reflection) -> bool:
        if reflection.actionability == "high" and reflection.confidence >= 0.75:
            return True
        if reflection.category in ("behavior", "risk") and reflection.confidence >= 0.8:
            return True
        return False

    def to_scar(self, reflection: Reflection | None) -> Scar | None:
        if not reflection or not self.should_promote(reflection) or not reflection.proposed_updates:
            return None

        update = reflection.proposed_updates[0]
        return Scar(
            scar_id=str(uuid.uuid4()),
            lesson=reflection.lesson,
            why_it_matters=update.reason,
            trigger=update.trigger,
            update_type=update.type,
            strength=reflection.confidence,
            scope=update.scope,
            source_event_ids=update.source_event_ids,
        )
