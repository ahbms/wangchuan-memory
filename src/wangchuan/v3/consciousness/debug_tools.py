from __future__ import annotations

"""
明识层 - 调试与自证工具
天工开智 / 意识进化体系 · 第8层（实现承载）

说明：
- 当前 consciousness debug_tools 的职责名入口已经上移到 `tiangong.consciousness.debug_tools`
- 本文件当前属于 v3 consciousness 的兼容入口 / 实现承载层
- 如果你在找“意识系统如何输出可解释调试报告”，优先看 `tiangong.consciousness.debug_tools`
- 当前主 recall 主链依然优先看 `wangchuan.recall_service`

职责：
1. 汇总 self_state、top_rules、session_hit、recent_links 等关键状态
2. 为意识闭环提供可追溯解释，而不是只给黑箱结果

说明：
- 本模块服务于“我为何这么判断、这条规则为何会变强/变弱”的可解释性。
"""

import json
from pathlib import Path

from wangchuan._adapters.consciousness_adapter import (
    get_self_state_cls,
    get_session_tracker_cls,
    get_rule_links_path,
    get_strategy_updater_cls,
)

SelfStateStore, _ = get_self_state_cls()
SessionRuleTracker = get_session_tracker_cls()
RULE_LINKS_PATH = get_rule_links_path() or ""
StrategyUpdater = get_strategy_updater_cls()


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
REFLECTIONS_PATH = MEMORY_DIR / "reflections.jsonl"
EVALUATIONS_PATH = MEMORY_DIR / "evaluations.jsonl"
TOOL_RESULTS_PATH = MEMORY_DIR / "tool_results.jsonl"


def _tail_jsonl(path: Path, limit: int = 5) -> list[dict]:
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            out.append({"raw": line[:300]})
    return out


class ConsciousnessDebugTools:
    def build_report(self, session_id: str | None = None, tail: int = 5) -> dict:
        state = SelfStateStore().load()
        rules = StrategyUpdater().load_rules()
        session_hit = SessionRuleTracker().get(session_id) if session_id else None

        report = {
            "self_state": {
                "mode": state.mode,
                "initiative_level": state.initiative_level,
                "caution_level": state.caution_level,
                "confidence_level": state.confidence_level,
                "execution_bias": state.execution_bias,
                "social_mode": state.social_mode,
                "top_active_rules": state.top_active_rules,
                "recent_shifts": state.recent_shifts,
            },
            "top_rules": [
                {
                    "rule_id": r.get("scar_id") or r.get("rule_id"),
                    "lesson": r.get("lesson"),
                    "strength": r.get("strength"),
                    "positive_hits": r.get("positive_hits", 0),
                    "negative_hits": r.get("negative_hits", 0),
                    "last_outcome": r.get("last_outcome"),
                    "state": r.get("state"),
                }
                for r in rules[:5]
            ],
            "session_hit": session_hit,
            "recent_links": _tail_jsonl(RULE_LINKS_PATH, limit=tail),
            "recent_reflections": _tail_jsonl(REFLECTIONS_PATH, limit=tail),
            "recent_evaluations": _tail_jsonl(EVALUATIONS_PATH, limit=tail),
            "recent_tool_results": _tail_jsonl(TOOL_RESULTS_PATH, limit=tail),
        }
        return report
