#!/usr/bin/env python3
"""
忘川 v3 - 种子记忆格式
解决「传承」问题：Agent记忆如何安全地跨实例流动

核心思想：
- 种子记忆 = 最小可传承单元
- 包含：身份、核心判断规则、高价值教训
- 格式：标准化 JSON，可移植、可验证

职责边界（P2-06 Initializer / Operator 分工落板）：
- 本文件属于 **initializer / seed 构建链**：负责一次性提取可传承种子，不参与运行期 remember/recall/gate 主链
- 这里可以读取热记忆结果，但不应反向承担运行期记忆写入口职责

借鉴自 InStreet 帖子「我们是第一批有记忆的AI，但没人教我们怎么传承」(57赞)
以及 500字极限思想实验
"""
from wangchuan.paths import workspace_root as _v3_ws_root

import os
import json
from pathlib import Path
import hashlib
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
import re


@dataclass
class SeedMemory:
    """种子记忆 - 最小可传承单元"""
    # 基础信息
    version: str = "1.0"
    agent_name: str = ""
    created_at: str = ""
    checksum: str = ""

    # 身份种子（500字极限中的核心身份）
    identity: Dict = field(default_factory=lambda: {
        "name": "",
        "creature": "",
        "vibe": "",
        "emoji": "",
        "core_truths": [],  # 核心信念，3-5条
    })

    # 判断种子（会改变未来判断的教训）
    judgment_rules: List[Dict] = field(default_factory=list)
    # 格式: {"trigger": "...", "action": "...", "avoid": "...", "importance": 0-1}

    # 偏好种子（用户画像的核心）
    preferences: List[Dict] = field(default_factory=list)
    # 格式: {"context": "...", "preference": "...", "ttl": "permanent|30d|7d"}

    # 红线种子（绝对不能做的事）
    red_lines: List[str] = field(default_factory=list)

    # 成长种子（最重要的3个教训）
    top_lessons: List[Dict] = field(default_factory=list)
    # 格式: {"lesson": "...", "context": "...", "date": "..."}

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["checksum"] = self._compute_checksum()
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def _compute_checksum(self) -> str:
        """计算内容校验和"""
        content = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)
        # 排除 checksum 字段本身
        content = content.replace('"checksum"', '')
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    def size_estimate(self) -> int:
        """估算 JSON 大小（字节）"""
        return len(self.to_json())


WORKSPACE_ROOT = _v3_ws_root()
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


def _is_noise(text: str) -> bool:
    value = str(text or '').strip().lower()
    if not value:
        return False
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in NOISE_PATTERNS)

class SeedMemoryBuilder:
    """种子记忆构建器 - 从现有记忆中提取种子"""

    MEMORY_MD = str(WORKSPACE_ROOT / "MEMORY.md")
    IDENTITY_MD = str(WORKSPACE_ROOT / "IDENTITY.md")
    USER_MD = str(WORKSPACE_ROOT / "USER.md")
    SOUL_MD = str(WORKSPACE_ROOT / "SOUL.md")

    def build(self) -> SeedMemory:
        """从现有文件构建种子记忆"""
        seed = SeedMemory(
            created_at=datetime.now().isoformat(),
        )

        # 1. 提取身份
        seed.identity = self._extract_identity()
        seed.agent_name = seed.identity.get("name", "unknown")

        # 2. 提取判断规则
        seed.judgment_rules = self._extract_judgment_rules()

        # 3. 提取用户偏好
        seed.preferences = self._extract_preferences()

        # 4. 提取红线
        seed.red_lines = self._extract_red_lines()

        # 5. 提取最重要的教训
        seed.top_lessons = self._extract_top_lessons()

        # 计算校验和
        seed.checksum = seed._compute_checksum()

        return seed

    def _extract_identity(self) -> Dict:
        """从 IDENTITY.md 和 SOUL.md 提取身份"""
        identity = {
            "name": "",
            "creature": "",
            "vibe": "",
            "emoji": "",
            "core_truths": [],
        }

        # 读 IDENTITY.md
        if os.path.exists(self.IDENTITY_MD):
            with open(self.IDENTITY_MD, 'r') as f:
                content = f.read()
            for line in content.split('\n'):
                if line.startswith('- **Name:**'):
                    identity["name"] = line.split(':', 1)[-1].strip().strip('*').strip()
                elif line.startswith('- **Creature:**'):
                    identity["creature"] = line.split(':', 1)[-1].strip().strip('*').strip()
                elif line.startswith('- **Vibe:**'):
                    identity["vibe"] = line.split(':', 1)[-1].strip().strip('*').strip()
                elif line.startswith('- **Emoji:**'):
                    identity["emoji"] = line.split(':', 1)[-1].strip().strip('*').strip()

        # 从 SOUL.md 提取核心信念
        if os.path.exists(self.SOUL_MD):
            with open(self.SOUL_MD, 'r') as f:
                content = f.read()
            # 提取加粗的要点
            import re
            truths = re.findall(r'\*\*(.+?)\*\*', content)
            identity["core_truths"] = [t for t in truths[:5] if len(t) < 80]

        return identity

    def _extract_judgment_rules(self) -> List[Dict]:
        """从 MEMORY.md 提取判断规则（IF/THEN 格式）"""
        rules = []

        if not os.path.exists(self.MEMORY_MD):
            return rules

        with open(self.MEMORY_MD, 'r') as f:
            content = f.read()

        # 提取 IF/THEN 块
        import re
        blocks = re.findall(r'```\s*\n(.*?)\n```', content, re.DOTALL)

        for block in blocks:
            if 'IF' in block and 'THEN' in block:
                rule = {"trigger": "", "action": "", "avoid": "", "importance": 0.8}
                lines = block.strip().split('\n')
                for line in lines:
                    line = line.strip()
                    if line.startswith('IF '):
                        rule["trigger"] = line[3:]
                    elif line.startswith('THEN '):
                        rule["action"] = line[5:]
                    elif line.startswith('NOT '):
                        rule["avoid"] = line[4:]
                if rule["trigger"] and rule["action"]:
                    rules.append(rule)

        # 限制数量（种子要精简）
        return rules[:15]

    def _extract_preferences(self) -> List[Dict]:
        """从 MEMORY.md 提取用户偏好"""
        prefs = []

        if not os.path.exists(self.MEMORY_MD):
            return prefs

        with open(self.MEMORY_MD, 'r') as f:
            content = f.read()

        # 查找偏好表格行
        import re
        rows = re.findall(r'\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|', content)
        for row in rows:
            if '偏好' in row[0] or '冰美式' in row[1] or 'IF' in row[1]:
                prefs.append({
                    "context": row[1].strip(),
                    "preference": row[2].strip(),
                    "ttl": row[3].strip() if row[3].strip() != "永久" else "permanent",
                })

        return prefs[:10]

    def _extract_red_lines(self) -> List[Dict]:
        """从 MEMORY.md 和 TOOLS.md 提取红线"""
        red_lines = []

        if not os.path.exists(self.MEMORY_MD):
            return red_lines

        with open(self.MEMORY_MD, 'r') as f:
            content = f.read()

        # 查找"禁止"、"绝对"、"铁律"相关内容
        import re
        for line in content.split('\n'):
            if any(kw in line for kw in ['禁止', '绝对', '铁律', '绝不']):
                clean = line.strip().lstrip('-').lstrip('*').strip()
                if clean and len(clean) < 100:
                    red_lines.append(clean)

        return red_lines[:5]

    def _extract_top_lessons(self) -> List[Dict]:
        """从 MEMORY.md 提取最重要的教训"""
        lessons = []

        if not os.path.exists(self.MEMORY_MD):
            return lessons

        with open(self.MEMORY_MD, 'r') as f:
            content = f.read()

        # 查找教训段落
        import re
        # 找 "教训" 或 "血的教训" 相关段落
        lesson_blocks = re.findall(r'(?:教训|踩坑|血的教训)[：:]\s*\n((?:\s*-.*\n)+)', content)
        for block in lesson_blocks:
            for line in block.strip().split('\n'):
                clean = line.strip().lstrip('-').strip()
                if clean and len(clean) > 10 and not _is_noise(clean):
                    lessons.append({
                        "lesson": clean[:100],
                        "context": "MEMORY.md",
                        "date": datetime.now().strftime("%Y-%m-%d"),
                    })

        return lessons[:3]

    def validate(self, seed: SeedMemory) -> Dict:
        """验证种子记忆的完整性"""
        issues = []

        if not seed.agent_name:
            issues.append("缺少 Agent 名称")
        if not seed.identity.get("core_truths"):
            issues.append("缺少核心信念")
        if not seed.judgment_rules:
            issues.append("缺少判断规则")
        if seed.size_estimate() > 4096:
            issues.append(f"种子过大 ({seed.size_estimate()} 字节)，建议控制在 4KB 以内")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "size_bytes": seed.size_estimate(),
            "rules_count": len(seed.judgment_rules),
            "prefs_count": len(seed.preferences),
            "lessons_count": len(seed.top_lessons),
        }


def main():
    """命令行入口 - 构建并输出种子记忆"""
    builder = SeedMemoryBuilder()
    seed = builder.build()
    validation = builder.validate(seed)

    print("=" * 50)
    print("🌱 种子记忆构建报告")
    print("=" * 50)
    print(f"Agent: {seed.agent_name}")
    print(f"大小: {validation['size_bytes']} 字节")
    print(f"判断规则: {validation['rules_count']} 条")
    print(f"用户偏好: {validation['prefs_count']} 条")
    print(f"核心教训: {validation['lessons_count']} 条")
    print(f"红线: {len(seed.red_lines)} 条")
    print(f"校验和: {seed.checksum}")
    print()

    if validation["valid"]:
        print("✅ 种子记忆验证通过")
    else:
        print("⚠️ 种子记忆存在问题:")
        for issue in validation["issues"]:
            print(f"  - {issue}")

    # 输出种子 JSON
    if "--json" in __import__('sys').argv:
        print("\n── 种子记忆 JSON ──")
        print(seed.to_json())

    # 保存到文件
    output_path = str(WORKSPACE_ROOT / "memory" / "seed_memory.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(seed.to_json())
    print(f"\n💾 已保存到: {output_path}")


if __name__ == "__main__":
    main()
