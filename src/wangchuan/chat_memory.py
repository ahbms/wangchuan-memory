"""
忘川对话记忆模块 (Wangchuan Chat Memory) — legacy storage / compat shell
存储和管理 legacy 对话历史

⚠️ 说明：
- 仅当直接实例化本 legacy 类且未传 `db_path` 时，才会落库到 wangchuan/.wangchuan/memory.db
- 该路径是历史兼容存档库，不是当前默认主库；当前默认主库见 paths.default_db_path() / .index/index.sqlite
- 本模块主要承担 legacy 对话历史存档，不是当前主 recall 主链
- 不属于当前主回答前 recall 主链，也不是新的功能扩展入口
- 当前主回答回忆链优先使用 wangchuan/recall_service.py
- recall_service.py 当前内部再承载到 wangchuan/v3/pipeline_v3.py
- 若只是需要 legacy fallback factory，应优先通过 wangchuan.compat 收口，而不是上游直接依赖本文件
- 若是在排查当前生产记忆主链，请不要优先从本文件开始
"""

import json
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional, Any
import os

try:
    from .legacy_types import ChatMessage
except ImportError:
    from legacy_types import ChatMessage


class ChatMemory:
    """
    legacy 对话记忆存储壳（compat storage shell）

    功能：
    - 持久化存储 legacy 对话历史
    - 按会话ID检索
    - 支持时间范围查询
    - 与忘川上下文协议集成

    注意：
    - 当前生产 recall 主链不以本类为首选入口
    - 上游若只是需要 fallback 构造，应优先通过 wangchuan.compat 收口
    """
    
    def __init__(self, db_path: str = None):
        """
        初始化对话记忆
        
        Args:
            db_path: 数据库路径；不传时仅作为 legacy 存档 fallback
                落到 .wangchuan/memory.db，不作为当前图谱增强 recall 主链默认库
        """
        if db_path is None:
            # legacy 对话历史库（非当前 recall 主链）
            base_dir = os.path.dirname(os.path.abspath(__file__))
            db_dir = os.path.join(base_dir, '.wangchuan')
            os.makedirs(db_dir, exist_ok=True)
            self.db_path = os.path.join(db_dir, 'memory.db')
        else:
            self.db_path = db_path
        
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            # 对话历史表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS chat_history (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 会话信息表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    platform TEXT,
                    start_time TEXT,
                    last_active TEXT,
                    message_count INTEGER DEFAULT 0,
                    summary TEXT
                )
            ''')
            
            # 创建索引
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_chat_session 
                ON chat_history(session_id, timestamp)
            ''')
            
            # 任务上下文表（vNext 新增）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS task_context (
                    task_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    status TEXT DEFAULT 'active',
                    phase TEXT DEFAULT 'planning',
                    open_questions TEXT DEFAULT '[]',
                    completed_steps TEXT DEFAULT '[]',
                    trace_id TEXT DEFAULT '',
                    last_intent TEXT DEFAULT '',
                    last_action TEXT DEFAULT '',
                    context_data TEXT DEFAULT '{}',
                    created_at TEXT,
                    updated_at TEXT,
                    FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
                )
            ''')
            
            # 任务上下文索引
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_task_session 
                ON task_context(session_id, status)
            ''')
            
            conn.commit()
        finally:
            conn.close()
    
    def save_message(self, session_id: str, role: str, content: str,
                    user_id: str = None, platform: str = None,
                    metadata: Dict = None, timestamp: str = None) -> str:
        """
        保存单条消息
        
        Args:
            session_id: 会话ID
            role: 'user' 或 'assistant'
            content: 消息内容
            user_id: 用户ID
            platform: 平台（qqbot/telegram等）
            metadata: 额外元数据
            timestamp: 自定义时间戳（可选，默认当前时间）
        
        Returns:
            消息ID
        """
        import random
        msg_id = f"msg_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000, 9999)}"
        if timestamp is None:
            timestamp = datetime.now().isoformat()
        
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            # 保存消息
            cursor.execute('''
                INSERT INTO chat_history (id, session_id, role, content, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (msg_id, session_id, role, content, timestamp, 
                  json.dumps(metadata or {}, ensure_ascii=False)))
            
            # 更新或创建会话信息
            cursor.execute('''
                INSERT INTO chat_sessions (session_id, user_id, platform, start_time, last_active, message_count)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_active = ?,
                    message_count = message_count + 1
            ''', (session_id, user_id, platform, timestamp, timestamp, timestamp))
        
            conn.commit()
        finally:
            conn.close()
        
        return msg_id
    
    def get_session_history(self, session_id: str, 
                           limit: int = 50) -> List[ChatMessage]:
        """
        获取会话历史
        
        Args:
            session_id: 会话ID
            limit: 返回消息数量
        
        Returns:
            ChatMessage列表
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, session_id, role, content, timestamp, metadata
                FROM chat_history
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (session_id, limit))
            
            messages = []
            for row in cursor.fetchall():
                messages.append(ChatMessage(
                    id=row[0],
                    session_id=row[1],
                    role=row[2],
                    content=row[3],
                    timestamp=row[4],
                    metadata=json.loads(row[5]) if row[5] else {}
                ))
        finally:
            conn.close()
        
        # 按时间正序返回
        return list(reversed(messages))
    
    def get_recent_sessions(self, limit: int = 10) -> List[Dict]:
        """获取最近的会话列表"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT session_id, user_id, platform, start_time, last_active, message_count
                FROM chat_sessions
                ORDER BY last_active DESC
                LIMIT ?
            ''', (limit,))
            
            sessions = []
            for row in cursor.fetchall():
                sessions.append({
                    'session_id': row[0],
                    'user_id': row[1],
                    'platform': row[2],
                    'start_time': row[3],
                    'last_active': row[4],
                    'message_count': row[5]
                })
        finally:
            conn.close()
        return sessions
    
    def search_messages(self, keyword: str, session_id: str = None,
                       limit: int = 20) -> List[ChatMessage]:
        """搜索消息内容"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if session_id:
            cursor.execute('''
                SELECT id, session_id, role, content, timestamp, metadata
                FROM chat_history
                WHERE session_id = ? AND content LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (session_id, f'%{keyword}%', limit))
        else:
            cursor.execute('''
                SELECT id, session_id, role, content, timestamp, metadata
                FROM chat_history
                WHERE content LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (f'%{keyword}%', limit))
        
        messages = []
        for row in cursor.fetchall():
            messages.append(ChatMessage(
                id=row[0],
                session_id=row[1],
                role=row[2],
                content=row[3],
                timestamp=row[4],
                metadata=json.loads(row[5]) if row[5] else {}
            ))
        
        conn.close()
        return messages
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 总消息数
        cursor.execute('SELECT COUNT(*) FROM chat_history')
        total_messages = cursor.fetchone()[0]
        
        # 总会话数
        cursor.execute('SELECT COUNT(*) FROM chat_sessions')
        total_sessions = cursor.fetchone()[0]
        
        # 今日消息数
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT COUNT(*) FROM chat_history 
            WHERE date(timestamp) = ?
        ''', (today,))
        today_messages = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_messages': total_messages,
            'total_sessions': total_sessions,
            'today_messages': today_messages
        }
    
    def export_session(self, session_id: str) -> str:
        """导出会话为JSON字符串"""
        messages = self.get_session_history(session_id, limit=1000)
        
        data = {
            'session_id': session_id,
            'exported_at': datetime.now().isoformat(),
            'message_count': len(messages),
            'messages': [
                {
                    'role': m.role,
                    'content': m.content,
                    'timestamp': m.timestamp,
                    'metadata': m.metadata
                }
                for m in messages
            ]
        }
        
        return json.dumps(data, ensure_ascii=False, indent=2)
    
    # ==================== 任务上下文管理 (vNext) ====================
    
    def save_task_context(self, task_id: str, session_id: str, topic: str,
                         status: str = 'active', phase: str = 'planning',
                         open_questions: List[str] = None,
                         completed_steps: List[str] = None,
                         trace_id: str = '', last_intent: str = '',
                         last_action: str = '',
                         context_data: Dict = None) -> str:
        """
        保存或更新任务上下文
        
        Args:
            task_id: 任务ID
            session_id: 会话ID
            topic: 任务主题
            status: 任务状态 (active/paused/completed/abandoned)
            phase: 任务阶段 (planning/executing/reviewing/done)
            open_questions: 待解决问题列表
            completed_steps: 已完成步骤列表
            trace_id: Pipeline追踪ID
            last_intent: 最后意图
            last_action: 最后动作
            context_data: 额外上下文数据
        
        Returns:
            任务ID
        """
        now = datetime.now().isoformat()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO task_context 
            (task_id, session_id, topic, status, phase, open_questions, 
             completed_steps, trace_id, last_intent, last_action, 
             context_data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                status = ?,
                phase = ?,
                open_questions = ?,
                completed_steps = ?,
                trace_id = ?,
                last_intent = ?,
                last_action = ?,
                context_data = ?,
                updated_at = ?
        ''', (
            task_id, session_id, topic, status, phase,
            json.dumps(open_questions or [], ensure_ascii=False),
            json.dumps(completed_steps or [], ensure_ascii=False),
            trace_id, last_intent, last_action,
            json.dumps(context_data or {}, ensure_ascii=False),
            now, now,
            # ON CONFLICT 更新值
            status, phase,
            json.dumps(open_questions or [], ensure_ascii=False),
            json.dumps(completed_steps or [], ensure_ascii=False),
            trace_id, last_intent, last_action,
            json.dumps(context_data or {}, ensure_ascii=False),
            now
        ))
        
        conn.commit()
        conn.close()
        
        return task_id
    
    def get_task_context(self, task_id: str) -> Optional[Dict]:
        """获取单个任务上下文"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT task_id, session_id, topic, status, phase,
                   open_questions, completed_steps, trace_id,
                   last_intent, last_action, context_data,
                   created_at, updated_at
            FROM task_context
            WHERE task_id = ?
        ''', (task_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            'task_id': row[0],
            'session_id': row[1],
            'topic': row[2],
            'status': row[3],
            'phase': row[4],
            'open_questions': json.loads(row[5]) if row[5] else [],
            'completed_steps': json.loads(row[6]) if row[6] else [],
            'trace_id': row[7],
            'last_intent': row[8],
            'last_action': row[9],
            'context_data': json.loads(row[10]) if row[10] else {},
            'created_at': row[11],
            'updated_at': row[12]
        }
    
    def get_active_tasks(self, session_id: str = None,
                        status: str = 'active') -> List[Dict]:
        """获取活跃任务列表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if session_id:
            cursor.execute('''
                SELECT task_id, session_id, topic, status, phase,
                       open_questions, completed_steps, trace_id,
                       last_intent, last_action, context_data,
                       created_at, updated_at
                FROM task_context
                WHERE session_id = ? AND status = ?
                ORDER BY updated_at DESC
            ''', (session_id, status))
        else:
            cursor.execute('''
                SELECT task_id, session_id, topic, status, phase,
                       open_questions, completed_steps, trace_id,
                       last_intent, last_action, context_data,
                       created_at, updated_at
                FROM task_context
                WHERE status = ?
                ORDER BY updated_at DESC
            ''', (status,))
        
        tasks = []
        for row in cursor.fetchall():
            tasks.append({
                'task_id': row[0],
                'session_id': row[1],
                'topic': row[2],
                'status': row[3],
                'phase': row[4],
                'open_questions': json.loads(row[5]) if row[5] else [],
                'completed_steps': json.loads(row[6]) if row[6] else [],
                'trace_id': row[7],
                'last_intent': row[8],
                'last_action': row[9],
                'context_data': json.loads(row[10]) if row[10] else {},
                'created_at': row[11],
                'updated_at': row[12]
            })
        
        conn.close()
        return tasks
    
    def update_task_step(self, task_id: str, step: str,
                        phase: str = None) -> bool:
        """添加任务完成步骤"""
        task = self.get_task_context(task_id)
        if not task:
            return False
        
        steps = task['completed_steps']
        if step not in steps:
            steps.append(step)
        
        update_data = {
            'completed_steps': json.dumps(steps, ensure_ascii=False),
            'updated_at': datetime.now().isoformat()
        }
        if phase:
            update_data['phase'] = phase
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        set_clause = ', '.join([f"{k} = ?" for k in update_data.keys()])
        values = list(update_data.values()) + [task_id]
        
        cursor.execute(f'''
            UPDATE task_context SET {set_clause}
            WHERE task_id = ?
        ''', values)
        
        conn.commit()
        conn.close()
        return True
    
    def complete_task(self, task_id: str, 
                     final_phase: str = 'done') -> bool:
        """标记任务完成"""
        return self.update_task_step(task_id, '__completed__', 
                                    phase=final_phase) and \
               self._set_task_status(task_id, 'completed')
    
    def _set_task_status(self, task_id: str, status: str) -> bool:
        """内部方法：设置任务状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE task_context SET status = ?, updated_at = ?
            WHERE task_id = ?
        ''', (status, datetime.now().isoformat(), task_id))
        
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        
        return affected > 0
    
    def get_task_history(self, session_id: str, 
                        limit: int = 20) -> List[Dict]:
        """获取会话的任务历史（包括已完成）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT task_id, topic, status, phase,
                   completed_steps, created_at, updated_at
            FROM task_context
            WHERE session_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
        ''', (session_id, limit))
        
        tasks = []
        for row in cursor.fetchall():
            tasks.append({
                'task_id': row[0],
                'topic': row[1],
                'status': row[2],
                'phase': row[3],
                'step_count': len(json.loads(row[4])) if row[4] else 0,
                'created_at': row[5],
                'updated_at': row[6]
            })
        
        conn.close()
        return tasks


# 便捷函数
def create_chat_memory(db_path: str | None = None) -> ChatMemory:
    """创建 legacy/compat 对话记忆存储实例。"""
    return ChatMemory(db_path)


__all__ = [
    "ChatMessage",
    "ChatMemory",
    "create_chat_memory",
]


# 测试
if __name__ == "__main__":
    print("=== 忘川对话记忆测试 ===\n")
    
    memory = create_chat_memory()
    
    # 模拟保存昨天的对话
    print("1. 保存模拟对话...")
    session_id = "qqbot_test_session"
    
    # 用户消息
    memory.save_message(
        session_id=session_id,
        role='user',
        content='帮我创建一个天工开智的圈子',
        user_id='user_123',
        platform='qqbot',
        metadata={'intent': 'create_circle'}
    )
    
    # AI回复
    memory.save_message(
        session_id=session_id,
        role='assistant',
        content='好的！我来创建天工开智圈子',
        metadata={'action': 'create_circle'}
    )
    
    print("   ✓ 消息已保存")
    
    # 查看统计
    print("\n2. 统计信息:")
    stats = memory.get_stats()
    print(f"   总消息: {stats['total_messages']}")
    print(f"   总会话: {stats['total_sessions']}")
    print(f"   今日消息: {stats['today_messages']}")
    
    # 查看历史
    print("\n3. 会话历史:")
    history = memory.get_session_history(session_id)
    for msg in history:
        print(f"   [{msg.role}] {msg.content[:40]}...")
    
    print("\n=== 测试完成 ===")
