"""
忘川 LLM 记忆增强模块

实现 Mem0 风格的自动事实提取功能：
- 每次 remember 自动 LLM 提取事实
- 多级记忆支持 (User/Session/Agent)
- 实体链接与图谱增强

用法：
    from wangchuan.v3.llm_memory import LLM MemoryMixin
    
    memory = Memory()
    memory.remember_with_extraction(
        "用户说他喜欢喝冰美式，不吃辣，晚上一般10点下班",
        user_id="user123",
        session_id="session456"
    )
"""

import os
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import logging

from wangchuan.paths import default_db_path


logger = logging.getLogger(__name__)


class LLMExtractor:
    """LLM 事实提取器"""
    
    def __init__(self, api_key: str = None, provider: str = "openai"):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        self.provider = provider
    
    def extract(self, text: str, extraction_type: str = "auto") -> List[Dict[str, Any]]:
        """
        从文本中提取结构化事实
        
        Args:
            text: 输入文本
            extraction_type: auto/preference/fact/rule
            
        Returns:
            [{"content": str, "type": str, "importance": float, "entities": list}, ...]
        """
        if not self.api_key:
            return []
        
        prompt = self._build_prompt(text, extraction_type)
        
        try:
            if self.provider == "openai" or "OPENAI" in os.getenv("OPENAI_API_KEY", ""):
                import openai
                client = openai.OpenAI(api_key=self.api_key)
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=1500
                )
                content = response.choices[0].message.content
                
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]
                
                results = json.loads(content.strip())
                return results if isinstance(results, list) else [results]
            
            elif self.provider == "anthropic" or "anthropic" in str(os.getenv("ANTHROPIC_API_KEY", "")):
                import anthropic
                client = anthropic.Anthropic(api_key=self.api_key)
                response = client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=1500,
                    messages=[{"role": "user", "content": prompt}]
                )
                content = response.content[0].text
                
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                
                results = json.loads(content.strip())
                return results if isinstance(results, list) else [results]
                
        except Exception as e:
            print(f"[LLMExtractor] 提取失败: {e}")
            return []
    
    def _build_prompt(self, text: str, extraction_type: str) -> str:
        if extraction_type == "auto":
            return f"""从以下对话/文本中提取所有有价值的信息。

要求：
1. 提取用户偏好 (preference)：喜欢/不喜欢、习惯、风格等
2. 提取事实 (fact)：个人信息、经历、知识等
3. 提取规则 (rule)：要求、限制、约定等
4. 提取实体 (entities)：人名、地名、物品等

返回 JSON 数组格式：
[
  {{
    "content": "提取的信息",
    "type": "preference|fact|rule",
    "importance": 0.0-1.0,
    "entities": ["实体1", "实体2"]
  }}
]

文本：
{text}"""
        return f"""从以下文本中提取{extraction_type}信息，返回 JSON 数组："""


class MultiLevelMemory:
    """多级记忆管理器 (User/Session/Agent)
    
    对标 Mem0 的多级记忆：
    - User Memory: 用户级偏好和属性
    - Session Memory: 当前会话的上下文
    - Agent Memory: 智能体自身状态和知识
    """
    
    LEVELS = ["user", "session", "agent", "extracted"]
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            from wangchuan.paths import default_db_path
            db_path = str(default_db_path())
        self.db_path = db_path
        self._ensure_tables()
    
    def _ensure_tables(self):
        """确保实体表和 scoped sidecar 索引存在。"""
        if not os.path.exists(self.db_path):
            return

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity TEXT NOT NULL,
                    memory_id INTEGER,
                    created_at TEXT,
                    FOREIGN KEY (memory_id) REFERENCES memories(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_scope_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL,
                    scope_level TEXT NOT NULL,
                    scope_value TEXT NOT NULL,
                    created_at TEXT,
                    UNIQUE(scope_level, scope_value, memory_id),
                    FOREIGN KEY (memory_id) REFERENCES memories(id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity ON memory_entities(entity)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_scope_lookup
                ON memory_scope_index(scope_level, scope_value, memory_id)
            """)
            self._ensure_memory_entities_unique_constraint(conn)
            conn.commit()
        except Exception as e:
            logger.warning("[MultiLevelMemory] ensure tables failed: %s", e)
        finally:
            conn.close()

    def _ensure_memory_entities_unique_constraint(self, conn: sqlite3.Connection) -> None:
        """补 memory_entities 幂等唯一约束，并兼容旧库迁移。"""
        indexes = conn.execute("PRAGMA index_list(memory_entities)").fetchall()
        has_unique = any(str(row[1]) == "uq_memory_entities_entity_memory" for row in indexes)
        if has_unique:
            return

        conn.execute(
            """
            DELETE FROM memory_entities
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM memory_entities
                GROUP BY entity, memory_id
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_entities_entity_memory
            ON memory_entities(entity, memory_id)
            """
        )
    
    def add_user_memory(self, user_id: str, content: str, **kwargs) -> Dict:
        """添加用户级记忆"""
        kwargs["user_id"] = user_id
        return self._add_memory(content, "user", **kwargs)
    
    def add_session_memory(self, session_id: str, content: str, **kwargs) -> Dict:
        """添加会话级记忆"""
        kwargs["session_id"] = session_id
        return self._add_memory(content, "session", **kwargs)
    
    def add_agent_memory(self, agent_id: str, content: str, **kwargs) -> Dict:
        """添加智能体级记忆"""
        kwargs["agent_id"] = agent_id
        return self._add_memory(content, "agent", **kwargs)
    
    def _add_memory_scope_index(self, memory_id: int, scope_level: str, scope_value: str, conn: sqlite3.Connection | None = None) -> None:
        """为主链写入补充 scoped sidecar 索引。"""
        normalized_level = str(scope_level or "").strip().lower()
        normalized_value = str(scope_value or "").strip()
        if normalized_level not in {"user", "session", "agent"} or not normalized_value:
            return
        if not os.path.exists(self.db_path):
            return

        owns_conn = conn is None
        if owns_conn:
            conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_scope_index (memory_id, scope_level, scope_value, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (memory_id, normalized_level, normalized_value, datetime.now().isoformat())
            )
            if owns_conn:
                conn.commit()
        finally:
            if owns_conn:
                conn.close()

    def _add_memory(self, content: str, level: str, **metadata) -> Dict:
        """通用记忆添加，统一委托 memory_api 主写入链。"""
        if not os.path.exists(self.db_path):
            return {"success": False, "error": "数据库不存在"}

        importance = float(metadata.get("importance", 0.7) or 0.7)
        scope_value = None
        if level == "user":
            scope_value = str(metadata.get("user_id") or "").strip()
        elif level == "session":
            scope_value = str(metadata.get("session_id") or "").strip()
        elif level == "agent":
            scope_value = str(metadata.get("agent_id") or "").strip()

        if level in {"user", "session", "agent"} and not scope_value:
            return {"success": False, "error": f"{level}_id 不能为空"}

        from wangchuan.memory_api import Memory

        semantic_type = str(metadata.get("memory_type") or ("extracted" if level == "extracted" else "memory")).strip().lower()
        extracted_entities = list(metadata.get("entities", []) or metadata.get("extracted_entities", []) or [])
        base_tags = [level, "llm_memory_adapter"]
        if level == "agent":
            base_tags.append("memory")
        elif level in {"user", "session"}:
            base_tags.append("user")

        remember_result = Memory(db_path=self.db_path).remember(
            content=content,
            importance=importance,
            tags=base_tags,
            metadata={
                "source_layer": str(metadata.get("source_layer") or "scar").strip().lower(),
                "memory_type": semantic_type,
                "scope_level": level if level in {"user", "session", "agent"} else "",
                "scope_value": scope_value or "",
                "scope_user_id": str(metadata.get("user_id") or "").strip(),
                "scope_session_id": str(metadata.get("session_id") or "").strip(),
                "scope_agent_id": str(metadata.get("agent_id") or "").strip(),
                "extracted_entities": extracted_entities,
                "hot_memory_candidate": metadata.get("hot_memory_candidate", semantic_type in {"preference", "rule", "lesson", "decision", "memory"}),
                "extracted_from": metadata.get("extracted_from"),
            },
        )
        if not remember_result.get("success"):
            return {"success": False, "error": remember_result.get("message") or remember_result.get("error") or "memory_api.remember failed"}
        return {
            "success": True,
            "memory_id": remember_result.get("memory_id"),
            "level": level,
            "scope_value": scope_value,
        }
    
    def _query_by_scope(self, level: str, scope_value: Optional[str], limit: int = 10) -> List[Dict]:
        if not os.path.exists(self.db_path):
            return []

        normalized_scope = str(scope_value or "").strip()
        if level in {"user", "session", "agent"} and not normalized_scope:
            return []

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT m.*, si.scope_level, si.scope_value
                FROM memories m
                JOIN memory_scope_index si ON si.memory_id = m.id
                WHERE si.scope_level = ?
                  AND si.scope_value = ?
                ORDER BY m.confidence DESC, m.created_at DESC
                LIMIT ?
                """,
                (level, normalized_scope, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def query_by_user(self, user_id: str, limit: int = 10) -> List[Dict]:
        """查询指定用户的 scoped 记忆。"""
        return self._query_by_scope("user", user_id, limit=limit)

    def query_by_session(self, session_id: str = None, limit: int = 10) -> List[Dict]:
        """查询指定会话的 scoped 记忆。"""
        return self._query_by_scope("session", session_id, limit=limit)

    def query_by_agent(self, agent_id: str = None, limit: int = 10) -> List[Dict]:
        """查询指定智能体的 scoped 记忆。"""
        return self._query_by_scope("agent", agent_id, limit=limit)

    def query_by_level(self, level: str, scope_value: Optional[str] = None, limit: int = 10) -> List[Dict]:
        """按级别查询记忆。user/session/agent 必须提供对应 scope_value。"""
        if level not in self.LEVELS:
            return []
        if level in {"user", "session", "agent"}:
            return self._query_by_scope(level, scope_value, limit=limit)

        if not os.path.exists(self.db_path):
            return []

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT * FROM memories WHERE temperature = ? ORDER BY created_at DESC LIMIT ?""",
                (level, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    
    def get_level_stats(self) -> Dict[str, int]:
        """获取各级别记忆统计。scoped level 通过统一 sidecar 统计。"""
        if not os.path.exists(self.db_path):
            return {}

        conn = sqlite3.connect(self.db_path)
        try:
            stats = {}
            for level in self.LEVELS:
                if level in {"user", "session", "agent"}:
                    count = conn.execute(
                        "SELECT COUNT(DISTINCT memory_id) FROM memory_scope_index WHERE scope_level = ?",
                        (level,)
                    ).fetchone()[0]
                elif level == "extracted":
                    try:
                        count = conn.execute(
                            "SELECT COUNT(*) FROM memory_schema_index WHERE memory_type = 'extracted'"
                        ).fetchone()[0]
                    except sqlite3.OperationalError:
                        count = 0
                else:
                    count = 0
                stats[level] = count
            return stats
        finally:
            conn.close()


class EntityLinker:
    """实体链接器 - 将提取的实体与现有记忆关联"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = str(default_db_path())
        self.db_path = db_path
    
    def link_entity(self, entity: str, memory_id: int) -> Dict:
        """将实体与记忆关联"""
        if not os.path.exists(self.db_path):
            return {"success": False}
        
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT OR IGNORE INTO memory_entities (entity, memory_id, created_at)
                   VALUES (?, ?, ?)""",
                (entity.lower(), memory_id, datetime.now().isoformat())
            )
            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            conn.close()
    
    def find_related_memories(self, entity: str, limit: int = 5) -> List[Dict]:
        """查找关联记忆"""
        if not os.path.exists(self.db_path):
            return []
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT m.* FROM memories m
                   JOIN memory_entities e ON m.id = e.memory_id
                   WHERE e.entity = ?
                   ORDER BY m.confidence DESC
                   LIMIT ?""",
                (entity.lower(), limit)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def create_memory_with_extraction(
    content: str,
    user_id: str = None,
    session_id: str = None,
    agent_id: str = None,
    auto_extract: bool = True,
    db_path: str = None
) -> Dict[str, Any]:
    """
    带 LLM 提取的记忆创建。

    当前职责已收口到 memory_api 主写链：
    1. 本模块负责提取结构化事实
    2. memory_api.Memory.remember() 负责真正落库
    3. user/session/agent scope 通过 metadata.sidecar 写入统一主链索引
    """
    if db_path is None:
        db_path = str(default_db_path())

    extractor = LLMExtractor()
    from wangchuan.memory_api import Memory
    memory = Memory(db_path=db_path)

    if auto_extract:
        extracted_facts = extractor.extract(content)
    else:
        extracted_facts = []

    if not extracted_facts:
        extracted_facts = [{
            "content": content,
            "type": "memory",
            "importance": 0.6,
            "entities": []
        }]

    stored_memories = []

    for fact in extracted_facts:
        fact_content = fact.get("content", "")
        fact_type = str(fact.get("type", "memory") or "memory").strip().lower()
        importance = float(fact.get("importance", 0.6) or 0.6)
        entities = list(fact.get("entities", []) or [])

        metadata = {
            "source_layer": "scar",
            "memory_type": fact_type,
            "extracted_from": "llm" if extractor.api_key else "direct",
            "scope_level": "user" if user_id else "session" if session_id else "agent" if agent_id else "extracted",
            "scope_value": str(user_id or session_id or agent_id or "").strip(),
            "scope_user_id": str(user_id or "").strip(),
            "scope_session_id": str(session_id or "").strip(),
            "scope_agent_id": str(agent_id or "").strip(),
            "extracted_entities": entities,
            "hot_memory_candidate": fact_type in {"preference", "rule", "lesson", "decision", "memory"},
        }

        tags = [fact_type, "llm_extracted"]
        if user_id:
            tags.append("user")
        elif session_id:
            tags.append("session")
        elif agent_id:
            tags.append("agent")

        result = memory.remember(
            content=fact_content,
            importance=importance,
            tags=tags,
            metadata=metadata,
        )

        if result.get("success"):
            stored_memories.append({
                "memory_id": result.get("memory_id"),
                "content": fact_content,
                "memory_type": fact_type,
            })

    return {
        "success": True,
        "original_content": content,
        "extracted": extracted_facts,
        "stored": stored_memories,
    }


if __name__ == "__main__":
    print("🧠 忘川 LLM 记忆增强模块")
    print("=" * 50)
    
    result = create_memory_with_extraction(
        "用户说：他叫张三，喜欢喝冰美式，不吃辣。工作在望京。"
    )
    
    print(f"原始内容: {result['original_content']}")
    print(f"提取事实: {len(result['extracted'])} 条")
    for fact in result["extracted"]:
        print(f"  - [{fact['type']}] {fact['content']} (importance: {fact['importance']})")
    print(f"存储记忆: {len(result['stored'])} 条")
