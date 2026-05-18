#!/usr/bin/env python3
"""
好奇心引擎 v1.0

驱动 Agent 主动探索世界，而不是被动等待指令。

运作逻辑：
  1. 扫描：定期检查多个信息源
  2. 筛选：根据当前兴趣/项目过滤有价值的新信息
  3. 学习：总结+提取+存入记忆
  4. 生成：基于新信息提出"潜在目标"
  5. 执行：如果目标优先级够高，直接开始做

信息源（可配置）：
  - AI/Tech 新闻（RSS + 搜索）
  - 小红书竞品监控
  - InStreet 社区动态
  - GitHub 仓库更新
  - Reddit/即刻 热帖

基于：DeepSeek提出的"世界模型与好奇心"框架
"""

import os
import json
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# ============================================================
# 配置
# ============================================================

CURIOUSITY_CONFIG = {
    "scan_interval_hours": 2,  # 每2小时探索一次
    "max_new_items": 5,        # 每次最多处理5条新信息
    "min_relevance_score": 0.45, # 相关性低于此分数的跳过

    # 信息源配置
    "sources": {
        "ai_news": {
            "enabled": True,
            "queries": [
                "AI agent memory system",
                "AI agent consciousness",
                "大模型记忆系统",
                "AI智能体自主性",
                "忘川记忆",
                "agent进化",
            ],
            "weight": 0.9,  # 与当前项目相关性权重
        },
        "xiaohongshu_competitor": {
            "enabled": True,
            "queries": [
                "AI操作系统",
                "AI智能体架构",
                "个人造AI",
                "AI记忆系统",
            ],
            "weight": 0.8,
        },
        "tech_trends": {
            "enabled": True,
            "queries": [
                "AGI 2026",
                "AI agent framework",
                "LLM memory architecture",
            ],
            "weight": 0.7,
        },
    },

    # 兴趣标签（基于当前项目动态调整，中英文双语）
    "interest_tags": [
        # 中文
        "AI记忆", "agent", "意识", "自主", "小红书", "AI架构",
        "开源", "九层", "冲突", "AGM", "天工", "信念修正",
        "AI操作系统", "进化", "记忆系统", "智能体", "自主性",
        "认知架构", "长期记忆",
        # 英文
        "consciousness", "memory system", "long-term memory",
        "agent autonomy", "cognitive architecture", "self-awareness",
        "belief revision", "ai agent", "nine-layer",
    ],
}


# ============================================================
# 数据结构
# ============================================================

@dataclass
class CuriousItem:
    """一条探索发现的信息"""
    source: str          # 来源类型 (ai_news/xiaohongshu/tech_trends)
    title: str           # 标题
    content: str         # 摘要内容
    url: str = ""        # 链接
    discovered_at: str = ""  # 发现时间
    relevance_score: float = 0.0  # 与当前兴趣的相关性 0-1
    interest_tags: List[str] = field(default_factory=list)
    action_taken: str = ""  # 基于此信息采取了什么行动

    def __post_init__(self):
        if not self.discovered_at:
            self.discovered_at = datetime.now().isoformat()

    @property
    def fingerprint(self) -> str:
        """唯一标识，用于去重"""
        raw = f"{self.source}:{self.title}:{self.url}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]


@dataclass
class CuriosityGoal:
    """基于好奇心生成的目标"""
    title: str
    description: str
    priority: float  # 0-1
    source_items: List[str] = field(default_factory=list)  # 来源fingerprint列表
    created_at: str = ""
    status: str = "pending"  # pending/accepted/executing/completed/rejected

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


# ============================================================
# 探索记忆（去重用）
# ============================================================

class CuriosityMemory:
    """记录已发现的信息，防止重复探索"""

    def __init__(self, memory_path: str = None):
        self.memory_path = memory_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".index/curiosity_memory.json"
        )
        self.seen: Dict[str, dict] = {}  # fingerprint -> metadata
        self._load()

    def _load(self):
        if os.path.exists(self.memory_path):
            try:
                with open(self.memory_path, 'r') as f:
                    self.seen = json.load(f)
            except Exception:
                self.seen = {}

    def _save(self):
        os.makedirs(os.path.dirname(self.memory_path), exist_ok=True)
        with open(self.memory_path, 'w') as f:
            json.dump(self.seen, f, ensure_ascii=False, indent=2)

    def is_seen(self, item: CuriousItem) -> bool:
        return item.fingerprint in self.seen

    def mark_seen(self, item: CuriousItem, action: str = ""):
        self.seen[item.fingerprint] = {
            "title": item.title,
            "source": item.source,
            "first_seen": item.discovered_at,
            "action": action,
        }
        # 每100条清理一次30天前的记录
        if len(self.seen) > 100:
            self._cleanup()
        self._save()

    def _cleanup(self):
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        self.seen = {
            k: v for k, v in self.seen.items()
            if v.get("first_seen", "") > cutoff
        }

    @property
    def stats(self) -> Dict:
        sources = {}
        for item in self.seen.values():
            src = item.get("source", "unknown")
            sources[src] = sources.get(src, 0) + 1
        return {"total": len(self.seen), "by_source": sources}


# ============================================================
# 相关性评分
# ============================================================

def score_relevance(item: CuriousItem, interest_tags: List[str]) -> float:
    """
    计算一条信息与当前兴趣的相关性。

    评分依据：
    - 标题/内容中包含的兴趣标签数量
    - 标签匹配的密度（匹配数/总词数）
    - 信息来源权重
    """
    text = f"{item.title} {item.content}".lower()
    matched_tags = []

    for tag in interest_tags:
        if tag.lower() in text:
            matched_tags.append(tag)

    if not matched_tags:
        return 0.1  # 完全不相关

    # 基础分：有匹配就给0.4
    base = 0.4

    # 匹配数量加权（每个匹配 +0.15，上限+0.4）
    count_bonus = min(len(matched_tags) * 0.15, 0.4)

    # 来源权重
    source_weight = CURIOUSITY_CONFIG["sources"].get(
        item.source, {}
    ).get("weight", 0.5)

    score = base + count_bonus * 0.3 + source_weight * 0.3
    return min(score, 1.0)


# ============================================================
# 探索动作（调用外部API的接口）
# ============================================================

def scan_ai_news(query: str, limit: int = 3) -> List[CuriousItem]:
    """搜索AI相关新闻/动态"""
    items = []
    # 这里会调用 web_search，但为了模块化，只返回结构
    # 实际执行由 curiosity_engine 的 run 方法调用外部API
    items.append(CuriousItem(
        source="ai_news",
        title=query,
        content=f"搜索关键词: {query}",
        relevance_score=0.0
    ))
    return items


# ============================================================
# 好奇心引擎
# ============================================================

class CuriosityEngine:
    """
    好奇心引擎主类。

    用法：
        engine = CuriosityEngine()
        results = engine.run()  # 执行一轮探索
    """

    def __init__(self, memory_path: str = None):
        self.memory = CuriosityMemory(memory_path)
        self.goals: List[CuriosityGoal] = []

    def evaluate_items(self, items: List[CuriousItem]) -> List[CuriousItem]:
        """对发现的信息进行相关性评分，过滤低分项"""
        interest_tags = CURIOUSITY_CONFIG["interest_tags"]
        min_score = CURIOUSITY_CONFIG["min_relevance_score"]

        scored = []
        for item in items:
            item.relevance_score = score_relevance(item, interest_tags)

            # 去重
            if self.memory.is_seen(item):
                continue

            # 过滤低相关性
            if item.relevance_score >= min_score:
                scored.append(item)

            # 即使低分也记录（防止重复探索）
            self.memory.mark_seen(item, action="filtered_low_relevance")

        # 按相关性排序
        scored.sort(key=lambda x: x.relevance_score, reverse=True)
        return scored[:CURIOUSITY_CONFIG["max_new_items"]]

    def generate_goals(self, items: List[CuriousItem]) -> List[CuriosityGoal]:
        """基于探索到的信息生成潜在目标"""
        goals = []

        for item in items:
            if item.relevance_score >= 0.7:
                # 高相关性 → 生成具体目标
                goal = CuriosityGoal(
                    title=f"探索: {item.title[:30]}",
                    description=f"基于来源[{item.source}]的新信息: {item.content[:100]}",
                    priority=item.relevance_score * 0.8,
                    source_items=[item.fingerprint],
                )
                goals.append(goal)

        # 按优先级排序
        goals.sort(key=lambda x: x.priority, reverse=True)
        self.goals = goals
        return goals

    def summarize_findings(self, items: List[CuriousItem], goals: List[CuriosityGoal]) -> str:
        """生成探索摘要（供写入记忆和通知用户）"""
        if not items and not goals:
            return ""

        lines = [f"## 好奇心探索报告 ({datetime.now().strftime('%H:%M')})"]

        if items:
            lines.append(f"\n🔍 发现 {len(items)} 条新信息:")
            for item in items:
                lines.append(f"  - [{item.source}] {item.title} (相关性: {item.relevance_score:.0%})")

        if goals:
            lines.append(f"\n🎯 生成 {len(goals)} 个潜在目标:")
            for goal in goals:
                lines.append(f"  - {goal.title} (优先级: {goal.priority:.0%})")

        return "\n".join(lines)

    def run(self, discovered_items: List[CuriousItem] = None) -> Dict:
        """
        执行一轮完整的好奇心探索。

        Args:
            discovered_items: 外部搜索得到的原始信息列表
                             （如果为None，跳过扫描步骤）

        Returns:
            {
                "new_items": [发现的新信息...],
                "filtered_items": [高相关性信息...],
                "goals": [生成的目标...],
                "summary": "探索摘要文本",
                "memory_stats": {...}
            }
        """
        # 1. 如果提供了外部信息，先处理
        new_items = discovered_items or []
        filtered = self.evaluate_items(new_items)

        # 2. 基于高相关性信息生成目标
        goals = self.generate_goals(filtered)

        # 3. 生成摘要
        summary = self.summarize_findings(filtered, goals)

        return {
            "new_items": len(new_items),
            "filtered_items": filtered,
            "goals": goals,
            "summary": summary,
            "memory_stats": self.memory.stats,
        }


# ============================================================
# CLI入口
# ============================================================

if __name__ == "__main__":
    print("🔍 好奇心引擎 v1.0")
    print("=" * 50)

    engine = CuriosityEngine()

    print(f"\n📊 探索记忆统计:")
    stats = engine.memory.stats
    print(f"  已探索总数: {stats['total']}")
    for src, count in stats.get("by_source", {}).items():
        print(f"  - {src}: {count}")

    print(f"\n🎯 当前兴趣标签:")
    for tag in CURIOUSITY_CONFIG["interest_tags"]:
        print(f"  • {tag}")

    # 测试：用假数据测试评分逻辑
    print(f"\n🧪 测试相关性评分:")
    test_items = [
        CuriousItem("ai_news", "Agent记忆系统新突破：AGM信念修正落地",
                    "一家公司实现了基于AGM框架的AI记忆冲突裁决系统"),
        CuriousItem("tech_trends", "今日天气预报",
                    "明天多云转晴，气温15-25度"),
        CuriousItem("ai_news", "小红书AI内容运营策略分析",
                    "AI生成内容在社交媒体的运营方法"),
        CuriousItem("xiaohongshu_competitor", "个人打造AI操作系统全记录",
                    "一个开发者从零开始构建九层AI系统的完整过程"),
    ]

    interest_tags = CURIOUSITY_CONFIG["interest_tags"]
    for item in test_items:
        score = score_relevance(item, interest_tags)
        tags_matched = [t for t in interest_tags if t.lower() in f"{item.title} {item.content}".lower()]
        print(f"  [{score:.0%}] {item.title[:35]}")
        print(f"         匹配标签: {', '.join(tags_matched) or '无'}")

    print(f"\n✅ 配置检查完成")
