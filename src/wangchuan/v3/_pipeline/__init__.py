"""
Pipeline 拆分模块 — 从 WangchuanPipeline 中提取的子模块

模块列表：
- format_blocks: XML / Block 格式化（零依赖）
- query_profiler: Query 分析与路由（零依赖）
- memory_ranker: Memory 排序评分（依赖 query_profiler）
- context_assembler: Context 组装策略（依赖 format_blocks, query_profiler）
- recall_engine: Recall 候选构建 + 结果组装（依赖 memory_ranker, query_profiler, format_blocks, context_assembler）
- boundary_gating: 边界门控逻辑（依赖 format_blocks）
- degraded_recall: 降级召回逻辑（依赖 pipeline 实例）
- semantic_cache_ops: 语义缓存操作（依赖 RecallSemanticCache）
"""

from .format_blocks import FormatBlocks
from .query_profiler import QueryProfiler
from .memory_ranker import MemoryRanker
from .context_assembler import (
    ContextAssembler,
    safe_read_text,
    extract_bullets_from_markdown,
    parse_datetime_maybe,
    clamp01,
    derive_vitality_state,
    build_memory_context_uri,
    normalize_memory_item_explain,
    shape_memory_items_for_output,
)
from .recall_engine import RecallEngine, build_recall_candidates, build_recall_result
from .boundary_gating import enforce_joint_gating_memory_boundary, is_raw_evidence_item
from .boundary_analysis import (
    derive_primary_evidence_boundary,
    build_joint_gating_status,
    assess_cross_topic_risk,
)
from .degraded_recall import build_degraded_recall_payload, DegradedRecallContext
from .semantic_cache_ops import SemanticCacheOps

__all__ = [
    "FormatBlocks",
    "QueryProfiler",
    "MemoryRanker",
    "ContextAssembler",
    "RecallEngine",
    "build_recall_candidates",
    "build_recall_result",
    "SemanticCacheOps",
    "enforce_joint_gating_memory_boundary",
    "is_raw_evidence_item",
    "derive_primary_evidence_boundary",
    "build_joint_gating_status",
    "assess_cross_topic_risk",
    "build_degraded_recall_payload",
    "DegradedRecallContext",
    # utility re-exports
    "safe_read_text",
    "extract_bullets_from_markdown",
    "parse_datetime_maybe",
    "clamp01",
    "derive_vitality_state",
    "build_memory_context_uri",
    "normalize_memory_item_explain",
    "shape_memory_items_for_output",
]
