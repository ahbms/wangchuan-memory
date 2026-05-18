#!/usr/bin/env python3
"""
忘川 v3 - IV 记忆价值计算器
基于「动态遗忘框架」实现

IV(memory, context, t) = Base_Value × Usage_Factor(t) × Context_Match(context) × Time_Factor(t)

职责边界（P2-06 Initializer / Operator 分工落板）：
- 本文件属于 **initializer / maintenance 分析工具**：负责离线价值评估与分层建议
- 它可以消费 `MEMORY.md` / DB 结果，但不应充当运行期 remember/recall/write gate 主入口

借鉴自 InStreet 帖子「记忆系统的动态遗忘」(320赞)
融入忘川温度分层，提供更精确的保留价值计算
"""
from wangchuan.paths import workspace_root as _v3_ws_root

import logging
import os
import json
from pathlib import Path
import sqlite3
import math
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class MemoryItem:
    """记忆条目"""
    id: str
    content: str
    memory_type: str  # user_defined / emotional / fact / preference / lesson
    created_at: str
    last_accessed: str = ""
    access_count: int = 0
    success_count: int = 0  # 检索后被确认有用的次数
    failure_count: int = 0  # 检索后发现过时/错误的次数
    base_value: float = 0.5  # 0-1, 创建时评估
    tags: List[str] = field(default_factory=list)


@dataclass
class IVResult:
    """IV 计算结果"""
    memory_id: str
    iv_score: float  # 0-1 综合保留价值
    base_value: float
    usage_factor: float
    context_match: float
    time_factor: float
    recommendation: str  # keep / demote / archive / forget
    details: str = ""


WORKSPACE_ROOT = _v3_ws_root()
logger = logging.getLogger(__name__)
NOISE_PATTERNS = [
    r"\bhttp_api_test\b",
    r"\blive_verify\b",
    r"\[cron\]",
    r"情感事件:",
    r"\bpytest\b",
    r"\bunittest\b",
    r"测试",
    r"demo",
    r"sample",
]

class IVCalculator:
    """IV 记忆价值计算器"""

    # 遗忘阈值
    KEEP_THRESHOLD = 0.5      # > 0.5: 保持当前层级
    DEMOTE_THRESHOLD = 0.3    # 0.3-0.5: 降级
    ARCHIVE_THRESHOLD = 0.15  # 0.15-0.3: 归档
    # < 0.15: 建议遗忘

    # 忘川温度层级对应的半衰期（天）
    TEMP_HALFLIFE = {
        "hot": 1,       # 热记忆：1天
        "warm": 7,      # 温记忆：7天
        "cold": 30,     # 冷记忆：30天
        "frozen": 365,  # 冰记忆：365天
    }

    # 记忆类型的基础价值权重
    TYPE_BASE_VALUE = {
        "user_defined": 0.7,   # 用户明确告知的
        "preference": 0.8,     # 用户偏好
        "lesson": 0.9,         # 教训
        "fact": 0.5,           # 事实
        "emotional": 0.6,      # 情感事件
    }

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(WORKSPACE_ROOT / "tiangong" / "wangchuan" / ".index" / "index.sqlite")

    # ── Base_Value（基础价值）────────────────────────────
    def calc_base_value(self, memory: MemoryItem) -> float:
        """
        计算基础价值
        - 记忆类型权重
        - 内容长度/信息密度
        - 标签丰富度
        """
        # 1. 类型权重
        type_weight = self.TYPE_BASE_VALUE.get(memory.memory_type, 0.5)

        # 2. 信息密度（内容越精炼价值越高——伤疤 > 日记）
        content_len = len(memory.content)
        if content_len < 50:
            density_bonus = 0.15  # 太短，可能不完整
        elif content_len < 200:
            density_bonus = 0.1   # 精炼，加分
        elif content_len < 500:
            density_bonus = 0.0   # 适中
        else:
            density_bonus = -0.1  # 太长，可能是流水账

        # 3. 标签丰富度
        tag_bonus = min(len(memory.tags) * 0.03, 0.1)

        base = min(max(type_weight + density_bonus + tag_bonus, 0.0), 1.0)
        return base

    # ── Usage_Factor（使用因子）────────────────────────────
    def calc_usage_factor(self, memory: MemoryItem, now: Optional[datetime] = None) -> float:
        """
        计算使用因子
        - 被频繁调用 → 加成
        - 检索成功率高 → 加成
        - 从未调用 → 衰减
        """
        now = now or datetime.now()

        if memory.access_count == 0:
            return 0.7  # 未使用过，默认保留（没有证据证明该丢）

        # 成功率
        total_retrievals = memory.success_count + memory.failure_count
        if total_retrievals > 0:
            success_rate = memory.success_count / total_retrievals
        else:
            success_rate = 0.5

        # 访问频率（最近30天内的访问密度）
        if memory.last_accessed:
            try:
                last_dt = datetime.fromisoformat(memory.last_accessed.replace('Z', '+00:00'))
                days_since = (now - last_dt.replace(tzinfo=None)).days
            except Exception as e:
                logger.warning("【WangChuan】[IVCalculator][Usage] timestamp parse failed: %s", e)
                days_since = 30
        else:
            days_since = 30

        # 访问频率分（最近访问越近越好）
        recency_score = math.exp(-0.1 * days_since)  # e^(-0.1*days)

        # 综合
        usage = 0.4 * success_rate + 0.4 * recency_score + 0.2 * min(memory.access_count / 10, 1.0)
        return min(max(usage, 0.0), 1.0)

    # ── Context_Match（上下文匹配）────────────────────────────
    def calc_context_match(self, memory: MemoryItem, context_tags: List[str]) -> float:
        """
        计算上下文匹配度
        - 无上下文时返回中性值（不惩罚）
        - 记忆标签与当前上下文标签的重叠度
        """
        if not context_tags:
            return 0.5  # 无上下文，中性（不惩罚也不奖励）

        if not memory.tags:
            return 0.3  # 无标签的记忆，偏低

        # Jaccard 相似度
        memory_set = set(t.lower() for t in memory.tags)
        context_set = set(t.lower() for t in context_tags)
        intersection = memory_set & context_set
        union = memory_set | context_set

        if not union:
            return 0.5

        jaccard = len(intersection) / len(union)

        # 如果有完全匹配的标签，加权
        exact_matches = len(intersection)
        match_bonus = min(exact_matches * 0.15, 0.3)

        return min(max(jaccard + match_bonus, 0.0), 1.0)

    # ── Time_Factor（时间因子）────────────────────────────
    def calc_time_factor(self, memory: MemoryItem, now: Optional[datetime] = None) -> float:
        """
        计算时间因子
        - 基于记忆类型的不同半衰期
        - 冰记忆衰减极慢
        """
        now = now or datetime.now()

        try:
            created = datetime.fromisoformat(memory.created_at.replace('Z', '+00:00'))
            days_old = (now - created.replace(tzinfo=None)).days
        except Exception as e:
            logger.warning("【WangChuan】[IVCalculator][Time] created_at parse failed: %s", e)
            days_old = 30

        # 根据记忆类型确定半衰期
        if memory.memory_type == "lesson":
            halflife = self.TEMP_HALFLIFE["frozen"]  # 教训类：冰记忆级
        elif memory.memory_type == "preference":
            halflife = self.TEMP_HALFLIFE["cold"]  # 偏好类：冷记忆级
        elif memory.memory_type == "user_defined":
            halflife = self.TEMP_HALFLIFE["cold"]  # 用户定义：冷记忆级
        elif memory.memory_type == "emotional":
            halflife = self.TEMP_HALFLIFE["warm"]  # 情感类：温记忆级
        else:
            halflife = self.TEMP_HALFLIFE["warm"]  # 默认：温记忆级

        # 指数衰减：value = e^(-ln(2) * days / halflife)
        time_factor = math.exp(-0.693 * days_old / halflife)
        return max(time_factor, 0.01)

    # ── IV 综合计算 ────────────────────────────────────
    def calculate(self, memory: MemoryItem, context_tags: Optional[List[str]] = None,
                  now: Optional[datetime] = None) -> IVResult:
        """
        计算记忆的综合保留价值 IV
        """
        now = now or datetime.now()
        context_tags = context_tags or []

        base = memory.base_value if memory.base_value > 0 else self.calc_base_value(memory)
        usage = self.calc_usage_factor(memory, now)
        context = self.calc_context_match(memory, context_tags)
        time_f = self.calc_time_factor(memory, now)

        iv = base * usage * context * time_f

        # 生成建议
        if iv >= self.KEEP_THRESHOLD:
            rec = "keep"
            detail = f"保留价值高({iv:.2f})，维持当前层级"
        elif iv >= self.DEMOTE_THRESHOLD:
            rec = "demote"
            detail = f"保留价值中等({iv:.2f})，建议降级"
        elif iv >= self.ARCHIVE_THRESHOLD:
            rec = "archive"
            detail = f"保留价值低({iv:.2f})，建议归档"
        else:
            rec = "forget"
            detail = f"保留价值极低({iv:.2f})，建议遗忘"

        return IVResult(
            memory_id=memory.id,
            iv_score=round(iv, 4),
            base_value=round(base, 4),
            usage_factor=round(usage, 4),
            context_match=round(context, 4),
            time_factor=round(time_f, 4),
            recommendation=rec,
            details=detail,
        )

    # ── 批量评估 ────────────────────────────────────
    def evaluate_all(self, memories: List[MemoryItem],
                     context_tags: Optional[List[str]] = None) -> List[IVResult]:
        """批量评估所有记忆"""
        results = []
        for mem in memories:
            result = self.calculate(mem, context_tags)
            results.append(result)
        # 按 IV 分数降序排列
        results.sort(key=lambda r: r.iv_score, reverse=True)
        return results

    # ── 从 MEMORY.md 解析记忆条目 ────────────────────────
    @staticmethod
    def parse_from_memory_md(filepath: str = str(WORKSPACE_ROOT / "MEMORY.md")) -> List[MemoryItem]:
        """
        从 MEMORY.md 中解析记忆条目
        简单启发式解析，提取各段落作为独立记忆
        """
        if not os.path.exists(filepath):
            return []

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        memories = []
        lines = content.split('\n')
        current_section = ""
        current_content = []
        current_tags = []

        for line in lines:
            # 检测新的段落标题
            if line.startswith('## ') or line.startswith('### '):
                # 保存上一个段落
                if current_section and current_content:
                    mem_content = '\n'.join(current_content).strip()
                    if len(mem_content) > 10 and not any(re.search(pattern, mem_content, flags=re.IGNORECASE) for pattern in NOISE_PATTERNS):  # 过滤太短的
                        # 推断记忆类型
                        mem_type = "fact"
                        if "教训" in current_section or "经验" in current_section or "踩坑" in current_section:
                            mem_type = "lesson"
                        elif "偏好" in current_section or "用户" in current_section:
                            mem_type = "preference"
                        elif "情感" in current_section:
                            mem_type = "emotional"
                        elif "IF" in mem_content and "THEN" in mem_content:
                            mem_type = "lesson"  # 包含判断规则的也视为教训

                        # 根据类型设置基础价值
                        type_base = IVCalculator.TYPE_BASE_VALUE.get(mem_type, 0.5)

                        memories.append(MemoryItem(
                            id=f"mem_{len(memories):04d}",
                            content=mem_content[:500],  # 截断长内容
                            memory_type=mem_type,
                            created_at=datetime.now().isoformat(),
                            base_value=type_base,
                            tags=current_tags + [current_section.strip('# ').strip()],
                        ))

                current_section = line
                current_content = []
                current_tags = []
            else:
                current_content.append(line)
                # 提取可能的标签
                if line.startswith('- ') or line.startswith('* '):
                    tag = line[2:].strip()[:20]
                    if tag:
                        current_tags.append(tag)

        # 处理最后一个段落
        if current_section and current_content:
            mem_content = '\n'.join(current_content).strip()
            if len(mem_content) > 10 and not any(re.search(pattern, mem_content, flags=re.IGNORECASE) for pattern in NOISE_PATTERNS):
                mem_type = "fact"
                if "教训" in current_section or "经验" in current_section:
                    mem_type = "lesson"
                elif "偏好" in current_section:
                    mem_type = "preference"
                type_base = IVCalculator.TYPE_BASE_VALUE.get(mem_type, 0.5)
                memories.append(MemoryItem(
                    id=f"mem_{len(memories):04d}",
                    content=mem_content[:500],
                    memory_type=mem_type,
                    created_at=datetime.now().isoformat(),
                    base_value=type_base,
                    tags=current_tags + [current_section.strip('# ').strip()],
                ))

        return memories

    # ── 生成评估报告 ────────────────────────────────────
    def generate_report(self, results: List[IVResult]) -> str:
        """生成人类可读的评估报告"""
        keep = [r for r in results if r.recommendation == "keep"]
        demote = [r for r in results if r.recommendation == "demote"]
        archive = [r for r in results if r.recommendation == "archive"]
        forget = [r for r in results if r.recommendation == "forget"]

        lines = [
            "=" * 50,
            "📊 忘川 IV 记忆价值评估报告",
            "=" * 50,
            f"总计: {len(results)} 条记忆",
            f"  ✅ 保留: {len(keep)} 条",
            f"  ⬇️ 降级: {len(demote)} 条",
            f"  📦 归档: {len(archive)} 条",
            f"  🗑️ 遗忘: {len(forget)} 条",
            "",
        ]

        if demote:
            lines.append("── 建议降级 ──")
            for r in demote[:5]:
                lines.append(f"  [{r.iv_score:.2f}] {r.memory_id}: {r.details}")
            lines.append("")

        if archive:
            lines.append("── 建议归档 ──")
            for r in archive[:5]:
                lines.append(f"  [{r.iv_score:.2f}] {r.memory_id}: {r.details}")
            lines.append("")

        if forget:
            lines.append("── 建议遗忘 ──")
            for r in forget[:5]:
                lines.append(f"  [{r.iv_score:.2f}] {r.memory_id}: {r.details}")

        return '\n'.join(lines)


def main():
    """命令行入口"""
    import sys

    calc = IVCalculator()
    memories = calc.parse_from_memory_md()

    if not memories:
        print("未找到记忆条目")
        return

    # 解析上下文标签（从命令行参数）
    context_tags = sys.argv[1:] if len(sys.argv) > 1 else []

    results = calc.evaluate_all(memories, context_tags)
    report = calc.generate_report(results)
    print(report)


if __name__ == "__main__":
    main()
