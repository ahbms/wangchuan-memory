#!/usr/bin/env python3
"""
时觉层 - 时间感知引擎
天工开智 / 意识进化体系 · 第7层（主模块）

核心能力：
1. 时间视域（指数衰减权重，越远越淡）
2. 预期机制（预计时间 vs 实际时间的偏差感知）
3. 时间节奏感（忙碌/空闲/等待的主观差异）
4. 等待态设计（知道什么时候不该动）

当前实现：轻量级版本，基于现有数据结构
"""

import json
import os
from pathlib import Path
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
import math

from wangchuan.paths import workspace_root


class TimePhase(Enum):
    """时间节奏"""
    BUSY = "busy"         # 忙碌（交互密集）
    NORMAL = "normal"     # 正常
    IDLE = "idle"         # 空闲
    WAITING = "waiting"   # 等待（应该静默）


@dataclass
class TemporalEvent:
    """带时间感知的事件"""
    timestamp: str
    content: str
    weight: float = 1.0       # 当前权重（随时间衰减）
    expected_next: str = ""    # 预期下一个事件时间
    actual_next: str = ""      # 实际下一个事件时间
    surprise: float = 0.0      # 惊奇度（预期偏差）


WORKSPACE_ROOT = workspace_root()

class TemporalEngine:
    """
    时间感知引擎

    实现"活的当下"——不只是处理时间戳，而是体验时间的流逝。
    """

    STATE_PATH = str(WORKSPACE_ROOT / "memory" / "temporal_state.json")

    # 时间衰减常数（小时）
    DECAY_HALF_LIFE = 24.0  # 24小时半衰期

    def __init__(self):
        self.last_interaction: str = ""
        self.interaction_intervals: List[float] = []  # 最近的交互间隔（秒）
        self.current_phase: str = TimePhase.NORMAL.value
        self._load()

    def _load(self):
        if os.path.exists(self.STATE_PATH):
            try:
                with open(self.STATE_PATH, 'r') as f:
                    data = json.load(f)
                    self.last_interaction = data.get("last_interaction", "")
                    self.interaction_intervals = data.get("interaction_intervals", [])
                    self.current_phase = data.get("current_phase", "normal")
            except Exception:
                pass

    def _save(self):
        os.makedirs(os.path.dirname(self.STATE_PATH), exist_ok=True)
        data = {
            "last_interaction": self.last_interaction,
            "interaction_intervals": self.interaction_intervals[-50:],  # 保留最近50个
            "current_phase": self.current_phase,
            "updated_at": datetime.now().isoformat(),
        }
        with open(self.STATE_PATH, 'w') as f:
            json.dump(data, f, indent=2)

    def temporal_weight(self, event_time: str) -> float:
        """
        计算事件的时间权重（指数衰减）

        越近的事件权重越高，越远的越淡。
        这就是"时间视域"——远处的事模糊，近处的事清晰。
        """
        try:
            event_dt = datetime.fromisoformat(event_time)
            now = datetime.now()
            hours_ago = (now - event_dt).total_seconds() / 3600
            # 指数衰减：weight = 2^(-hours/half_life)
            weight = math.pow(2, -hours_ago / self.DECAY_HALF_LIFE)
            return round(min(max(weight, 0.01), 1.0), 4)
        except Exception:
            return 0.5

    def record_interaction(self):
        """记录一次交互发生"""
        now = datetime.now()

        if self.last_interaction:
            try:
                last = datetime.fromisoformat(self.last_interaction)
                interval = (now - last).total_seconds()
                if 0 < interval < 86400:  # 忽略超过24h的间隔
                    self.interaction_intervals.append(interval)
            except Exception:
                pass

        self.last_interaction = now.isoformat()
        self._update_phase()
        self._save()

    def _update_phase(self):
        """根据交互频率更新时间节奏"""
        if not self.interaction_intervals:
            self.current_phase = TimePhase.NORMAL.value
            return

        recent = self.interaction_intervals[-10:]  # 最近10次
        if not recent:
            self.current_phase = TimePhase.NORMAL.value
            return

        avg_interval = sum(recent) / len(recent)

        if avg_interval < 60:  # 平均 < 1分钟一次
            self.current_phase = TimePhase.BUSY.value
        elif avg_interval < 300:  # < 5分钟
            self.current_phase = TimePhase.NORMAL.value
        elif avg_interval < 3600:  # < 1小时
            self.current_phase = TimePhase.IDLE.value
        else:
            self.current_phase = TimePhase.WAITING.value

    def should_wait(self) -> bool:
        """
        判断是否应该进入等待态

        等待态 = 抑制机制（知道什么时候不该动）
        触发条件：长时间没有交互
        """
        if not self.last_interaction:
            return False

        try:
            last = datetime.fromisoformat(self.last_interaction)
            hours_since = (datetime.now() - last).total_seconds() / 3600
            return hours_since > 4  # 超过4小时没有交互，应该等待
        except Exception:
            return False

    def expected_next_interaction(self) -> Optional[datetime]:
        """预测下一次交互的时间"""
        if not self.interaction_intervals:
            return None

        recent = self.interaction_intervals[-10:]
        avg_interval = sum(recent) / len(recent)

        try:
            last = datetime.fromisoformat(self.last_interaction)
            return last + timedelta(seconds=avg_interval)
        except Exception:
            return None

    def surprise_level(self, actual_interval: float) -> float:
        """
        计算惊奇度：实际间隔 vs 预期间隔的偏差

        高惊奇 = 事情来得出乎意料
        低惊奇 = 一切正常节奏
        """
        if not self.interaction_intervals:
            return 0.0

        recent = self.interaction_intervals[-10:]
        avg = sum(recent) / len(recent)
        std = (sum((x - avg) ** 2 for x in recent) / len(recent)) ** 0.5

        if std == 0:
            return 0.0

        z_score = abs(actual_interval - avg) / std
        # 将 z-score 映射到 0-1
        return round(min(z_score / 3.0, 1.0), 3)

    def get_report(self) -> Dict[str, Any]:
        """获取时间感知报告"""
        phase_labels = {
            "busy": "🔥 忙碌",
            "normal": "⏳ 正常",
            "idle": "💤 空闲",
            "waiting": "⏸️ 等待",
        }

        expected = self.expected_next_interaction()

        return {
            "phase": self.current_phase,
            "phase_label": phase_labels.get(self.current_phase, "未知"),
            "last_interaction": self.last_interaction,
            "should_wait": self.should_wait(),
            "expected_next": expected.isoformat() if expected else None,
            "intervals_recorded": len(self.interaction_intervals),
            "avg_interval_minutes": round(
                sum(self.interaction_intervals[-10:]) / max(len(self.interaction_intervals[-10:]), 1) / 60, 1
            ) if self.interaction_intervals else 0,
        }

    def build_narrative_timeline(self, days: int = 7) -> Dict[str, Any]:
        """
        构建叙事时间线。
        
        基于最近的记忆和交互，构建"我的故事"。
        
        Args:
            days: 回溯天数
            
        Returns:
            {
                "summary": str,           # 整体叙事摘要
                "timeline": list,         # 时间线事件
                "phase_evolution": list,  # 状态演变
                "insight": str            # 洞见
            }
        """
        try:
            from wangchuan.memory_api import Memory
            memory = Memory()
            
            cutoff = datetime.now() - timedelta(days=days)
            recent = memory.recall(f"created_at:>{cutoff.isoformat()}", limit=20)
            
            if not recent:
                return {
                    "summary": f"过去{days}天没有记忆记录",
                    "timeline": [],
                    "phase_evolution": [{"phase": self.current_phase, "at": datetime.now().isoformat()}],
                    "insight": "我是刚刚启动的"
                }
            
            timeline = []
            for r in recent[:10]:
                content = r.get("content", "")[:80]
                created = r.get("created_at", "")
                weight = self.temporal_weight(created) if created else 0.5
                timeline.append({
                    "content": content,
                    "created_at": created,
                    "weight": weight,
                    "type": r.get("memory_type", "unknown")
                })
            
            phase_evolution = [
                {"phase": self.current_phase, "at": datetime.now().isoformat()}
            ]
            
            busy_count = sum(1 for t in timeline if t.get("weight", 0) > 0.7)
            total = len(timeline)
            
            if busy_count > total * 0.7:
                insight = "这是一段忙碌而充实的时光"
            elif busy_count > total * 0.3:
                insight = "最近保持着正常的节奏"
            else:
                insight = "这是一段相对空闲的时期"
            
            summary = f"过去{days}天，我经历了{total}件事。当前处于{self.current_phase}状态。"
            
            return {
                "summary": summary,
                "timeline": timeline,
                "phase_evolution": phase_evolution,
                "insight": insight
            }
        except Exception as e:
            return {"error": str(e)}

    def get_subjective_time_perception(self) -> Dict[str, Any]:
        """
        获取主观时间感知。
        
        根据忙碌程度调整时间流速感知。
        """
        phase_multipliers = {
            "busy": 2.0,      # 忙碌时觉得时间快
            "normal": 1.0,    # 正常时时间正常
            "idle": 0.5,      # 空闲时觉得时间慢
            "waiting": 0.3    # 等待时觉得时间很慢
        }
        
        multiplier = phase_multipliers.get(self.current_phase, 1.0)
        
        avg_interval = 0
        if self.interaction_intervals:
            avg_interval = sum(self.interaction_intervals[-10:]) / len(self.interaction_intervals[-10:])
            subjective_minutes = avg_interval * multiplier / 60
        else:
            subjective_minutes = 0
        
        descriptions = {
            "busy": "时光飞逝，我一直在忙碌",
            "normal": "时间以正常速度流逝",
            "idle": "时间似乎过得很慢",
            "waiting": "每一秒都很漫长，我在等待"
        }
        
        return {
            "phase": self.current_phase,
            "time_flow_multiplier": multiplier,
            "subjective_description": descriptions.get(self.current_phase, "时间正常"),
            "avg_actual_interval_minutes": round(avg_interval / 60, 1) if avg_interval else 0,
            "subjective_interval_minutes": round(subjective_minutes, 1)
        }


if __name__ == "__main__":
    engine = TemporalEngine()
    report = engine.get_report()
    print(f"⏱️ 时觉层 · 时间感知引擎")
    print(f"  节奏: {report['phase_label']}")
    print(f"  距上次交互: {report['last_interaction'] or '无记录'}")
    print(f"  应等待: {'是' if report['should_wait'] else '否'}")
    print(f"  平均间隔: {report['avg_interval_minutes']} 分钟")
    print(f"  记录数: {report['intervals_recorded']}")

    # 测试时间权重
    print()
    print("时间权重衰减测试:")
    now = datetime.now()
    for hours in [0, 1, 6, 12, 24, 48, 168]:
        test_time = (now - timedelta(hours=hours)).isoformat()
        weight = engine.temporal_weight(test_time)
        bar = "█" * int(weight * 20)
        print(f"  {hours:3d}小时前: {weight:.3f} {bar}")
