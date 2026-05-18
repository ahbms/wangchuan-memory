"""
边界门控模块 — 从 RecallEngine 中提取的 boundary gating 逻辑

负责 is_raw_evidence_item 判断和 enforce_joint_gating_memory_boundary，
确保 raw route 下仅保留原始证据项。
"""

from typing import Dict

from .format_blocks import FormatBlocks


# ---------------------------------------------------------------------------
# Raw evidence 判断
# ---------------------------------------------------------------------------

def is_raw_evidence_item(item: Dict[str, object]) -> bool:
    """判断是否为原始证据项"""
    source_layer = str(item.get("source_layer") or "").strip().lower()
    evidence_level = str(item.get("evidence_level") or "").strip().lower()
    recall_source_type = str(item.get("recall_source_type") or "").strip().lower()
    source_anchor = str(item.get("source_anchor") or "")
    provenance = str(item.get("provenance") or "")
    return (
        source_layer == "raw"
        or recall_source_type == "raw"
        or evidence_level == "raw"
        or "memory/raw/" in source_anchor
        or "memory/raw/" in provenance
    )


# ---------------------------------------------------------------------------
# 联合 gating boundary
# ---------------------------------------------------------------------------

def enforce_joint_gating_memory_boundary(memory_layer: Dict[str, object]) -> Dict[str, object]:
    """强制联合 gating boundary

    当 route 为 raw 时，仅保留 raw evidence 项，
    并重新生成 block 和 metadata_summary。
    """
    layer = dict(memory_layer or {})
    if str(layer.get("route") or "") != "raw":
        return layer

    items = list(layer.get("items", []) or [])
    candidate_items = list(layer.get("candidate_items", []) or items)
    raw_items = [dict(item) for item in items if is_raw_evidence_item(item)]
    raw_candidate_items = [dict(item) for item in candidate_items if is_raw_evidence_item(item)]

    if raw_items:
        layer["items"] = raw_items
    if raw_candidate_items:
        layer["candidate_items"] = raw_candidate_items
    elif raw_items:
        layer["candidate_items"] = list(raw_items)

    filtered_items = list(layer.get("items", []) or [])
    layer["block"] = FormatBlocks.format_memory_recall_block(filtered_items, "raw")

    metadata_summary = dict(layer.get("metadata_summary") or {})
    metadata_summary.update({
        "source_layers": sorted({item.get("source_layer", "") for item in filtered_items if item.get("source_layer")}),
        "memory_types": sorted({item.get("memory_type", "") for item in filtered_items if item.get("memory_type")}),
        "subject_domains": sorted({item.get("subject_domain", "") for item in filtered_items if item.get("subject_domain")}),
        "evidence_levels": sorted({item.get("evidence_level", "") for item in filtered_items if item.get("evidence_level")}),
        "joint_gating_boundary": "raw_evidence_priority",
        "raw_evidence_items": len(filtered_items),
    })
    layer["metadata_summary"] = metadata_summary
    return layer
