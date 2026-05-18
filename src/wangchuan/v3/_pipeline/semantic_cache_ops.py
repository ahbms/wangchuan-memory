"""
语义缓存操作模块 — 从 recall() 中提取的缓存状态构建、查找、命中处理、回写逻辑

依赖 RecallSemanticCache（tiangong.context.semantic_cache）。
"""

import sqlite3
import time
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SemanticCacheOps:
    """封装 recall 语义缓存的完整生命周期操作"""

    def __init__(self, semantic_cache, assemble_engine, db_path: str):
        """
        Args:
            semantic_cache: RecallSemanticCache 实例
            assemble_engine: AssembleEngine 实例（用于加载 session state）
            db_path: 数据库路径
        """
        self._cache = semantic_cache
        self._assemble_engine = assemble_engine
        self._db_path = db_path

    def build_cache_state(self, session_id: str) -> Dict[str, Any]:
        """构建缓存状态指纹

        覆盖 summary、task checkpoint、explicit feedback 状态，
        用于语义缓存的状态指纹计算。
        """
        state = {
            'last_compacted_message_id': '',
            'task_updated_at': '',
            'summary_updated_at': '',
            'explicit_feedback_state': {'max_feedback_id': 0, 'max_created_at': ''},
        }
        if not session_id:
            return state
        try:
            summary_state = self._assemble_engine.state_store.load_session_summary(session_id)
            checkpoint_state = self._assemble_engine.state_store.load_task_checkpoint(session_id)
            explicit_feedback_state = {'max_feedback_id': 0, 'max_created_at': ''}
            with sqlite3.connect(self._db_path) as conn:
                feedback_row = conn.execute(
                    """
                    SELECT COALESCE(MAX(id), 0), COALESCE(MAX(created_at), '')
                    FROM gm_feedback
                    WHERE session_id = ?
                      AND feedback_type IN ('explicit_pos', 'explicit_neg', 'follow_up')
                    """,
                    (session_id,),
                ).fetchone()
                if feedback_row:
                    explicit_feedback_state = {
                        'max_feedback_id': int(feedback_row[0] or 0),
                        'max_created_at': str(feedback_row[1] or ''),
                    }
            state = {
                'last_compacted_message_id': str(summary_state.get('last_compacted_message_id') or ''),
                'summary_updated_at': str(summary_state.get('updated_at') or ''),
                'task_updated_at': str(checkpoint_state.get('updated_at') or ''),
                'explicit_feedback_state': explicit_feedback_state,
            }
        except Exception:
            pass
        return state

    def lookup(
        self,
        session_id: str,
        query: str,
        top_k: int,
        profile: Dict[str, Any],
        state: Dict[str, Any],
    ):
        """查找缓存

        Returns:
            (cache_meta, cached_recall) — cached_recall 为 None 表示 miss
        """
        return self._cache.get(
            session_id=session_id,
            query=query,
            top_k=top_k,
            profile=profile,
            state=state,
        )

    def handle_hit(
        self,
        cached_recall: Dict[str, Any],
        cache_meta: Dict[str, Any],
        session_id: str,
        query: str,
        observability,
        shape_memory_items_fn,
    ) -> Dict[str, Any]:
        """处理缓存命中：shape 输出、记录指标、返回完整 payload

        Args:
            cached_recall: 缓存中的 recall 结果
            cache_meta: 缓存 key 元数据
            session_id: 会话 ID
            query: 原始查询
            observability: ContextObservability 实例
            shape_memory_items_fn: _shape_memory_items_for_output 方法引用

        Returns:
            完整的 recall payload dict
        """
        cached_recall = dict(cached_recall)
        cached_recall['memory_items'] = shape_memory_items_fn(
            list(cached_recall.get('memory_items', []) or [])
        )
        observability.state_store.append_metric(session_id, 'semantic_cache_metrics', {
            'status': 'hit',
            'cache_key': cache_meta.get('cache_key', ''),
            'semantic_family': cache_meta.get('semantic_family', ''),
            'state_fingerprint': cache_meta.get('state_fingerprint', ''),
            'query': query[:120],
        })
        cached_nodes = list(cached_recall.get('nodes', []) or [])
        cached_metrics = observability.capture_recall_metrics(
            session_id=session_id,
            query=query,
            stable_prefix=cached_recall.get('stable_prefix', ''),
            dynamic_suffix=cached_recall.get('dynamic_suffix', ''),
            final_context=cached_recall.get('context', ''),
            extra={
                'memory_route': cached_recall.get('memory_route', 'default'),
                'scope_route': cached_recall.get('scope_route', 'memory'),
                'context_route': cached_recall.get('context_route', 'default'),
                'selected_sections': ','.join(cached_recall.get('selected_sections', []) or []),
                'memory_items': len(cached_recall.get('memory_items', []) or []),
                'history_support_items': int((cached_recall.get('history_support') or {}).get('support_items') or 0),
                'retrieved_nodes': len(cached_nodes),
                'assembled_nodes': min(len(cached_nodes), 3),
                'semantic_cache': 'hit',
                'semantic_cache_family': cache_meta.get('semantic_family', ''),
            },
        )
        cached_recall['recall_metrics'] = cached_metrics
        cached_recall['semantic_cache'] = {
            'status': 'hit',
            'cache_key': cache_meta.get('cache_key', ''),
            'semantic_family': cache_meta.get('semantic_family', ''),
            'state_fingerprint': cache_meta.get('state_fingerprint', ''),
        }
        return cached_recall

    def write_back(
        self,
        session_id: str,
        query: str,
        top_k: int,
        result_payload: Dict[str, Any],
        profile: Dict[str, Any],
        state: Dict[str, Any],
        observability,
        cache_meta: Dict[str, Any],
    ):
        """将结果写回缓存（排除 assembly 对象）"""
        cacheable_payload = dict(result_payload)
        cacheable_payload.pop('assembly', None)
        self._cache.put(
            session_id=session_id,
            query=query,
            top_k=top_k,
            value=cacheable_payload,
            profile=profile,
            state=state,
        )
        observability.state_store.append_metric(session_id, 'semantic_cache_metrics', {
            'status': 'miss',
            'cache_key': cache_meta.get('cache_key', ''),
            'semantic_family': cache_meta.get('semantic_family', ''),
            'state_fingerprint': cache_meta.get('state_fingerprint', ''),
            'query': query[:120],
        })
