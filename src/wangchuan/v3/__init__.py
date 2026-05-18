#!/usr/bin/env python3
"""
忘川 v3.0 - 图谱增强记忆系统
Knowledge Graph Enhanced Memory System

融合 graph-memory 知识图谱 + 忘川温度分层架构
"""

__version__ = "1.3"
__author__ = "虾元帅"

from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

from .config import WangchuanV3Config, get_config, set_config
from .core.ingest import IngestEngine, Message
from .core.extract import ExtractEngine, Triple
from .core.assemble import AssembleEngine, ContextAssembly
from .graph.vector import VectorEngine
from .retrieval.hybrid import HybridRetriever

class WangchuanV3:
    """忘川 v3.0 主类"""
    
    def __init__(self, config: WangchuanV3Config = None):
        self.config = config or get_config()
        
        # 核心引擎
        self.ingest = IngestEngine(self.config.db_path)
        self.extract = ExtractEngine(self.config.db_path, self.config.llm)
        self.assemble = AssembleEngine(
            self.config.db_path,
            self.config.graph.max_context_nodes,
            self.config.graph.fresh_tail_messages
        )
        
        # 向量引擎 (如果配置了Embedding)
        self.vector = None
        if self.config.is_embedding_available():
            self.vector = VectorEngine(
                self.config.db_path,
                self.config.embedding.api_key,
                self.config.embedding.base_url,
                self.config.embedding.model,
                self.config.embedding.dimensions
            )
        
        # 混合检索器
        self.retriever = HybridRetriever(
            self.config.db_path,
            self.vector,
            self.config.graph.ppr_damping
        )
    
    def remember(self, session_id: str, role: str, content: str, **kwargs):
        """
        记住一条消息
        
        Args:
            session_id: 会话ID
            role: 角色 (user/assistant/system)
            content: 内容
        """
        msg = Message(
            session_id=session_id,
            role=role,
            content=content,
            **kwargs
        )
        return self.ingest.ingest(msg)
    
    def recall(self, session_id: str, query: str = None) -> str:
        """
        回忆上下文
        
        Args:
            session_id: 会话ID
            query: 查询内容(用于PPR排序)
        
        Returns:
            格式化的上下文字符串
        """
        assembly = self.assemble.assemble(session_id, query)
        return self.assemble.format_for_prompt(assembly)
    
    def search(self, query: str, top_k: int = 10) -> List[Dict]:
        """
        混合搜索
        
        Args:
            query: 查询文本
            top_k: 返回结果数
        
        Returns:
            检索结果列表
        """
        # P5-05 延伸：v3 对外搜索主入口优先走统一结构化 recall，
        # 让调用方默认拿到 `memory_schema_index` + sidecar 真值层合并后的字段；
        # 图检索继续保留为 fallback。
        try:
            from wangchuan.memory_api import Memory

            memory = Memory(db_path=self.config.db_path)
            rows = memory.recall(query, limit=top_k)
            results = []
            for row in rows:
                results.append({
                    'memory_id': row.get('memory_id'),
                    'content': row.get('content', ''),
                    'score': row.get('score'),
                    'type': row.get('memory_type') or row.get('type') or 'unknown',
                    'memory_type': row.get('memory_type'),
                    'source_layer': row.get('source_layer'),
                    'lifecycle': row.get('lifecycle'),
                    'promotion_state': row.get('promotion_state'),
                    'recall_source_type': row.get('recall_source_type'),
                    'schema_version': row.get('schema_version'),
                    'reader': row.get('reader') or 'memory_api.recall',
                    'structured': True,
                })
            if results:
                return results
        except Exception as e:
            logger.warning("【WangChuan】[V3][Search] structured recall fallback triggered: %s", e)

        results = self.retriever.retrieve(query, top_k=top_k)
        return [
            {
                'node_id': r.node_id,
                'name': r.name,
                'type': r.node_type,
                'score': r.score,
                'sources': r.sources,
                'reader': 'hybrid_retriever_fallback',
                'structured': False,
            }
            for r in results
        ]
    
    def embed(self, text: str, entity_type: str, entity_id: str) -> Optional[Dict]:
        """
        生成向量嵌入
        
        Args:
            text: 要嵌入的文本
            entity_type: 实体类型 (message/node)
            entity_id: 实体ID
        
        Returns:
            嵌入结果或None
        """
        if not self.vector:
            logger.warning("【WangChuan】[V3][Embed] vector engine is not configured")
            return None
        
        result = self.vector.embed_text(text, entity_type, entity_id)
        if result:
            return {
                'embedding_id': result.embedding_id,
                'dimensions': result.dimensions,
                'model': result.model_name
            }
        return None
    
    def stats(self) -> dict:
        """获取统计信息"""
        import sqlite3
        
        with sqlite3.connect(self.config.db_path) as conn:
            cursor = conn.cursor()
            
            stats = {}
            
            # 各表记录数
            for table in ['gm_messages', 'gm_signals', 'gm_nodes', 'gm_edges', 'gm_communities']:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    stats[table] = cursor.fetchone()[0]
                except Exception as e:
                    logger.error(f"Init error: {e}")
                    stats[table] = 0
            
            # 节点类型分布
            try:
                cursor.execute("SELECT node_type, COUNT(*) FROM gm_nodes GROUP BY node_type")
                stats['node_types'] = {row[0]: row[1] for row in cursor.fetchall()}
            except Exception as e:
                logger.error(f"Init error: {e}")
                stats['node_types'] = {}
            
            return stats

# 便捷函数
def create_wangchuan_v3(
    llm_api_key: str = None,
    embedding_api_key: str = None,
    llm_base_url: str = "https://api.openai.com/v1",
    embedding_base_url: str = "https://api.openai.com/v1"
) -> WangchuanV3:
    """
    创建忘川v3实例
    
    Args:
        llm_api_key: LLM API密钥 (用于三元组提取)
        embedding_api_key: Embedding API密钥 (用于向量搜索)
        llm_base_url: LLM API基础URL
        embedding_base_url: Embedding API基础URL
    
    Returns:
        WangchuanV3实例
    """
    from .config import LLMConfig, EmbeddingConfig
    
    config = WangchuanV3Config()
    
    if llm_api_key:
        config.llm = LLMConfig(
            api_key=llm_api_key,
            base_url=llm_base_url
        )
    
    if embedding_api_key:
        config.embedding = EmbeddingConfig(
            api_key=embedding_api_key,
            base_url=embedding_base_url
        )
    
    return WangchuanV3(config)

__all__ = [
    'WangchuanV3',
    'WangchuanV3Config',
    'Message',
    'Triple',
    'ContextAssembly',
    'create_wangchuan_v3',
    'get_config',
    'set_config'
]
