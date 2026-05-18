"""
忘川上下文传递协议 (Wangchuan Context Protocol - WCP)
天工开智 - legacy 上下文协议兼容层（compat / migration）

解决子智能体编排中的核心问题：
- 上下文丢失（Context Loss）
- 信息孤岛
- 跨智能体信息传递失败

设计原则：
- 显式传递：上下文必须显式声明和传递
- 版本控制：支持上下文版本追踪
- 增量更新：只传递变化的部分
- 持久化：所有上下文变更记录到数据库

⚠️ 边界说明：
- 本文件属于 legacy / migration 兼容层，不是当前忘川公开默认入口
- 它不属于 `wangchuan` 包根公开 contract，也不是新的功能扩展首选入口
- 若只是在理解当前对外主链，请优先看 `wangchuan` 包根、`recall_service.py` 与 `v3` 实现承载层说明
- 当前保留它，主要是为了兼容旧上下文协议与迁移场景，而不是鼓励新接入直接依赖本文件
"""

import json
import sqlite3
import zlib
import base64
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from enum import Enum
import os
import threading


class ContextScope(Enum):
    """上下文作用域"""
    SESSION = "session"      # 会话级（单次对话）
    TASK = "task"           # 任务级（单次任务）
    AGENT = "agent"         # Agent级（跨任务）
    GLOBAL = "global"       # 全局级（系统级）


class ContextPriority(Enum):
    """上下文优先级"""
    CRITICAL = 0    # 关键信息（必须传递）
    HIGH = 1        # 重要信息（建议传递）
    NORMAL = 2      # 普通信息（可选传递）
    LOW = 3         # 低优先级（按需传递）


@dataclass
class ContextItem:
    """上下文项"""
    key: str
    value: Any
    scope: ContextScope
    priority: ContextPriority
    source_agent: str           # 来源agent
    created_at: str
    expires_at: Optional[str] = None  # 过期时间
    version: int = 1            # 版本号
    checksum: str = ""          # 校验和
    
    def to_dict(self) -> Dict:
        return {
            "key": self.key,
            "value": self.value,
            "scope": self.scope.value,
            "priority": self.priority.value,
            "source_agent": self.source_agent,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "version": self.version,
            "checksum": self.checksum
        }
    
    def compute_checksum(self) -> str:
        """计算内容校验和"""
        content = json.dumps(self.value, sort_keys=True)
        return base64.b64encode(zlib.crc32(content.encode()).to_bytes(4, 'big')).decode()[:8]


@dataclass
class ContextBundle:
    """上下文包 - 用于agent间传递"""
    bundle_id: str
    session_id: str
    task_id: str
    from_agent: str
    to_agent: str
    items: List[ContextItem]
    created_at: str
    protocol_version: str = "1.0"
    
    def to_dict(self) -> Dict:
        return {
            "bundle_id": self.bundle_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "items": [item.to_dict() for item in self.items],
            "created_at": self.created_at,
            "protocol_version": self.protocol_version
        }
    
    def to_json(self) -> str:
        """序列化为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)
    
    def serialize_compressed(self) -> str:
        """压缩序列化（用于网络传输）"""
        json_str = self.to_json()
        compressed = zlib.compress(json_str.encode())
        return base64.b64encode(compressed).decode()
    
    @classmethod
    def deserialize_compressed(cls, data: str) -> 'ContextBundle':
        """解压缩反序列化"""
        compressed = base64.b64decode(data)
        json_str = zlib.decompress(compressed).decode()
        return cls.from_json(json_str)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'ContextBundle':
        """从JSON反序列化"""
        data = json.loads(json_str)
        items = [ContextItem(**item) for item in data.pop('items', [])]
        # 转换枚举
        for item in items:
            item.scope = ContextScope(item.scope)
            item.priority = ContextPriority(item.priority)
        data['items'] = items
        return cls(**data)


class ContextProtocol:
    """
    忘川上下文传递协议
    
    核心功能：
    1. 上下文注册与存储
    2. 上下文查询与检索
    3. 上下文打包与传递
    4. 上下文版本管理
    5. 上下文过期清理
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """
        初始化上下文协议
        
        Args:
            db_path: 数据库路径，默认 ~/.tiangong/context_protocol.db
        """
        if db_path is None:
            home = os.path.expanduser("~")
            db_dir = os.path.join(home, ".tiangong")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "context_protocol.db")
        
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            # 上下文项表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS context_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    source_agent TEXT NOT NULL,
                    session_id TEXT,
                    task_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    version INTEGER DEFAULT 1,
                    checksum TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 上下文传递记录
            conn.execute("""
                CREATE TABLE IF NOT EXISTS context_transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bundle_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    from_agent TEXT NOT NULL,
                    to_agent TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    serialized_data TEXT,
                    transferred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 创建索引
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_context_key 
                ON context_items(key, scope, session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_context_agent 
                ON context_items(source_agent)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_transfer_session 
                ON context_transfers(session_id, task_id)
            """)
            conn.commit()
    
    def register(self, item: ContextItem, session_id: str = "", task_id: str = ""):
        """
        注册上下文项
        
        Args:
            item: 上下文项
            session_id: 会话ID
            task_id: 任务ID
        """
        with self._lock:
            # 计算校验和
            item.checksum = item.compute_checksum()
            
            with sqlite3.connect(self.db_path) as conn:
                # 检查是否已存在
                cursor = conn.execute(
                    """SELECT id, version FROM context_items 
                       WHERE key = ? AND scope = ? AND session_id = ? AND task_id = ?""",
                    (item.key, item.scope.value, session_id, task_id)
                )
                existing = cursor.fetchone()
                
                if existing:
                    # 更新现有记录
                    new_version = existing[1] + 1
                    conn.execute(
                        """UPDATE context_items 
                           SET value = ?, priority = ?, version = ?, checksum = ?,
                               updated_at = CURRENT_TIMESTAMP
                           WHERE id = ?""",
                        (json.dumps(item.value), item.priority.value, 
                         new_version, item.checksum, existing[0])
                    )
                else:
                    # 插入新记录
                    conn.execute(
                        """INSERT INTO context_items 
                           (key, value, scope, priority, source_agent, session_id, task_id,
                            created_at, expires_at, version, checksum)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (item.key, json.dumps(item.value), item.scope.value,
                         item.priority.value, item.source_agent, session_id, task_id,
                         item.created_at, item.expires_at, item.version, item.checksum)
                    )
                
                conn.commit()
    
    def get(self, key: str, scope: ContextScope = None, 
            session_id: str = "", task_id: str = "") -> Optional[ContextItem]:
        """
        获取上下文项
        
        Args:
            key: 键名
            scope: 作用域（可选）
            session_id: 会话ID
            task_id: 任务ID
            
        Returns:
            ContextItem或None
        """
        with sqlite3.connect(self.db_path) as conn:
            if scope:
                cursor = conn.execute(
                    """SELECT key, value, scope, priority, source_agent, 
                              created_at, expires_at, version, checksum
                       FROM context_items 
                       WHERE key = ? AND scope = ? AND session_id = ? AND task_id = ?
                       ORDER BY version DESC LIMIT 1""",
                    (key, scope.value, session_id, task_id)
                )
            else:
                # 不指定scope时，按优先级查找
                cursor = conn.execute(
                    """SELECT key, value, scope, priority, source_agent, 
                              created_at, expires_at, version, checksum
                       FROM context_items 
                       WHERE key = ? AND session_id = ? AND task_id = ?
                       ORDER BY priority ASC, version DESC LIMIT 1""",
                    (key, session_id, task_id)
                )
            
            row = cursor.fetchone()
            if row:
                return ContextItem(
                    key=row[0],
                    value=json.loads(row[1]),
                    scope=ContextScope(row[2]),
                    priority=ContextPriority(row[3]),
                    source_agent=row[4],
                    created_at=row[5],
                    expires_at=row[6],
                    version=row[7],
                    checksum=row[8]
                )
            return None
    
    def get_context_for_agent(self, agent_id: str, session_id: str = "", 
                             task_id: str = "", 
                             min_priority: ContextPriority = ContextPriority.NORMAL) -> Dict[str, Any]:
        """
        获取传递给指定agent的上下文
        
        Args:
            agent_id: 目标agent ID
            session_id: 会话ID
            task_id: 任务ID
            min_priority: 最小优先级（只返回优先级高于此的）
            
        Returns:
            上下文字典
        """
        context = {}
        
        with sqlite3.connect(self.db_path) as conn:
            # 1. 获取全局上下文
            cursor = conn.execute(
                """SELECT key, value, priority FROM context_items 
                   WHERE scope = 'global' AND priority <= ?
                   ORDER BY priority ASC""",
                (min_priority.value,)
            )
            for row in cursor.fetchall():
                context[row[0]] = json.loads(row[1])
            
            # 2. 获取Agent级上下文
            cursor = conn.execute(
                """SELECT key, value, priority FROM context_items 
                   WHERE scope = 'agent' AND source_agent = ? AND priority <= ?
                   ORDER BY priority ASC""",
                (agent_id, min_priority.value)
            )
            for row in cursor.fetchall():
                context[row[0]] = json.loads(row[1])
            
            # 3. 获取会话级上下文
            if session_id:
                cursor = conn.execute(
                    """SELECT key, value, priority FROM context_items 
                       WHERE scope = 'session' AND session_id = ? AND priority <= ?
                       ORDER BY priority ASC""",
                    (session_id, min_priority.value)
                )
                for row in cursor.fetchall():
                    context[row[0]] = json.loads(row[1])
            
            # 4. 获取任务级上下文
            if task_id:
                cursor = conn.execute(
                    """SELECT key, value, priority FROM context_items 
                       WHERE scope = 'task' AND task_id = ? AND priority <= ?
                       ORDER BY priority ASC""",
                    (task_id, min_priority.value)
                )
                for row in cursor.fetchall():
                    context[row[0]] = json.loads(row[1])
        
        return context
    
    def create_bundle(self, from_agent: str, to_agent: str, 
                     session_id: str, task_id: str,
                     items: List[ContextItem]) -> ContextBundle:
        """
        创建上下文包
        
        Args:
            from_agent: 来源agent
            to_agent: 目标agent
            session_id: 会话ID
            task_id: 任务ID
            items: 上下文项列表
            
        Returns:
            ContextBundle
        """
        bundle_id = f"bundle_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{from_agent}_{to_agent}"
        
        bundle = ContextBundle(
            bundle_id=bundle_id,
            session_id=session_id,
            task_id=task_id,
            from_agent=from_agent,
            to_agent=to_agent,
            items=items,
            created_at=datetime.utcnow().isoformat()
        )
        
        # 记录传递
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO context_transfers 
                   (bundle_id, session_id, task_id, from_agent, to_agent, 
                    item_count, serialized_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (bundle.bundle_id, bundle.session_id, bundle.task_id,
                 bundle.from_agent, bundle.to_agent, len(bundle.items),
                 bundle.serialize_compressed())
            )
            conn.commit()
        
        return bundle
    
    def transfer(self, from_agent: str, to_agent: str, 
                session_id: str, task_id: str,
                keys: Optional[List[str]] = None,
                scopes: Optional[List[ContextScope]] = None) -> ContextBundle:
        """
        执行上下文传递
        
        Args:
            from_agent: 来源agent
            to_agent: 目标agent
            session_id: 会话ID
            task_id: 任务ID
            keys: 指定传递的键（None表示自动选择）
            scopes: 指定作用域（None表示全部）
            
        Returns:
            ContextBundle
        """
        items = []
        
        with sqlite3.connect(self.db_path) as conn:
            if keys:
                # 传递指定键
                for key in keys:
                    item = self.get(key, None, session_id, task_id)
                    if item:
                        items.append(item)
            else:
                # 自动选择：基于优先级和作用域
                scope_filter = ""
                params = [session_id, task_id, ContextPriority.NORMAL.value]
                
                if scopes:
                    scope_list = ','.join([f"'{s.value}'" for s in scopes])
                    scope_filter = f"AND scope IN ({scope_list})"
                
                cursor = conn.execute(
                    f"""SELECT key, value, scope, priority, source_agent, 
                              created_at, expires_at, version, checksum
                       FROM context_items 
                       WHERE (session_id = ? OR scope = 'global') 
                       AND (task_id = ? OR scope IN ('global', 'agent', 'session'))
                       AND priority <= ?
                       {scope_filter}
                       ORDER BY priority ASC, created_at DESC""",
                    params
                )
                
                seen_keys = set()
                for row in cursor.fetchall():
                    key = row[0]
                    if key not in seen_keys:
                        seen_keys.add(key)
                        items.append(ContextItem(
                            key=key,
                            value=json.loads(row[1]),
                            scope=ContextScope(row[2]),
                            priority=ContextPriority(row[3]),
                            source_agent=row[4],
                            created_at=row[5],
                            expires_at=row[6],
                            version=row[7],
                            checksum=row[8]
                        ))
        
        return self.create_bundle(from_agent, to_agent, session_id, task_id, items)
    
    def receive(self, bundle: ContextBundle) -> int:
        """
        接收上下文包
        
        Args:
            bundle: 上下文包
            
        Returns:
            接收的项数
        """
        count = 0
        for item in bundle.items:
            self.register(item, bundle.session_id, bundle.task_id)
            count += 1
        
        print(f"[WCP] 接收上下文包 {bundle.bundle_id}: {count} 项")
        return count
    
    def get_transfer_history(self, session_id: str = "", 
                            task_id: str = "") -> List[Dict]:
        """获取上下文传递历史"""
        with sqlite3.connect(self.db_path) as conn:
            if session_id and task_id:
                cursor = conn.execute(
                    """SELECT bundle_id, from_agent, to_agent, item_count, transferred_at
                       FROM context_transfers 
                       WHERE session_id = ? AND task_id = ?
                       ORDER BY transferred_at DESC""",
                    (session_id, task_id)
                )
            elif session_id:
                cursor = conn.execute(
                    """SELECT bundle_id, from_agent, to_agent, item_count, transferred_at
                       FROM context_transfers 
                       WHERE session_id = ?
                       ORDER BY transferred_at DESC""",
                    (session_id,)
                )
            else:
                cursor = conn.execute(
                    """SELECT bundle_id, from_agent, to_agent, item_count, transferred_at
                       FROM context_transfers 
                       ORDER BY transferred_at DESC LIMIT 100"""
                )
            
            return [
                {
                    'bundle_id': row[0],
                    'from_agent': row[1],
                    'to_agent': row[2],
                    'item_count': row[3],
                    'transferred_at': row[4]
                }
                for row in cursor.fetchall()
            ]
    
    def cleanup_expired(self):
        """清理过期上下文"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """DELETE FROM context_items 
                   WHERE expires_at IS NOT NULL 
                   AND expires_at < datetime('now')"""
            )
            deleted = cursor.rowcount
            conn.commit()
        
        if deleted > 0:
            print(f"[WCP] 清理 {deleted} 项过期上下文")
        return deleted
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with sqlite3.connect(self.db_path) as conn:
            stats = {}
            
            # 总上下文项数
            cursor = conn.execute("SELECT COUNT(*) FROM context_items")
            stats['total_items'] = cursor.fetchone()[0]
            
            # 各作用域数量
            cursor = conn.execute(
                "SELECT scope, COUNT(*) FROM context_items GROUP BY scope"
            )
            stats['by_scope'] = {row[0]: row[1] for row in cursor.fetchall()}
            
            # 传递记录数
            cursor = conn.execute("SELECT COUNT(*) FROM context_transfers")
            stats['total_transfers'] = cursor.fetchone()[0]
            
            # 今日传递数
            cursor = conn.execute(
                """SELECT COUNT(*) FROM context_transfers 
                   WHERE date(transferred_at) = date('now')"""
            )
            stats['today_transfers'] = cursor.fetchone()[0]
            
            return stats


# 便捷函数
def create_context_item(key: str, value: Any, source_agent: str,
                       scope: ContextScope = ContextScope.TASK,
                       priority: ContextPriority = ContextPriority.NORMAL,
                       expires_in_minutes: Optional[int] = None) -> ContextItem:
    """创建上下文项的便捷函数"""
    now = datetime.utcnow()
    expires = None
    if expires_in_minutes:
        expires = (now + timedelta(minutes=expires_in_minutes)).isoformat()
    
    return ContextItem(
        key=key,
        value=value,
        scope=scope,
        priority=priority,
        source_agent=source_agent,
        created_at=now.isoformat(),
        expires_at=expires
    )


# 使用示例
if __name__ == "__main__":
    wcp = ContextProtocol()
    
    print("=== 忘川上下文协议测试 ===\n")
    
    # 注册上下文
    print("1. 注册上下文项")
    item1 = create_context_item(
        key="user_preference",
        value={"language": "zh-CN", "style": "detailed"},
        source_agent="tianxin",
        scope=ContextScope.SESSION,
        priority=ContextPriority.HIGH
    )
    wcp.register(item1, session_id="sess_001", task_id="task_001")
    
    item2 = create_context_item(
        key="search_results",
        value={"urls": ["http://example.com/1", "http://example.com/2"]},
        source_agent="agent_search",
        scope=ContextScope.TASK,
        priority=ContextPriority.CRITICAL
    )
    wcp.register(item2, session_id="sess_001", task_id="task_001")
    
    print("   ✓ 注册完成\n")
    
    # 查询上下文
    print("2. 查询上下文")
    ctx = wcp.get_context_for_agent(
        agent_id="agent_analyze",
        session_id="sess_001",
        task_id="task_001"
    )
    print(f"   获取到 {len(ctx)} 项上下文")
    for k, v in ctx.items():
        print(f"   - {k}: {v}")
    print()
    
    # 上下文传递
    print("3. 上下文传递")
    bundle = wcp.transfer(
        from_agent="agent_search",
        to_agent="agent_analyze",
        session_id="sess_001",
        task_id="task_001"
    )
    print(f"   创建上下文包: {bundle.bundle_id}")
    print(f"   包含 {len(bundle.items)} 项\n")
    
    # 序列化/反序列化
    print("4. 序列化测试")
    compressed = bundle.serialize_compressed()
    print(f"   压缩后长度: {len(compressed)} 字符")
    restored = ContextBundle.deserialize_compressed(compressed)
    print(f"   反序列化成功: {restored.bundle_id}\n")
    
    # 统计
    print("5. 统计信息")
    stats = wcp.get_stats()
    print(f"   {stats}\n")
    
    print("=== 测试完成 ===")
