from __future__ import annotations

"""
明识层 - 协议模型
天工开智 / 意识进化体系 · 第8层（实现承载）

说明：
- 当前 consciousness schemas 的职责名入口已经上移到 `tiangong.consciousness.schemas`
- 本文件当前属于 v3 consciousness 的兼容入口 / 实现承载层
- 如果你在找“意识层内部事件 / 反思 / 伤疤 / 状态对象到底长什么样”，优先看 `tiangong.consciousness.schemas`
- 当前主 recall 主链依然优先看 `wangchuan.recall_service`
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from typing import Any, Dict, List, Literal, Optional

EventType = Literal[
    "conversation", "task", "error", "success", "correction", "feedback", "heartbeat", "system"
]
UpdateType = Literal[
    "behavior_rule", "identity_shift", "risk_adjustment", "style_adjustment", "goal_priority_update"
]
ScopeType = Literal["global", "user", "channel", "task_type", "session"]
OutcomeType = Literal["positive", "neutral", "negative"]


@dataclass
class Event:
    event_id: str
    ts: str
    type: EventType
    source: str
    summary: str
    content: str = ""
    importance: float = 0.0
    tags: List[str] = field(default_factory=list)
    raw_refs: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProposedUpdate:
    type: UpdateType
    scope: ScopeType
    trigger: str
    reason: str
    confidence: float
    new_value: Any = None
    old_value: Any = None
    ttl: str = "30d"
    source_event_ids: List[str] = field(default_factory=list)


@dataclass
class Reflection:
    reflection_id: str
    event_id: str
    lesson: str
    category: str
    scope: ScopeType
    confidence: float
    actionability: str
    proposed_updates: List[ProposedUpdate] = field(default_factory=list)


@dataclass
class Scar:
    scar_id: str
    lesson: str
    why_it_matters: str
    trigger: str
    update_type: UpdateType
    strength: float
    scope: ScopeType = "global"
    source_event_ids: List[str] = field(default_factory=list)
    state: str = "active"


@dataclass
class SelfState:
    version: int
    updated_at: str
    mode: str
    initiative_level: float
    caution_level: float
    confidence_level: float
    execution_bias: float
    social_mode: str
    top_active_rules: List[str] = field(default_factory=list)
    top_active_goals: List[str] = field(default_factory=list)
    recent_shifts: List[str] = field(default_factory=list)


@dataclass
class Evaluation:
    evaluation_id: str
    target: str
    outcome: OutcomeType
    reason: str
    reinforcement: float = 0.0
    decay: float = 0.0
    related_rule_ids: List[str] = field(default_factory=list)
    source_event_id: Optional[str] = None
