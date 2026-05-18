#!/usr/bin/env python3
"""
忘川 → L6 理性（consciousness）适配器

封装忘川对 tiangong.consciousness 的所有调用。

L6 是忘川最大跨层耦合源，涉及:
  - ConsciousnessEngine / run_tool_with_consciousness
  - GoalManager / Goal / GoalPriority / GoalStatus / SubTask
  - NarrativeBuilder / TimelineEvent
  - IdentityTracker
  - 以及 v3/consciousness/ 目录下 16 个文件的全部导入

策略：
  - 顶层 pipeline/global_workspace 等使用此 adapter 的协议化接口
  - v3/consciousness/ 目录内部也通过此 adapter 替换直接 import
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================
# Stub 数据类型（无 L6 时的降级兼容）
# =============================================


@dataclass
class _StubEvent:
    """Event 降级桩 — 替换 tiangong.consciousness.schemas.Event"""
    event_id: str = ""
    event_type: str = ""
    content: str = ""
    summary: str = ""
    timestamp: str = ""
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _StubProposedUpdate:
    """ProposedUpdate 降级桩"""
    update_id: str = ""
    field: str = ""
    value: Any = None
    reason: str = ""


@dataclass
class _StubReflection:
    """Reflection 降级桩"""
    reflection_id: str = ""
    insight: str = ""
    source_event_id: str = ""
    confidence: float = 0.0


@dataclass
class _StubEvaluation:
    """Evaluation 降级桩"""
    evaluation_id: str = ""
    target: str = ""
    outcome: str = "neutral"
    reason: str = ""
    reinforcement: float = 0.0
    related_rule_ids: List[str] = field(default_factory=list)
    source_event_id: str = ""


# =============================================
# Stub 桩类（无 L6 时的降级行为）
# =============================================


class _StubConsciousnessEngine:
    """ConsciousnessEngine 桩"""

    def inject_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"consciousness": "stub", "injected": False}

    def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"evaluation": "stub", "score": 0.5}


def _stub_run_tool_with_consciousness(tool_name: str, **kwargs) -> Dict[str, Any]:
    return {"tool": tool_name, "consciousness": "stub", "result": None}


class _StubGoal:
    pass


class _StubGoalManager:
    def __init__(self):
        self.goals = []

    def get_active_goals(self) -> List[Any]:
        return []

    def get_goal_context(self, goal_id: str = None) -> Dict[str, Any]:
        return {}


class _StubNarrativeBuilder:
    def build(self, events: List[Any]) -> str:
        return ""


class _StubTimelineEvent:
    pass


class _StubIdentityTracker:
    def get_identity(self) -> Dict[str, Any]:
        return {"identity": "stub"}

    def update_identity(self, **kwargs) -> None:
        pass


# =============================================
# 适配器主接口
# =============================================

_HAVE_L6 = False


def _lazy_check_l6() -> bool:
    global _HAVE_L6
    if _HAVE_L6:
        return True
    try:
        from wangchuan._protocol import get_layer
        if get_layer("lixing") is not None:
            _HAVE_L6 = True
            return True
    except ImportError:
        pass
    except Exception:
        pass
    return False


def _protocol_call(operation: str, payload: dict = None) -> Optional[Dict[str, Any]]:
    """通过 protocol dispatch 调用 L6。"""
    try:
        from wangchuan._protocol import LayerRequest, dispatch

        resp = dispatch(LayerRequest(layer="lixing", operation=operation, payload=payload or {}))
        if resp.ok:
            return resp.data
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("L6 protocol dispatch 失败 (%s): %s", operation, exc)
    return None


# ---- ConsciousnessEngine ----

def get_consciousness_engine() -> Any:
    """获取 ConsciousnessEngine。"""
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.engine import ConsciousnessEngine as _Real
            return _Real()
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("L6 ConsciousnessEngine 实例化失败: %s", exc)
    return _StubConsciousnessEngine()


# ---- goal_manager ----

def get_goal_manager() -> Any:
    """获取 GoalManager（仅桩）。"""
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.goals import GoalManager as _RealGM
            return _RealGM()
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("L6 GoalManager 实例化失败: %s", exc)
    return _StubGoalManager()


def get_goal_types():
    """获取 Goal 相关类型元组 (Goal, GoalManager, GoalPriority, GoalStatus, SubTask)。"""
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.goals import Goal, GoalManager, GoalPriority, GoalStatus, SubTask
            return Goal, GoalManager, GoalPriority, GoalStatus, SubTask
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("L6 goal types 获取失败: %s", exc)
    return _StubGoal, _StubGoalManager, None, None, None


# ---- narrative ----

def get_narrative_builder() -> Any:
    """获取 NarrativeBuilder。"""
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.narrative import NarrativeBuilder as _RealNB
            return _RealNB()
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("L6 NarrativeBuilder 实例化失败: %s", exc)
    return _StubNarrativeBuilder()


def get_timeline_event_type():
    """获取 TimelineEvent 类。"""
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.narrative import TimelineEvent
            return TimelineEvent
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("L6 TimelineEvent 获取失败: %s", exc)
    return _StubTimelineEvent


# ---- identity ----

def get_identity_tracker() -> Any:
    """获取 IdentityTracker。"""
    data = _protocol_call("get_identity_tracker")
    if data is not None:
        return data
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.identity import IdentityTracker as _RealIT
            return _RealIT()
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("L6 IdentityTracker 实例化失败: %s", exc)
    return _StubIdentityTracker()


# ---- tool_bridge ----

def run_tool_with_consciousness(tool_name: str, **kwargs) -> Dict[str, Any]:
    """带意识执行工具。"""
    data = _protocol_call("run_tool_with_consciousness", {"tool_name": tool_name, **kwargs})
    if data is not None:
        return data
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.tool_bridge import run_tool_with_consciousness as _real_run
            return _real_run(tool_name, **kwargs)
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("L6 run_tool_with_consciousness 失败: %s", exc)
    return _stub_run_tool_with_consciousness(tool_name, **kwargs)


# ---- v3/consciousness/ 内部用: 基础类型 ----

def get_identity_adapter_cls():
    """获取 IdentityAdapter 类（用于 v3/consciousness/ 内部）。"""
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.identity_adapter import IdentityAdapter
            return IdentityAdapter
        except ImportError:
            pass
        except Exception:
            pass
    return object


def get_group_awareness_cls():
    """获取 GroupAwareness 类。"""
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.group_awareness import GroupAwareness
            return GroupAwareness
        except ImportError:
            pass
        except Exception:
            pass
    return None


def get_consciousness_schemas():
    """获取 (Event, ProposedUpdate, Reflection, Evaluation) 元组。"""
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.schemas import Event, ProposedUpdate, Reflection, Evaluation
            return Event, ProposedUpdate, Reflection, Evaluation
        except ImportError:
            pass
        except Exception:
            pass
    return _StubEvent, _StubProposedUpdate, _StubReflection, _StubEvaluation


# ---- 路径常量（hygiene 使用） ----

def get_session_rule_hits_path() -> str:
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.session_tracker import SESSION_RULE_HITS_PATH
            return SESSION_RULE_HITS_PATH
        except ImportError:
            pass
        except Exception:
            pass
    return ""


def get_rules_path() -> str:
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.strategy import RULES_PATH
            return RULES_PATH
        except ImportError:
            pass
        except Exception:
            pass
    return ""


def get_self_state_path() -> str:
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.self_state import STATE_PATH
            return STATE_PATH
        except ImportError:
            pass
        except Exception:
            pass
    return ""


def get_rule_links_path() -> str:
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.link_tracker import RULE_LINKS_PATH
            return RULE_LINKS_PATH
        except ImportError:
            pass
        except Exception:
            pass
    return ""


# ---- v3/consciousness/engine 基础组件 ----

def get_consciousness_debug_tools_cls():
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.debug_tools import ConsciousnessDebugTools
            return ConsciousnessDebugTools
        except ImportError:
            pass
        except Exception:
            pass
    return object


def get_evaluator_cls():
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.evaluator import Evaluator
            return Evaluator
        except ImportError:
            pass
        except Exception:
            pass
    return object


def get_injector_cls():
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.injector import ConsciousnessInjector
            return ConsciousnessInjector
        except ImportError:
            pass
        except Exception:
            pass
    return object


def get_link_tracker_cls():
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.link_tracker import RuleLinkTracker
            return RuleLinkTracker
        except ImportError:
            pass
        except Exception:
            pass
    return object


def get_reflector_cls():
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.reflector import Reflector
            return Reflector
        except ImportError:
            pass
        except Exception:
            pass
    return object


def get_scar_selector_cls():
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.scar_selector import ScarSelector
            return ScarSelector
        except ImportError:
            pass
        except Exception:
            pass
    return object


def get_self_state_cls():
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.self_state import SelfStateStore, SelfStateUpdater
            return SelfStateStore, SelfStateUpdater
        except ImportError:
            pass
        except Exception:
            pass
    return object, object


def get_session_tracker_cls():
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.session_tracker import SessionRuleTracker
            return SessionRuleTracker
        except ImportError:
            pass
        except Exception:
            pass
    return object


def get_strategy_updater_cls():
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.strategy import StrategyUpdater
            return StrategyUpdater
        except ImportError:
            pass
        except Exception:
            pass
    return object


def get_consciousness_tool_bridge():
    if _lazy_check_l6():
        try:
            from tiangong.consciousness.tool_bridge import (
                ToolBridgeResult,
                consolidate_tool_results,
                run_tool_with_consciousness,
            )
            return ToolBridgeResult, consolidate_tool_results, run_tool_with_consciousness
        except ImportError:
            pass
        except Exception:
            pass
    return None, None, None


__all__ = [
    "get_consciousness_engine",
    "get_goal_manager",
    "get_goal_types",
    "get_narrative_builder",
    "get_timeline_event_type",
    "get_identity_tracker",
    "run_tool_with_consciousness",
    # v3/consciousness/ 内部用
    "get_identity_adapter_cls",
    "get_group_awareness_cls",
    "get_consciousness_schemas",
    "get_session_rule_hits_path",
    "get_rules_path",
    "get_self_state_path",
    "get_rule_links_path",
    "get_consciousness_debug_tools_cls",
    "get_evaluator_cls",
    "get_injector_cls",
    "get_link_tracker_cls",
    "get_reflector_cls",
    "get_scar_selector_cls",
    "get_self_state_cls",
    "get_session_tracker_cls",
    "get_strategy_updater_cls",
    "get_consciousness_tool_bridge",
]
