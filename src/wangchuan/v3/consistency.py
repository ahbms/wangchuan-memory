#!/usr/bin/env python3
"""
叙我层 - 叙事一致性检查
天工开智 v2 · 第3层

检测回复风格的异常突变，区分"进化"和"矛盾"。
"""
from wangchuan.paths import workspace_root as _v3_ws_root

import os
import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class StyleProfile:
    """风格画像"""
    avg_length: float = 0.0          # 平均回复长度
    emoji_ratio: float = 0.0         # emoji 使用率
    question_ratio: float = 0.0      # 问句比例
    formality: str = "casual"        # formal/casual
    humor_markers: int = 0           # 幽默标记出现次数

    def to_dict(self) -> Dict:
        return {
            "avg_length": self.avg_length,
            "emoji_ratio": self.emoji_ratio,
            "question_ratio": self.question_ratio,
            "formality": self.formality,
            "humor_markers": self.humor_markers,
        }


@dataclass
class ConsistencyResult:
    """一致性检查结果"""
    consistent: bool
    drift_type: Optional[str]  # None / "evolution" / "contradiction"
    details: str = ""
    score: float = 1.0  # 1.0 = 完全一致, 0.0 = 完全不一致


WORKSPACE_ROOT = _v3_ws_root()

class ConsistencyChecker:
    """叙事一致性检查器"""

    PROFILE_PATH = str(WORKSPACE_ROOT / "memory" / "style_profile.json")

    def __init__(self, db_path: str = str(WORKSPACE_ROOT / "tiangong" / "wangchuan" / ".index" / "index.sqlite")):
        self.db_path = db_path
        self.profile = self._load_profile()

    def _load_profile(self) -> StyleProfile:
        """加载风格画像"""
        if os.path.exists(self.PROFILE_PATH):
            try:
                with open(self.PROFILE_PATH, 'r') as f:
                    data = json.load(f)
                    return StyleProfile(**data)
            except Exception as e:
                logger.warning("【WangChuan】[Consistency][Profile] load failed: %s", e)
        return StyleProfile()

    def save_profile(self):
        """保存风格画像"""
        try:
            with open(self.PROFILE_PATH, 'w') as f:
                json.dump(self.profile.to_dict(), f, indent=2)
        except Exception as e:
            logger.warning("【WangChuan】[Consistency][Profile] save failed: %s", e)

    def update_from_recent(self, n: int = 50) -> StyleProfile:
        """从最近 N 条助手回复更新风格画像"""
        if not os.path.exists(self.db_path):
            return self.profile

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT content FROM gm_messages WHERE role='assistant' "
                "ORDER BY timestamp DESC LIMIT ?",
                (n,)
            ).fetchall()
        except Exception as e:
            logger.warning("【WangChuan】[Consistency][Profile] recent fetch failed: %s", e)
            return self.profile
        finally:
            conn.close()

        if not rows:
            return self.profile

        texts = [r[0] for r in rows if r[0]]
        if not texts:
            return self.profile

        # 计算风格指标
        lengths = [len(t) for t in texts]
        self.profile.avg_length = sum(lengths) / len(lengths)

        # emoji 比例
        import re
        emoji_pattern = re.compile(
            "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001F900-\U0001F9FF"
            "\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF🦉🦞]+", flags=re.UNICODE
        )
        emoji_count = sum(len(emoji_pattern.findall(t)) for t in texts)
        total_chars = sum(lengths)
        self.profile.emoji_ratio = emoji_count / max(total_chars, 1)

        # 问句比例
        question_count = sum(1 for t in texts if '?' in t or '？' in t)
        self.profile.question_ratio = question_count / len(texts)

        # 幽默标记
        humor_signals = ['哈哈', '😄', '😂', '🤣', '🦞', 'hhh', '草']
        self.profile.humor_markers = sum(
            sum(1 for s in humor_signals if s in t) for t in texts
        )

        self.save_profile()
        return self.profile

    def check(self, response_text: str) -> ConsistencyResult:
        """检查单条回复是否与风格画像一致"""
        p = self.profile
        if p.avg_length == 0:
            return ConsistencyResult(consistent=True, drift_type=None, details="无历史数据")

        # 长度偏差
        length_ratio = len(response_text) / max(p.avg_length, 1)
        length_ok = 0.2 < length_ratio < 5.0  # 容许 5 倍偏差

        if not length_ok:
            if length_ratio > 5:
                return ConsistencyResult(
                    consistent=False,
                    drift_type="contradiction",
                    details=f"回复异常长：{len(response_text)}字 vs 平均{p.avg_length:.0f}字",
                    score=0.3,
                )
            elif length_ratio < 0.2:
                return ConsistencyResult(
                    consistent=True,  # 短回复一般没问题
                    drift_type=None,
                    details="短回复",
                    score=0.8,
                )

        return ConsistencyResult(consistent=True, drift_type=None, score=0.95)


def run_consistency_cycle(response_text: str = "") -> ConsistencyResult:
    """兼容运行入口，保留统一 cycle 级日志锚点。"""
    try:
        checker = ConsistencyChecker()
        checker.update_from_recent(50)
        return checker.check(response_text)
    except Exception as e:
        logger.warning("【WangChuan】[Consistency] cycle failed: %s", e)
        return ConsistencyResult(consistent=True, drift_type=None, details=str(e), score=0.0)


if __name__ == "__main__":
    checker = ConsistencyChecker()
    profile = checker.update_from_recent(100)
    print(f"📊 风格画像更新:")
    print(f"  平均长度: {profile.avg_length:.0f} 字")
    print(f"  Emoji率: {profile.emoji_ratio:.3f}")
    print(f"  问句率: {profile.question_ratio:.2f}")
    print(f"  幽默标记: {profile.humor_markers} 次")
