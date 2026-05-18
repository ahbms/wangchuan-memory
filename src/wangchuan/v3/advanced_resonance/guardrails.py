#!/usr/bin/env python3
"""Guardrails for advanced resonance capabilities.

These are the hard constraints that every advanced capability must satisfy
before it can be promoted from support lane to primary lane.

Guardrails:
1. G1: No silent promotion — capabilities cannot change primary ranking silently
2. G2: No zero-base override — advanced score cannot override a zero base score
3. G3: Support lane isolation — support capabilities cannot demote primary candidates
4. G4: Audit trail — every advanced score must carry a reason string
5. G5: Kill switch — any capability can be disabled instantly via feature gate
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def guardrail_no_silent_promotion(
    control_results: List[Dict[str, Any]],
    treatment_results: List[Dict[str, Any]],
    capability_id: str,
) -> Tuple[bool, List[str]]:
    """G1: Advanced capability must not change which candidate is top-1 silently."""
    violations = []

    control_top1 = None
    for r in control_results:
        if control_top1 is None or r.get("base_score", 0) > control_top1.get("base_score", 0):
            control_top1 = r

    treatment_top1 = None
    for r in treatment_results:
        score = r.get("resonance_score", 0) + r.get(f"advanced_{capability_id}_score", 0)
        if treatment_top1 is None or score > (
            treatment_top1.get("resonance_score", 0) + treatment_top1.get(f"advanced_{capability_id}_score", 0)
        ):
            treatment_top1 = r

    if control_top1 and treatment_top1:
        if control_top1.get("memory_id") != treatment_top1.get("memory_id"):
            violations.append(
                f"G1: top-1 changed from {control_top1.get('memory_id')} "
                f"to {treatment_top1.get('memory_id')} — requires explicit approval"
            )

    return len(violations) == 0, violations


def guardrail_no_zero_base_override(
    treatment_results: List[Dict[str, Any]],
    capability_id: str,
) -> Tuple[bool, List[str]]:
    """G2: Advanced score cannot be the sole ranking factor."""
    violations = []
    for r in treatment_results:
        base = r.get("resonance_score", 0)
        advanced = r.get(f"advanced_{capability_id}_score", 0)
        if advanced > 0 and base == 0:
            violations.append(
                f"G2: memory_id={r.get('memory_id')} has advanced={advanced} "
                f"but base=0 — would bypass primary ranking"
            )
    return len(violations) == 0, violations


def guardrail_support_lane_isolation(
    control_results: List[Dict[str, Any]],
    treatment_results: List[Dict[str, Any]],
    capability_id: str,
) -> Tuple[bool, List[str]]:
    """G3: Support capability cannot demote a primary-lane candidate."""
    violations = []

    control_ranked = sorted(control_results, key=lambda r: r.get("base_score", 0), reverse=True)
    treatment_ranked = sorted(
        treatment_results,
        key=lambda r: r.get("resonance_score", 0) + r.get(f"advanced_{capability_id}_score", 0),
        reverse=True,
    )

    # Find candidates that were in top-3 control but fell out of top-3 treatment
    control_top3_ids = {r.get("memory_id") for r in control_ranked[:3]}
    treatment_top3_ids = {r.get("memory_id") for r in treatment_ranked[:3]}

    demoted = control_top3_ids - treatment_top3_ids
    if demoted:
        violations.append(
            f"G3: candidates demoted from top-3: {sorted(demoted)} — "
            f"support lane must not reorder primary candidates"
        )

    return len(violations) == 0, violations


def guardrail_audit_trail(
    treatment_results: List[Dict[str, Any]],
    capability_id: str,
) -> Tuple[bool, List[str]]:
    """G4: Every non-zero advanced score must have a reason string."""
    violations = []
    reason_key = f"advanced_{capability_id}_reason"
    score_key = f"advanced_{capability_id}_score"

    for r in treatment_results:
        score = r.get(score_key, 0)
        reason = r.get(reason_key, "")
        if score > 0 and not reason:
            violations.append(
                f"G4: memory_id={r.get('memory_id')} has advanced score {score} "
                f"but no reason string"
            )
    return len(violations) == 0, violations


ALL_GUARDRAILS = [
    ("G1_no_silent_promotion", guardrail_no_silent_promotion),
    ("G2_no_zero_base_override", guardrail_no_zero_base_override),
    ("G3_support_lane_isolation", guardrail_support_lane_isolation),
    ("G4_audit_trail", guardrail_audit_trail),
]


def run_all_guardrails(
    control_results: List[Dict[str, Any]],
    treatment_results: List[Dict[str, Any]],
    capability_id: str,
) -> Tuple[bool, Dict[str, Any]]:
    """Run all guardrails and return (all_pass, report)."""
    all_pass = True
    report: Dict[str, Any] = {"capability_id": capability_id, "guardrails": {}}

    for name, fn in ALL_GUARDRAILS:
        if name == "G1_no_silent_promotion":
            ok, violations = fn(control_results, treatment_results, capability_id)
        elif name == "G2_no_zero_base_override":
            ok, violations = fn(treatment_results, capability_id)
        elif name == "G3_support_lane_isolation":
            ok, violations = fn(control_results, treatment_results, capability_id)
        elif name == "G4_audit_trail":
            ok, violations = fn(treatment_results, capability_id)
        else:
            ok, violations = True, []

        report["guardrails"][name] = {
            "pass": ok,
            "violations": violations,
        }
        if not ok:
            all_pass = False

    report["all_pass"] = all_pass
    return all_pass, report
