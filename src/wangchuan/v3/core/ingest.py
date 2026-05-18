#!/usr/bin/env python3
"""
忘川 v3.0 - 消息摄取模块 (Ingest)
零LLM处理，快速存储原始消息
"""

import sqlite3
import json
import hashlib
import os
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any
from dataclasses import dataclass

from wangchuan.paths import data_root

logger = logging.getLogger(__name__)

SCHEMA_PATH = data_root() / 'v3' / 'schema.sql'
PACKAGE_SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'schema.sql'

@dataclass
class Message:
    """标准消息格式"""
    session_id: str
    role: str          # user / assistant / system
    content: str
    message_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    metadata: Optional[Dict] = None
    
    def estimate_tokens(self) -> int:
        """估算token数 (粗略估计: 1 token ≈ 4 chars for Chinese, 4 chars for English)"""
        return len(self.content) // 4 + 1

class IngestEngine:
    """消息摄取引擎"""
    
    # 信号检测模式 (零LLM，基于规则)
    SIGNAL_PATTERNS = {
        'error': [
            r'error[:\s]', r'exception[:\s]', r'failed[:\s]',
            r'失败', r'错误', r'异常', r'报错',
            r'traceback', r'syntaxerror', r'importerror'
        ],
        'correction': [
            r'修正', r'纠正', r'更正', r'修复', r'fixed', r'corrected',
            r'应该', r'不对', r'错了', r'改为'
        ],
        'completion': [
            r'完成', r'搞定', r'解决了', r'成功了', r'done', r'completed',
            r'works?', r'working', r'可以了', r'行了'
        ],
        'question': [
            r'[?？]', r'如何', r'怎么', r'为什么', r'什么是',
            r'how to', r'what is', r'why', r'help'
        ]
    }
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """初始化数据库"""
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.db_path) as conn:
                schema_path = SCHEMA_PATH if SCHEMA_PATH.exists() else PACKAGE_SCHEMA_PATH
                with open(schema_path, 'r', encoding='utf-8') as f:
                    conn.executescript(f.read())
        except Exception as e:
            logger.warning("【WangChuan】[Ingest][Schema] init failed path=%s: %s", SCHEMA_PATH, e)
            raise
    
    def ingest(self, message: Message) -> int:
        """
        摄取单条消息
        
        Returns:
            message_db_id: 数据库中的消息ID
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 1. 存储消息
            cursor.execute("""
                INSERT INTO gm_messages 
                (session_id, message_id, role, content, timestamp, token_count)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                message.session_id,
                message.message_id or self._generate_msg_id(),
                message.role,
                message.content,
                message.timestamp or datetime.now(),
                message.estimate_tokens()
            ))
            
            msg_db_id = cursor.lastrowid
            
            # 2. 信号检测 (零LLM)
            signals = self._detect_signals(message)
            for sig_type, confidence, extracted in signals:
                cursor.execute("""
                    INSERT INTO gm_signals 
                    (message_id, signal_type, confidence, extracted_text)
                    VALUES (?, ?, ?, ?)
                """, (msg_db_id, sig_type, confidence, extracted))
            
            conn.commit()
            
            return msg_db_id
    
    def ingest_batch(self, messages: List[Message]) -> List[int]:
        """批量摄取消息"""
        return [self.ingest(msg) for msg in messages]
    
    def _detect_signals(self, message: Message) -> List[tuple]:
        """
        基于规则的信号检测
        
        Returns:
            List of (signal_type, confidence, extracted_text)
        """
        import re
        
        signals = []
        content_lower = message.content.lower()
        
        for sig_type, patterns in self.SIGNAL_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, content_lower, re.IGNORECASE):
                    # 提取匹配上下文 (前后50字符)
                    match = re.search(pattern, message.content, re.IGNORECASE)
                    if match:
                        start = max(0, match.start() - 50)
                        end = min(len(message.content), match.end() + 50)
                        extracted = message.content[start:end]
                        
                        # 计算置信度 (基于模式匹配强度)
                        confidence = self._calculate_signal_confidence(
                            sig_type, message, pattern
                        )
                        
                        signals.append((sig_type, confidence, extracted))
                        break  # 同类型只记录一次
        
        return signals
    
    def _calculate_signal_confidence(self, sig_type: str, message: Message, pattern: str) -> float:
        """计算信号置信度"""
        base_confidence = 0.7
        
        # 角色加权
        if message.role == 'assistant' and sig_type in ['error', 'correction']:
            base_confidence += 0.1  # assistant报错更可信
        
        if message.role == 'user' and sig_type == 'question':
            base_confidence += 0.15  # user提问更可信
        
        # 内容长度加权
        if len(message.content) > 200:
            base_confidence += 0.05  # 长内容通常更详细
        
        return min(base_confidence, 0.95)
    
    def _generate_msg_id(self) -> str:
        """生成消息ID"""
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
        random_suffix = hashlib.md5(timestamp.encode()).hexdigest()[:8]
        return f"msg_{timestamp}_{random_suffix}"
    
    def get_unprocessed_signals(self, limit: int = 100) -> List[Dict]:
        """获取未处理的信号 (供compact模块使用)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT s.*, m.content, m.session_id, m.role
                FROM gm_signals s
                JOIN gm_messages m ON s.message_id = m.id
                WHERE s.processed = FALSE
                ORDER BY s.timestamp ASC
                LIMIT ?
            """, (limit,))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def mark_signals_processed(self, signal_ids: List[int]):
        """标记信号为已处理"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                "UPDATE gm_signals SET processed = TRUE WHERE id = ?",
                [(sid,) for sid in signal_ids]
            )
            conn.commit()
