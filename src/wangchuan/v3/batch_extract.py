#!/usr/bin/env python3
"""
忘川 v3.0 - 批量知识提炼
从gm_messages中提取重要对话，总结为知识存入gm_nodes
"""
from wangchuan.paths import workspace_root as _v3_ws_root

import sqlite3
import hashlib
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple

WORKSPACE_ROOT = _v3_ws_root()
DB_PATH = str(WORKSPACE_ROOT / 'tiangong' / 'wangchuan' / '.index' / 'index.sqlite')


def get_unprocessed_messages(limit: int = 100) -> List[Dict]:
    """获取还未提炼的对话消息"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # 获取最近的消息（排除已处理的knowledge_base会话）
    c.execute("""
        SELECT id, session_id, role, content, timestamp
        FROM gm_messages
        WHERE session_id != 'knowledge_base'
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    
    rows = c.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def extract_topics_from_messages(messages: List[Dict]) -> List[Dict]:
    """从对话中提炼主题和知识点"""
    # 按session分组
    sessions = {}
    for msg in messages:
        sid = msg['session_id']
        if sid not in sessions:
            sessions[sid] = []
        sessions[sid].append(msg)
    
    topics = []
    
    for sid, msgs in sessions.items():
        # 合并对话内容
        dialogue = "\n".join([
            f"{'👤' if m['role'] == 'user' else '🤖'} {m['content'][:200]}"
            for m in msgs[-20:]  # 最近20条
        ])
        
        if len(dialogue) < 50:
            continue
        
        # 从对话中提取关键模式（基于规则，不用LLM）
        topic = _extract_topic_rules(dialogue, msgs)
        if topic:
            topics.append(topic)
    
    return topics


def _extract_topic_rules(dialogue: str, messages: List[Dict]) -> Dict:
    """基于规则的主题提取"""
    import re
    
    content_lower = dialogue.lower()
    
    # 定义关键词→知识类别的映射
    topic_patterns = [
        # 技术决策
        (r'决定|选择|采用|方案|架构', 'decision', 0.7),
        # 规则/铁律
        (r'铁律|必须|禁止|一定不能|永远不要', 'rule', 0.85),
        # 教训
        (r'教训|经验|下次|以后|避免|不再', 'lesson', 0.8),
        # 技术知识
        (r'安装|配置|部署|代码|API|数据库|服务器', 'technical', 0.6),
        # 用户偏好
        (r'喜欢|偏好|习惯|喜欢|不想', 'preference', 0.75),
        # 重要事件
        (r'完成|搞定|成功|里程碑|终于', 'milestone', 0.7),
        # 问题/错误
        (r'错误|问题|失败|bug|报错|异常', 'issue', 0.65),
    ]
    
    best_match = None
    best_score = 0
    
    for pattern, category, base_score in topic_patterns:
        matches = re.findall(pattern, content_lower)
        if matches:
            score = base_score * min(len(matches) / 3, 1.0)  # 频次加成
            if score > best_score:
                best_score = score
                best_match = category
    
    if best_match and best_score > 0.3:
        # 提取关键句子（包含匹配词的句子）
        sentences = re.split(r'[。\n]', dialogue)
        key_sentences = []
        for s in sentences:
            s = s.strip()
            if len(s) > 10 and len(s) < 300:
                key_sentences.append(s)
        
        if key_sentences:
            # 取最相关的几条
            summary = "\n".join(key_sentences[:5])
            return {
                'category': best_match,
                'confidence': min(best_score, 0.9),
                'content': summary[:500],
                'message_ids': [m['id'] for m in messages]
            }
    
    return None


def save_knowledge_nodes(topics: List[Dict]) -> int:
    """将提炼的知识存入gm_nodes"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    saved = 0
    now = datetime.now().isoformat()
    
    for topic in topics:
        content = topic['content']
        category = topic['category']
        confidence = topic['confidence']
        
        # 生成节点ID
        node_id = hashlib.sha256(content.encode()).hexdigest()[:32]
        
        # 从内容第一行提取名称
        first_line = content.split('\n')[0][:60].strip()
        
        try:
            c.execute("""
                INSERT OR IGNORE INTO gm_nodes 
                (node_id, node_type, name, description, pagerank_score, 
                 access_count, fact_confidence, source_message_ids, first_seen, last_accessed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f"node_{node_id}",
                category,
                first_line,
                content,
                confidence * 0.1,  # pagerank初始值
                1,
                confidence,
                str(topic.get('message_ids', [])),
                now,
                now
            ))
            saved += 1
        except Exception as e:
            print(f"  保存失败: {e}")
    
    # 重建FTS索引
    try:
        c.execute("INSERT INTO gm_nodes_fts(gm_nodes_fts) VALUES('rebuild')")
    except Exception:
        pass
    
    conn.commit()
    conn.close()
    return saved


def run_batch_extract(limit: int = 200):
    """运行批量提取"""
    print(f"🧠 批量知识提炼")
    print(f"=" * 40)
    
    # 1. 获取消息
    messages = get_unprocessed_messages(limit)
    print(f"📥 读取 {len(messages)} 条消息")
    
    if not messages:
        print("无新消息")
        return
    
    # 2. 提炼主题
    topics = extract_topics_from_messages(messages)
    print(f"🎯 提炼出 {len(topics)} 个知识点")
    
    # 3. 保存
    saved = save_knowledge_nodes(topics)
    print(f"💾 存入 {saved} 个知识节点")
    
    # 4. 统计
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM gm_nodes")
    total = c.fetchone()[0]
    print(f"📊 知识图谱总计: {total} 个节点")
    conn.close()


if __name__ == '__main__':
    run_batch_extract()
