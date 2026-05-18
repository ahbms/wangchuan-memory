#!/usr/bin/env python3
"""
忘川 v3.0 配置管理
支持 LLM API 和 Embedding API 配置
"""

import os
import json
import logging
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

from wangchuan.paths import default_db_path

logger = logging.getLogger(__name__)

@dataclass
class LLMConfig:
    """LLM 配置用于三元组提取"""
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.3
    max_tokens: int = 2000
    
    @classmethod
    def from_env(cls) -> Optional['LLMConfig']:
        """从环境变量加载"""
        api_key = os.getenv('LLM_API_KEY') or os.getenv('OPENAI_API_KEY') or os.getenv('ANTHROPIC_API_KEY')
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            base_url=os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1'),
            model=os.getenv('LLM_MODEL', 'gpt-4o-mini')
        )

@dataclass
class EmbeddingConfig:
    """Embedding 配置用于向量搜索"""
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "text-embedding-3-small"
    dimensions: int = 512
    
    @classmethod
    def from_env(cls) -> Optional['EmbeddingConfig']:
        """从环境变量加载"""
        api_key = os.getenv('EMBEDDING_API_KEY') or os.getenv('OPENAI_API_KEY')
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            base_url=os.getenv('EMBEDDING_BASE_URL', 'https://api.openai.com/v1'),
            model=os.getenv('EMBEDDING_MODEL', 'text-embedding-3-small'),
            dimensions=int(os.getenv('EMBEDDING_DIMENSIONS', '512'))
        )

@dataclass
class GraphConfig:
    """图算法配置"""
    ppr_damping: float = 0.85           # PageRank阻尼系数
    ppr_iterations: int = 100           # PageRank迭代次数
    ppr_tolerance: float = 1e-6         # 收敛阈值
    community_resolution: float = 1.0   # 社区检测分辨率
    max_context_nodes: int = 20         # 最大上下文节点数
    fresh_tail_messages: int = 10       # 保留的原始消息数
    similarity_threshold: float = 0.85  # 向量相似度阈值

@dataclass
class WangchuanV3Config:
    """忘川v3完整配置"""
    db_path: str = str(default_db_path())
    llm: Optional[LLMConfig] = None
    embedding: Optional[EmbeddingConfig] = None
    graph: GraphConfig = None
    
    def __post_init__(self):
        if self.graph is None:
            self.graph = GraphConfig()
    
    @classmethod
    def load(cls, config_path: Optional[str] = None) -> 'WangchuanV3Config':
        """
        加载配置，优先级:
        1. 配置文件
        2. 环境变量
        3. 默认值
        """
        # 尝试从 openclaw.json 加载
        if config_path is None:
            config_path = os.path.expanduser('~/.openclaw/openclaw.json')
        
        llm_config = LLMConfig.from_env()
        embedding_config = EmbeddingConfig.from_env()
        
        # 尝试从配置文件加载
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    data = json.load(f)
                
                # 读取 plugins.graph-memory 配置
                gm_config = data.get('plugins', {}).get('entries', {}).get('graph-memory', {}).get('config', {})
                
                if 'llm' in gm_config and not llm_config:
                    llm_data = gm_config['llm']
                    llm_config = LLMConfig(
                        api_key=llm_data.get('apiKey', ''),
                        base_url=llm_data.get('baseURL', 'https://api.openai.com/v1'),
                        model=llm_data.get('model', 'gpt-4o-mini')
                    )
                
                if 'embedding' in gm_config and not embedding_config:
                    emb_data = gm_config['embedding']
                    embedding_config = EmbeddingConfig(
                        api_key=emb_data.get('apiKey', ''),
                        base_url=emb_data.get('baseURL', 'https://api.openai.com/v1'),
                        model=emb_data.get('model', 'text-embedding-3-small'),
                        dimensions=emb_data.get('dimensions', 512)
                    )
            except Exception as e:
                logger.warning("【WangChuan】[Config] config file load failed: %s", e)
        
        return cls(
            llm=llm_config,
            embedding=embedding_config
        )
    
    def is_llm_available(self) -> bool:
        """检查LLM是否可用"""
        return self.llm is not None and bool(self.llm.api_key)
    
    def is_embedding_available(self) -> bool:
        """检查Embedding是否可用"""
        return self.embedding is not None and bool(self.embedding.api_key)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'db_path': self.db_path,
            'llm': asdict(self.llm) if self.llm else None,
            'embedding': asdict(self.embedding) if self.embedding else None,
            'graph': asdict(self.graph)
        }

# 全局配置实例
_config: Optional[WangchuanV3Config] = None

def get_config() -> WangchuanV3Config:
    """获取全局配置"""
    global _config
    if _config is None:
        _config = WangchuanV3Config.load()
    return _config

def set_config(config: WangchuanV3Config):
    """设置全局配置"""
    global _config
    _config = config
