from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from wangchuan._adapters.consciousness_adapter import (
    get_rules_path as _get_rules_path,
    get_rule_links_path as _get_rule_links_path,
    get_self_state_path as _get_self_state_path,
    get_session_rule_hits_path as _get_session_rule_hits_path,
)

SESSION_RULE_HITS_PATH = _get_session_rule_hits_path() or ""
RULES_PATH = _get_rules_path() or ""
STATE_PATH = _get_self_state_path() or ""
RULE_LINKS_PATH = _get_rule_links_path() or ""


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
REFLECTIONS_PATH = MEMORY_DIR / "reflections.jsonl"
SCARS_PATH = MEMORY_DIR / "scars.jsonl"
EVALUATIONS_PATH = MEMORY_DIR / "evaluations.jsonl"
TOOL_RESULTS_PATH = MEMORY_DIR / "tool_results.jsonl"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _trim_jsonl(path: Path, keep_last: int) -> int:
    if not path.exists():
        return 0
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    before = len(lines)
    kept = lines[-keep_last:] if keep_last > 0 else []
    path.write_text(("\n".join(kept) + ("\n" if kept else "")), encoding="utf-8")
    return max(0, before - len(kept))


class ConsciousnessHygiene:
    def prune_rules(self, *, min_strength: float = 0.12, keep_top: int = 12, cooldown_days: int = 14) -> dict:
        if not RULES_PATH.exists():
            return {"before": 0, "after": 0, "removed": 0}
        rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        before = len(rules)
        now = datetime.now().astimezone()
        kept = []
        for idx, rule in enumerate(rules):
            ts = _parse_ts(rule.get("updated_at"))
            stale = bool(ts and ts < now - timedelta(days=cooldown_days))
            weak = float(rule.get("strength", 0.0)) < min_strength
            if idx < keep_top:
                kept.append(rule)
                continue
            if rule.get("state") == "cooldown" and stale:
                continue
            if weak and stale:
                continue
            kept.append(rule)
        kept = kept[: max(keep_top, len(kept))]
        RULES_PATH.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"before": before, "after": len(kept), "removed": before - len(kept)}

    def prune_session_hits(self, *, max_age_hours: int = 72) -> dict:
        if not SESSION_RULE_HITS_PATH.exists():
            return {"before": 0, "after": 0, "removed": 0}
        data = json.loads(SESSION_RULE_HITS_PATH.read_text(encoding="utf-8"))
        before = len(data)
        now = datetime.now().astimezone()
        kept = {}
        for sid, item in data.items():
            ts = _parse_ts(item.get("updated_at"))
            if ts and ts >= now - timedelta(hours=max_age_hours):
                kept[sid] = item
        SESSION_RULE_HITS_PATH.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"before": before, "after": len(kept), "removed": before - len(kept)}

    def trim_memory_logs(self, *, reflections_keep: int = 200, scars_keep: int = 100,
                        evaluations_keep: int = 200, tool_results_keep: int = 200,
                        rule_links_keep: int = 300) -> dict:
        return {
            "reflections_trimmed": _trim_jsonl(REFLECTIONS_PATH, reflections_keep),
            "scars_trimmed": _trim_jsonl(SCARS_PATH, scars_keep),
            "evaluations_trimmed": _trim_jsonl(EVALUATIONS_PATH, evaluations_keep),
            "tool_results_trimmed": _trim_jsonl(TOOL_RESULTS_PATH, tool_results_keep),
            "rule_links_trimmed": _trim_jsonl(RULE_LINKS_PATH, rule_links_keep),
        }

    def compact_self_state(self, *, max_rules: int = 5, max_shifts: int = 5) -> dict:
        if not STATE_PATH.exists():
            return {"updated": False}
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        data["top_active_rules"] = (data.get("top_active_rules") or [])[:max_rules]
        data["recent_shifts"] = (data.get("recent_shifts") or [])[:max_shifts]
        STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"updated": True, "top_active_rules": len(data["top_active_rules"]), "recent_shifts": len(data["recent_shifts"])}

    def run(self) -> dict:
        return {
            "rules": self.prune_rules(),
            "session_hits": self.prune_session_hits(),
            "memory_logs": self.trim_memory_logs(),
            "self_state": self.compact_self_state(),
        }
