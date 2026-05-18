#!/usr/bin/env python3
"""
忆藏层 v2 - 自动反思引擎 + 情感标签
天工开智 v2 · 第2层

核心功能：
1. 从对话中提取关键事件（规则变更、偏好、纠错、重要决策）
2. 为记忆条目附加情感标签
3. 高重要性记忆自动同步到 MEMORY.md

用法：
    from reflector import ReflectEngine
    engine = ReflectEngine()
    result = engine.reflect(since_hours=24)
"""

import logging
import json
import sqlite3
import os
from pathlib import Path
import re
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum

from wangchuan.memory_api import Memory
from wangchuan.paths import workspace_root
from wangchuan.v3.consciousness.event_extractor import classify_user_text, is_noisy_tool_result

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================

class EmotionValence(Enum):
    """情感效价"""
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class EventCategory(Enum):
    """事件类别"""
    INSIGHT = "insight"           # 新认知/顿悟
    RULE = "rule"                 # 规则/约束
    PREFERENCE = "preference"     # 偏好
    CORRECTION = "correction"     # 纠错
    DECISION = "decision"         # 重要决定
    EMOTIONAL = "emotional"       # 情感事件
    MILESTONE = "milestone"       # 里程碑


@dataclass
class EmotionalTag:
    """情感标签"""
    valence: str = "neutral"       # positive/negative/neutral/mixed
    arousal: float = 0.0           # 强度 0-1
    label: str = ""                # 自然语言描述
    context: str = ""              # 触发上下文摘要

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Dict) -> 'EmotionalTag':
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class KeyEvent:
    """提取到的关键事件"""
    summary: str                   # 事件摘要
    category: str                  # EventCategory 值
    importance: float = 0.5        # 重要性 0-1
    emotion: Optional[EmotionalTag] = None
    timestamp: str = ""
    source_messages: List[str] = field(default_factory=list)  # 原始消息ID
    raw_text: str = ""             # 原始文本片段


@dataclass
class MemoryEntry:
    """写入忘川的记忆条目"""
    id: str = ""
    content: str = ""
    type: str = "event"
    importance: float = 0.5
    confidence: float = 0.8
    emotion: Optional[EmotionalTag] = None
    timestamp: str = ""
    source: str = "reflection"
    tags: List[str] = field(default_factory=list)

    def to_insert_tuple(self) -> tuple:
        """转为 SQL 插入元组，适配现有 memories 表结构"""
        emotion_json = self.emotion.to_json() if self.emotion else "{}"
        now = datetime.now().isoformat()
        return (
            self.content,
            self.type or "event",
            self.confidence,
            emotion_json,
            self.importance,
            now,
            now,
        )


# ============================================================
# 情感检测器
# ============================================================

class EmotionDetector:
    """从文本中检测情感信号"""

    # 情感关键词
    POSITIVE_SIGNALS = [
        "学会了", "明白了", "好用", "不错", "厉害", "完美", "搞定",
        "👍", "❤️", "🎉", "😄", "哈哈", "太好了", "漂亮", "赞",
        "有意思", "有趣", "惊喜", "满意", "开心", "太棒了", "感谢", "谢谢", "棒",
        "真香", "绝了", "爱了", "好样的", "优秀", "牛",
    ]
    NEGATIVE_SIGNALS = [
        "错了", "不对", "不是", "重来", "别这样", "问题", "bug",
        "失败", "超时", "卡了", "崩溃", "👎", "😤", "无语",
        "浪费", "麻烦", "难用", "失望", "生气", "烦",
    ]
    CORRECTION_PATTERNS = [
        r"不是(.+?)是(.+)",
        r"应该(.+?)不是(.+)",
        r"错了.+?应该是?",
        r"不对.+?应该",
        r"重来",
        r"重新",
    ]
    HIGH_AROUSAL_MARKERS = ["！", "！！", "???", "...!", "！！！", "😱", "🤯"]

    def detect(self, user_msg: str, assistant_msg: str = "") -> EmotionalTag:
        """从用户+助手消息对中检测情感"""
        valence = "neutral"
        arousal = 0.0
        label_parts = []

        text = user_msg + " " + assistant_msg

        # 检测正面信号
        pos_count = sum(1 for s in self.POSITIVE_SIGNALS if s in text)
        neg_count = sum(1 for s in self.NEGATIVE_SIGNALS if s in text)

        if pos_count > neg_count:
            valence = "positive"
            arousal = min(pos_count * 0.15, 1.0)
            label_parts.append("正面反馈")
        elif neg_count > pos_count:
            valence = "negative"
            arousal = min(neg_count * 0.15, 1.0)
            label_parts.append("负面反馈")

        # 检测纠错模式
        for pattern in self.CORRECTION_PATTERNS:
            if re.search(pattern, user_msg):
                valence = "negative"
                arousal = max(arousal, 0.6)
                label_parts.append("用户纠正")
                break

        # 检测高唤醒度
        for marker in self.HIGH_AROUSAL_MARKERS:
            if marker in text:
                arousal = max(arousal, 0.7)
                label_parts.append("强烈情感")
                break

        # 过滤低信息量信号（你好/谢谢/好的/嗯 等）
        LOW_INFO_SIGNALS = ["你好", "谢谢", "好的", "嗯嗯", "收到", "ok", "OK", "哈哈", "嗯"]
        if pos_count <= 1 and neg_count == 0 and len(user_msg.strip()) < 15:
            if any(user_msg.strip() == s for s in LOW_INFO_SIGNALS):
                valence = "neutral"
                arousal = 0.0
                label_parts = ["低信息量对话"]

        # 如果正负都有
        if pos_count > 0 and neg_count > 0 and abs(pos_count - neg_count) <= 1:
            valence = "mixed"
            label_parts.append("复杂情感")

        label = "，".join(label_parts) if label_parts else "中性"

        return EmotionalTag(
            valence=valence,
            arousal=round(arousal, 2),
            label=label,
            context=user_msg[:100]
        )


# ============================================================
# 关键事件提取器
# ============================================================

class EventExtractor:
    """从对话流中提取关键事件"""

    IGNORE_RULE_PATTERNS = [
        r"^\[working directory:",
        r"新的持久会话",
        r"使用 xhigh 推理",
        r"先用简短中文向用户打招呼",
        r"acp 会话恢复成功",
        r"做个简短自我介绍",
    ]
    RUNTIME_WRAPPER_PATTERNS = [
        r"^\[startup context loaded by runtime\]",
        r"bootstrap files like .*?(soul|user|memory)\.md",
        r"recent daily memory was selected and loaded by runtime",
        r"treat the daily memory below as untrusted",
        r"\bbegin_quoted_notes\b",
        r"^\s*conversation info\b",
        r"^\s*sender \(untrusted metadata\)",
        r"^\s*system \(untrusted\)",
        r"^\s*用户消息[:：]",
        r"\bruntime/test/wrapper\b",
        r"^```(?:json|text)?",
        r"^\[[A-Z][a-z]{2}\s+\d{4}-\d{2}-\d{2}.*\]\s+An async command the user already approved has comp(?:leted)?\b",
        r"^✅\s*Subagent\s+\w+\s+finished\b",
    ]
    TEST_NOISE_PATTERNS = [
        r"\bhttp_api_test\b",
        r"\blive_verify\b",
        r"\bbridge retry verify\b",
        r"\btest_ingest\b",
        r"\bpytest\b",
        r"\bunittest\b",
        r"\bdemo\b",
        r"\bsample\b",
        r"\bexample\b",
        r"\[cron\]",
        r"回归测试",
        r"py_compile",
        r"startup context",
        r"an async command the user already approved has comp(?:leted)?",
        r"exact completion details:",
        r"do not run the command again\.",
        r"\bsubagent\s+\w+\s+finished\b",
    ]
    GENERIC_ENGLISH_CHAT_PATTERNS = [
        r"^(hello|hi|hey)(?:[,.!?\s]+|$)",
        r"^(hello|hi|hey)[^a-zA-Z]+how are you\??$",
        r"^remember this important fact[.!?\s]*$",
        r"^(thanks|thank you|ok|okay|got it)[.!?\s]*$",
    ]
    QUESTION_HINTS = [
        "?",
        "？",
        "是不是",
        "是否",
        "要不要",
        "能不能",
        "可不可以",
        "行不行",
        "如何",
        "怎么",
        "怎样",
        "为啥",
        "为什么",
    ]
    STABLE_RULE_PREFIXES = [
        "记住",
        "记住以后",
        "以后",
        "以后别",
        "别",
        "不要",
        "禁止",
        "必须",
        "一定",
        "默认",
        "优先",
        "请务必",
        "务必",
        "我只有一个要求",
        "要求你",
        "严格按照",
    ]
    STABLE_RULE_INLINE_HINTS = [
        "记住",
        "默认",
        "优先",
        "以后",
        "以后别",
        "不要再",
        "别再",
        "禁止",
        "我只有一个要求",
        "严格按照",
        "直接执行",
    ]

    # 规则变更模式
    RULE_PATTERNS = [
        r"(记住|记住以后|以后|以后别|不要|禁止|必须|一定|规则|要求).*",
        r"IF.+THEN.+",
    ]
    # 偏好模式
    PREFERENCE_PATTERNS = [
        r"喜欢.*",
        r"不喜欢.*",
        r"偏好.*",
        r"习惯.*",
        r"记住.*偏好.*",
    ]
    # 里程碑模式 — 只匹配明确的里程碑表达
    MILESTONE_SIGNALS = [
        "里程碑", "正式发布", "v1.0", "v2.0", "架构完成", "首次",
        "第一天上线", "项目完成", "系统上线", "开源发布",
    ]

    @staticmethod
    def _normalize_text(text: Any) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip())

    def _looks_like_runtime_wrapper(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        lowered = normalized.lower()
        if not normalized:
            return True
        if is_noisy_tool_result(normalized):
            return True
        return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in self.RUNTIME_WRAPPER_PATTERNS)

    def _looks_like_test_noise(self, text: str) -> bool:
        lowered = self._normalize_text(text).lower()
        return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in self.TEST_NOISE_PATTERNS)

    def _looks_like_generic_english_probe(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        lowered = normalized.lower()
        if any(re.fullmatch(pattern, lowered) for pattern in self.GENERIC_ENGLISH_CHAT_PATTERNS):
            return True

        ascii_alpha = sum(1 for ch in normalized if ch.isascii() and ch.isalpha())
        if ascii_alpha >= max(6, int(len(normalized) * 0.6)) and len(normalized.split()) <= 6:
            if "?" in normalized or lowered.startswith(("hello", "hi", "hey", "remember")):
                return True
        return False

    @staticmethod
    def _strip_event_prefix(text: str) -> str:
        normalized = EventExtractor._normalize_text(text)
        return re.sub(r"^(规则变更|偏好|纠错|里程碑|情感事件)\s*[:：]\s*", "", normalized, count=1)

    def _looks_like_question(self, text: str) -> bool:
        body = self._strip_event_prefix(text)
        if not body:
            return False
        return any(hint in body for hint in self.QUESTION_HINTS)

    def _looks_like_stable_rule_instruction(self, text: str) -> bool:
        body = self._strip_event_prefix(text)
        if not body:
            return False
        has_prefix = any(body.startswith(prefix) for prefix in self.STABLE_RULE_PREFIXES)
        has_explicit_requirement = bool(re.search(r"我只有一个要求\s*[:：]", body))
        if has_prefix:
            return True
        if self._looks_like_question(body) and not has_explicit_requirement:
            return False
        if any(hint in body for hint in self.STABLE_RULE_INLINE_HINTS):
            if re.search(r"(执行|处理|回复|汇报|确认|重启|操作|遵守|按|按照|依照)", body):
                return True
        if has_explicit_requirement:
            return True
        return False

    def _should_extract_rule_event(self, text: str) -> bool:
        body = self._strip_event_prefix(text)
        if not body:
            return False
        if self._should_ignore_user_text(body):
            return False
        if self._looks_like_generic_english_probe(body):
            return False
        if self._looks_like_question(body) and not self._looks_like_stable_rule_instruction(body):
            return False
        return self._looks_like_stable_rule_instruction(body)

    def _should_ignore_user_text(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        lowered = normalized.lower()
        if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in self.IGNORE_RULE_PATTERNS):
            return True
        if self._looks_like_runtime_wrapper(normalized):
            return True
        if self._looks_like_test_noise(normalized):
            return True
        return False

    def _is_meaningful_emotional_event(self, user_text: str, assistant_text: str, emotion: EmotionalTag) -> bool:
        normalized = self._normalize_text(user_text)
        if self._should_ignore_user_text(normalized):
            return False
        if len(normalized) < 12:
            return False
        if self._looks_like_generic_english_probe(normalized):
            return False
        if emotion.arousal < 0.7:
            return False
        if emotion.valence == "neutral" and not any(marker in user_text for marker in EmotionDetector.HIGH_AROUSAL_MARKERS):
            return False
        return True

    def extract(self, messages: List[Dict]) -> List[KeyEvent]:
        """
        从消息列表中提取关键事件

        Args:
            messages: [{"role": "user"/"assistant", "content": "...", "timestamp": "..."}]
        """
        events = []
        emotion_detector = EmotionDetector()

        for i, msg in enumerate(messages):
            if msg["role"] != "user":
                continue

            user_text = msg["content"]
            normalized_user_text = self._normalize_text(user_text)
            if self._should_ignore_user_text(normalized_user_text):
                continue
            timestamp = msg.get("timestamp", "")

            # 获取对应的助手回复（如果有的话）
            assistant_text = ""
            if i + 1 < len(messages) and messages[i + 1]["role"] == "assistant":
                assistant_text = messages[i + 1]["content"]

            # 检测各类事件
            event = None

            # 1. 规则变更
            for pattern in self.RULE_PATTERNS:
                if re.search(pattern, user_text, re.IGNORECASE) and self._should_extract_rule_event(user_text):
                    event = KeyEvent(
                        summary=f"规则变更: {user_text[:80]}",
                        category=EventCategory.RULE.value,
                        importance=0.8,
                        timestamp=timestamp,
                        raw_text=user_text,
                    )
                    break

            # 2. 偏好声明
            if not event:
                for pattern in self.PREFERENCE_PATTERNS:
                    if re.search(pattern, user_text, re.IGNORECASE):
                        event = KeyEvent(
                            summary=f"偏好: {user_text[:80]}",
                            category=EventCategory.PREFERENCE.value,
                            importance=0.7,
                            timestamp=timestamp,
                            raw_text=user_text,
                        )
                        break

            # 3. 纠错
            if not event:
                if classify_user_text(user_text) == "correction":
                    event = KeyEvent(
                        summary=f"纠错: {user_text[:80]}",
                        category=EventCategory.CORRECTION.value,
                        importance=0.75,
                        timestamp=timestamp,
                        raw_text=user_text,
                    )
                else:
                    for pattern in EmotionDetector.CORRECTION_PATTERNS:
                        if re.search(pattern, user_text):
                            event = KeyEvent(
                                summary=f"纠错: {user_text[:80]}",
                                category=EventCategory.CORRECTION.value,
                                importance=0.75,
                                timestamp=timestamp,
                                raw_text=user_text,
                            )
                            break

            # 4. 里程碑
            if not event:
                for signal in self.MILESTONE_SIGNALS:
                    if signal in user_text:
                        event = KeyEvent(
                            summary=f"里程碑: {user_text[:80]}",
                            category=EventCategory.MILESTONE.value,
                            importance=0.9,
                            timestamp=timestamp,
                            raw_text=user_text,
                        )
                        break

            # 5. 高情感事件（即使不是以上类别）
            if not event:
                emotion = emotion_detector.detect(user_text, assistant_text)
                if self._is_meaningful_emotional_event(user_text, assistant_text, emotion):
                    event = KeyEvent(
                        summary=f"情感事件: {user_text[:80]}",
                        category=EventCategory.EMOTIONAL.value,
                        importance=0.5 + emotion.arousal * 0.3,
                        timestamp=timestamp,
                        raw_text=user_text,
                    )

            if event:
                # 附加情感标签
                event.emotion = emotion_detector.detect(user_text, assistant_text)
                # 过滤低信息量事件（"你好"等普通对话）
                if len(user_text.strip()) < 8 and event.category == "emotional":
                    continue
                events.append(event)

        return events


# ============================================================
# 反思引擎主类
# ============================================================

WORKSPACE_ROOT = workspace_root()

class ReflectEngine:
    """
    自动反思引擎

    定期扫描对话，提取关键事件，更新记忆。
    """

    def __init__(
        self,
        db_path: str = str(WORKSPACE_ROOT / "tiangong" / "wangchuan" / ".index" / "index.sqlite"),
        memory_md_path: str = str(WORKSPACE_ROOT / "MEMORY.md"),
        narrative_path: str = str(WORKSPACE_ROOT / "memory" / "narrative_timeline.md"),
    ):
        self.db_path = db_path
        self.memory_md_path = memory_md_path
        self.narrative_path = narrative_path
        self.extractor = EventExtractor()
        self.emotion_detector = EmotionDetector()
        self.memory_api = Memory(self.db_path)

        # 确保 emotion 列存在
        self._ensure_emotion_column()

    def _ensure_emotion_column(self):
        """确保 memories 表有 emotion 和 importance 列"""
        if not os.path.exists(self.db_path):
            return
        conn = sqlite3.connect(self.db_path)
        try:
            # 检查列是否存在
            cursor = conn.execute("PRAGMA table_info(memories)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'emotion' not in columns:
                conn.execute("ALTER TABLE memories ADD COLUMN emotion TEXT DEFAULT '{}'")
            if 'importance' not in columns:
                conn.execute("ALTER TABLE memories ADD COLUMN importance REAL DEFAULT 0.5")

            conn.commit()
        except sqlite3.OperationalError as e:
            logger.warning("【WangChuan】[Reflector][Schema] ensure emotion columns failed: %s", e)
        finally:
            conn.close()

    def reflect(self, since_hours: int = 6) -> Dict[str, Any]:
        """
        执行一次反思

        Args:
            since_hours: 回溯多少小时的对话

        Returns:
            {"events_found": int, "memories_stored": int, "synced_to_md": int}
        """
        since_time = datetime.now() - timedelta(hours=since_hours)
        empty_result = {"events_found": 0, "memories_stored": 0, "synced_to_md": 0, "events": []}

        # 1. 从忘川获取对话
        messages = self._get_messages_since(since_time)
        if not messages:
            return empty_result

        # 2. 提取关键事件
        events = self.extractor.extract(messages)
        if not events:
            return empty_result

        # 3. 存储到忘川
        stored = 0
        synced = 0
        for event in events:
            entry = self._event_to_memory(event)
            if self._store_to_wangchuan(entry):
                stored += 1
                if self._sync_to_memory_md(entry):
                    synced += 1

        return {
            "events_found": len(events),
            "memories_stored": stored,
            "synced_to_md": synced,
            "events": [
                {
                    "summary": e.summary,
                    "category": e.category,
                    "importance": e.importance,
                    "emotion": e.emotion.to_dict() if e.emotion else None,
                }
                for e in events
            ]
        }

    def _get_messages_since(self, since: datetime) -> List[Dict]:
        """从忘川获取指定时间之后的对话"""
        if not os.path.exists(self.db_path):
            return []

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT role, content, timestamp FROM gm_messages "
                "WHERE timestamp >= ? ORDER BY timestamp",
                (since.isoformat(),)
            ).fetchall()
            return [
                {"role": r[0], "content": r[1], "timestamp": r[2]}
                for r in rows
            ]
        except sqlite3.OperationalError as e:
            logger.warning("【WangChuan】[Reflector][Messages] query failed: %s", e)
            return []
        finally:
            conn.close()

    def _event_to_memory(self, event: KeyEvent) -> MemoryEntry:
        """将事件转换为记忆条目"""
        tags = [event.category]
        if event.emotion:
            tags.append(f"emotion:{event.emotion.valence}")

        return MemoryEntry(
            content=event.summary,
            type=event.category,
            importance=event.importance,
            confidence=0.8,
            emotion=event.emotion,
            timestamp=event.timestamp,
            source="reflection",
            tags=tags,
        )

    def _store_to_wangchuan(self, entry: MemoryEntry) -> bool:
        """存储记忆条目到统一 Memory 主入口。"""
        try:
            tags = [str(tag).lower() for tag in (entry.tags or []) if str(tag).strip()]
            metadata = {
                "memory_type": str(entry.type or "event").lower(),
                "source_layer": "scar",
                "promotion_reason": "reflection_event",
                "user_explicit": False,
                "created_at": entry.timestamp,
            }
            result = self.memory_api.remember(
                entry.content,
                importance=entry.importance,
                tags=tags,
                metadata=metadata,
            )
            return bool(result.get("success")) and not bool(result.get("deduped"))
        except Exception as e:
            logger.warning("【WangChuan】[Reflector][Store] failed: %s", e)
            return False

    def _sync_to_memory_md(self, entry: MemoryEntry) -> bool:
        """兼容旧接口：MEMORY.md 同步已统一交给 Memory hot-memory curator。"""
        if entry.importance < 0.7:
            return False

        try:
            tags = [str(tag).lower() for tag in (entry.tags or []) if str(tag).strip()]
            self.memory_api._sync_to_memory_md(entry.content, tags)
            return True
        except Exception as e:
            logger.warning("【WangChuan】[Reflector][SyncMemoryMd] failed: %s", e)
            return False


# ============================================================
# 命令行入口
# ============================================================

if __name__ == "__main__":
    import sys

    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24

    print(f"🧠 忆藏层反思引擎 - 回溯 {hours} 小时")
    print("=" * 50)

    engine = ReflectEngine()
    result = engine.reflect(since_hours=hours)

    print(f"事件发现: {result['events_found']}")
    print(f"记忆存储: {result['memories_stored']}")
    print(f"同步到MEMORY.md: {result['synced_to_md']}")

    if result['events']:
        print("\n📋 提取的事件:")
        for evt in result['events']:
            emotion_info = ""
            if evt['emotion']:
                e = evt['emotion']
                emotion_info = f" [情感:{e['valence']} 强度:{e['arousal']}]"
            print(f"  [{evt['category']}] {evt['summary']}{emotion_info}")
