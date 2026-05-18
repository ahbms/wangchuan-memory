#!/usr/bin/env python3
"""Feature gate registry for WangChuan advanced resonance capabilities.

Provides a config-driven on/off switch for each advanced capability.
Every capability must be registered here before it can be used in the pipeline.
Capabilities are OFF by default — explicitly opt in via config.

Usage:
    from wangchuan.v3.feature_gate import FeatureGate
    gate = FeatureGate.from_config(config_dict)
    if gate.is_enabled("affective_resonance"):
        ...  # safe to use
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Canonical capability registry
# Each entry: capability_id -> default_enabled
_CAPABILITY_DEFAULTS: Dict[str, bool] = {
    "affective_resonance": False,
    "adaptive_weighting": False,
    "causal_grading": False,
}

# Which lane each capability belongs to
# "support" = can only influence support decisions, never primary ranking
# "primary" = can influence primary ranking (requires higher bar)
_CAPABILITY_LANES: Dict[str, str] = {
    "affective_resonance": "support",
    "adaptive_weighting": "support",
    "causal_grading": "support",
}


@dataclass
class CapabilityStatus:
    """Status of a single capability."""
    capability_id: str
    enabled: bool
    lane: str
    reason: str = ""
    guardrails_pass: bool = True
    guardrail_violations: List[str] = field(default_factory=list)


class FeatureGate:
    """Central gate for advanced resonance capabilities."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._overrides: Dict[str, bool] = {}
        self._load_overrides()

    def _load_overrides(self) -> None:
        raw = self._config.get("feature_overrides") or {}
        if isinstance(raw, dict):
            self._overrides = {str(k): bool(v) for k, v in raw.items()}

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "FeatureGate":
        return cls(config)

    def is_enabled(self, capability_id: str) -> bool:
        """Check if a capability is enabled. Defaults to False."""
        if capability_id not in _CAPABILITY_DEFAULTS:
            return False
        if capability_id in self._overrides:
            return self._overrides[capability_id]
        return _CAPABILITY_DEFAULTS[capability_id]

    def get_lane(self, capability_id: str) -> str:
        """Get the lane for a capability."""
        return _CAPABILITY_LANES.get(capability_id, "support")

    def list_capabilities(self) -> List[CapabilityStatus]:
        """List all registered capabilities with their status."""
        statuses = []
        for cap_id, default in _CAPABILITY_DEFAULTS.items():
            enabled = self.is_enabled(cap_id)
            lane = self.get_lane(cap_id)
            reason = ""
            if not enabled:
                if cap_id in self._overrides and not self._overrides[cap_id]:
                    reason = "explicitly disabled in config"
                else:
                    reason = "disabled by default (opt-in required)"
            else:
                reason = "enabled in config"
            statuses.append(CapabilityStatus(
                capability_id=cap_id,
                enabled=enabled,
                lane=lane,
                reason=reason,
            ))
        return statuses

    def check_guardrails(self, capability_id: str, resonance_scores: List[Dict[str, Any]]) -> CapabilityStatus:
        """Run guardrails check for a capability.
        
        Returns CapabilityStatus with guardrail_violations populated.
        """
        violations = []
        enabled = self.is_enabled(capability_id)
        lane = self.get_lane(capability_id)

        if not enabled:
            violations.append(f"capability '{capability_id}' is not enabled")

        if lane == "support":
            # Support-lane capabilities must not be the sole ranking factor
            for item in resonance_scores or []:
                advanced_score = float(item.get(f"advanced_{capability_id}_score") or 0)
                base_score = float(item.get("resonance_score") or 0)
                if advanced_score > 0 and base_score == 0:
                    violations.append(
                        f"memory_id={item.get('memory_id')}: advanced score {advanced_score} "
                        f"without base resonance score — would bypass primary ranking"
                    )

        return CapabilityStatus(
            capability_id=capability_id,
            enabled=enabled,
            lane=lane,
            guardrails_pass=len(violations) == 0,
            guardrail_violations=violations,
        )

    def export_status(self) -> Dict[str, Any]:
        """Export full gate status as JSON-serializable dict."""
        return {
            "capabilities": [
                {
                    "capability_id": s.capability_id,
                    "enabled": s.enabled,
                    "lane": s.lane,
                    "reason": s.reason,
                    "guardrails_pass": s.guardrails_pass,
                    "guardrail_violations": s.guardrail_violations,
                }
                for s in self.list_capabilities()
            ],
            "overrides": dict(self._overrides),
        }


def load_feature_gate_config(config_path: Path) -> FeatureGate:
    """Load feature gate from a JSON config file."""
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return FeatureGate(data)
    return FeatureGate()
