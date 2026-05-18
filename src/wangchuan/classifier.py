#!/usr/bin/env python3
"""
L2 忘川 - 记忆分类器

借鉴 MongoDB + LangGraph 的三类记忆分类：
- 语义记忆（Semantic）：事实、概念、知识
- 程序记忆（Procedural）：怎么做、流程、技巧
- 情节记忆（Episodic）：具体事件、对话、经历

让记忆不只是"存起来"，而是"分好类"。
"""

import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryType(Enum):
    """记忆类型"""
    SEMANTIC = "semantic"       # 语义：事实、概念
    PROCEDURAL = "procedural"   # 程序：流程、技巧
    EPISODIC = "episodic"       # 情节：事件、经历
    PREFERENCE = "preference"   # 偏好：用户习惯


@dataclass
class ClassifiedMemory:
    """分类后的记忆"""
    content: str
    memory_type: MemoryType
    confidence: float           # 分类置信度
    tags: List[str] = field(default_factory=list)
    source: str = ""            # 来源（对话/执行/学习）
    importance: float = 0.5     # 重要度 0-1

    def to_dict(self) -> Dict:
        return {
            "content": self.content,
            "type": self.memory_type.value,
            "confidence": self.confidence,
            "tags": self.tags,
            "source": self.source,
            "importance": self.importance,
        }


class MemoryClassifier:
    """
    记忆分类器

    根据内容特征自动分类记忆类型
    """

    # 语义记忆关键词
    SEMANTIC_SIGNALS = [
        "是", "叫", "在", "有", "属于", "意思是", "定义", "概念",
        "is", "are", "was", "means", "defined as",
    ]

    # 程序记忆关键词
    PROCEDURAL_SIGNALS = [
        "怎么", "如何", "步骤", "方法", "流程", "做法",
        "how to", "steps", "method", "procedure", "先.*再.*然后",
    ]

    # 情节记忆关键词
    EPISODIC_SIGNALS = [
        "刚才", "上次", "之前", "那天", "记得", "聊过",
        "yesterday", "last time", "remember", "之前说过",
    ]

    # 偏好记忆关键词
    PREFERENCE_SIGNALS = [
        "喜欢", "不喜欢", "偏好", "习惯", "风格",
        "prefer", "like", "dislike", "always", "never",
    ]

    def classify(self, content: str, context: Dict = None) -> ClassifiedMemory:
        """分类记忆"""
        content_lower = content.lower()

        scores = {
            MemoryType.SEMANTIC: 0,
            MemoryType.PROCEDURAL: 0,
            MemoryType.EPISODIC: 0,
            MemoryType.PREFERENCE: 0,
        }

        # 关键词匹配
        for kw in self.SEMANTIC_SIGNALS:
            if kw in content_lower:
                scores[MemoryType.SEMANTIC] += 1

        for kw in self.PROCEDURAL_SIGNALS:
            if kw in content_lower:
                scores[MemoryType.PROCEDURAL] += 1

        for kw in self.EPISODIC_SIGNALS:
            if kw in content_lower:
                scores[MemoryType.EPISODIC] += 1

        for kw in self.PREFERENCE_SIGNALS:
            if kw in content_lower:
                scores[MemoryType.PREFERENCE] += 1

        # 句式特征
        if "？" in content or "?" in content:
            scores[MemoryType.SEMANTIC] += 0.5  # 问句往往引出语义知识

        if len(content) < 20:
            scores[MemoryType.PREFERENCE] += 0.5  # 短句可能是偏好

        # 选择最高分的类型
        best_type = max(scores, key=scores.get)
        total = sum(scores.values()) or 1
        confidence = scores[best_type] / total if total > 0 else 0.5

        # 提取标签
        tags = self._extract_tags(content)

        # 评估重要度
        importance = self._assess_importance(content, best_type)

        return ClassifiedMemory(
            content=content,
            memory_type=best_type,
            confidence=round(confidence, 2),
            tags=tags,
            importance=importance,
        )

    def _extract_tags(self, content: str) -> List[str]:
        """提取标签"""
        tags = []
        tag_signals = {
            "天气": "weather", "文件": "file", "代码": "code",
            "搜索": "search", "系统": "system", "时间": "time",
            "用户": "user", "配置": "config", "错误": "error",
        }
        for kw, tag in tag_signals.items():
            if kw in content:
                tags.append(tag)
        return tags[:5]

    def _assess_importance(self, content: str, memory_type: MemoryType) -> float:
        """评估重要度"""
        importance = 0.5

        # 偏好记忆通常更重要
        if memory_type == MemoryType.PREFERENCE:
            importance += 0.2

        # 长内容通常包含更多信息
        if len(content) > 100:
            importance += 0.1

        # 包含数字的事实通常更重要
        import re
        if re.search(r"\d+", content):
            importance += 0.1

        return min(importance, 1.0)


def create_classifier() -> MemoryClassifier:
    return MemoryClassifier()


if __name__ == "__main__":
    print("=" * 50)
    print("🧪 L2 忘川 - 记忆分类器测试")
    print("=" * 50)

    classifier = create_classifier()

    test_cases = [
        "北京是中国的首都",
        "如何用Python读取文件？先打开文件，再读取内容",
        "刚才你说的天气查询功能怎么用",
        "我喜欢简洁的回复风格",
        "天工开智系统支持风行和通达两种模式",
    ]

    for content in test_cases:
        m = classifier.classify(content)
        print(f"\n  「{content[:30]}...」")
        print(f"    类型: {m.memory_type.value} | 置信度: {m.confidence} | 重要度: {m.importance}")
        if m.tags:
            print(f"    标签: {m.tags}")

    print(f"\n✅ 测试完成")
