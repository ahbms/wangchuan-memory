#!/usr/bin/env python3
"""
忘川 v3.0 - 向量嵌入模块
支持火山引擎/豆包 Embedding API
"""

import logging
import sqlite3
import json
import hashlib
import struct
from typing import List, Optional, Dict
from dataclasses import dataclass
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

@dataclass
class EmbeddingResult:
    """嵌入结果"""
    embedding_id: str
    entity_type: str      # message / node
    entity_id: str
    vector: List[float]   # 向量
    model_name: str
    dimensions: int

class VectorEngine:
    """向量嵌入引擎"""
    
    def __init__(self, db_path: str, api_key: str = None, base_url: str = None, model: str = None, dimensions: int = 512):
        self.db_path = db_path
        self.api_key = api_key
        self.base_url = base_url or "https://api.openai.com/v1"
        self.model = model or "text-embedding-3-small"
        self.dimensions = dimensions
        
        # 火山引擎豆包embedding模型
        self.volc_models = {
            "doubao-embedding": "ep-20241215123456-abcdef",  # 示例endpoint
            "kimi-embedding": "kimi-embedding-v1"
        }
    
    def embed_text(self, text: str, entity_type: str, entity_id: str) -> Optional[EmbeddingResult]:
        """
        为文本生成向量嵌入
        
        Args:
            text: 要嵌入的文本
            entity_type: 实体类型 (message/node)
            entity_id: 实体ID
        
        Returns:
            EmbeddingResult or None
        """
        if not self.api_key:
            logger.warning("【WangChuan】[VectorEngine] embedding skipped: api key not configured")
            return None
        
        try:
            # 调用API
            vector = self._call_embedding_api(text)
            
            if not vector:
                return None
            
            # 生成嵌入ID
            embedding_id = self._generate_embedding_id(text, entity_id)
            
            # 存储到数据库
            self._store_embedding(embedding_id, entity_type, entity_id, vector)
            
            return EmbeddingResult(
                embedding_id=embedding_id,
                entity_type=entity_type,
                entity_id=entity_id,
                vector=vector,
                model_name=self.model,
                dimensions=len(vector)
            )
            
        except Exception as e:
            logger.warning("【WangChuan】[VectorEngine] embed failed: %s", e)
            return None
    
    def _call_embedding_api(self, text: str) -> Optional[List[float]]:
        """
        调用Embedding API
        
        支持:
        - OpenAI API格式
        - 火山引擎/豆包 API格式
        - 讯飞星火 API格式
        """
        # 判断API类型
        if "xf-yun.com" in self.base_url or "xunfei" in self.base_url:
            return self._call_xfyun_embedding(text)
        elif "volces.com" in self.base_url or "ark" in self.base_url:
            return self._call_volc_embedding(text)
        else:
            return self._call_openai_embedding(text)
    
    def _call_xfyun_embedding(self, text: str) -> Optional[List[float]]:
        """调用讯飞星火Embedding API"""
        # 讯飞星火API格式
        url = f"{self.base_url}/embeddings"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "input": [text[:8000]],  # 讯飞需要数组格式
            "model": self.model
        }
        
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers=headers,
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
                
                # 讯飞响应格式: {"data": [{"embedding": [...]}]}
                if 'data' in result and len(result['data']) > 0:
                    embedding = result['data'][0].get('embedding', [])
                    # 如果维度不匹配，截断或填充
                    if len(embedding) > self.dimensions:
                        embedding = embedding[:self.dimensions]
                    elif len(embedding) < self.dimensions:
                        embedding = embedding + [0.0] * (self.dimensions - len(embedding))
                    return embedding
                
                return None
                
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            logger.warning("【WangChuan】[VectorEngine][XFYun] api error=%s body=%s", e.code, error_body[:200])
            return None
        except Exception as e:
            logger.warning("【WangChuan】[VectorEngine][XFYun] request failed: %s", e)
            return None
    
    def _call_volc_embedding(self, text: str) -> Optional[List[float]]:
        """调用火山引擎Embedding API"""
        # 火山引擎豆包embedding端点
        # 注意: 豆包embedding需要单独的endpoint ID
        url = f"{self.base_url}/embeddings"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "input": text[:8000],  # 限制长度
            "model": self.model,
            "encoding_format": "float"
        }
        
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers=headers,
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
                
                # 解析响应
                if 'data' in result and len(result['data']) > 0:
                    return result['data'][0].get('embedding', [])
                
                return None
                
        except urllib.error.HTTPError as e:
            logger.warning("【WangChuan】[VectorEngine][Volc] api error=%s body=%s", e.code, e.read().decode())
            return None
        except Exception as e:
            logger.warning("【WangChuan】[VectorEngine][Volc] request failed: %s", e)
            return None
    
    def _call_openai_embedding(self, text: str) -> Optional[List[float]]:
        """调用OpenAI格式Embedding API"""
        url = f"{self.base_url}/embeddings"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "input": text[:8000],
            "model": self.model
        }
        
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers=headers,
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
                
                if 'data' in result and len(result['data']) > 0:
                    return result['data'][0].get('embedding', [])
                
                return None
                
        except Exception as e:
            logger.warning("【WangChuan】[VectorEngine][OpenAI] api failed: %s", e)
            return None
    
    def _store_embedding(self, embedding_id: str, entity_type: str, entity_id: str, vector: List[float]):
        """存储嵌入到数据库"""
        # 将向量序列化为二进制
        vector_bytes = struct.pack(f'{len(vector)}f', *vector)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO gm_embeddings
                (embedding_id, entity_type, entity_id, model_name, dimensions, embedding)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                embedding_id,
                entity_type,
                entity_id,
                self.model,
                len(vector),
                vector_bytes
            ))
            
            conn.commit()
    
    def get_embedding(self, entity_type: str, entity_id: str) -> Optional[List[float]]:
        """从数据库获取嵌入"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT embedding, dimensions FROM gm_embeddings
                WHERE entity_type = ? AND entity_id = ?
            """, (entity_type, entity_id))
            
            row = cursor.fetchone()
            if row:
                vector_bytes, dimensions = row
                expected_bytes = dimensions * 4
                if not vector_bytes or len(vector_bytes) != expected_bytes:
                    return None
                # 反序列化
                return list(struct.unpack(f'{dimensions}f', vector_bytes))
            
            return None
    
    def search_similar(
        self,
        query_text: str,
        top_k: int = 10,
        entity_type: Optional[str] = None
    ) -> List[Dict]:
        """
        向量相似度搜索
        
        注意: 纯SQLite实现，使用余弦相似度计算
        生产环境建议使用专门的向量数据库
        """
        # 获取查询向量
        query_vector = self._call_embedding_api(query_text)
        if not query_vector:
            return []
        
        # 从数据库获取所有候选向量
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            if entity_type:
                cursor.execute("""
                    SELECT entity_id, entity_type, embedding, dimensions
                    FROM gm_embeddings
                    WHERE entity_type = ?
                """, (entity_type,))
            else:
                cursor.execute("""
                    SELECT entity_id, entity_type, embedding, dimensions
                    FROM gm_embeddings
                """)
            
            rows = cursor.fetchall()
        
        # 计算余弦相似度
        results = []
        skipped_bad_vectors = 0
        for entity_id, ent_type, vector_bytes, dimensions in rows:
            expected_bytes = dimensions * 4
            if not vector_bytes or len(vector_bytes) != expected_bytes:
                skipped_bad_vectors += 1
                continue
            try:
                vector = struct.unpack(f'{dimensions}f', vector_bytes)
            except struct.error:
                skipped_bad_vectors += 1
                continue
            similarity = self._cosine_similarity(query_vector, vector)
            
            results.append({
                'entity_id': entity_id,
                'entity_type': ent_type,
                'similarity': similarity
            })

        if skipped_bad_vectors:
            logger.warning("【WangChuan】[VectorEngine][Search] skipped bad vectors=%s", skipped_bad_vectors)
        
        # 排序并返回top_k
        results.sort(key=lambda x: x['similarity'], reverse=True)
        return results[:top_k]
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        if len(vec1) != len(vec2):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a * a for a in vec1) ** 0.5
        norm2 = sum(b * b for b in vec2) ** 0.5
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)
    
    def _generate_embedding_id(self, text: str, entity_id: str) -> str:
        """生成嵌入ID"""
        content = f"{entity_id}:{text[:100]}"
        hash_val = hashlib.sha256(content.encode()).hexdigest()[:16]
        return f"emb_{hash_val}"
    
    def deduplicate_nodes(self, threshold: float = 0.85) -> List[tuple]:
        """
        向量去重
        
        Returns:
            List of (keep_node_id, merge_node_ids)
        """
        # 获取所有节点嵌入
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT e.entity_id, e.embedding, e.dimensions, n.node_type
                FROM gm_embeddings e
                JOIN gm_nodes n ON e.entity_id = n.node_id
                WHERE e.entity_type = 'node'
            """)
            
            rows = cursor.fetchall()
        
        # 计算相似度矩阵
        vectors = []
        for entity_id, vector_bytes, dimensions, node_type in rows:
            expected_bytes = dimensions * 4
            if not vector_bytes or len(vector_bytes) != expected_bytes:
                continue
            try:
                vector = struct.unpack(f'{dimensions}f', vector_bytes)
            except struct.error:
                continue
            vectors.append((entity_id, vector, node_type))
        
        # 找到相似节点
        to_merge = []
        processed = set()
        
        for i, (id1, vec1, type1) in enumerate(vectors):
            if id1 in processed:
                continue
            
            similar_ids = []
            for j, (id2, vec2, type2) in enumerate(vectors):
                if i != j and id2 not in processed and type1 == type2:
                    sim = self._cosine_similarity(vec1, vec2)
                    if sim >= threshold:
                        similar_ids.append((id2, sim))
                        processed.add(id2)
            
            if similar_ids:
                to_merge.append((id1, similar_ids))
                processed.add(id1)
        
        return to_merge
