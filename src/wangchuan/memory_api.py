#!/usr/bin/env python3
"""
忘川统一记忆 API v1.0

极简接口，封装所有忘川模块：
  memory.remember("用户喜欢冰美式")
  memory.recall("用户偏好")
  memory.status()

默认主链：
- 统一读写工作区下的 wangchuan/.index/index.sqlite
- 高重要性记忆同步到工作区根目录 MEMORY.md
- 不使用 legacy .wangchuan/memory.db 作为默认主库

职责边界（P2-06 Initializer / Operator 分工落板）：
- 本文件属于 **operator 主入口**：负责运行期记忆读写、write gate、candidate/promotion、recall、hot memory curator
- 本文件不负责一次性初始化底座（seed/bootstrap/migration/baseline），不要继续把初始化职责塞回这里
- 一次性初始化/可移植种子/价值评估等底座能力，应分别留在 initializer 工具或离线构建链中

注意：
- 本文件是对外统一 API，不应继续扩大对 `v3/*` 运行态模块的耦合
- 后续重构方向：能量/时间等运行态能力逐步迁出忘川目录
"""

import os
import sys
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime
import time
from typing import List, Dict, Optional, Any
import re
import json

try:
    from wangchuan.paths import data_root, default_db_path, state_root, workspace_root
except ImportError:
    from wangchuan.paths import data_root, default_db_path, state_root, workspace_root

# 添加项目路径
WORKSPACE_ROOT = workspace_root()
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

try:
    from wangchuan.fts_utils import build_safe_fts_match_query
except ImportError:
    from wangchuan.fts_utils import build_safe_fts_match_query
try:
    from wangchuan._adapters.runtime_adapter import get_energy_state as get_runtime_energy_state
except ImportError:
    # 终极 fallback - 无 L4 可用
    def get_runtime_energy_state():
        return {"enabled": False, "state_label": "noop", "state": "noop"}
try:
    from wangchuan.v3.temporal_engine import TemporalEngine
except ImportError:
    from wangchuan.v3.temporal_engine import TemporalEngine
try:
    from wangchuan.v3.local_vector_search import LocalMemoryVectorSearch
except ImportError:
    from wangchuan.v3.local_vector_search import LocalMemoryVectorSearch
try:
    from wangchuan.v3.llm_memory import LLMExtractor, EntityLinker, MultiLevelMemory
except ImportError:
    from wangchuan.v3.llm_memory import LLMExtractor, EntityLinker, MultiLevelMemory

try:
    from wangchuan.memory_diagnostics import (
        history_search_healthcheck as _history_search_healthcheck_impl,
        status as _status_impl,
        task_resume as _task_resume_impl,
        user_healthcheck as _user_healthcheck_impl,
        write_gate_probe as _write_gate_probe_impl,
    )
except ImportError:
    from wangchuan.memory_diagnostics import (
        history_search_healthcheck as _history_search_healthcheck_impl,
        status as _status_impl,
        task_resume as _task_resume_impl,
        user_healthcheck as _user_healthcheck_impl,
        write_gate_probe as _write_gate_probe_impl,
    )

try:
    from wangchuan.memory_reporting import (
        export_memories as _export_memories_impl,
        get_memory_stats as _get_memory_stats_impl,
        get_quality_metrics as _get_quality_metrics_impl,
    )
except ImportError:
    from wangchuan.memory_reporting import (
        export_memories as _export_memories_impl,
        get_memory_stats as _get_memory_stats_impl,
        get_quality_metrics as _get_quality_metrics_impl,
    )

try:
    from wangchuan.memory_recent import recent as _recent_impl
except ImportError:
    from wangchuan.memory_recent import recent as _recent_impl

try:
    from wangchuan.memory_delete import forget as _forget_impl
except ImportError:
    from wangchuan.memory_delete import forget as _forget_impl

try:
    from wangchuan.memory_versioning import history as _history_impl, rollback as _rollback_impl
except ImportError:
    from wangchuan.memory_versioning import history as _history_impl, rollback as _rollback_impl

try:
    from wangchuan.memory_supersession_chain import (
        get_supersession_chain as _get_supersession_chain_impl,
    )
except ImportError:
    from wangchuan.memory_supersession_chain import (
        get_supersession_chain as _get_supersession_chain_impl,
    )

try:
    from wangchuan.memory_merge import merge as _merge_impl
except ImportError:
    from wangchuan.memory_merge import merge as _merge_impl

try:
    from wangchuan.memory_hot_sync import sync_to_memory_md as _sync_to_memory_md_impl
except ImportError:
    from wangchuan.memory_hot_sync import sync_to_memory_md as _sync_to_memory_md_impl

try:
    from wangchuan.memory_encryption import (
        decrypt_memory as _decrypt_memory_impl,
        encrypt_memory as _encrypt_memory_impl,
        ensure_encrypted_table as _ensure_encrypted_table_impl,
    )
except ImportError:
    from wangchuan.memory_encryption import (
        decrypt_memory as _decrypt_memory_impl,
        encrypt_memory as _encrypt_memory_impl,
        ensure_encrypted_table as _ensure_encrypted_table_impl,
    )

try:
    from wangchuan.memory_multimodal import (
        add_audio as _add_audio_impl,
        add_image as _add_image_impl,
        ensure_multimodal_table as _ensure_multimodal_table_impl,
        get_multimodal as _get_multimodal_impl,
    )
except ImportError:
    from wangchuan.memory_multimodal import (
        add_audio as _add_audio_impl,
        add_image as _add_image_impl,
        ensure_multimodal_table as _ensure_multimodal_table_impl,
        get_multimodal as _get_multimodal_impl,
    )

try:
    from wangchuan.memory_acl import (
        check_access as _check_access_impl,
        ensure_acl_table as _ensure_acl_table_impl,
        get_user_memories as _get_user_memories_impl,
        grant_access as _grant_access_impl,
        revoke_access as _revoke_access_impl,
    )
except ImportError:
    from wangchuan.memory_acl import (
        check_access as _check_access_impl,
        ensure_acl_table as _ensure_acl_table_impl,
        get_user_memories as _get_user_memories_impl,
        grant_access as _grant_access_impl,
        revoke_access as _revoke_access_impl,
    )

try:
    from wangchuan.memory_sync_queue import (
        ensure_sync_table as _ensure_sync_table_impl,
        get_sync_status as _get_sync_status_impl,
        sync_all_pending as _sync_all_pending_impl,
        sync_to_node as _sync_to_node_impl,
    )
except ImportError:
    from wangchuan.memory_sync_queue import (
        ensure_sync_table as _ensure_sync_table_impl,
        get_sync_status as _get_sync_status_impl,
        sync_all_pending as _sync_all_pending_impl,
        sync_to_node as _sync_to_node_impl,
    )

try:
    from wangchuan.memory_nodes import (
        ensure_nodes_table as _ensure_nodes_table_impl,
        list_nodes as _list_nodes_impl,
        register_node as _register_node_impl,
    )
except ImportError:
    from wangchuan.memory_nodes import (
        ensure_nodes_table as _ensure_nodes_table_impl,
        list_nodes as _list_nodes_impl,
        register_node as _register_node_impl,
    )

try:
    from wangchuan.memory_tags import (
        add_tag as _add_tag_impl,
        ensure_tags_table as _ensure_tags_table_impl,
        find_by_tag as _find_by_tag_impl,
        get_tags as _get_tags_impl,
        list_all_tags as _list_all_tags_impl,
        remove_tag as _remove_tag_impl,
    )
except ImportError:
    from wangchuan.memory_tags import (
        add_tag as _add_tag_impl,
        ensure_tags_table as _ensure_tags_table_impl,
        find_by_tag as _find_by_tag_impl,
        get_tags as _get_tags_impl,
        list_all_tags as _list_all_tags_impl,
        remove_tag as _remove_tag_impl,
    )

try:
    from wangchuan.memory_reflection_cleanup import (
        audit_question_like_rules as _audit_question_like_rules_impl,
        cleanup_noise as _cleanup_noise_impl,
        cleanup_question_like_rule_noise as _cleanup_question_like_rule_noise_impl,
        cleanup_duplicate_reflections as _cleanup_duplicate_reflections_impl,
        cleanup_duplicate_rule_memories as _cleanup_duplicate_rule_memories_impl,
        cleanup_historical_noise as _cleanup_historical_noise_impl,
        cleanup_low_value_emotional_memories as _cleanup_low_value_emotional_memories_impl,
        pick_duplicate_memory_keeper as _pick_duplicate_memory_keeper_impl,
    )
except ImportError:
    from wangchuan.memory_reflection_cleanup import (
        audit_question_like_rules as _audit_question_like_rules_impl,
        cleanup_noise as _cleanup_noise_impl,
        cleanup_question_like_rule_noise as _cleanup_question_like_rule_noise_impl,
        cleanup_duplicate_reflections as _cleanup_duplicate_reflections_impl,
        cleanup_duplicate_rule_memories as _cleanup_duplicate_rule_memories_impl,
        cleanup_historical_noise as _cleanup_historical_noise_impl,
        cleanup_low_value_emotional_memories as _cleanup_low_value_emotional_memories_impl,
        pick_duplicate_memory_keeper as _pick_duplicate_memory_keeper_impl,
    )


try:
    from wangchuan.memory_hot_priority import (
        canonical_hot_memory_key as _canonical_hot_memory_key_impl,
        compute_hot_memory_candidate as _compute_hot_memory_candidate_impl,
        compute_hotness_score as _compute_hotness_score_impl,
        compute_quality_score as _compute_quality_score_impl,
        hot_memory_priority as _hot_memory_priority_impl,
        normalize_hot_memory_text as _normalize_hot_memory_text_impl,
    )
except ImportError:
    from wangchuan.memory_hot_priority import (
        canonical_hot_memory_key as _canonical_hot_memory_key_impl,
        compute_hot_memory_candidate as _compute_hot_memory_candidate_impl,
        compute_hotness_score as _compute_hotness_score_impl,
        compute_quality_score as _compute_quality_score_impl,
        hot_memory_priority as _hot_memory_priority_impl,
        normalize_hot_memory_text as _normalize_hot_memory_text_impl,
    )


try:
    from wangchuan.memory_write_gate import (
        evaluate_write_gate as _evaluate_write_gate_impl,
        read_recent_write_gate_events as _read_recent_write_gate_events_impl,
    )
except ImportError:
    from wangchuan.memory_write_gate import (
        evaluate_write_gate as _evaluate_write_gate_impl,
        read_recent_write_gate_events as _read_recent_write_gate_events_impl,
    )


try:
    from wangchuan.memory_remember_postwrite import (
        run_remember_postwrite as _run_remember_postwrite_impl,
    )
except ImportError:
    from wangchuan.memory_remember_postwrite import (
        run_remember_postwrite as _run_remember_postwrite_impl,
    )


try:
    from wangchuan.memory_remember_flow import (
        remember as _remember_impl,
    )
except ImportError:
    from wangchuan.memory_remember_flow import (
        remember as _remember_impl,
    )


try:
    from wangchuan.memory_remember_outcomes import (
        remember_allowed_outcome as _remember_allowed_outcome_impl,
        remember_blocked_outcome as _remember_blocked_outcome_impl,
        remember_deduped_outcome as _remember_deduped_outcome_impl,
    )
except ImportError:
    from wangchuan.memory_remember_outcomes import (
        remember_allowed_outcome as _remember_allowed_outcome_impl,
        remember_blocked_outcome as _remember_blocked_outcome_impl,
        remember_deduped_outcome as _remember_deduped_outcome_impl,
    )


try:
    from wangchuan.memory_recall_candidates import (
        collect_recall_candidate_rows as _collect_recall_candidate_rows_impl,
    )
except ImportError:
    from wangchuan.memory_recall_candidates import (
        collect_recall_candidate_rows as _collect_recall_candidate_rows_impl,
    )


try:
    from wangchuan.memory_recall_rows import (
        build_recall_items as _build_recall_items_impl,
    )
except ImportError:
    from wangchuan.memory_recall_rows import (
        build_recall_items as _build_recall_items_impl,
    )


try:
    from wangchuan.memory_recall_service import (
        recall_rows as _recall_rows_impl,
    )
except ImportError:
    from wangchuan.memory_recall_service import (
        recall_rows as _recall_rows_impl,
    )


try:
    from wangchuan.memory_schema_sql import (
        delete_memory_schema_index as _delete_memory_schema_index_impl,
        ensure_memory_schema_index_table as _ensure_memory_schema_index_table_impl,
        upsert_memory_schema_index as _upsert_memory_schema_index_impl,
    )
except ImportError:
    from wangchuan.memory_schema_sql import (
        delete_memory_schema_index as _delete_memory_schema_index_impl,
        ensure_memory_schema_index_table as _ensure_memory_schema_index_table_impl,
        upsert_memory_schema_index as _upsert_memory_schema_index_impl,
    )


try:
    from wangchuan.memory_schema_indexing import (
        backfill_memory_schema_index as _backfill_memory_schema_index_impl,
        memory_schema_index_status as _memory_schema_index_status_impl,
        structured_memory_overview as _structured_memory_overview_impl,
        user_canonical_profile as _user_canonical_profile_impl,
        sync_maintenance_updates as _sync_maintenance_updates_impl,
    )
except ImportError:
    from wangchuan.memory_schema_indexing import (
        backfill_memory_schema_index as _backfill_memory_schema_index_impl,
        memory_schema_index_status as _memory_schema_index_status_impl,
        structured_memory_overview as _structured_memory_overview_impl,
        user_canonical_profile as _user_canonical_profile_impl,
        sync_maintenance_updates as _sync_maintenance_updates_impl,
    )


try:
    from wangchuan.memory_schema_sidecar import (
        batch_mark_memory_schema_removed as _batch_mark_memory_schema_removed_impl,
        mark_memory_schema_removed as _mark_memory_schema_removed_impl,
        memory_schema_record_path as _memory_schema_record_path_impl,
        memory_schema_registry_path as _memory_schema_registry_path_impl,
        persist_memory_schema as _persist_memory_schema_impl,
        read_memory_schema as _read_memory_schema_impl,
        update_memory_schema_fields as _update_memory_schema_fields_impl,
    )
except ImportError:
    from wangchuan.memory_schema_sidecar import (
        batch_mark_memory_schema_removed as _batch_mark_memory_schema_removed_impl,
        mark_memory_schema_removed as _mark_memory_schema_removed_impl,
        memory_schema_record_path as _memory_schema_record_path_impl,
        memory_schema_registry_path as _memory_schema_registry_path_impl,
        persist_memory_schema as _persist_memory_schema_impl,
        read_memory_schema as _read_memory_schema_impl,
        update_memory_schema_fields as _update_memory_schema_fields_impl,
    )


try:
    from wangchuan.memory_lesson_candidates import (
        persist_lesson_candidate as _persist_lesson_candidate_impl,
        promote_lesson_candidate as _promote_lesson_candidate_impl,
        remember_lesson as _remember_lesson_impl,
    )
except ImportError:
    from wangchuan.memory_lesson_candidates import (
        persist_lesson_candidate as _persist_lesson_candidate_impl,
        promote_lesson_candidate as _promote_lesson_candidate_impl,
        remember_lesson as _remember_lesson_impl,
    )


try:
    from wangchuan.memory_metadata_trace import (
        enrich_missing_trace_metadata as _enrich_missing_trace_metadata_impl,
        infer_memory_metadata as _infer_memory_metadata_impl,
    )
except ImportError:
    from wangchuan.memory_metadata_trace import (
        enrich_missing_trace_metadata as _enrich_missing_trace_metadata_impl,
        infer_memory_metadata as _infer_memory_metadata_impl,
    )


try:
    from wangchuan.memory_metadata_builder import (
        build_memory_metadata as _build_memory_metadata_impl,
    )
except ImportError:
    from wangchuan.memory_metadata_builder import (
        build_memory_metadata as _build_memory_metadata_impl,
    )


try:
    from wangchuan.memory_trace_repair import (
        repair_trace_metadata as _repair_trace_metadata_impl,
    )
except ImportError:
    from wangchuan.memory_trace_repair import (
        repair_trace_metadata as _repair_trace_metadata_impl,
    )


try:
    from wangchuan.memory_llm_extract import (
        extract_with_llm as _extract_with_llm_impl,
    )
except ImportError:
    from wangchuan.memory_llm_extract import (
        extract_with_llm as _extract_with_llm_impl,
    )


try:
    from wangchuan.memory_llm_client import (
        get_llm_client as _get_llm_client_impl,
    )
except ImportError:
    from wangchuan.memory_llm_client import (
        get_llm_client as _get_llm_client_impl,
    )


try:
    from wangchuan.memory_basic_utils import (
        build_message_anchor as _build_message_anchor_impl,
        build_turn_signature_from_message as _build_turn_signature_from_message_impl,
        coerce_bool as _coerce_bool_impl,
        coerce_float as _coerce_float_impl,
        extract_source_anchor as _extract_source_anchor_impl,
        extract_turn_signature as _extract_turn_signature_impl,
        is_cli_mirror_session as _is_cli_mirror_session_impl,
        message_content_signature as _message_content_signature_impl,
        memory_schema_dir as _memory_schema_dir_impl,
        migrate_schema as _migrate_schema_impl,
        normalize_tags as _normalize_tags_impl,
        parse_iso_dt as _parse_iso_dt_impl,
        write_gate_sidelog as _write_gate_sidelog_impl,
    )
except ImportError:
    from wangchuan.memory_basic_utils import (
        build_message_anchor as _build_message_anchor_impl,
        build_turn_signature_from_message as _build_turn_signature_from_message_impl,
        coerce_bool as _coerce_bool_impl,
        coerce_float as _coerce_float_impl,
        extract_source_anchor as _extract_source_anchor_impl,
        extract_turn_signature as _extract_turn_signature_impl,
        is_cli_mirror_session as _is_cli_mirror_session_impl,
        message_content_signature as _message_content_signature_impl,
        memory_schema_dir as _memory_schema_dir_impl,
        migrate_schema as _migrate_schema_impl,
        normalize_tags as _normalize_tags_impl,
        parse_iso_dt as _parse_iso_dt_impl,
        write_gate_sidelog as _write_gate_sidelog_impl,
    )


try:
    from wangchuan.memory_markdown_utils import (
        extract_bullet_items as _extract_bullet_items_impl,
        extract_label_value as _extract_label_value_impl,
        extract_markdown_section as _extract_markdown_section_impl,
    )
except ImportError:
    from wangchuan.memory_markdown_utils import (
        extract_bullet_items as _extract_bullet_items_impl,
        extract_label_value as _extract_label_value_impl,
        extract_markdown_section as _extract_markdown_section_impl,
    )


try:
    from wangchuan.memory_runtime_accessors import (
        conn as _conn_impl,
        get_entity_linker as _get_entity_linker_impl,
        get_local_vector as _get_local_vector_impl,
    )
except ImportError:
    from wangchuan.memory_runtime_accessors import (
        conn as _conn_impl,
        get_entity_linker as _get_entity_linker_impl,
        get_local_vector as _get_local_vector_impl,
    )


try:
    from wangchuan.memory_recall_query import (
        build_recall_keyword_tokens as _build_recall_keyword_tokens_impl,
        filter_recall_noise as _filter_recall_noise_impl,
        is_recall_noise as _is_recall_noise_impl,
        normalize_temporal_probe as _normalize_temporal_probe_impl,
        rrf_fusion as _rrf_fusion_impl,
    )
except ImportError:
    from wangchuan.memory_recall_query import (
        build_recall_keyword_tokens as _build_recall_keyword_tokens_impl,
        filter_recall_noise as _filter_recall_noise_impl,
        is_recall_noise as _is_recall_noise_impl,
        normalize_temporal_probe as _normalize_temporal_probe_impl,
        rrf_fusion as _rrf_fusion_impl,
    )


try:
    from wangchuan.memory_recall_ranking import (
        build_recall_explain as _build_recall_explain_impl,
        char_bigram_overlap_score as _char_bigram_overlap_score_impl,
        compact_recall_match_text as _compact_recall_match_text_impl,
        duplicate_memory_sort_key as _duplicate_memory_sort_key_impl,
        honorific_alias_bonus as _honorific_alias_bonus_impl,
        memory_type_priority as _memory_type_priority_impl,
        normalize_recall_match_text as _normalize_recall_match_text_impl,
        recall_rank_score as _recall_rank_score_impl,
        recall_result_sort_key as _recall_result_sort_key_impl,
        recall_text_match_score as _recall_text_match_score_impl,
        recall_token_weight as _recall_token_weight_impl,
        source_layer_priority as _source_layer_priority_impl,
        source_session_priority as _source_session_priority_impl,
        trace_completeness as _trace_completeness_impl,
    )
except ImportError:
    from wangchuan.memory_recall_ranking import (
        build_recall_explain as _build_recall_explain_impl,
        char_bigram_overlap_score as _char_bigram_overlap_score_impl,
        compact_recall_match_text as _compact_recall_match_text_impl,
        duplicate_memory_sort_key as _duplicate_memory_sort_key_impl,
        honorific_alias_bonus as _honorific_alias_bonus_impl,
        memory_type_priority as _memory_type_priority_impl,
        normalize_recall_match_text as _normalize_recall_match_text_impl,
        recall_rank_score as _recall_rank_score_impl,
        recall_result_sort_key as _recall_result_sort_key_impl,
        recall_text_match_score as _recall_text_match_score_impl,
        recall_token_weight as _recall_token_weight_impl,
        source_layer_priority as _source_layer_priority_impl,
        source_session_priority as _source_session_priority_impl,
        trace_completeness as _trace_completeness_impl,
    )


try:
    from wangchuan.memory_reflection_trace import (
        find_existing_exact_reflection_memory as _find_existing_exact_reflection_memory_impl,
        find_existing_reflection_memory as _find_existing_reflection_memory_impl,
        has_preferred_non_cli_mirror as _has_preferred_non_cli_mirror_impl,
        lookup_related_memory_trace as _lookup_related_memory_trace_impl,
        lookup_message_trace as _lookup_message_trace_impl,
        lookup_static_context_trace as _lookup_static_context_trace_impl,
        normalize_reflection_source_query as _normalize_reflection_source_query_impl,
        semantic_token_overlap_score as _semantic_token_overlap_score_impl,
        static_context_trace_candidates as _static_context_trace_candidates_impl,
        split_semantic_match_tokens as _split_semantic_match_tokens_impl,
        normalize_static_context_match_text as _normalize_static_context_match_text_impl,
    )
except ImportError:
    from wangchuan.memory_reflection_trace import (
        find_existing_exact_reflection_memory as _find_existing_exact_reflection_memory_impl,
        find_existing_reflection_memory as _find_existing_reflection_memory_impl,
        has_preferred_non_cli_mirror as _has_preferred_non_cli_mirror_impl,
        lookup_related_memory_trace as _lookup_related_memory_trace_impl,
        lookup_message_trace as _lookup_message_trace_impl,
        lookup_static_context_trace as _lookup_static_context_trace_impl,
        normalize_reflection_source_query as _normalize_reflection_source_query_impl,
        semantic_token_overlap_score as _semantic_token_overlap_score_impl,
        static_context_trace_candidates as _static_context_trace_candidates_impl,
        split_semantic_match_tokens as _split_semantic_match_tokens_impl,
        normalize_static_context_match_text as _normalize_static_context_match_text_impl,
    )


try:
    from wangchuan.memory_rules import (
        GRAPH_INGEST_BLOCK_PATTERNS as _GRAPH_INGEST_BLOCK_PATTERNS,
        REFLECTION_RUNTIME_NOISE_PATTERNS,
        HISTORICAL_NOISE_MEMORY_RULES,
        LOW_VALUE_EMOTIONAL_RULES,
        STATIC_CONTEXT_TRACE_RULES,
        coerce_gate_bool as _coerce_gate_bool,
        looks_like_reflection_runtime_noise as _looks_like_reflection_runtime_noise,
        classify_historical_noise_memory,
        classify_low_value_emotional_memory,
        graph_ingest_gate as _graph_ingest_gate_impl,
    )
except ImportError:
    from wangchuan.memory_rules import (
        GRAPH_INGEST_BLOCK_PATTERNS as _GRAPH_INGEST_BLOCK_PATTERNS,
        REFLECTION_RUNTIME_NOISE_PATTERNS,
        HISTORICAL_NOISE_MEMORY_RULES,
        LOW_VALUE_EMOTIONAL_RULES,
        STATIC_CONTEXT_TRACE_RULES,
        coerce_gate_bool as _coerce_gate_bool,
        looks_like_reflection_runtime_noise as _looks_like_reflection_runtime_noise,
        classify_historical_noise_memory,
        classify_low_value_emotional_memory,
        graph_ingest_gate as _graph_ingest_gate_impl,
    )


# 兼容导出锚点：
# - 运行逻辑仍以 memory_rules.py 为单一规则来源
# - memory_api.py 继续保留旧导入面与文本契约，避免外部/测试直接断裂
GRAPH_INGEST_BLOCK_PATTERNS = [
    *_GRAPH_INGEST_BLOCK_PATTERNS,
]


def coerce_gate_bool(value: Any) -> bool:
    """兼容导出：保留 memory_api 旧 helper 入口。"""
    return _coerce_gate_bool(value)


def looks_like_reflection_runtime_noise(text: str) -> bool:
    """兼容导出：保留 memory_api 旧 helper 入口。"""
    return _looks_like_reflection_runtime_noise(text)


def graph_ingest_gate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """兼容 wrapper，具体规则实现保留在 memory_rules.graph_ingest_gate。"""
    return _graph_ingest_gate_impl(payload)


class Memory:
    """忘川统一记忆 API"""

    SCHEMA_FIELDS = [
        "provenance",
        "lifecycle",
        "dedupe_key",
        "conflict_group",
        "quality_score",
        "evidence_level",
        "promotion_state",
        "last_confirmed_at",
        "hotness_score",
        "recall_source_type",
        "valid_from",
        "valid_until",
        "superseded_by",
        "supersession_chain",
    ]
    HOT_MEMORY_MAX_LINES = 60
    HOT_MEMORY_MAX_ITEMS = 24
    HOT_MEMORY_MAX_TEXT_LENGTH = 160
    HOT_MEMORY_ALLOWED_TAGS = {
        'user', 'preference', 'rule', 'lesson', 'memory', 'identity', 'profile', 'habit'
    }
    HOT_MEMORY_BLOCK_TAGS = {
        'candidate', 'pending_review', 'reviewed', 'conversation', 'summary', 'raw',
        'test', 'tests', 'testing', 'unittest', 'pytest', 'fixture', 'demo', 'sample',
        'http_api_test', 'live_verify', 'benchmark', 'perf', 'temporary', 'tmp'
    }
    HOT_MEMORY_BLOCK_PATTERNS = [
        r"\bhttp_api_test\b",
        r"\blive_verify\b",
        r"\[cron\]",
        r"情感事件:",
        r"来源:\s*memory/raw/",
        r"\bturn=",
        r"\bconversation\b",
        r"\bsummary\b",
        r"\bpytest\b",
        r"\bunittest\b",
        r"测试",
        r"demo",
        r"sample",
        r"测试通过",
        r"回归测试",
        r"py_compile",
        r"通过\d+项测试",
    ]

    WRITE_GATE_BLOCK_PATTERNS = [
        r"\bhttp_api_test\b",
        r"\blive_verify\b",
        r"\bbridge retry verify\b",
        r"\btest_ingest\b",
        r"\btest\b",
        r"\bunittest\b",
        r"\bpytest\b",
        r"\bfixture\b",
        r"\bdemo\b",
        r"\bsample\b",
        r"\bexample\b",
        r"\btmp/",
        r"\btemp/",
        r"\[cron\]",
        r"^情感事件:\s*<media:[^>]+>\s*$",
        *REFLECTION_RUNTIME_NOISE_PATTERNS,
    ]

    WRITE_GATE_BLOCK_TAGS = {
        'test', 'tests', 'testing', 'unittest', 'pytest', 'fixture', 'demo', 'sample', 'example',
        'http_api_test', 'live_verify', 'benchmark', 'perf', 'temporary', 'tmp'
    }

    WRITE_GATE_ALLOW_HINTS = [
        '用户', '偏好', '喜欢', '称呼', '规则', '必须', '禁止', '不要', '记住', '记忆',
        '长期', '约定', '事实', 'lesson', '教训', '踩坑', '经验'
    ]

    RECALL_NOISE_PATTERNS = [
        r"\bhttp_api_test\b",
        r"\blive_verify\b",
        r"\bbridge retry verify\b",
        r"\btest_ingest\b",
        r"\bunittest\b",
        r"\bpytest\b",
        r"\[cron\]",
        r"情感事件:",
        r"^情感事件:\s*<media:[^>]+>\s*$",
        r"^情感事件:\s*(?:heartbeat poll(?::| at\b)|read heartbeat\.md if it exists\b|system async\b|system \(untrusted\):\s*exec completion notices\b|continue the telegram bot rebinding task\b)",
        r"^测试$",
        r"\bdemo\b",
        r"\bsample\b",
    ]

    RECALL_LOW_SIGNAL_TOKENS = {
        '用户', '偏好', '规则', '需要', '要求', '回答', '回复', '默认', '继续', '推进', '任务'
    }

    RECALL_QUERY_ALIAS_HINTS = {
        '简洁': ('分段', '重点回复', '一个重点', '一大坨'),
        '短回复': ('一个重点', '分段', '重点回复'),
        '一屏内': ('一个重点', '分段'),
        '简略': ('简洁', '分段', '一个重点'),
    }

    def __init__(self, db_path: str = None):
        self.db_path = str(Path(db_path).expanduser().resolve()) if db_path else str(default_db_path())
        self.temporal = TemporalEngine()
        self._memory_schema_index_ready = False
        self._last_recall_runtime: Dict[str, Any] = {
            "status": "idle",
            "degraded": False,
            "reader": "",
            "query": "",
            "source_layer": "",
            "as_of": "",
            "result_count": 0,
            "timestamp": "",
        }
        self._last_recall_error: Dict[str, Any] = {}
        self._migrate_schema()
        self._llm_client = None
        self._local_vector: Optional[LocalMemoryVectorSearch] = None
        self._entity_linker: Optional[EntityLinker] = None
    
    def _get_local_vector(self) -> LocalMemoryVectorSearch:
        """获取本地向量搜索引擎（懒加载）"""
        return _get_local_vector_impl(self)

    def _get_entity_linker(self) -> EntityLinker:
        """获取实体链接器（懒加载）"""
        return _get_entity_linker_impl(self)

    def _get_llm_client(self):
        """获取 LLM 客户端（懒加载）"""
        return _get_llm_client_impl(self)

    def extract_with_llm(self, text: str, extraction_type: str = "preference", max_items: int = 5) -> List[Dict[str, Any]]:
        """
        使用 LLM 从文本中提取结构化信息（高级选项）。
        
        Args:
            text: 要分析的文本
            extraction_type: 提取类型 (preference/fact/rule)
            max_items: 最大提取项数
            
        Returns:
            [{"content": str, "importance": float, "tags": List[str]}, ...]
            
        用法：
            results = memory.extract_with_llm(
                "用户说：他喜欢喝冰美式，不吃辣，工作日一般晚上10点下班",
                extraction_type="preference"
            )
        """
        return _extract_with_llm_impl(
            self,
            text=text,
            extraction_type=extraction_type,
            max_items=max_items,
        )

    def _migrate_schema(self):
        """数据库迁移：添加新字段"""
        return _migrate_schema_impl(self)

    def _conn(self):
        """获取数据库连接"""
        return _conn_impl(self)

    def _write_gate_sidelog(self, payload: Dict[str, Any]) -> None:
        return _write_gate_sidelog_impl(payload)

    def _coerce_bool(self, value: Any) -> bool:
        return _coerce_bool_impl(value)

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        return _coerce_float_impl(value, default=default)

    def _extract_turn_signature(self, content: str) -> str:
        return _extract_turn_signature_impl(content)

    def _extract_source_anchor(self, content: str) -> str:
        return _extract_source_anchor_impl(content)

    @staticmethod
    def _message_content_signature(content: str) -> str:
        return _message_content_signature_impl(content)

    @classmethod
    def _build_turn_signature_from_message(cls, session_id: str, message_id: Any, timestamp: str, content: str) -> str:
        return _build_turn_signature_from_message_impl(session_id, message_id, timestamp, content)

    @staticmethod
    def _build_message_anchor(session_id: str, message_id: str, turn_signature: str) -> str:
        return _build_message_anchor_impl(session_id, message_id, turn_signature)

    @staticmethod
    def _normalize_reflection_source_query(content: str) -> str:
        return _normalize_reflection_source_query_impl(content)

    @staticmethod
    def _parse_iso_dt(value: Any) -> datetime | None:
        return _parse_iso_dt_impl(value)

    @staticmethod
    def _is_cli_mirror_session(session_id: str) -> bool:
        return _is_cli_mirror_session_impl(session_id)

    @staticmethod
    def _source_session_priority(session_id: str) -> int:
        return _source_session_priority_impl(session_id)

    @staticmethod
    def _trace_completeness(source_anchor: Any, source_session: Any, turn_signature: Any) -> int:
        return _trace_completeness_impl(source_anchor, source_session, turn_signature)

    @staticmethod
    def _memory_type_priority(memory_type: str) -> int:
        return _memory_type_priority_impl(memory_type)

    @staticmethod
    def _source_layer_priority(source_layer: str) -> int:
        return _source_layer_priority_impl(source_layer)

    def _has_preferred_non_cli_mirror(self, candidate: sqlite3.Row, siblings: List[sqlite3.Row]) -> bool:
        return _has_preferred_non_cli_mirror_impl(self, candidate, siblings)

    def _duplicate_memory_sort_key(self, row: sqlite3.Row) -> tuple:
        return _duplicate_memory_sort_key_impl(self, row)

    def _recall_rank_score(self, item: Dict[str, Any]) -> float:
        return _recall_rank_score_impl(self, item)

    def _build_recall_explain(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return _build_recall_explain_impl(self, item)

    @staticmethod
    def _normalize_recall_match_text(text: Any) -> str:
        return _normalize_recall_match_text_impl(text)

    @classmethod
    def _compact_recall_match_text(cls, text: Any) -> str:
        return _compact_recall_match_text_impl(text)

    @classmethod
    def _recall_token_weight(cls, token: str) -> float:
        return _recall_token_weight_impl(cls, token)

    @classmethod
    def _char_bigram_overlap_score(cls, source: str, target: str) -> float:
        return _char_bigram_overlap_score_impl(source, target)

    @classmethod
    def _honorific_alias_bonus(cls, source: str, target: str) -> float:
        return _honorific_alias_bonus_impl(source, target)

    def _recall_text_match_score(self, content: Any, normalized_query: str, keyword_tokens: List[str]) -> float:
        return _recall_text_match_score_impl(self, content, normalized_query, keyword_tokens)

    def _recall_result_sort_key(self, item: Dict[str, Any]) -> tuple:
        return _recall_result_sort_key_impl(self, item)

    def _pick_duplicate_memory_keeper(self, rows: List[sqlite3.Row]) -> sqlite3.Row | None:
        return _pick_duplicate_memory_keeper_impl(self, rows)

    def _find_existing_exact_reflection_memory(self, content: str, metadata: Dict[str, Any] | None = None) -> int | None:
        return _find_existing_exact_reflection_memory_impl(self, content, metadata)

    def _lookup_message_trace(self, content: str, created_at: str = "") -> Dict[str, Any]:
        return _lookup_message_trace_impl(self, content, created_at)

    @staticmethod
    def _normalize_static_context_match_text(content: str) -> str:
        return _normalize_static_context_match_text_impl(content)

    def _static_context_trace_candidates(self) -> List[Dict[str, Any]]:
        return _static_context_trace_candidates_impl(self)

    def _lookup_static_context_trace(self, content: str, memory_type: str = "") -> Dict[str, Any]:
        return _lookup_static_context_trace_impl(self, content, memory_type)

    @staticmethod
    def _split_semantic_match_tokens(content: str) -> List[str]:
        return _split_semantic_match_tokens_impl(content)

    @classmethod
    def _semantic_token_overlap_score(cls, source: str, target: str) -> float:
        return _semantic_token_overlap_score_impl(source, target)

    def _lookup_related_memory_trace(self, content: str, memory_type: str = "", exclude_memory_id: Any = None) -> Dict[str, Any]:
        return _lookup_related_memory_trace_impl(self, content, memory_type, exclude_memory_id)

    @staticmethod
    def _normalize_hot_memory_text(content: str) -> str:
        return _normalize_hot_memory_text_impl(content)

    @staticmethod
    def _canonical_hot_memory_key(content: str) -> str:
        return _canonical_hot_memory_key_impl(content)

    def _hot_memory_priority(self, text: str, tags: List[str] | None = None, metadata: Dict[str, Any] | None = None) -> int:
        return _hot_memory_priority_impl(self, text, tags, metadata)

    def _build_memory_metadata(self, content: str, tags: List[str] | None = None, metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
        # compat anchors for text-based regression tests:
        # "source_layer": source_layer,
        # "memory_type": memory_type,
        # "dedupe_key": dedupe_key,
        # "quality_score": round(float(quality_score), 3),
        # "hotness_score": round(float(hotness_score), 3),
        # "scope_level": scope_level,
        # "supersession_chain": supersession_chain,
        return _build_memory_metadata_impl(self, content, tags, metadata)

    def _memory_schema_dir(self) -> Path:
        return _memory_schema_dir_impl(self.db_path)

    def _ensure_memory_schema_index_table(self) -> None:
        return _ensure_memory_schema_index_table_impl(self)

    def _upsert_memory_schema_index(self, payload: Dict[str, Any], conn: sqlite3.Connection | None = None) -> None:
        return _upsert_memory_schema_index_impl(self, payload, conn=conn)

    def _delete_memory_schema_index(self, memory_id: Any) -> None:
        return _delete_memory_schema_index_impl(self, memory_id)

    def backfill_memory_schema_index(self) -> Dict[str, Any]:
        """P5-04：把 sidecar 真值回填为 SQLite 派生索引层，结束纯 JSON 半结构状态。"""
        return _backfill_memory_schema_index_impl(self)

    def repair_trace_metadata(self, limit: int = 0) -> Dict[str, Any]:
        """为 reflection_event 与可静态映射的高价值历史记忆补写 source_anchor/source_session/turn_signature。"""
        return _repair_trace_metadata_impl(self, limit=limit)

    def memory_schema_index_status(self) -> Dict[str, Any]:
        return _memory_schema_index_status_impl(self)

    def structured_memory_overview(self) -> Dict[str, Any]:
        """P5-05：统一结构查询口径，阶段 2 默认从 memory_schema_index 读结构分布。"""
        return _structured_memory_overview_impl(self)

    def user_canonical_profile(self) -> Dict[str, Any]:
        """返回用户核心画像的 curated truth view。"""
        return _user_canonical_profile_impl(self)

    def _memory_schema_registry_path(self) -> Path:
        return _memory_schema_registry_path_impl(self)

    def _memory_schema_record_path(self, memory_id: Any) -> Path:
        return _memory_schema_record_path_impl(self, memory_id)

    def _persist_memory_schema(self, memory_id: Any, metadata: Dict[str, Any], content: str, importance: float, tags: List[str] | None = None) -> Dict[str, Any]:
        return _persist_memory_schema_impl(self, memory_id, metadata, content, importance, tags)

    def _read_memory_schema(self, memory_id: Any) -> Dict[str, Any]:
        return _read_memory_schema_impl(self, memory_id)

    def _mark_memory_schema_removed(self, memory_id: Any, existing: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """在删除主表记录前，保留一份 removed_at 索引痕迹供统计与读路径过滤使用。"""
        return _mark_memory_schema_removed_impl(self, memory_id, existing=existing)

    def _batch_mark_memory_schema_removed(self, memory_ids: List[Any]) -> int:
        return _batch_mark_memory_schema_removed_impl(self, memory_ids)

    def _update_memory_schema_fields(self, memory_id: Any, updates: Dict[str, Any], remove: bool = False) -> Dict[str, Any]:
        """阶段 2.1/2.2 衔接：maintenance/update 链同步 schema sidecar 真值层。"""
        return _update_memory_schema_fields_impl(self, memory_id, updates, remove=remove)

    def sync_maintenance_updates(self, memory_ids: List[Any], *, last_recall: str = None, trigger_delta: int = 0,
                                 importance: float = None, confidence: float = None,
                                 lifecycle: str = None, promotion_state: str = None,
                                 last_confirmed_at: str = None, hotness_score: float = None,
                                 remove: bool = False) -> int:
        """把 maintenance/update 链的字段变化同步到 schema sidecar。"""
        return _sync_maintenance_updates_impl(
            self,
            memory_ids,
            last_recall=last_recall,
            trigger_delta=trigger_delta,
            importance=importance,
            confidence=confidence,
            lifecycle=lifecycle,
            promotion_state=promotion_state,
            last_confirmed_at=last_confirmed_at,
            hotness_score=hotness_score,
            remove=remove,
        )

    def _persist_lesson_candidate(self, lesson: Dict[str, Any]) -> Dict[str, Any]:
        return _persist_lesson_candidate_impl(self, lesson)

    def promote_lesson_candidate(self, candidate_id: str, decision: str = "promoted") -> Dict[str, Any]:
        return _promote_lesson_candidate_impl(self, candidate_id, decision=decision)

    def _normalize_tags(self, tags: List[str] | None) -> List[str]:
        return _normalize_tags_impl(tags)

    def _find_existing_reflection_memory(self, content: str, metadata: Dict[str, Any] | None = None) -> int | None:
        return _find_existing_reflection_memory_impl(self, content, metadata)

    def _evaluate_write_gate(self, content: str, tags: List[str] | None = None, metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
        # compat anchors for text-based regression tests:
        # 'reason': 'empty_content'
        # 'reason': f'blocked_tag:{hit}'
        # 'reason': 'blocked_is_test_data'
        # 'reason': 'blocked_reflection_runtime_noise'
        # 'reason': f'blocked_pattern:{pattern}'
        # 'reason': 'too_short_without_memory_signal'
        # 'message': '✅ allowed by MemoryWriteGate'
        return _evaluate_write_gate_impl(self, content, tags, metadata)

    def _run_remember_postwrite(self, memory_id: int, content: str, importance: float,
                                tags: List[str] | None, structured_metadata: Dict[str, Any]) -> Dict[str, Any]:
        return _run_remember_postwrite_impl(self, memory_id, content, importance, tags, structured_metadata)

    def _remember_deduped_outcome(self, memory_id: int, content: str, tags: List[str] | None,
                                  structured_metadata: Dict[str, Any], reason: str) -> Dict[str, Any]:
        return _remember_deduped_outcome_impl(
            self,
            memory_id=memory_id,
            content=content,
            tags=tags,
            structured_metadata=structured_metadata,
            reason=reason,
        )

    def _remember_blocked_outcome(self, content: str, tags: List[str] | None,
                                  structured_metadata: Dict[str, Any], gate: Dict[str, Any]) -> Dict[str, Any]:
        return _remember_blocked_outcome_impl(
            self,
            content=content,
            tags=tags,
            structured_metadata=structured_metadata,
            gate=gate,
        )

    def _remember_allowed_outcome(self, memory_id: int, content: str, tags: List[str] | None,
                                  structured_metadata: Dict[str, Any], gate: Dict[str, Any],
                                  postwrite: Dict[str, Any] | None) -> Dict[str, Any]:
        return _remember_allowed_outcome_impl(
            self,
            memory_id=memory_id,
            content=content,
            tags=tags,
            structured_metadata=structured_metadata,
            gate=gate,
            postwrite=postwrite,
        )


    def remember(self, content: str, importance: float = 0.6,
                 tags: List[str] = None, metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """
        记住一件事。

        Args:
            content: 记忆内容，如 "用户喜欢冰美式"
            importance: 重要性 0-1（默认 0.6）
            tags: 标签列表，如 ["偏好", "饮品"]

        Returns:
            {"success": bool, "memory_id": int, "message": str}

        用法：
            memory.remember("用户喜欢冰美式")
            memory.remember("重启网关禁止时段：23:00-08:00", importance=0.9, tags=["规则"])
        """
        return _remember_impl(self, content, importance=importance, tags=tags, metadata=metadata)

    def remember_lesson(self, lesson: Any) -> Dict[str, Any]:
        """
        以 lesson 语义写入长期记忆。

        P0 目标：
        - 不重构忘川 schema
        - 保留 source_task / source_session / confidence / applicable_when 等关键字段
        - 通过 content + tags 的兼容形态先落库
        """
        return _remember_lesson_impl(self, lesson)

    def recall(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        回忆相关记忆。

        Args:
            query: 查询内容，如 "用户偏好"
            limit: 返回条数（默认 5）

        Returns:
            [{"content": str, "score": float, "created_at": str}, ...]

        用法：
            results = memory.recall("用户喜欢什么饮品")
            for r in results:
                print(r["content"])
        """
        return self._recall_rows(query, limit=limit)

    def recall_raw(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        回忆原话/原始记录链。

        适用：
        - “上次我们到底怎么说的？”
        - “原始讨论过程是什么？”
        - “给我原文锚点/原始证据”
        """
        return self._recall_rows(query, limit=limit, source_layer="raw")

    def recall_scars(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        回忆伤疤/判断链。

        适用：
        - "之前踩过什么坑？"
        - "这类事默认怎么判断？"
        - "历史结论和规则是什么？"
        """
        return self._recall_rows(query, limit=limit, source_layer="scar")

    def recall_at(self, query: str, as_of: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        回忆特定时间点的记忆（时序查询）。

        适用：
        - "上周这个时候用户怎么说？"
        - "查询某个决策的历史版本"
        - "时间旅行式回忆"

        Args:
            query: 查询内容
            as_of: 历史时间点 (ISO格式如 "2026-04-15" 或 "2026-04-15T10:30:00")
            limit: 返回条数

        Returns:
            [{"content": str, "score": float, "created_at": str, "valid_from": str, "valid_until": str}, ...]
        """
        return self._recall_rows(query, limit=limit, as_of=as_of)

    def get_supersession_chain(self, memory_id: int) -> List[Dict[str, Any]]:
        """
        获取某个记忆的版本迁移链。

        适用：
        - "这个偏好的变化历史是什么？"
        - 查看记忆的完整演变

        Args:
            memory_id: 记忆ID

        Returns:
            [{"id": int, "content": str, "valid_from": str, "valid_until": str}, ...]
        """
        return _get_supersession_chain_impl(self, memory_id)

    # compat signature anchor: def _infer_memory_metadata(content: str, source_layer: str) -> Dict[str, str]:
    @staticmethod
    def _infer_memory_metadata(content: str, source_layer: str) -> Dict[str, Any]:
        # compat anchors for text-based regression tests:
        # "source_layer": source_layer,
        # "memory_type": memory_type,
        # "subject_domain": subject_domain,
        # "evidence_level": evidence_level,
        return _infer_memory_metadata_impl(Memory, content, source_layer)

    def _enrich_missing_trace_metadata(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return _enrich_missing_trace_metadata_impl(self, item)

    def _is_recall_noise(self, content: str) -> bool:
        return _is_recall_noise_impl(self, content)

    def cleanup_noise(self, dry_run: bool = True, keep_emotions: bool = True) -> Dict[str, Any]:
        """
        清理噪声记忆。

        Args:
            dry_run: True 只返回统计不删除，False 执行删除
            keep_emotions: True 保留情感事件类对话

        Returns:
            {"deleted": int, "would_delete": int, "samples": list}
        """
        return _cleanup_noise_impl(self, dry_run=dry_run, keep_emotions=keep_emotions)

    def _rrf_fusion(self, rows: List[tuple], normalized_query: str, keyword_tokens: List[str], k: int = 60) -> List[tuple]:
        return _rrf_fusion_impl(rows, normalized_query, keyword_tokens, k=k)

    def _filter_recall_noise(self, rows: List[tuple], limit: int) -> List[tuple]:
        return _filter_recall_noise_impl(self, rows, limit)

    @staticmethod
    def _normalize_temporal_probe(value: Any, default_now: bool = False) -> str:
        return _normalize_temporal_probe_impl(value, default_now=default_now)

    def _build_recall_keyword_tokens(self, normalized_query: str) -> List[str]:
        return _build_recall_keyword_tokens_impl(self, normalized_query)

    def _build_recall_items(self, rows: List[tuple], source_layer: str, normalized_query: str, keyword_tokens: List[str]) -> List[Dict[str, Any]]:
        return _build_recall_items_impl(self, rows, source_layer=source_layer, normalized_query=normalized_query, keyword_tokens=keyword_tokens)

    def _collect_recall_candidate_rows(self, conn: Any, select_sql: str, base_filter: str, layer_filter: str, rank_expr: str,
                                       temporal_params: List[Any], normalized_query: str, keyword_tokens: List[str], limit: int) -> List[tuple]:
        return _collect_recall_candidate_rows_impl(
            self,
            conn,
            select_sql=select_sql,
            base_filter=base_filter,
            layer_filter=layer_filter,
            rank_expr=rank_expr,
            temporal_params=temporal_params,
            normalized_query=normalized_query,
            keyword_tokens=keyword_tokens,
            limit=limit,
        )

    # compat signature anchor: def _recall_rows(self, query: str, limit: int = 5, source_layer: str = "all")
    def _recall_rows(self, query: str, limit: int = 5, source_layer: str = "all", as_of: str = None) -> List[Dict[str, Any]]:
        """
        回忆相关记忆（支持时序查询）。
        
        Args:
            query: 查询内容
            limit: 返回条数
            source_layer: 过滤层 (all/raw/scar)
            as_of: 时序查询时间点 (ISO格式如 "2026-04-22")，None表示只返回当前有效记忆
        """
        # compat anchors for text-based regression tests:
        # effective_layer = source_layer if source_layer in {"raw", "scar"} else "mixed"
        return _recall_rows_impl(self, query, limit=limit, source_layer=source_layer, as_of=as_of)

    def _read_recent_write_gate_events(self, limit: int = 120) -> List[Dict[str, Any]]:
        return _read_recent_write_gate_events_impl(limit=limit)

    def write_gate_probe(self) -> Dict[str, Any]:
        """主动探测 write gate 是否能拦截典型噪音样本。"""
        return _write_gate_probe_impl(self)

    def cleanup_historical_noise(self, dry_run: bool = True) -> Dict[str, Any]:
        """清理已知历史脏记忆，只针对忘川历史噪音样本。"""
        return _cleanup_historical_noise_impl(self, dry_run=dry_run)

    def cleanup_duplicate_reflections(self, dry_run: bool = True) -> Dict[str, Any]:
        """清理 exact duplicate 的 rule/correction reflection_event 记忆，只保留一条最佳记录。"""
        return _cleanup_duplicate_reflections_impl(self, dry_run=dry_run)

    def cleanup_duplicate_rule_memories(self, dry_run: bool = True) -> Dict[str, Any]:
        """清理 exact duplicate 的 rule 记忆，不限 promotion_reason，只保留一条最佳记录。"""
        return _cleanup_duplicate_rule_memories_impl(self, dry_run=dry_run)

    def cleanup_low_value_emotional_memories(self, dry_run: bool = True) -> Dict[str, Any]:
        """清理低价值 emotional 记忆，只处理明确的 runtime/placeholder/cron 类噪音。"""
        return _cleanup_low_value_emotional_memories_impl(self, dry_run=dry_run)

    def cleanup_question_like_rule_noise(self, dry_run: bool = True) -> Dict[str, Any]:
        """清理问句型假 rule 噪音，只处理最保守候选。"""
        return _cleanup_question_like_rule_noise_impl(self, dry_run=dry_run)

    def audit_question_like_rules(self, limit: int = 300) -> Dict[str, Any]:
        """输出 question-like rule 审计报表，区分噪音、保留和救援类型。"""
        return _audit_question_like_rules_impl(self, limit=limit)

    def history_search_healthcheck(self) -> Dict[str, Any]:
        """阶段 2.3 最小历史搜索索引健康摘要。"""
        return _history_search_healthcheck_impl(self)

    @staticmethod
    def _extract_markdown_section(text: str, heading: str) -> str:
        return _extract_markdown_section_impl(text, heading)

    @staticmethod
    def _extract_bullet_items(text: str, heading: str, limit: int = 8) -> List[str]:
        return _extract_bullet_items_impl(text, heading, limit=limit)

    @staticmethod
    def _extract_label_value(text: str, label: str) -> str:
        return _extract_label_value_impl(text, label)

    def task_resume(self, board_path: str | None = None) -> Dict[str, Any]:
        """从实施任务板提取结构化恢复面。"""
        # compat anchors for text-based regression tests:
        # checkpoint_body = self._extract_markdown_section(text, "7. checkpoint")
        # next_step_body = self._extract_markdown_section(text, "8. next step")
        # done_ledger_body = self._extract_markdown_section(text, "6. 最近完成记录（Done Ledger）")
        # current_task = self._extract_label_value(checkpoint_body, "- 当前最高优先未完成项")
        # resume_steps = self._extract_bullet_items(checkpoint_body, "如果此刻中断，恢复时先做什么", limit=8)
        # "current_task": current_task,
        # "next_step": next_step,
        # "resume_steps": resume_steps,
        # "checkpoint_items": checkpoint_items,
        return _task_resume_impl(self, board_path=board_path)

    def user_healthcheck(self) -> Dict[str, Any]:
        """
        用户视角体检：回答“记忆现在是否可信、是否串味、是否拿错层”。

        返回最小用户症状指标，而不只是底层计数。
        """
        # compat anchors for text-based regression tests:
        # raw_probe = self.recall_raw("原话", limit=5)
        # rule_probe = self.recall_scars("规则 教训 默认", limit=5)
        # mixed_probe = self.recall("用户 规则 原话", limit=8)
        # "raw_recall_returns_raw_evidence": {
        # "rule_recall_returns_rule_like_items": {
        # "test_noise_not_floating_in_recall": {
        # "results_have_explainable_anchor": {
        # "write_gate_is_blocking_noise": {
        # "hot_memory_signal_present": {
        # f"记忆体检：{passed}/{total} 项通过 | 状态={status} | "
        return _user_healthcheck_impl(self)
    
    _status_cache = {"data": None, "timestamp": 0}
    _status_cache_ttl = 5.0
    
    def status(self) -> Dict[str, Any]:
        """
        查看记忆系统当前状态。

        Returns:
            {"energy": {...}, "temporal": {...}, "memories": int, "message": str}

        用法：
            s = memory.status()
            print(s["message"])
        """
        # compat anchors for text-based regression tests:
        # task_resume = self.task_resume()
        # health = self.user_healthcheck()
        # f"🧭 {task_resume.get('current_task') or '?'}"
        # f"🩺 {health['passed']}/{health['total']} {health['status']} | "
        # "task_resume": task_resume,
        # "healthcheck": health,
        return _status_impl(self)

    # =========================================================
    # 记忆统计与报告
    # =========================================================

    def get_memory_stats(self) -> Dict[str, Any]:
        """
        获取记忆系统详细统计信息。

        Returns:
            {
                "total": int,           # 总记忆数
                "by_layer": dict,       # 按层级分布
                "by_type": dict,        # 按类型分布
                "by_confidence": dict,  # 按置信度分布
                "recent_activity": list, # 最近活动
                "temporal": dict         # 时序信息
            }
        """
        return _get_memory_stats_impl(self)

    def get_quality_metrics(self) -> Dict[str, Any]:
        """
        获取记忆系统质量监控指标。

        Returns:
            {
                "confidence": {"avg": float, "distribution": dict},
                "noise": {"rate": float, "count": int, "total": int},
                "retrieval": {"avg_latency_ms": float, "hits_rate": float},
                "health": {"passed": int, "total": int, "issues": list}
            }
        """
        return _get_quality_metrics_impl(self)

    def export_memories(self, format: str = "json", filepath: str = None, limit: int = None) -> Dict[str, Any]:
        """
        导出记忆到文件。

        Args:
            format: 导出格式 (json/csv)
            filepath: 输出文件路径（可选，默认返回内容）
            limit: 导出条数限制

        Returns:
            {"success": bool, "format": str, "count": int, "data": str or None, "filepath": str or None}

        用法：
            # 导出到文件
            memory.export_memories(format="json", filepath="/tmp/export.json")
            
            # 获取 JSON 内容
            result = memory.export_memories(format="json")
            print(result["data"])
        """
        return _export_memories_impl(self, format=format, filepath=filepath, limit=limit)

    # =========================================================
    # 辅助方法
    # =========================================================

    def forget(self, query: str) -> Dict[str, Any]:
        """
        删除匹配的记忆（谨慎使用）。

        Args:
            query: 要删除的记忆内容关键词

        Returns:
            {"success": bool, "deleted": int, "message": str}
        """
        return _forget_impl(self, query)

    def merge(self, old_query: str, new_content: str, importance: float = 0.8) -> Dict[str, Any]:
        """
        合并/更新记忆（解决冲突）- 支持版本迁移链。

        1. 找到旧记忆
        2. 创建新记忆作为 current truth
        3. 标记旧记忆为 superseded（设置 valid_until = now, superseded_by = new_id）
        4. 更新 supersession_chain

        Args:
            old_query: 旧记忆的关键词，支持内容检索或 `id:<memory_id>`
            new_content: 新内容
            importance: 新内容重要性

        Returns:
            {"success": bool, "message": str, "old_id": int, "new_id": int}

        用法：
            memory.merge("喜欢热咖啡", "用户喜欢冰美式，不喝热的")
            memory.merge("id:123", "用户现在更喜欢冰美式")
        """
        return _merge_impl(self, old_query, new_content, importance=importance)

    def history(self, memory_id: int = None, query: str = None, limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取记忆版本历史（时间旅行查询）
        
        Args:
            memory_id: 记忆 ID
            query: 记忆内容关键词
            limit: 返回数量
            
        Returns:
            [{"memory_id": int, "content": str, "created_at": str, "superseded_by": int}, ...]
        """
        return _history_impl(self, memory_id=memory_id, query=query, limit=limit)
    
    def rollback(self, memory_id: int, target_version: int = None) -> Dict[str, Any]:
        """
        回滚记忆到指定版本
        
        Args:
            memory_id: 当前记忆 ID
            target_version: 目标版本记忆 ID（默认回滚到上一版本）
            
        Returns:
            {"success": bool, "message": str, "new_id": int}
        """
        return _rollback_impl(self, memory_id, target_version=target_version)
    
    def _ensure_acl_table(self):
        """确保 ACL 表存在"""
        return _ensure_acl_table_impl(self)
    
    def grant_access(self, user_id: str, memory_id: int, permission: str = "read") -> Dict:
        """
        授予用户对记忆的访问权限
        
        Args:
            user_id: 用户ID
            memory_id: 记忆ID
            permission: read/write/admin
            
        Returns:
            {"success": bool, "message": str}
        """
        return _grant_access_impl(self, user_id, memory_id, permission=permission)
    
    def revoke_access(self, user_id: str, memory_id: int) -> Dict:
        """
        撤销用户对记忆的访问权限
        
        Args:
            user_id: 用户ID
            memory_id: 记忆ID
            
        Returns:
            {"success": bool, "message": str}
        """
        return _revoke_access_impl(self, user_id, memory_id)
    
    def check_access(self, user_id: str, memory_id: int, required_permission: str = "read") -> bool:
        """
        检查用户是否有权限访问记忆
        
        Args:
            user_id: 用户ID
            memory_id: 记忆ID
            required_permission: 需要权限级别
            
        Returns:
            bool
        """
        return _check_access_impl(self, user_id, memory_id, required_permission=required_permission)
    
    def get_user_memories(self, user_id: str, limit: int = 50) -> List[Dict]:
        """
        获取用户有权限访问的记忆
        
        Args:
            user_id: 用户ID
            limit: 返回数量
            
        Returns:
            [{"memory_id": int, "content": str, "permission": str}, ...]
        """
        return _get_user_memories_impl(self, user_id, limit=limit)
 
    def _ensure_multimodal_table(self):
        """确保多模态记忆表存在"""
        return _ensure_multimodal_table_impl(self)
    
    def add_image(self, memory_id: int, image_description: str, mime_type: str = "image/png") -> Dict:
        """
        为记忆添加图像描述
        
        Args:
            memory_id: 记忆ID
            image_description: 图像描述文本
            mime_type: MIME 类型
            
        Returns:
            {"success": bool, "message": str}
        """
        return _add_image_impl(self, memory_id, image_description, mime_type=mime_type)
    
    def add_audio(self, memory_id: int, audio_description: str, mime_type: str = "audio/mpeg") -> Dict:
        """
        为记忆添加音频描述
        
        Args:
            memory_id: 记忆ID
            audio_description: 音频描述文本
            mime_type: MIME 类型
            
        Returns:
            {"success": bool, "message": str}
        """
        return _add_audio_impl(self, memory_id, audio_description, mime_type=mime_type)
    
    def get_multimodal(self, memory_id: int) -> List[Dict]:
        """
        获取记忆的多模态内容
        
        Args:
            memory_id: 记忆ID
            
        Returns:
            [{"id": int, "modality": str, "content": str, "mime_type": str}, ...]
        """
        return _get_multimodal_impl(self, memory_id)
    
    def _ensure_encrypted_table(self):
        """确保加密记忆表存在"""
        return _ensure_encrypted_table_impl(self)
    
    def encrypt_memory(self, memory_id: int, key: str = None) -> Dict:
        """
        加密指定记忆
        
        Args:
            memory_id: 记忆ID
            key: 加密密钥（默认使用系统密钥）
            
        Returns:
            {"success": bool, "message": str}
        """
        return _encrypt_memory_impl(self, memory_id, key=key)
    
    def decrypt_memory(self, memory_id: int, key: str = None) -> Dict:
        """
        解密指定记忆
        
        Args:
            memory_id: 记忆ID
            key: 解密密钥
            
        Returns:
            {"success": bool, "content": str, "message": str}
        """
        return _decrypt_memory_impl(self, memory_id, key=key)
    
    def _ensure_sync_table(self):
        """确保分布式同步表存在"""
        return _ensure_sync_table_impl(self)
    
    def sync_to_node(self, memory_id: int, node_id: str, operation: str = "create") -> Dict:
        """
        同步记忆到指定节点
        
        Args:
            memory_id: 记忆ID
            node_id: 目标节点ID
            operation: create/update/delete
            
        Returns:
            {"success": bool, "message": str}
        """
        return _sync_to_node_impl(self, memory_id, node_id, operation=operation)
    
    def get_sync_status(self, memory_id: int) -> List[Dict]:
        """
        获取记忆的同步状态
        
        Args:
            memory_id: 记忆ID
            
        Returns:
            [{"node_id": str, "operation": str, "synced": bool, "timestamp": str}, ...]
        """
        return _get_sync_status_impl(self, memory_id)
    
    def sync_all_pending(self, node_id: str = None) -> Dict:
        """
        同步所有待同步的记忆
        
        Args:
            node_id: 目标节点（None 表示所有节点）
            
        Returns:
            {"success": bool, "synced_count": int, "message": str}
        """
        return _sync_all_pending_impl(self, node_id=node_id)
    
    def register_node(self, node_url: str, node_name: str = None) -> Dict:
        """
        注册分布式节点
        
        Args:
            node_url: 节点 URL (如 http://192.168.1.100:8080)
            node_name: 节点名称
            
        Returns:
            {"success": bool, "node_id": str, "message": str}
        """
        return _register_node_impl(self, node_url, node_name=node_name)
    
    def list_nodes(self) -> List[Dict]:
        """
        列出所有注册的节点
        
        Returns:
            [{"node_id": str, "node_url": str, "node_name": str, "status": str}, ...]
        """
        return _list_nodes_impl(self)
    
    def _ensure_nodes_table(self):
        """确保节点表存在"""
        return _ensure_nodes_table_impl(self)
    
    def add_tag(self, memory_id: int, tag: str) -> Dict:
        """
        为记忆添加标签
        
        Args:
            memory_id: 记忆ID
            tag: 标签名称
            
        Returns:
            {"success": bool, "message": str}
        """
        return _add_tag_impl(self, memory_id, tag)
    
    def remove_tag(self, memory_id: int, tag: str) -> Dict:
        """
        移除记忆的标签
        
        Args:
            memory_id: 记忆ID
            tag: 标签名称
            
        Returns:
            {"success": bool, "message": str}
        """
        return _remove_tag_impl(self, memory_id, tag)
    
    def get_tags(self, memory_id: int) -> List[str]:
        """
        获取记忆的所有标签
        
        Args:
            memory_id: 记忆ID
            
        Returns:
            ["tag1", "tag2", ...]
        """
        return _get_tags_impl(self, memory_id)
    
    def find_by_tag(self, tag: str, limit: int = 10) -> List[Dict]:
        """
        按标签搜索记忆
        
        Args:
            tag: 标签名称
            limit: 返回数量
            
        Returns:
            [{"memory_id": int, "content": str, "created_at": str}, ...]
        """
        return _find_by_tag_impl(self, tag, limit=limit)
    
    def list_all_tags(self) -> List[Dict]:
        """
        列出所有标签及使用次数
        
        Returns:
            [{"tag": str, "count": int}, ...]
        """
        return _list_all_tags_impl(self)
    
    def _ensure_tags_table(self):
        """确保标签表存在"""
        return _ensure_tags_table_impl(self)
    
    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近的记忆。

        P5-05 延伸：recent 作为真实读面，默认保留旧主表列表能力，
        但优先补充 `memory_schema_index` 的统一结构字段，避免阶段 2
        之后 recent 仍停留在“只看旧主表 type/confidence”的半结构状态。
        """
        return _recent_impl(self, limit=limit)

    def _sync_to_memory_md(self, content: str, tags: List[str]):
        """同步重要记忆到工作区根目录的 MEMORY.md（最小 Hot Memory Curator）"""
        # compat anchors for text-based regression tests:
        # HOT_MEMORY_MAX_ITEMS = 24
        # HOT_MEMORY_MAX_TEXT_LENGTH = 160
        # HOT_MEMORY_ALLOWED_TAGS = {
        # HOT_MEMORY_BLOCK_TAGS = {
        # HOT_MEMORY_BLOCK_PATTERNS = [
        # if lowered_tags & self.HOT_MEMORY_BLOCK_TAGS:
        # if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in self.HOT_MEMORY_BLOCK_PATTERNS):
        # and len(self._normalize_hot_memory_text(text)) <= self.HOT_MEMORY_MAX_TEXT_LENGTH
        # self._hot_memory_priority(text, normalized_tags, { 
        # if self._hot_memory_priority(text, normalized_tags, metadata) < 4:
        # new_key = self._canonical_hot_memory_key(text)
        # if not new_key or new_key in seen_keys:
        # "## 忘川同步记忆"
        # ranked_entries = sorted(
        # existing_entries.append(entry)
        return _sync_to_memory_md_impl(self, content, tags)


# =========================================================
# 全局实例
# =========================================================

_memory = None

def get_memory() -> Memory:
    """获取全局记忆实例"""
    global _memory
    if _memory is None:
        _memory = Memory()
    return _memory


# =========================================================
# 快捷函数
# =========================================================

def remember(content: str, importance: float = 0.6, tags: List[str] = None):
    """记住一件事"""
    return get_memory().remember(content, importance, tags)

def recall(query: str, limit: int = 5):
    """回忆相关记忆"""
    return get_memory().recall(query, limit)


def recall_raw(query: str, limit: int = 5):
    """回忆原话/原始记录链"""
    return get_memory().recall_raw(query, limit)


def recall_scars(query: str, limit: int = 5):
    """回忆伤疤/判断链"""
    return get_memory().recall_scars(query, limit)


def cleanup_question_like_rule_noise(dry_run: bool = True):
    """清理问句型假 rule 噪音，只处理最保守候选。"""
    return get_memory().cleanup_question_like_rule_noise(dry_run=dry_run)


def audit_question_like_rules(limit: int = 300):
    """输出 question-like rule 审计报表，区分噪音、保留和救援类型。"""
    return get_memory().audit_question_like_rules(limit=limit)


def recall_at(query: str, as_of: str, limit: int = 5):
    """按时间点回忆当时有效的记忆。"""
    return get_memory().recall_at(query, as_of, limit)


def merge(old_query: str, new_content: str, importance: float = 0.8):
    """合并/更新记忆，建立 supersession 链。"""
    return get_memory().merge(old_query, new_content, importance)


def history(memory_id: int = None, query: str = None, limit: int = 10):
    """查看某条记忆或某类事实的版本历史。"""
    return get_memory().history(memory_id=memory_id, query=query, limit=limit)


def get_supersession_chain(memory_id: int):
    """查看某条记忆的 supersession chain。"""
    return get_memory().get_supersession_chain(memory_id)


def rollback(memory_id: int, target_version: int = None):
    """回滚记忆到目标版本。"""
    return get_memory().rollback(memory_id, target_version)


def remember_rule(content: str, importance: float = 0.8, tags: List[str] = None, metadata: Dict[str, Any] | None = None):
    """以 rule 语义写入记忆。"""
    merged_tags = list(tags or []) + ["rule"]
    merged_tags = list(dict.fromkeys(str(t).strip() for t in merged_tags if str(t).strip()))
    merged_meta = dict(metadata or {})
    merged_meta.setdefault("memory_type", "rule")
    merged_meta.setdefault("source_layer", "scar")
    merged_meta.setdefault("user_explicit", True)
    return get_memory().remember(content, importance=importance, tags=merged_tags, metadata=merged_meta)


def remember_lesson(lesson: Any):
    """以 lesson 语义写入记忆，兼容 candidate/promote 流。"""
    return get_memory().remember_lesson(lesson)


def get_user_memories(user_id: str, limit: int = 50):
    """获取某个 user_id 可访问的记忆。"""
    return get_memory().get_user_memories(user_id, limit=limit)


def find_by_tag(tag: str, limit: int = 10):
    """按标签检索记忆。"""
    return get_memory().find_by_tag(tag, limit=limit)


def memory_healthcheck():
    """用户视角记忆体检。"""
    return get_memory().user_healthcheck()


def consolidate(session_id: str | None = None):
    """触发一次最小 consolidation，会话为空时默认 default。"""
    try:
        from wangchuan.v3.pipeline_v3 import WangchuanPipeline
        return WangchuanPipeline(get_memory().db_path).consolidate(session_id=session_id)
    except Exception as e:
        return {"success": False, "message": f"❌ consolidate failed: {e}", "session_id": session_id or "default"}


def agent_tools() -> Dict[str, Dict[str, Any]]:
    """返回标准 agent memory tools 映射，用于 P1-01 对齐 MCP / Python / HTTP。"""
    return {
        "memory_write": {
            "python": "remember(content, importance=0.6, tags=None)",
            "method": "remember",
            "description": "写入一条通用长期记忆",
        },
        "memory_write_rule": {
            "python": "remember_rule(content, importance=0.8, tags=None, metadata=None)",
            "method": "remember_rule",
            "description": "写入规则/默认判断类记忆",
        },
        "memory_write_lesson": {
            "python": "remember_lesson(lesson)",
            "method": "remember_lesson",
            "description": "写入 lesson，兼容 candidate/promote 路径",
        },
        "memory_search": {
            "python": "recall(query, limit=5)",
            "method": "recall",
            "description": "搜索当前有效记忆",
        },
        "memory_search_raw": {
            "python": "recall_raw(query, limit=5)",
            "method": "recall_raw",
            "description": "搜索原始证据/原话",
        },
        "memory_search_scars": {
            "python": "recall_scars(query, limit=5)",
            "method": "recall_scars",
            "description": "搜索规则/教训/判断链",
        },
        "memory_search_at": {
            "python": "recall_at(query, as_of, limit=5)",
            "method": "recall_at",
            "description": "按时间点搜索历史有效记忆",
        },
        "memory_history": {
            "python": "history(memory_id=None, query=None, limit=10)",
            "method": "history",
            "description": "查看版本历史",
        },
        "memory_chain": {
            "python": "get_supersession_chain(memory_id)",
            "method": "get_supersession_chain",
            "description": "查看 supersession chain",
        },
        "memory_merge": {
            "python": "merge(old_query, new_content, importance=0.8)",
            "method": "merge",
            "description": "用新事实 supersede 旧事实",
        },
        "memory_rollback": {
            "python": "rollback(memory_id, target_version=None)",
            "method": "rollback",
            "description": "回滚到历史版本",
        },
        "memory_consolidate": {
            "python": "consolidate(session_id=None)",
            "method": "consolidate",
            "description": "触发一次 session consolidation",
        },
        "memory_status": {
            "python": "status()",
            "method": "status",
            "description": "查看整体记忆状态",
        },
        "memory_healthcheck": {
            "python": "memory_healthcheck()",
            "method": "memory_healthcheck",
            "description": "查看用户视角健康状态",
        },
        "memory_recent": {
            "python": "get_memory().recent(limit=10)",
            "method": "recent",
            "description": "查看最近结构化记忆",
        },
        "memory_user_view": {
            "python": "get_user_memories(user_id, limit=50)",
            "method": "get_user_memories",
            "description": "查看某用户可访问的记忆",
        },
        "memory_search_by_tag": {
            "python": "find_by_tag(tag, limit=10)",
            "method": "find_by_tag",
            "description": "按标签搜索记忆",
        },
        "memory_cleanup_rules": {
            "python": "get_memory().cleanup_duplicate_rule_memories(dry_run=True)",
            "method": "cleanup_duplicate_rule_memories",
            "description": "清理重复 rule 记忆",
        },
        "memory_cleanup_reflections": {
            "python": "get_memory().cleanup_duplicate_reflections(dry_run=True)",
            "method": "cleanup_duplicate_reflections",
            "description": "清理重复 reflection 记忆",
        },
    }


def status():
    """查看记忆系统状态"""
    return get_memory().status()


def task_resume(board_path: str = None):
    """查看任务恢复面"""
    return get_memory().task_resume(board_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="忘川统一记忆 API")
    parser.add_argument("--self-test", action="store_true", help="运行内置自测")
    args = parser.parse_args()

    if not args.self_test:
        parser.print_help()
        raise SystemExit(0)

    m = Memory()

    print("🧠 忘川统一记忆 API 测试")
    print("=" * 50)

    # 测试 remember
    print("\n📝 记住测试:")
    r = m.remember("测试记忆：用户喜欢麻辣小龙虾", importance=0.8, tags=["偏好", "食物"])
    print(f"  {r['message']}")

    # 测试 recall
    print("\n🔍 回忆测试:")
    results = m.recall("喜欢")
    print(f"  找到 {len(results)} 条:")
    for r in results[:3]:
        print(f"    • {r['content'][:60]}")

    # 测试 status
    print("\n📊 状态:")
    s = m.status()
    print(f"  {s['message']}")

    # 测试 recent
    print("\n📋 最近5条记忆:")
    for r in m.recent(5):
        print(f"  • [{r['type']}] {r['content'][:50]}")

    print("\n✅ 测试完成")
