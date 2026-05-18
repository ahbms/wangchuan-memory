#!/usr/bin/env python3
"""
意识层群觉兼容入口 / 实现承载层入口

说明：
- 当前正式职责名入口优先看 tiangong.consciousness.group_awareness
- 本文件当前属于群觉层的兼容入口 / 实现承载层
- 如果你在找“系统如何估计对方当下状态并给出社会性回复建议”，优先看 tiangong.consciousness.group_awareness
- 当前主 recall 主链依然优先看 wangchuan.recall_service
- 不要再把本文件当成新的群觉层入口继续扩散使用
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List

from wangchuan.paths import workspace_root


class SocialMode(Enum):
    """当前更适合采取的社会互动模式"""
    DIRECT = "direct"          # 直接回答，少铺垫
    SUPPORTIVE = "supportive"  # 先接住情绪，再推进
    COLLABORATIVE = "collaborative"  # 一起想、一起来做
    RESERVED = "reserved"      # 少打扰，克制输出


@dataclass
class CounterpartModel:
    """对当前对话对象的轻量心智画像（运行时，不是长期人格定论）"""
    user_id: str = "unknown"
    inferred_state: str = "neutral"      # neutral / engaged / confused / frustrated / affirmative
    trust_level: float = 0.5              # 0-1
    emotional_tone: str = "neutral"      # neutral / positive / negative / urgent
    cooperation_level: float = 0.5        # 0-1
    last_signal: str = ""
    updated_at: str = ""


@dataclass
class SocialCue:
    """从单条消息里抽出的社会线索"""
    text: str
    cue_type: str                         # affirmation / confusion / correction / urgency / warmth / neutral
    valence: float                        # -1 ~ 1
    intensity: float                      # 0 ~ 1
    evidence: List[str] = field(default_factory=list)
    timestamp: str = ""


@dataclass
class SocialGuidance:
    """给主回复链提供的社会性建议"""
    mode: str
    inferred_state: str
    should_push_forward: bool
    should_soften_tone: bool
    should_be_brief: bool
    rationale: str
    cues: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""


WORKSPACE_ROOT = workspace_root()


class GroupAwareness:
    """
    群觉层最小主模块。

    做的事很朴素：
    - 从消息里提取社会线索
    - 更新对当前对象的轻量状态估计
    - 给出一份可解释的回复策略建议
    """

    STATE_PATH = str(WORKSPACE_ROOT / "memory" / "group_awareness_state.json")
    HISTORY_PATH = str(WORKSPACE_ROOT / "memory" / "group_awareness_log.jsonl")

    def __init__(self):
        self.models: Dict[str, CounterpartModel] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.STATE_PATH):
            try:
                with open(self.STATE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for user_id, payload in data.get("models", {}).items():
                    self.models[user_id] = CounterpartModel(**payload)
            except Exception:
                self.models = {}

    def _save(self):
        os.makedirs(os.path.dirname(self.STATE_PATH), exist_ok=True)
        data = {
            "models": {user_id: asdict(model) for user_id, model in self.models.items()},
            "updated_at": datetime.now().isoformat(),
        }
        with open(self.STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _append_log(self, item: Dict[str, Any]):
        os.makedirs(os.path.dirname(self.HISTORY_PATH), exist_ok=True)
        with open(self.HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def _get_model(self, user_id: str | None) -> CounterpartModel:
        key = user_id or "unknown"
        if key not in self.models:
            self.models[key] = CounterpartModel(user_id=key, updated_at=datetime.now().isoformat())
        return self.models[key]

    def extract_cues(self, text: str) -> List[SocialCue]:
        text = (text or "").strip()
        if not text:
            return []

        lowered = text.lower()
        now = datetime.now().isoformat()
        cues: List[SocialCue] = []

        def add(cue_type: str, valence: float, intensity: float, evidence: List[str]):
            cues.append(SocialCue(
                text=text,
                cue_type=cue_type,
                valence=valence,
                intensity=intensity,
                evidence=evidence,
                timestamp=now,
            ))

        if any(k in text for k in ["先别动", "别动", "暂停", "等等", "等下", "我先想想", "先想想"]):
            add("hold", -0.05, 0.9, ["出现暂停/暂缓推进词"])
        if any(k in text for k in ["直接给答案", "直接说结论", "直接说", "别解释了", "不要解释", "少解释", "直接答"]):
            add("direct_answer", 0.2, 0.85, ["出现直接回答/少铺垫词"])
        if any(k in text for k in ["可以", "继续", "对", "行", "好", "OK", "ok"]):
            add("affirmation", 0.6, 0.6, ["出现肯定/放行词"])
        if any(k in text for k in ["不是", "不对", "答偏", "错了", "我指的是", "不是这个"]):
            add("correction", -0.4, 0.7, ["出现纠正/改向词"])
        if any(k in text for k in ["什么意思", "解释", "没懂", "为什么"]):
            add("confusion", -0.1, 0.5, ["出现求解释/求澄清词"])
        if any(k in text for k in ["看一下", "帮我看", "你看下", "看下"]):
            add("review_request", 0.15, 0.45, ["出现查看/分析请求，倾向任务分流而非求解释"])
        if any(k in text for k in ["赶紧", "马上", "立刻", "现在", "尽快"]):
            add("urgency", -0.1, 0.8, ["出现紧迫词"])
        if any(k in text for k in ["谢谢", "辛苦", "哈哈", "可以可以", "牛", "不错"]):
            add("warmth", 0.7, 0.5, ["出现正向社交词"])
        if not cues:
            add("neutral", 0.0, 0.2, ["未命中特殊社会线索"])
        if len(text) <= 6 and any(k in lowered for k in ["可以", "继续", "行", "好"]):
            add("greenlight", 0.5, 0.7, ["短确认消息，倾向授权继续推进"])
        return cues

    def update_model(self, text: str, user_id: str | None = None) -> CounterpartModel:
        model = self._get_model(user_id)
        cues = self.extract_cues(text)

        inferred_state = "neutral"
        emotional_tone = "neutral"
        trust_delta = 0.0
        coop_delta = 0.0

        cue_types = {cue.cue_type for cue in cues}
        if "hold" in cue_types:
            inferred_state = "reserved"
            emotional_tone = "neutral"
            coop_delta -= 0.04
        elif "correction" in cue_types:
            inferred_state = "frustrated"
            emotional_tone = "negative"
            trust_delta -= 0.03
        elif "confusion" in cue_types:
            inferred_state = "confused"
            emotional_tone = "neutral"
        elif "review_request" in cue_types:
            inferred_state = "engaged"
            emotional_tone = "neutral"
            coop_delta += 0.03
        elif "direct_answer" in cue_types:
            inferred_state = "affirmative"
            emotional_tone = "urgent"
            coop_delta += 0.04
        elif "affirmation" in cue_types or "greenlight" in cue_types:
            inferred_state = "affirmative"
            emotional_tone = "positive"
            trust_delta += 0.02
            coop_delta += 0.08
        elif "warmth" in cue_types:
            inferred_state = "engaged"
            emotional_tone = "positive"
            trust_delta += 0.04
            coop_delta += 0.04
        elif "urgency" in cue_types:
            inferred_state = "engaged"
            emotional_tone = "urgent"
            coop_delta += 0.03

        model.inferred_state = inferred_state
        model.emotional_tone = emotional_tone
        model.trust_level = min(max(model.trust_level + trust_delta, 0.0), 1.0)
        model.cooperation_level = min(max(model.cooperation_level + coop_delta, 0.0), 1.0)
        model.last_signal = text[:120]
        model.updated_at = datetime.now().isoformat()

        self._save()
        self._append_log({
            "user_id": model.user_id,
            "text": text,
            "cues": [asdict(c) for c in cues],
            "model": asdict(model),
            "timestamp": model.updated_at,
        })
        return model

    def get_guidance(self, text: str, user_id: str | None = None) -> SocialGuidance:
        model = self.update_model(text, user_id=user_id)
        cues = self.extract_cues(text)
        cue_types = {cue.cue_type for cue in cues}

        mode = SocialMode.DIRECT.value
        should_push_forward = False
        should_soften_tone = False
        should_be_brief = False
        rationale = "默认直接推进。"

        if "hold" in cue_types:
            mode = SocialMode.RESERVED.value
            should_be_brief = True
            rationale = "用户要求先暂停，不应继续推进，只做简短确认并等待。"
        elif "direct_answer" in cue_types:
            mode = SocialMode.DIRECT.value
            should_push_forward = True
            should_be_brief = True
            rationale = "用户明确要求直接给结论，应减少铺垫、立即回答。"
        elif "correction" in cue_types:
            mode = SocialMode.SUPPORTIVE.value
            should_soften_tone = True
            should_be_brief = True
            rationale = "用户在纠偏，先承认偏差并迅速对准，不要硬拗。"
        elif "confusion" in cue_types:
            mode = SocialMode.SUPPORTIVE.value
            should_soften_tone = True
            rationale = "用户需要澄清，先解释清楚，再继续推进。"
        elif "review_request" in cue_types:
            mode = SocialMode.COLLABORATIVE.value
            should_push_forward = True
            rationale = "用户是在请求查看/分析，适合进入并肩处理而不是先走解释分流。"
        elif "affirmation" in cue_types or "greenlight" in cue_types:
            mode = SocialMode.COLLABORATIVE.value
            should_push_forward = True
            should_be_brief = True
            rationale = "用户已明确放行，适合少废话直接继续。"
        elif "urgency" in cue_types:
            mode = SocialMode.DIRECT.value
            should_push_forward = True
            should_be_brief = True
            rationale = "用户强调时效，优先短路径完成。"
        elif "warmth" in cue_types:
            mode = SocialMode.COLLABORATIVE.value
            should_push_forward = True
            rationale = "互动氛围积极，可在保持效率前提下更像并肩协作。"
        elif model.cooperation_level < 0.3:
            mode = SocialMode.RESERVED.value
            should_soften_tone = True
            should_be_brief = True
            rationale = "当前合作感偏低，应克制输出，避免压迫感。"

        return SocialGuidance(
            mode=mode,
            inferred_state=model.inferred_state,
            should_push_forward=should_push_forward,
            should_soften_tone=should_soften_tone,
            should_be_brief=should_be_brief,
            rationale=rationale,
            cues=[asdict(c) for c in cues],
            timestamp=datetime.now().isoformat(),
        )

    def get_report(self, user_id: str | None = None) -> Dict[str, Any]:
        if user_id:
            model = self.models.get(user_id)
            return asdict(model) if model else {"user_id": user_id, "status": "unknown"}
        return {
            "layer": "群觉层",
            "module": "group_awareness.py",
            "models": len(self.models),
            "updated_at": datetime.now().isoformat(),
        }


__all__ = [
    "SocialMode",
    "CounterpartModel",
    "SocialCue",
    "SocialGuidance",
    "GroupAwareness",
]
