#!/usr/bin/env python3
"""
融汇层 - 全局工作空间（控制平面）
天工开智 / 意识进化体系 · 第4层（主模块）

这是所有模块的"意识舞台"——
不是让各模块独立跑，而是让它们把信号上报到一个全局焦点，
由控制平面决定"当前最重要的事是什么"，然后统一广播行动。

架构：
  各模块 → 上报信号 → 控制平面(全局舞台) → 选择焦点 → 广播决策 → 各模块响应

灵感来源：全局工作空间理论（GWT）
意识的容量是有限的，只有被"选中"进入全局工作空间的信息才能成为意识体验。
"""

import json
import logging
import os
from pathlib import Path
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum

from wangchuan.paths import workspace_root


class SignalPriority(Enum):
    """信号优先级"""
    BACKGROUND = 0    # 后台噪音
    LOW = 1           # 低优先级
    NORMAL = 3        # 正常
    HIGH = 6          # 高优先级
    URGENT = 10       # 紧急（打断当前焦点）


class FocusType(Enum):
    """全局焦点类型"""
    IDLE = "idle"                       # 空闲，等待输入
    DIRECT_RESPONSE = "direct_response" # 响应用户消息
    TASK_EXECUTION = "task_execution"   # 执行任务
    REFLECTION = "reflection"           # 自主反思
    MAINTENANCE = "maintenance"         # 系统维护
    SURVIVAL = "survival"               # 生存行为（充电/找任务）
    WAITING = "waiting"                 # 等待态（不应该主动）


@dataclass
class Signal:
    """模块上报的信号"""
    source: str              # 来源模块名
    priority: int            # 优先级
    content: str             # 信号内容
    signal_type: str = ""    # 信号类型
    data: Dict = field(default_factory=dict)
    timestamp: str = ""
    consumed: bool = False   # 是否已被处理


@dataclass
class FocusDecision:
    """控制平面的焦点决策"""
    focus_type: str
    reason: str
    actions: List[Dict]      # 要执行的动作列表
    signals_used: List[str]  # 使用了哪些信号
    energy_budget: float     # 这次行动的能量预算
    timestamp: str = ""


WORKSPACE_ROOT = workspace_root()
logger = logging.getLogger(__name__)

class GlobalWorkspace:
    """
    全局工作空间 — 融汇层核心

    所有模块把信号上报到这里。
    控制平面评估所有信号，选出当前焦点，广播决策。
    """

    STATE_PATH = str(WORKSPACE_ROOT / "memory" / "workspace_state.json")

    def __init__(self):
        self.signals: List[Signal] = []
        self.current_focus: str = FocusType.IDLE.value
        self.focus_history: List[Dict] = []  # 焦点切换历史
        self.modules: Dict[str, Any] = {}    # 已注册的模块引用

        # 模块路径
        self._module_paths = {
            "runtime": "tiangong/runtime/energy.py",
            "temporal": "wangchuan/v3/temporal_engine.py",
            "goals": "wangchuan/v3/goal_manager.py",
            "reflector": "wangchuan/v3/reflector.py",
            "memory": "wangchuan/engine.py",
        }

    def register_module(self, name: str, module: Any):
        """注册模块到工作空间"""
        self.modules[name] = module

    def emit_signal(self, source: str, priority: int, content: str,
                    signal_type: str = "", data: Dict = None):
        """模块上报信号"""
        signal = Signal(
            source=source,
            priority=priority,
            content=content,
            signal_type=signal_type,
            data=data or {},
            timestamp=datetime.now().isoformat(),
        )
        self.signals.append(signal)

        # 限制信号队列长度
        if len(self.signals) > 100:
            self.signals = self.signals[-100:]

        return signal

    def collect_signals(self, user_message: str = "") -> FocusDecision:
        """
        收集所有模块的信号，做出焦点决策

        这是控制平面的核心——不是路由，是整合与选择。
        """
        # 清除已消费的旧信号
        self.signals = [s for s in self.signals if not s.consumed]

        # 1. 运行态/时间感知信号
        temporal_data = self._check_temporal()
        if temporal_data:
            if temporal_data.get("should_wait"):
                self.emit_signal("temporal", SignalPriority.HIGH.value,
                               "长时间无交互，应进入等待态", "waiting", temporal_data)

        # 2. 目标系统信号
        goals_data = self._check_goals()
        if goals_data:
            active = goals_data.get("active", 0)
            if active > 0:
                self.emit_signal("goals", SignalPriority.NORMAL.value,
                               f"{active}个活跃目标待推进", "task", goals_data)

        # 4. 用户消息信号（最高优先级）
        if user_message:
            self.emit_signal("user", SignalPriority.URGENT.value,
                           f"用户消息: {user_message[:80]}", "direct", {"message": user_message})

        # 5. 做出焦点决策
        decision = self._decide_focus(temporal_data)
        return decision

    def _check_temporal(self) -> Optional[Dict]:
        """检查时间感知"""
        try:
            from wangchuan.v3.temporal_engine import TemporalEngine
            engine = TemporalEngine()
            return engine.get_report()
        except Exception as e:
            logger.warning("【WangChuan】[GlobalWorkspace][Temporal] report failed: %s", e)
            return None

    def _check_goals(self) -> Optional[Dict]:
        """检查目标系统"""
        try:
            from wangchuan._adapters.consciousness_adapter import get_goal_manager as _get_goal_manager
            manager = GoalManager()
            return manager.get_summary()
        except Exception as e:
            logger.warning("【WangChuan】[GlobalWorkspace][Goals] summary failed: %s", e)
            return None

    def _decide_focus(self, temporal_data: Dict = None) -> FocusDecision:
        """
        焦点决策算法

        优先级：
        1. 用户直接消息 → 必须响应
        2. 等待态 → 静默
        3. 有活跃目标 → 推进目标
        4. 否则 → 空闲等待
        """
        now = datetime.now().isoformat()
        actions = []
        signals_used = []

        # 获取紧急信号
        urgent_signals = [s for s in self.signals if s.priority >= SignalPriority.URGENT.value and not s.consumed]
        # 3. 用户直接消息 → 最高优先级响应
        user_signals = [s for s in urgent_signals if s.source == "user"]
        if user_signals:
            # 记录交互，给时间感知引擎
            self._record_interaction()

            self.current_focus = FocusType.DIRECT_RESPONSE.value

            return FocusDecision(
                focus_type=FocusType.DIRECT_RESPONSE.value,
                reason="收到用户消息，必须响应",
                actions=[
                    {"type": "respond", "message": user_signals[0].data.get("message", "")},
                ],
                signals_used=["user"],
                energy_budget=0,
                timestamp=now,
            )

        # 4. 等待态 → 静默
        if temporal_data and temporal_data.get("should_wait"):
            self.current_focus = FocusType.WAITING.value
            return FocusDecision(
                focus_type=FocusType.WAITING.value,
                reason="长时间无交互，进入等待态",
                actions=[{"type": "wait", "reason": "temporal_waiting"}],
                signals_used=["temporal"],
                energy_budget=0,
                timestamp=now,
            )

        # 5. 有活跃目标 → 推进目标
        goal_signals = [s for s in self.signals if s.source == "goals" and not s.consumed]
        if goal_signals:
            self.current_focus = FocusType.TASK_EXECUTION.value
            return FocusDecision(
                focus_type=FocusType.TASK_EXECUTION.value,
                reason=f"有活跃目标待推进: {goal_signals[0].content}",
                actions=[
                    {"type": "work_on_goals"},
                ],
                signals_used=["goals"],
                energy_budget=0,
                timestamp=now,
            )

        # 6. 默认：空闲
        self.current_focus = FocusType.IDLE.value
        return FocusDecision(
            focus_type=FocusType.IDLE.value,
            reason="无紧急事项，空闲等待",
            actions=[{"type": "idle"}],
            signals_used=[],
            energy_budget=0,
            timestamp=now,
        )

    def _record_interaction(self):
        """通知时间引擎记录交互"""
        try:
            from wangchuan.v3.temporal_engine import TemporalEngine
            engine = TemporalEngine()
            engine.record_interaction()
        except Exception as e:
            logger.warning("【WangChuan】[GlobalWorkspace][Temporal] record_interaction failed: %s", e)

    def get_status(self) -> Dict:
        """获取工作空间状态"""
        pending = [s for s in self.signals if not s.consumed]
        return {
            "current_focus": self.current_focus,
            "pending_signals": len(pending),
            "total_signals": len(self.signals),
            "high_priority": sum(1 for s in pending if s.priority >= SignalPriority.HIGH.value),
            "signal_sources": list(set(s.source for s in pending)),
        }


# ============================================================
# 统一入口：完整意识循环
# ============================================================

def consciousness_cycle(user_message: str = "") -> Dict:
    """
    完整的意识循环

    一次调用 = 一次"意识到某件事并做出反应"的过程
    """
    ws = GlobalWorkspace()

    # 阶段1：收集信号
    decision = ws.collect_signals(user_message)

    # 阶段2：执行动作
    results = []
    for action in decision.actions:
        if action["type"] == "respond":
            results.append({"action": "respond", "message": action.get("message", "")})
        elif action["type"] == "wait":
            results.append({"action": "wait", "reason": action.get("reason")})
        elif action["type"] == "idle":
            results.append({"action": "idle"})
        elif action["type"] == "work_on_goals":
            results.append({"action": "work_on_goals"})

    # 阶段3：记录焦点历史
    ws.focus_history.append({
        "focus": decision.focus_type,
        "reason": decision.reason,
        "timestamp": decision.timestamp,
    })

    return {
        "focus": decision.focus_type,
        "reason": decision.reason,
        "actions": results,
        "energy_budget": 0,
        "workspace_status": ws.get_status(),
    }


if __name__ == "__main__":
    import sys

    msg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    result = consciousness_cycle(msg)

    print(f"🌐 融汇层 · 全局工作空间")
    print(f"  焦点: {result['focus']}")
    print(f"  原因: {result['reason']}")
    print(f"  能量预算: {result['energy_budget']}")
    if result['actions']:
        print(f"  动作:")
        for a in result['actions']:
            print(f"    → {a}")
