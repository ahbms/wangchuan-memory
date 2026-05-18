"""
忘川本地向量搜索集成
为记忆检索提供本地 TF-IDF 向量搜索能力
"""

import sqlite3
import json
import math
import re
from pathlib import Path
from typing import List, Dict, Optional, Any
from collections import Counter


class LocalMemoryVectorSearch:
    """
    本地记忆向量搜索引擎
    使用字符 bigram TF-IDF 实现，无需外部 API
    """
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._vocab: Optional[List[str]] = None
        self._idf_cache: Optional[Dict[str, float]] = None
    
    def _tokenize(self, text: str) -> List[str]:
        """字符 bigram 分词"""
        text = re.sub(r'\s+', ' ', text.lower().strip())
        if len(text) < 2:
            return list(text)
        
        tokens = []
        for i in range(len(text) - 1):
            tokens.append(text[i:i+2])
        tokens.extend(list(text))
        return tokens
    
    def _build_vocab_and_idf(self) -> tuple:
        """构建词汇表和 IDF"""
        if self._vocab is not None:
            return self._vocab, self._idf_cache
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT content FROM memories WHERE content IS NOT NULL")
            docs = [row[0] for row in c.fetchall()]
        
        if not docs:
            self._vocab = []
            self._idf_cache = {}
            return self._vocab, self._idf_cache
        
        N = len(docs)
        doc_freq = Counter()
        all_tokens = set()
        
        for doc in docs:
            tokens = set(self._tokenize(str(doc)))
            all_tokens.update(tokens)
            for t in tokens:
                doc_freq[t] += 1
        
        idf = {}
        for token in all_tokens:
            idf[token] = math.log((N + 1) / (doc_freq[token] + 1)) + 1
        
        self._vocab = sorted(all_tokens)
        self._idf_cache = idf
        
        return self._vocab, self._idf_cache
    
    def _text_to_vector(self, text: str) -> Dict[str, float]:
        """文本转 TF-IDF 向量"""
        _, idf = self._build_vocab_and_idf()
        
        tokens = self._tokenize(text)
        if not tokens:
            return {}
        
        tf = Counter(tokens)
        total = sum(tf.values())
        
        vec = {}
        for token, count in tf.items():
            if token in idf:
                vec[token] = (count / total) * idf[token]
        
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            vec = {k: v / norm for k, v in vec.items()}
        
        return vec
    
    def _cosine_similarity(self, vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
        """余弦相似度"""
        if not vec1 or not vec2:
            return 0.0
        common = set(vec1.keys()) & set(vec2.keys())
        if not common:
            return 0.0
        dot = sum(vec1[k] * vec2[k] for k in common)
        return max(0.0, min(1.0, dot))
    
    def embed_memories(self) -> int:
        """为所有记忆生成本地向量（全量）"""
        self._vocab = None
        self._idf_cache = None
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT id, content FROM memories WHERE content IS NOT NULL")
            memories = c.fetchall()
        
        count = 0
        for memory in memories:
            text = str(memory['content'])
            vec = self._text_to_vector(text)
            
            if vec:
                vec_json = json.dumps(vec)
                
                with sqlite3.connect(self.db_path) as conn:
                    c = conn.cursor()
                    c.execute("""
                        INSERT OR REPLACE INTO memory_embeddings 
                        (memory_id, embedding_model, embedding_vector)
                        VALUES (?, ?, ?)
                    """, (memory['id'], 'local-tfidf', vec_json))
                    conn.commit()
                
                count += 1
        
        return count
    
    def embed_memory(self, memory_id: int, content: str) -> bool:
        """为单条记忆增量更新向量"""
        self._vocab = None
        self._idf_cache = None
        
        text = str(content)
        vec = self._text_to_vector(text)
        
        if not vec:
            return False
        
        try:
            vec_json = json.dumps(vec)
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("""
                    INSERT OR REPLACE INTO memory_embeddings 
                    (memory_id, embedding_model, embedding_vector)
                    VALUES (?, ?, ?)
                """, (memory_id, 'local-tfidf', vec_json))
                conn.commit()
            return True
        except Exception:
            return False
    
    def delete_memory(self, memory_id: int) -> bool:
        """删除记忆的向量索引"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,))
                conn.commit()
            return True
        except Exception:
            return False
    
    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        向量相似度搜索
        
        Returns:
            [{"memory_id": int, "content": str, "similarity": float}, ...]
        """
        query_vec = self._text_to_vector(query)
        if not query_vec:
            return []
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT m.id, m.content, me.embedding_vector
                FROM memories m
                JOIN memory_embeddings me ON m.id = me.memory_id
                WHERE me.embedding_model = 'local-tfidf'
            """)
            rows = c.fetchall()
        
        results = []
        for row in rows:
            try:
                stored_vec = json.loads(row['embedding_vector'])
            except json.JSONDecodeError:
                continue
            
            sim = self._cosine_similarity(query_vec, stored_vec)
            if sim > 0.01:
                results.append({
                    'memory_id': row['id'],
                    'content': row['content'],
                    'similarity': sim
                })
        
        results.sort(key=lambda x: x['similarity'], reverse=True)
        return results[:top_k]
    
    def get_stats(self) -> Dict:
        """统计信息"""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM memory_embeddings WHERE embedding_model = 'local-tfidf'")
            count = c.fetchone()[0]
        
        return {
            'indexed_memories': count,
            'vocab_size': len(self._vocab) if self._vocab else 0
        }
    
    def ensure_table(self):
        """确保向量表存在"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL,
                    embedding_model TEXT NOT NULL,
                    embedding_vector TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(memory_id, embedding_model)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model ON memory_embeddings(embedding_model)")
            conn.commit()
