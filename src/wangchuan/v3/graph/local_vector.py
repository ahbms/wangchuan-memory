#!/usr/bin/env python3
"""
忘川 v3.0 - 本地向量引擎
纯本地 TF-IDF + 字符 bigram，不依赖外部 API
对中文效果好，14-1000 节点规模足够用
"""
from wangchuan.paths import workspace_root as _v3_ws_root

import sqlite3
import json
import os
from pathlib import Path
import math
import struct
import hashlib
import re
from typing import List, Dict, Optional, Tuple
from collections import Counter
from dataclasses import dataclass


WORKSPACE_ROOT = _v3_ws_root()
DEFAULT_DB_PATH = str(WORKSPACE_ROOT / "tiangong" / "wangchuan" / ".index" / "index.sqlite")

@dataclass
class LocalEmbeddingResult:
    entity_id: str
    entity_type: str
    similarity: float


class LocalVectorEngine:
    """
    本地向量引擎
    
    使用字符 bigram TF-IDF 实现文本向量化和相似度搜索
    不依赖任何外部 API，纯 Python 实现
    
    适用规模: 10-10000 节点
    """
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._idf_cache: Optional[Dict[str, float]] = None
        self._vocab: Optional[List[str]] = None
    
    def _tokenize(self, text: str) -> List[str]:
        """字符 bigram 分词（对中文友好）"""
        # 清理
        text = re.sub(r'\s+', ' ', text.lower().strip())
        if len(text) < 2:
            return list(text)
        
        # bigram
        tokens = []
        for i in range(len(text) - 1):
            tokens.append(text[i:i+2])
        
        # 加入 unigram（补充）
        tokens.extend(list(text))
        
        return tokens
    
    def _build_vocab_and_idf(self) -> Tuple[List[str], Dict[str, float]]:
        """构建词汇表和 IDF 值"""
        if self._vocab is not None:
            return self._vocab, self._idf_cache
        
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT name, description, COALESCE(content, '') FROM gm_nodes")
            docs = c.fetchall()
        
        if not docs:
            self._vocab = []
            self._idf_cache = {}
            return self._vocab, self._idf_cache
        
        N = len(docs)
        
        # 统计每个 token 出现在多少文档中
        doc_freq = Counter()
        all_tokens = set()
        
        for name, desc, content in docs:
            text = f"{name} {desc} {content}"
            tokens = set(self._tokenize(text))
            all_tokens.update(tokens)
            for t in tokens:
                doc_freq[t] += 1
        
        # IDF
        idf = {}
        for token in all_tokens:
            idf[token] = math.log((N + 1) / (doc_freq[token] + 1)) + 1
        
        self._vocab = sorted(all_tokens)
        self._idf_cache = idf
        
        return self._vocab, idf
    
    def _text_to_vector(self, text: str) -> Dict[str, float]:
        """文本转 TF-IDF 向量（稀疏表示）"""
        _, idf = self._build_vocab_and_idf()
        
        tokens = self._tokenize(text)
        if not tokens:
            return {}
        
        # TF
        tf = Counter(tokens)
        total = sum(tf.values())
        
        # TF-IDF
        vec = {}
        for token, count in tf.items():
            if token in idf:
                vec[token] = (count / total) * idf[token]
        
        # L2 归一化
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            vec = {k: v / norm for k, v in vec.items()}
        
        return vec
    
    def _cosine_similarity(self, vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
        """余弦相似度（稀疏向量）"""
        if not vec1 or not vec2:
            return 0.0
        
        # 取交集
        common = set(vec1.keys()) & set(vec2.keys())
        if not common:
            return 0.0
        
        dot = sum(vec1[k] * vec2[k] for k in common)
        return max(0.0, min(1.0, dot))  # 已归一化，点积=余弦
    
    def embed_and_store(self, entity_type: str = 'node') -> int:
        """
        为所有节点生成本地向量并存储
        
        Returns:
            处理的节点数
        """
        # 重建 vocab/idf
        self._vocab = None
        self._idf_cache = None
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT node_id, name, description, COALESCE(content, '') as content FROM gm_nodes")
            nodes = c.fetchall()
        
        count = 0
        for node in nodes:
            text = f"{node['name']} {node['description']} {node['content']}"
            vec = self._text_to_vector(text)
            
            if vec:
                # 序列化为 JSON（比 struct 更灵活）
                vec_json = json.dumps(vec)
                embedding_id = f"local_{node['node_id']}"
                
                with sqlite3.connect(self.db_path) as conn:
                    c = conn.cursor()
                    c.execute("""
                        INSERT OR REPLACE INTO gm_embeddings
                        (embedding_id, entity_type, entity_id, model_name, dimensions, embedding)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (embedding_id, entity_type, node['node_id'], 
                          'local-bigram-tfidf', len(vec), vec_json.encode()))
                    conn.commit()
                
                count += 1
        
        return count
    
    def search_similar(self, query_text: str, top_k: int = 10,
                       entity_type: str = 'node') -> List[LocalEmbeddingResult]:
        """
        向量相似度搜索
        
        纯本地计算，无需外部 API
        """
        query_vec = self._text_to_vector(query_text)
        if not query_vec:
            return []
        
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT entity_id, embedding FROM gm_embeddings
                WHERE entity_type = ?
            """, (entity_type,))
            rows = c.fetchall()
        
        results = []
        for entity_id, vec_bytes in rows:
            try:
                # 尝试 JSON 格式（本地向量）
                stored_vec = json.loads(vec_bytes.decode() if isinstance(vec_bytes, bytes) else vec_bytes)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            
            sim = self._cosine_similarity(query_vec, stored_vec)
            if sim > 0.01:  # 过滤极低相似度
                results.append(LocalEmbeddingResult(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    similarity=sim
                ))
        
        results.sort(key=lambda x: x.similarity, reverse=True)
        return results[:top_k]
    
    def get_stats(self) -> Dict:
        """统计信息"""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM gm_embeddings WHERE model_name LIKE 'local%'")
            local_count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM gm_embeddings")
            total_count = c.fetchone()[0]
        
        return {
            'local_embeddings': local_count,
            'total_embeddings': total_count,
            'vocab_size': len(self._vocab) if self._vocab else 0
        }


if __name__ == "__main__":
    import sys
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
    
    engine = LocalVectorEngine(db_path)
    
    # 生成向量
    count = engine.embed_and_store()
    print(f"✅ 已为 {count} 个节点生成本地向量")
    
    # 测试搜索
    for query in ['Docker', '权限', 'Python环境']:
        results = engine.search_similar(query, top_k=3)
        print(f"\n🔍 '{query}':")
        for r in results:
            print(f"  {r.entity_id[:20]}... sim={r.similarity:.4f}")
    
    print(f"\n📊 统计: {engine.get_stats()}")
