from __future__ import annotations

"""
忘川记忆系统评测基准框架

参考 Mem0 的 LoCoMo/LongMemEval 评测基准：
- 记忆检索准确率
- 记忆一致性
- 遗忘曲线验证
- 多级记忆性能

说明：
- retrieval 与 latency 评测默认使用隔离库 + 固定 seed，避免被生产库现状污染
- consistency / forgetting / multi_level 仍基于当前主库，反映真实运行状态
"""

import json
import os
import random
import re
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


WORKSPACE = Path(
    os.getenv("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[3]
).resolve()
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from wangchuan import Memory


TEST_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    type TEXT DEFAULT 'fact',
    confidence REAL DEFAULT 0.7,
    evidence_count INTEGER DEFAULT 1,
    sentiment TEXT DEFAULT 'neutral',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_recall TIMESTAMP,
    emotion TEXT DEFAULT '{}',
    importance REAL DEFAULT 0.5,
    temperature TEXT DEFAULT 'warm',
    last_trigger TIMESTAMP,
    trigger_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_confidence ON memories(confidence);
CREATE INDEX IF NOT EXISTS idx_updated ON memories(updated_at);
CREATE VIRTUAL TABLE IF NOT EXISTS fts_memories
USING fts5(content, content=memories, content_rowid=id);
"""


class BenchmarkDataset:
    """评测数据集"""

    def __init__(self):
        self.preferences = [
            "用户喜欢冰美式咖啡",
            "用户不吃辣",
            "用户住在望京",
            "用户是程序员",
            "用户喜欢跑步",
            "用户养了一只猫",
            "用户喜欢周杰伦的歌",
            "用户不看恐怖片",
            "用户习惯晚睡晚起",
            "用户喜欢简约风格",
        ]

        self.facts = [
            "用户叫张三",
            "用户在字节跳动工作",
            "用户毕业于清华",
            "用户是北京人",
            "用户会Python和Go",
            "用户去过日本旅游",
            "用户喜欢吃火锅",
            "用户学过吉他",
            "用户喜欢打篮球",
            "用户参加过马拉松",
        ]

        self.rules = [
            "用户要求称呼他张哥",
            "用户不喜欢被问隐私",
            "用户只在上午9点后回复",
            "用户需要简洁的回答",
            "用户禁止发送表情包",
        ]

    def get_all(self) -> List[Dict[str, str]]:
        all_items: List[Dict[str, str]] = []
        for p in self.preferences:
            all_items.append({"content": p, "type": "preference"})
        for f in self.facts:
            all_items.append({"content": f, "type": "fact"})
        for r in self.rules:
            all_items.append({"content": r, "type": "rule"})
        return all_items

    def sample(self, n: int = 5) -> List[Dict[str, str]]:
        all_items = self.get_all()
        return random.sample(all_items, min(n, len(all_items)))


class MemoryBenchmark:
    """记忆系统评测基准"""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = str(WORKSPACE / "tiangong" / "wangchuan" / ".index" / "index.sqlite")
        self.db_path = db_path
        self.dataset = BenchmarkDataset()

    @staticmethod
    def _bootstrap_test_db(db_path: str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.executescript(TEST_DB_SCHEMA)
            conn.commit()

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", "", str(text or "").strip().lower())

    @classmethod
    def _text_matches_expected(cls, candidate: str, expected: str) -> bool:
        candidate_norm = cls._normalize_text(candidate)
        expected_norm = cls._normalize_text(expected)
        if not candidate_norm or not expected_norm:
            return False
        return expected_norm in candidate_norm or candidate_norm in expected_norm

    @classmethod
    def _char_bigrams(cls, text: str) -> set[str]:
        normalized = cls._normalize_text(text)
        if not normalized:
            return set()
        if len(normalized) < 2:
            return {normalized}
        return {normalized[i:i + 2] for i in range(len(normalized) - 1)}

    @classmethod
    def _query_aligns_with_expected(cls, query: str, expected_memories: Sequence[str]) -> bool:
        query_norm = cls._normalize_text(query)
        if not query_norm or not expected_memories:
            return True

        query_bigrams = cls._char_bigrams(query_norm)
        for expected in expected_memories:
            expected_norm = cls._normalize_text(expected)
            if not expected_norm:
                continue
            if query_norm in expected_norm or expected_norm in query_norm:
                return True
            if query_bigrams & cls._char_bigrams(expected_norm):
                return True
        return False

    @classmethod
    def _missing_expected_memories(cls, expected_memories: Sequence[str], corpus: Sequence[str]) -> List[str]:
        missing: List[str] = []
        for expected in expected_memories:
            if not any(cls._text_matches_expected(item, expected) for item in corpus):
                missing.append(expected)
        return missing

    def _build_seeded_db(self, items: Sequence[Dict[str, str]] | None = None) -> Tuple[tempfile.TemporaryDirectory, str, List[str]]:
        temp_dir = tempfile.TemporaryDirectory(prefix="wangchuan-benchmark-")
        db_path = str(Path(temp_dir.name) / "benchmark.sqlite")
        self._bootstrap_test_db(db_path)

        memory = Memory(db_path)
        seeded_contents: List[str] = []
        for item in items or self.dataset.get_all():
            result = memory.remember(
                item["content"],
                importance=0.6,
                tags=[item.get("type", "memory")],
            )
            if result.get("success"):
                seeded_contents.append(item["content"])

        return temp_dir, db_path, seeded_contents

    def evaluate_retrieval(
        self,
        query: str,
        expected_memories: Sequence[str],
        top_k: int = 5,
        db_path: str | None = None,
        corpus: Sequence[str] | None = None,
    ) -> Dict[str, Any]:
        """评估检索准确率。"""
        effective_db_path = db_path or self.db_path

        if corpus is not None:
            missing = self._missing_expected_memories(expected_memories, corpus)
            if missing:
                return {
                    "invalid_setup": True,
                    "error": f"expected memory not seeded: {missing}",
                    "recall": 0.0,
                    "precision": 0.0,
                    "mrr": 0.0,
                    "retrieved_count": 0,
                }

        if not self._query_aligns_with_expected(query, expected_memories):
            return {
                "invalid_setup": True,
                "error": "query does not align with expected memories",
                "recall": 0.0,
                "precision": 0.0,
                "mrr": 0.0,
                "retrieved_count": 0,
            }

        try:
            memory = Memory(effective_db_path)
            results = memory.recall(query, limit=max(top_k * 2, top_k))
            retrieved = [r.get("content", "") for r in results]

            recall_hits = 0
            for expected in expected_memories:
                if any(self._text_matches_expected(ret, expected) for ret in retrieved):
                    recall_hits += 1

            recall = recall_hits / len(expected_memories) if expected_memories else 0.0
            precision = recall  # 当前 benchmark 仍使用简化口径

            mrr = 0.0
            for index, retrieved_item in enumerate(retrieved):
                if any(self._text_matches_expected(retrieved_item, expected) for expected in expected_memories):
                    mrr = 1 / (index + 1)
                    break

            return {
                "invalid_setup": False,
                "recall": round(recall, 3),
                "precision": round(precision, 3),
                "mrr": round(mrr, 3),
                "retrieved_count": len(retrieved),
            }
        except Exception as e:
            return {
                "invalid_setup": False,
                "error": str(e),
                "recall": 0.0,
                "precision": 0.0,
                "mrr": 0.0,
                "retrieved_count": 0,
            }

    def evaluate_retrieval_suite(
        self,
        cases: Sequence[Tuple[str, Sequence[str]]],
        top_k: int = 5,
        db_path: str | None = None,
        corpus: Sequence[str] | None = None,
    ) -> Dict[str, Any]:
        """批量评估 retrieval，并把 invalid setup 从统计里剥离。"""
        case_results: List[Dict[str, Any]] = []
        valid_results: List[Dict[str, Any]] = []

        for query, expected in cases:
            result = self.evaluate_retrieval(
                query=query,
                expected_memories=expected,
                top_k=top_k,
                db_path=db_path,
                corpus=corpus,
            )
            case_result = {
                "query": query,
                "expected_memories": list(expected),
                **result,
            }
            case_results.append(case_result)
            if not result.get("invalid_setup"):
                valid_results.append(result)

        avg_recall = 0.0
        if valid_results:
            avg_recall = round(sum(item.get("recall", 0.0) for item in valid_results) / len(valid_results), 3)

        return {
            "mode": "isolated_seeded_corpus",
            "avg_recall": avg_recall,
            "tests": len(cases),
            "valid_tests": len(valid_results),
            "invalid_tests": len(cases) - len(valid_results),
            "cases": case_results,
        }

    def evaluate_consistency(self) -> Dict[str, Any]:
        """评估记忆一致性。"""
        if not os.path.exists(self.db_path):
            return {"consistency_score": 0, "error": "数据库不存在"}

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        try:
            memories = conn.execute(
                "SELECT content, confidence FROM memories ORDER BY created_at DESC LIMIT 100"
            ).fetchall()

            duplicates = 0
            for i, m1 in enumerate(memories):
                for m2 in memories[i + 1:]:
                    if m1["content"] == m2["content"]:
                        duplicates += 1

            similar = 0
            for i, m1 in enumerate(memories[:20]):
                for m2 in memories[i + 1:21]:
                    if m1["content"] != m2["content"]:
                        common = set(m1["content"].lower()) & set(m2["content"].lower())
                        if len(common) > len(m1["content"]) * 0.7:
                            similar += 1

            consistency = 1 - (duplicates + similar * 0.1) / len(memories) if memories else 0

            return {
                "consistency_score": round(max(0, min(1, consistency)), 3),
                "total_memories": len(memories),
                "duplicates": duplicates,
                "similar": similar,
            }
        finally:
            conn.close()

    def evaluate_forgetting(self, days_threshold: int = 7) -> Dict[str, Any]:
        """评估遗忘机制。"""
        if not os.path.exists(self.db_path):
            return {"forgetting_score": 0, "error": "数据库不存在"}

        threshold = (datetime.now() - timedelta(days=days_threshold)).isoformat()

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        try:
            old_memories = conn.execute(
                "SELECT confidence, importance FROM memories WHERE created_at < ?",
                (threshold,),
            ).fetchall()

            if not old_memories:
                return {"forgetting_score": 1.0, "old_memories": 0}

            decayed_count = sum(1 for m in old_memories if m["confidence"] < 0.8)
            forgetting_score = decayed_count / len(old_memories)

            return {
                "forgetting_score": round(forgetting_score, 3),
                "old_memories": len(old_memories),
                "decayed": decayed_count,
            }
        finally:
            conn.close()

    def evaluate_multi_level(self) -> Dict[str, Any]:
        """评估多级记忆性能。"""
        if not os.path.exists(self.db_path):
            return {"level_score": 0, "error": "数据库不存在"}

        conn = sqlite3.connect(self.db_path)
        try:
            levels = {}
            for level in ["user", "session", "agent", "extracted"]:
                count = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE temperature = ?",
                    (level,),
                ).fetchone()[0]
                levels[level] = count

            total = sum(levels.values())
            level_score = len([v for v in levels.values() if v > 0]) / 4

            return {
                "level_score": round(level_score, 3),
                "levels": levels,
                "total": total,
            }
        finally:
            conn.close()

    def evaluate_latency(self, iterations: int = 10) -> Dict[str, Any]:
        """评估响应延迟，使用隔离库避免污染主库。"""
        try:
            with tempfile.TemporaryDirectory(prefix="wangchuan-latency-") as temp_dir:
                db_path = str(Path(temp_dir) / "latency.sqlite")
                self._bootstrap_test_db(db_path)
                memory = Memory(db_path)

                memory.remember("latency warmup", importance=0.6)

                write_times = []
                for _ in range(iterations):
                    start = time.time()
                    memory.remember(f"latency_probe_{random.randint(1000, 9999)}", importance=0.6)
                    write_times.append(time.time() - start)

                read_times = []
                for _ in range(iterations):
                    start = time.time()
                    memory.recall("latency_probe", limit=5)
                    read_times.append(time.time() - start)

                return {
                    "mode": "isolated_temp_db",
                    "write_latency_avg": round(sum(write_times) / len(write_times) * 1000, 2),
                    "read_latency_avg": round(sum(read_times) / len(read_times) * 1000, 2),
                    "write_latency_p50": round(sorted(write_times)[len(write_times) // 2] * 1000, 2),
                    "read_latency_p50": round(sorted(read_times)[len(read_times) // 2] * 1000, 2),
                }
        except Exception as e:
            return {"error": str(e)}

    def run_full_benchmark(self) -> Dict[str, Any]:
        """运行完整评测基准。"""
        print("🧪 忘川记忆系统评测基准")
        print("=" * 50)

        results: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "retrieval": {},
            "consistency": {},
            "forgetting": {},
            "multi_level": {},
            "latency": {},
        }

        print("1. 检索准确率评测...")
        test_queries = [
            ("冰美式", ["冰美式"]),
            ("不吃辣", ["不吃辣"]),
            ("跑步", ["跑步"]),
        ]
        temp_dir, retrieval_db_path, seeded_contents = self._build_seeded_db(self.dataset.get_all())
        try:
            results["retrieval"] = self.evaluate_retrieval_suite(
                test_queries,
                db_path=retrieval_db_path,
                corpus=seeded_contents,
            )
        finally:
            temp_dir.cleanup()
        print(
            "   平均召回率: "
            f"{results['retrieval'].get('avg_recall', 0)} "
            f"(valid={results['retrieval'].get('valid_tests', 0)}, invalid={results['retrieval'].get('invalid_tests', 0)})"
        )

        print("2. 记忆一致性评测...")
        results["consistency"] = self.evaluate_consistency()
        print(f"   一致性分数: {results['consistency'].get('consistency_score', 0)}")

        print("3. 遗忘机制评测...")
        results["forgetting"] = self.evaluate_forgetting()
        print(f"   遗忘分数: {results['forgetting'].get('forgetting_score', 0)}")

        print("4. 多级记忆评测...")
        results["multi_level"] = self.evaluate_multi_level()
        print(f"   分层分数: {results['multi_level'].get('level_score', 0)}")

        print("5. 响应延迟评测...")
        results["latency"] = self.evaluate_latency(iterations=5)
        print(f"   写入延迟: {results['latency'].get('write_latency_avg', 'N/A')}ms")
        print(f"   读取延迟: {results['latency'].get('read_latency_avg', 'N/A')}ms")

        overall = (
            (results["retrieval"].get("avg_recall", 0) or 0) * 0.30
            + results["consistency"].get("consistency_score", 0) * 0.25
            + results["forgetting"].get("forgetting_score", 0) * 0.15
            + results["multi_level"].get("level_score", 0) * 0.15
            + (1 - min(results["latency"].get("read_latency_avg", 1000) / 1000, 1)) * 0.15
        )

        results["overall_score"] = round(overall, 3)

        print("=" * 50)
        print(f"📊 综合得分: {results['overall_score']}")

        return results

    def save_results(self, results: Dict[str, Any], path: str | None = None):
        """保存评测结果。"""
        if path is None:
            path = str(WORKSPACE / "tiangong" / "wangchuan" / "benchmark_results.json")

        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        print(f"📁 结果已保存到: {path}")


def run_benchmark():
    """运行评测基准。"""
    benchmark = MemoryBenchmark()
    results = benchmark.run_full_benchmark()
    benchmark.save_results(results)
    return results


if __name__ == "__main__":
    run_benchmark()
