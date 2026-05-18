#!/usr/bin/env python3
"""
忘川 v3 - 判例检查器
验证记忆写入是否符合「判例 > 日记」原则

核心规则：
- 判例 = 情境 + 判断 + 后果/修正（✅ 可写入长期记忆）
- 日记 = 只记录发生了什么（❌ 降级到日志）

借鉴自 InStreet 帖子「记忆不是为了记住，是为了不背叛自己」(61赞)
以及 garbagemon 的三层蒸馏机制
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class PrecedentCheck:
    """判例检查结果"""
    is_precedent: bool         # 是否符合判例格式
    score: float               # 0-1 质量分
    has_scenario: bool         # 有情境
    has_judgment: bool         # 有判断/结论
    has_outcome: bool          # 有后果/修正
    missing: List[str]         # 缺失的部分
    suggestion: str            # 改进建议
    auto_rewrite: str = ""     # 自动改写建议


class PrecedentChecker:
    """判例格式检查器"""

    # 情境关键词
    SCENARIO_PATTERNS = [
        r'IF\s+',
        r'当.*时',
        r'如果.*',
        r'遇到.*情况',
        r'在.*场景',
        r'触发条件',
        r'IF .* THEN',
    ]

    # 判断/结论关键词
    JUDGMENT_PATTERNS = [
        r'THEN\s+',
        r'应该.*',
        r'必须.*',
        r'优先.*',
        r'采用.*',
        r'判断.*',
        r'结论',
    ]

    # 后果/修正关键词
    OUTCOME_PATTERNS = [
        r'NOT\s+',
        r'避免.*',
        r'否则.*',
        r'后果.*',
        r'教训.*',
        r'踩坑.*',
        r'修正',
        r'改为',
    ]

    # 日记模式（应该拒绝）
    DIARY_PATTERNS = [
        r'今天.*做了',
        r'昨天.*',
        r'\d{4}-\d{2}-\d{2}.*完成',
        r'刚刚.*',
        r'然后.*又.*',
    ]

    def check(self, content: str, memory_type: str = "fact") -> PrecedentCheck:
        """
        检查内容是否符合判例格式
        """
        content_lower = content.lower()

        # 检查三个维度
        has_scenario = any(re.search(p, content, re.IGNORECASE) for p in self.SCENARIO_PATTERNS)
        has_judgment = any(re.search(p, content, re.IGNORECASE) for p in self.JUDGMENT_PATTERNS)
        has_outcome = any(re.search(p, content, re.IGNORECASE) for p in self.OUTCOME_PATTERNS)

        # 检查是否是日记模式
        is_diary = any(re.search(p, content, re.IGNORECASE) for p in self.DIARY_PATTERNS)

        # 计算缺失项
        missing = []
        if not has_scenario:
            missing.append("情境(IF)")
        if not has_judgment:
            missing.append("判断(THEN)")
        if not has_outcome:
            missing.append("后果(NOT/教训)")

        # 计算质量分
        score = 0.0
        if has_scenario:
            score += 0.35
        if has_judgment:
            score += 0.35
        if has_outcome:
            score += 0.20
        if not is_diary:
            score += 0.10

        # 特殊类型豁免
        if memory_type in ("preference", "user_defined"):
            # 偏好和用户定义的不需要完整判例格式
            if has_judgment:
                score = max(score, 0.7)

        is_precedent = score >= 0.6 and not is_diary

        # 生成建议
        suggestion = self._generate_suggestion(has_scenario, has_judgment, has_outcome, is_diary, content)

        # 自动生成改写建议
        auto_rewrite = ""
        if not is_precedent:
            auto_rewrite = self._auto_rewrite(content, has_scenario, has_judgment, has_outcome)

        return PrecedentCheck(
            is_precedent=is_precedent,
            score=round(score, 2),
            has_scenario=has_scenario,
            has_judgment=has_judgment,
            has_outcome=has_outcome,
            missing=missing,
            suggestion=suggestion,
            auto_rewrite=auto_rewrite,
        )

    def _generate_suggestion(self, has_s: bool, has_j: bool, has_o: bool,
                             is_diary: bool, content: str) -> str:
        """生成改进建议"""
        if is_diary:
            return "⚠️ 这是日记格式，不是判例。请提炼为行为模式：从事件中提取判断规则。"

        parts = []
        if not has_s:
            parts.append("补充分析境（IF条件）：什么情况下会触发这个规则？")
        if not has_j:
            parts.append("补充判断结论（THEN行动）：遇到这种情况应该怎么做？")
        if not has_o:
            parts.append("补充后果/修正（NOT禁忌）：这样做有什么好处，不这样做有什么后果？")

        if parts:
            return " | ".join(parts)
        return "✅ 符合判例格式"

    def _auto_rewrite(self, content: str, has_s: bool, has_j: bool, has_o: bool) -> str:
        """自动生成判例格式改写建议"""
        lines = []

        if not has_s:
            lines.append("IF [触发条件]")
        else:
            # 尝试提取已有的情境
            match = re.search(r'(?:IF|当|如果|遇到)(.*?)(?:THEN|应该|，)', content, re.IGNORECASE)
            if match:
                lines.append(f"IF {match.group(1).strip()}")
            else:
                lines.append("IF [触发条件]")

        if not has_j:
            lines.append("THEN [正确做法]")
        else:
            lines.append("THEN [已有结论，检查是否明确]")

        if not has_o:
            lines.append("NOT [避免的做法/后果]")

        return "\n".join(lines)

    def check_batch(self, contents: List[str], types: Optional[List[str]] = None) -> List[PrecedentCheck]:
        """批量检查"""
        types = types or ["fact"] * len(contents)
        return [self.check(c, t) for c, t in zip(contents, types)]

    def generate_report(self, checks: List[PrecedentCheck]) -> str:
        """生成批量检查报告"""
        passed = [c for c in checks if c.is_precedent]
        failed = [c for c in checks if not c.is_precedent]
        avg_score = sum(c.score for c in checks) / max(len(checks), 1)

        lines = [
            "=" * 50,
            "📋 判例格式检查报告",
            "=" * 50,
            f"总计: {len(checks)} 条记忆",
            f"  ✅ 合格判例: {len(passed)} 条",
            f"  ❌ 不合格: {len(failed)} 条",
            f"  📊 平均质量分: {avg_score:.2f}",
            "",
        ]

        if failed:
            lines.append("── 需要改进 ──")
            for i, c in enumerate(failed[:5]):
                lines.append(f"  {i+1}. 质量分 {c.score:.2f} | 缺失: {', '.join(c.missing)}")
                lines.append(f"     建议: {c.suggestion}")
                if c.auto_rewrite:
                    lines.append(f"     改写: {c.auto_rewrite.replace(chr(10), ' | ')}")
                lines.append("")

        return '\n'.join(lines)


def main():
    """命令行入口 - 从 MEMORY.md 读取并检查"""
    import sys

    checker = PrecedentChecker()

    # 从文件或 stdin 读取
    if len(sys.argv) > 1:
        content = ' '.join(sys.argv[1:])
    else:
        content = sys.stdin.read()

    if not content.strip():
        print("用法: echo '记忆内容' | python3 precedent_checker.py")
        print("或:   python3 precedent_checker.py '记忆内容'")
        return

    result = checker.check(content)
    print(f"判例质量: {result.score:.2f}")
    print(f"合格: {'✅' if result.is_precedent else '❌'}")
    print(f"  情境: {'✅' if result.has_scenario else '❌'}")
    print(f"  判断: {'✅' if result.has_judgment else '❌'}")
    print(f"  后果: {'✅' if result.has_outcome else '❌'}")
    if result.missing:
        print(f"缺失: {', '.join(result.missing)}")
    print(f"建议: {result.suggestion}")
    if result.auto_rewrite:
        print(f"\n改写建议:\n{result.auto_rewrite}")


if __name__ == "__main__":
    main()
