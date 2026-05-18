"""
降级召回模块 — 从 WangchuanPipeline 中提取的降级召回逻辑

当主召回流程异常时，构建降级的 recall payload。

设计：通过 DegradedRecallContext 数据类传入所有预计算数据，
完全消除对 pipeline 实例的依赖，打破循环引用。
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .boundary_gating import is_raw_evidence_item
from .boundary_analysis import (
    derive_primary_evidence_boundary as _derive_primary_evidence_boundary,
    build_joint_gating_status as _build_joint_gating_status,
    assess_cross_topic_risk as _assess_cross_topic_risk,
)
from .format_blocks import FormatBlocks
from .query_profiler import QueryProfiler
from .context_assembler import ContextAssembler


# ---------------------------------------------------------------------------
# 数据上下文 — 所有预计算数据集中在此
# ---------------------------------------------------------------------------

@dataclass
class DegradedRecallContext:
    """降级召回所需的所有预计算数据。

    Pipeline 负责计算所有值并填充此 context，
    degraded_recall 模块仅消费数据，不依赖 pipeline 实例。
    """

    # ---- 查询分析 ----
    query_preference_profile: Dict[str, object] = field(default_factory=dict)

    # ---- 组装 ----
    assembly: Any = None  # ContextAssembly or None

    # ---- 意识上下文 ----
    consciousness_context: str = ""

    # ---- 上下文构建 ----
    wakeup_pack: str = ""
    response_strategy: str = ""
    execution_guidance: str = ""

    # ---- 历史 ----
    history_support: Dict[str, object] = field(default_factory=dict)
    history_search_index: Dict[str, object] = field(default_factory=dict)

    # ---- 记忆层 ----
    memory_layer: Dict[str, object] = field(default_factory=lambda: {
        "route": "degraded_no_memory",
        "reader": "degraded_no_memory",
        "structured": False,
        "items": [],
        "candidate_items": [],
        "metadata_summary": {},
        "block": "",
    })

    # ---- 资源与技能 ----
    resource_items: List[Dict[str, object]] = field(default_factory=list)
    skill_items: List[Dict[str, object]] = field(default_factory=list)

    # ---- 提示节 ----
    selected_sections: List[str] = field(default_factory=list)

    # ---- 证据边界与门控 ----
    primary_evidence_boundary: Dict[str, object] = field(default_factory=dict)
    joint_gating: Dict[str, object] = field(default_factory=dict)
    cross_topic_risk: Dict[str, object] = field(default_factory=dict)

    # ---- 可观测性 ----
    runtime_health: Dict[str, object] = field(default_factory=dict)
    runtime_view: Dict[str, object] = field(default_factory=dict)
    recall_metrics: Dict[str, object] = field(default_factory=dict)


def _runtime_mode_from_primary_role(primary_role: str, degraded: bool) -> str:
    """从 primary_role 推导运行模式。"""
    if not degraded:
        return 'resonance_mainline'
    return 'no_memory' if primary_role in {'', 'no_memory'} else 'foundation_recall'


# ---------------------------------------------------------------------------
# 主函数：构建降级 recall payload
# ---------------------------------------------------------------------------

def build_degraded_recall_payload(
    context: DegradedRecallContext,
    query: str,
    session_id: str,
    top_k: int,
    stage: str,
    error: Exception | str,
    short_followup_mode: bool = False,
    started_at: float | None = None,
) -> Dict[str, object]:
    """构建降级 recall payload

    Args:
        context: 预计算数据上下文，替代 pipeline 实例
        query: 用户查询
        session_id: 会话 ID
        top_k: 返回结果数
        stage: 降级阶段名
        error: 触发降级的异常
        short_followup_mode: 是否为短追问模式
        started_at: 开始时间戳

    Returns:
        降级后的 recall payload dict
    """
    assembly = context.assembly
    consciousness_context = context.consciousness_context
    wakeup_pack = context.wakeup_pack
    response_strategy = context.response_strategy
    execution_guidance = context.execution_guidance
    history_support = context.history_support
    history_search_index = context.history_search_index
    query_preference_profile = context.query_preference_profile
    resource_items = context.resource_items
    skill_items = context.skill_items

    memory_layer = context.memory_layer

    memory_items = list(memory_layer.get('items', []) or [])
    scope_route = str(query_preference_profile.get('scope_route') or 'memory')

    if scope_route == 'resource' and resource_items:
        primary_kind = 'resource'
        primary_role = 'primary_resource'
        primary_reason = f'degraded:{stage}'
        primary_candidates = [{
            'candidate_kind': 'resource',
            'decision_role': 'primary_resource',
            'context_uri': resource_items[0].get('context_uri'),
            'title': resource_items[0].get('title'),
            'path': resource_items[0].get('path'),
            'why_selected': primary_reason,
        }]
        scope_context_block = FormatBlocks.format_resource_recall_block(resource_items)
    elif scope_route == 'skill' and skill_items:
        primary_kind = 'skill'
        primary_role = 'primary_skill'
        primary_reason = f'degraded:{stage}'
        primary_candidates = [{
            'candidate_kind': 'skill',
            'decision_role': 'primary_skill',
            'context_uri': skill_items[0].get('context_uri'),
            'name': skill_items[0].get('name'),
            'path': skill_items[0].get('path'),
            'why_selected': primary_reason,
        }]
        scope_context_block = FormatBlocks.format_skill_recall_block(skill_items)
    elif memory_items:
        primary_kind = 'memory_item'
        primary_role = 'memory_evidence'
        primary_reason = f'degraded:{stage}'
        first_memory = memory_items[0]
        primary_candidates = [{
            'candidate_kind': 'memory_item',
            'decision_role': 'memory_evidence',
            'context_uri': first_memory.get('context_uri'),
            'memory_id': first_memory.get('memory_id'),
            'content_preview': str(first_memory.get('content') or '')[:120],
            'memory_type': first_memory.get('memory_type'),
            'subject_domain': first_memory.get('subject_domain'),
            'why_selected': primary_reason,
        }]
        scope_context_block = memory_layer.get('block', '')
    else:
        primary_kind = 'no_memory'
        primary_role = 'no_memory'
        primary_reason = f'degraded:{stage}'
        primary_candidates = []
        scope_context_block = ''

    resonance_decision_view = {
        'query': query,
        'scope_route': scope_route,
        'summary': {
            'primary_kind': primary_kind,
            'primary_role': primary_role,
            'primary_reason': primary_reason,
            'fusion_count': len(primary_candidates),
            'seed_count': 0,
            'resonance_count': 0,
            'pattern_count': 0,
            'resource_count': len(resource_items),
            'skill_count': len(skill_items),
            'memory_count': len(memory_items),
        },
        'primary_candidates': primary_candidates,
        'supporting_candidates': [],
        'evidence_candidates': primary_candidates if primary_role == 'memory_evidence' else [],
        'pattern_candidates': [],
    }
    decision_context_block = FormatBlocks.format_resonance_decision_block(resonance_decision_view)
    degraded_block = FormatBlocks.format_recall_degraded_block(stage, str(error), 'simplified_fallback')

    stable_prefix = "\n\n".join(part for part in [
        wakeup_pack,
        consciousness_context,
        response_strategy,
        degraded_block,
        decision_context_block,
        scope_context_block,
        getattr(assembly, 'stable_prefix', ''),
    ] if part)
    dynamic_suffix = "\n\n".join(part for part in [
        getattr(assembly, 'dynamic_suffix', ''),
    ] if part)
    final_context = "\n\n".join(part for part in [stable_prefix, dynamic_suffix] if part)
    selected_sections = context.selected_sections

    primary_evidence_boundary = context.primary_evidence_boundary or _derive_primary_evidence_boundary(
        memory_layer,
        history_support,
        query_preference_profile,
        resonance_decision_view,
    )
    joint_gating = context.joint_gating or _build_joint_gating_status(
        memory_layer,
        query_preference_profile,
        history_support,
        primary_evidence_boundary,
        resonance_decision_view,
    )
    cross_topic_risk = context.cross_topic_risk or _assess_cross_topic_risk(
        query,
        memory_layer,
        query_preference_profile,
    )

    # 可观测性：使用 context 中预计算的 recall_metrics
    recall_metrics = context.recall_metrics

    resource_recall_block = FormatBlocks.format_resource_recall_block(resource_items)
    skill_recall_block = FormatBlocks.format_skill_recall_block(skill_items)

    degraded_runtime = context.runtime_health
    runtime_health = context.runtime_health

    return {
        'context': final_context,
        'stable_prefix': stable_prefix,
        'dynamic_suffix': dynamic_suffix,
        'recall_metrics': recall_metrics,
        'wakeup_pack': wakeup_pack,
        'consciousness_context': consciousness_context,
        'response_strategy': response_strategy,
        'execution_guidance': execution_guidance,
        'query_preference_profile': query_preference_profile,
        'scope_route': scope_route,
        'scope_route_profile': query_preference_profile.get('scope_route_profile', {}),
        'decision_context_block': decision_context_block,
        'scope_context_block': scope_context_block,
        'memory_recall_block': memory_layer.get('block', ''),
        'resource_recall_block': resource_recall_block,
        'skill_recall_block': skill_recall_block,
        'seed_candidates': [],
        'resonance_candidates': [],
        'graph_edge_resonance_candidates': [],
        'pattern_candidates': [],
        'fusion_candidates': primary_candidates,
        'resonance_decision_view': resonance_decision_view,
        'resource_items': resource_items,
        'skill_items': skill_items,
        'context_route': query_preference_profile.get('context_route', 'default'),
        'selected_sections': selected_sections,
        'memory_route': memory_layer.get('route'),
        'memory_reader': memory_layer.get('reader'),
        'memory_structured': memory_layer.get('structured', False),
        'memory_items': memory_items,
        'memory_metadata_summary': memory_layer.get('metadata_summary', {}),
        'history_support': history_support,
        'history_search_index': history_search_index,
        'primary_evidence_boundary': primary_evidence_boundary,
        'joint_gating': joint_gating,
        'cross_topic_risk': cross_topic_risk,
        'semantic_cache': {
            'status': 'bypass',
            'cache_key': '',
            'semantic_family': '',
            'state_fingerprint': '',
        },
        'retrieval_debug': {'short_followup_mode': short_followup_mode, 'degraded': True, 'degrade_stage': stage},
        'nodes': [],
        'degraded_runtime': degraded_runtime,
        'runtime_health': runtime_health,
        'assembly': assembly,
    }
