#!/usr/bin/env python3
"""Isolated experiment runner for advanced resonance capabilities.

Runs advanced capabilities in isolation, producing comparison reports
without affecting the main pipeline. Each experiment:
1. Runs the base pipeline (control)
2. Runs with the advanced capability enabled (treatment)
3. Produces a diff report showing what changed
4. Applies guardrails to ensure no foundation regression

Usage:
    python3 wangchuan/v3/advanced_resonance/experiment_runner.py \\
        --capability affective_resonance \\
        --seeds seeds.json \\
        --memories memories.json \\
        --output-dir benchmarks/wangchuan/reports/experiments/
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Feature gate
_self_parent = Path(__file__).resolve().parent
_v3_dir = _self_parent.parent
_tiangong_dir = _v3_dir.parent.parent
for p in [str(_tiangong_dir), str(_v3_dir), str(_self_parent)]:
    if p not in sys.path:
        sys.path.insert(0, p)
from feature_gate import FeatureGate

# Advanced modules
from affective_resonance import augment_resonance_candidates as affective_augment
from adaptive_weighting import apply_adaptive_weights
from causal_grading import augment_with_causal_grades


def _base_resonance_score(seed: Dict[str, Any], item: Dict[str, Any]) -> float:
    """Minimal base resonance scoring (mirrors pipeline_v3 logic)."""
    score = 0.0
    seed_type = str(seed.get("memory_type") or "").strip().lower()
    item_type = str(item.get("memory_type") or "").strip().lower()
    seed_domain = str(seed.get("subject_domain") or "").strip().lower()
    item_domain = str(item.get("subject_domain") or "").strip().lower()

    if seed_type and item_type and seed_type == item_type:
        score += 0.22
    if seed_domain and item_domain and seed_domain == item_domain:
        score += 0.18

    seed_terms = [str(t).strip().lower() for t in (seed.get("query_match_terms") or []) if t]
    content = str(item.get("content") or "").lower()
    overlapping = [t for t in seed_terms if t and t in content]
    if overlapping:
        score += 0.12 * min(len(overlapping), 2)

    return round(score, 6)


def _run_control(seeds: List[Dict], memories: List[Dict]) -> List[Dict[str, Any]]:
    """Run base pipeline scoring (control group)."""
    results = []
    for item in memories:
        best_score = 0.0
        best_seed = None
        for seed in seeds:
            s = _base_resonance_score(seed, item)
            if s > best_score:
                best_score = s
                best_seed = seed.get("memory_id")
        results.append({
            "memory_id": item.get("memory_id"),
            "base_score": best_score,
            "matched_seed": best_seed,
        })
    return results


def _run_treatment(
    capability_id: str,
    seeds: List[Dict],
    memories: List[Dict],
) -> List[Dict[str, Any]]:
    """Run with advanced capability enabled (treatment group)."""
    # Build base candidates
    candidates = []
    for item in memories:
        best_score = 0.0
        best_seed = None
        for seed in seeds:
            s = _base_resonance_score(seed, item)
            if s > best_score:
                best_score = s
                best_seed = seed.get("memory_id")
        candidates.append({
            "memory_id": item.get("memory_id"),
            "resonance_score": best_score,
            "matched_seed": best_seed,
            "content": item.get("content", ""),
        })

    # Apply advanced capability
    if capability_id == "affective_resonance":
        candidates = affective_augment(seeds, candidates)
    elif capability_id == "adaptive_weighting":
        weights = apply_adaptive_weights(candidates, {})
        # weights is empty in placeholder
    elif capability_id == "causal_grading":
        candidates = augment_with_causal_grades(seeds, candidates)

    return candidates


def _compute_diff(control: List[Dict], treatment: List[Dict], capability_id: str = "") -> Dict[str, Any]:
    """Compare control vs treatment results."""
    control_index = {r["memory_id"]: r for r in control}
    treatment_index = {r["memory_id"]: r for r in treatment}

    all_ids = sorted(set(control_index.keys()) | set(treatment_index.keys()))

    changed = []
    added = []
    removed = []

    for mid in all_ids:
        c = control_index.get(mid)
        t = treatment_index.get(mid)
        if c and not t:
            removed.append(mid)
        elif not c and t:
            added.append(mid)
        else:
            c_score = c.get("base_score") or c.get("resonance_score", 0)
            t_score = t.get("resonance_score") or t.get("base_score", 0)
            adv_key = f"advanced_{capability_id}_score" if capability_id else None
            t_adv = t.get(adv_key, 0) if adv_key else 0
            if abs(c_score - t_score) > 0.0001 or t_adv > 0:
                changed.append({
                    "memory_id": mid,
                    "control_score": c_score,
                    "treatment_score": t_score,
                    "advanced_score": t_adv,
                })

    return {
        "total_control": len(control),
        "total_treatment": len(treatment),
        "changed_count": len(changed),
        "added_count": len(added),
        "removed_count": len(removed),
        "changed": changed[:20],
        "added": added[:20],
        "removed": removed[:20],
    }


def run_experiment(
    capability_id: str,
    seeds: List[Dict],
    memories: List[Dict],
    feature_gate: Optional[FeatureGate] = None,
) -> Dict[str, Any]:
    """Run a full experiment for a capability."""
    gate = feature_gate or FeatureGate()

    # Check gate
    gate_status = gate.check_guardrails(capability_id, [])

    # Run control
    control = _run_control(seeds, memories)

    # Run treatment (only if enabled)
    if gate_status.enabled and gate_status.guardrails_pass:
        treatment = _run_treatment(capability_id, seeds, memories)
    else:
        treatment = control  # same as control if not enabled

    # Compute diff
    diff = _compute_diff(control, treatment, capability_id)

    return {
        "capability_id": capability_id,
        "enabled": gate_status.enabled,
        "lane": gate_status.lane,
        "guardrails_pass": gate_status.guardrails_pass,
        "guardrail_violations": gate_status.guardrail_violations,
        "control_count": len(control),
        "treatment_count": len(treatment),
        "diff": diff,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run advanced resonance experiment")
    parser.add_argument("--capability", required=True, help="Capability to test")
    parser.add_argument("--seeds", required=True, help="Seeds JSON file")
    parser.add_argument("--memories", required=True, help="Memories JSON file")
    parser.add_argument("--output-dir", default="benchmarks/wangchuan/reports/experiments")
    parser.add_argument("--config", default=None, help="Feature gate config JSON")
    args = parser.parse_args()

    with open(args.seeds, "r", encoding="utf-8") as f:
        seeds = json.load(f)
    with open(args.memories, "r", encoding="utf-8") as f:
        memories = json.load(f)

    config = {}
    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)

    gate = FeatureGate(config)
    result = run_experiment(args.capability, seeds, memories, gate)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"experiment_{args.capability}_latest.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
