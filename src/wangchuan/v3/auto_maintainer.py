#!/usr/bin/env python3
"""
忆藏层 v2 - 自动记忆整理器
天工开智 v2 · 第2层

每日维护任务：
1. 归档旧对话（7天前）
2. 合并相似记忆
3. 置信度衰减（30天未触发降低置信度）
4. 同步 MEMORY.md
"""
from wangchuan.paths import workspace_root as _v3_ws_root

import os
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any

from wangchuan.memory_api import Memory


WORKSPACE_ROOT = _v3_ws_root()

class AutoMaintainer:
    """自动记忆维护"""

    def __init__(
        self,
        db_path: str = str(WORKSPACE_ROOT / "tiangong" / "wangchuan" / ".index" / "index.sqlite"),
        memory_md_path: str = str(WORKSPACE_ROOT / "MEMORY.md"),
    ):
        self.db_path = db_path
        self.memory_md_path = memory_md_path
        self.memory_api = Memory(self.db_path)

    def merge_similar_memories(self, similarity_threshold: float = 0.85) -> Dict[str, Any]:
        """
        合并相似记忆
        
        基于内容相似度合并记忆，累加证据数，保留最高置信度。
        
        Args:
            similarity_threshold: 相似度阈值 (0-1)
            
        Returns:
            {"merged": int, "deleted": int, "details": list}
        """
        if not os.path.exists(self.db_path):
            return {"merged": 0, "deleted": 0, "details": []}
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        merged_count = 0
        deleted_count = 0
        details = []
        
        try:
            # 获取所有记忆（按内容分组）
            memories = conn.execute("""
                SELECT id, content, confidence, evidence_count, type, importance
                FROM memories 
                WHERE confidence >= 0.3
                ORDER BY content
            """).fetchall()
            
            # 简单相似度匹配：基于关键词重叠
            processed = set()
            
            for i, mem1 in enumerate(memories):
                if mem1["id"] in processed:
                    continue
                    
                similar_ids = []
                content1 = mem1["content"].lower()
                words1 = set(content1.split())
                
                if len(words1) < 3:
                    continue
                
                for j, mem2 in enumerate(memories):
                    if i >= j or mem2["id"] in processed:
                        continue
                    
                    content2 = mem2["content"].lower()
                    words2 = set(content2.split())
                    
                    # Jaccard 相似度
                    if len(words1 | words2) > 0:
                        similarity = len(words1 & words2) / len(words1 | words2)
                        
                        if similarity >= similarity_threshold:
                            similar_ids.append({
                                "id": mem2["id"],
                                "content": mem2["content"],
                                "confidence": mem2["confidence"],
                                "evidence_count": mem2["evidence_count"],
                            })
                            processed.add(mem2["id"])
                
                # 合并相似记忆
                if similar_ids:
                    # 找出置信度最高的作为主记忆
                    all_candidates = similar_ids + [{
                        "id": mem1["id"],
                        "content": mem1["content"],
                        "confidence": mem1["confidence"],
                        "evidence_count": mem1["evidence_count"],
                    }]
                    
                    primary = max(all_candidates, key=lambda x: x["confidence"])
                    total_evidence = sum(c["evidence_count"] for c in all_candidates)
                    avg_confidence = sum(c["confidence"] for c in all_candidates) / len(all_candidates)
                    
                    # 更新主记忆
                    conn.execute("""
                        UPDATE memories 
                        SET evidence_count = ?, confidence = ?
                        WHERE id = ?
                    """, (total_evidence, min(1.0, avg_confidence + 0.1), primary["id"]))
                    
                    # 删除其他相似记忆
                    to_delete = [c["id"] for c in all_candidates if c["id"] != primary["id"]]
                    if to_delete:
                        placeholders = ",".join("?" * len(to_delete))
                        conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", to_delete)
                        deleted_count += len(to_delete)
                    
                    merged_count += len(all_candidates) - 1
                    details.append({
                        "primary_id": primary["id"],
                        "merged_count": len(all_candidates) - 1,
                        "total_evidence": total_evidence,
                    })
                    
                    processed.add(mem1["id"])
            
            conn.commit()
            
        except Exception as e:
            print(f"[合并] 相似记忆合并失败: {e}")
            conn.rollback()
        finally:
            conn.close()
        
        return {
            "merged": merged_count,
            "deleted": deleted_count,
            "details": details[:10]  # 限制详情数量
        }

    def apply_interaction_decay(self, decay_factor: float = 0.005) -> Dict[str, Any]:
        """
        高频置信度衰减 - 每次交互后调用
        
        模拟真实记忆退化：每次交互后，置信度轻微衰减。
        
        Args:
            decay_factor: 每次衰减系数 (默认 0.005 = 0.5%)
            
        Returns:
            {"decayed": int, "forgotten": int}
        """
        if not os.path.exists(self.db_path):
            return {"decayed": 0, "forgotten": 0}
        
        conn = sqlite3.connect(self.db_path)
        try:
            # 衰减所有非满置信度的记忆
            cursor = conn.execute("""
                UPDATE memories 
                SET confidence = MAX(confidence * ?, 0.1)
                WHERE confidence < 1.0
            """, (1 - decay_factor,))
            decayed_count = cursor.rowcount
            
            # 删除过低置信度记忆
            cursor = conn.execute("DELETE FROM memories WHERE confidence < 0.15")
            forgotten_count = cursor.rowcount
            
            conn.commit()
            
        except Exception as e:
            print(f"[衰减] 高频衰减失败: {e}")
            conn.rollback()
            return {"decayed": 0, "forgotten": 0}
        finally:
            conn.close()
        
        return {"decayed": decayed_count, "forgotten": forgotten_count}

    def extract_facts_from_conversations(self, days: int = 1) -> Dict[str, Any]:
        """
        从对话日志中自动提取事实
        
        基于关键词触发：
        - "我喜欢"、"我习惯" → preference
        - "记住" → rule
        - "我发现" → fact
        
        Args:
            days: 回溯天数
            
        Returns:
            {"extracted": int, "duplicates": int, "facts": list}
        """
        import re
        import json
        
        if not os.path.exists(self.db_path):
            return {"extracted": 0, "duplicates": 0, "facts": []}
        
        # 查找对话日志
        chat_log_paths = [
            WORKSPACE_ROOT / "memory" / "chat_logs",
            WORKSPACE_ROOT / "memory" / "archive",
            WORKSPACE_ROOT / ".openclaw" / "logs",
        ]
        
        facts_found = []
        keywords = {
            "preference": ["我喜欢", "我习惯", "我喜欢的是", "我不喜欢"],
            "rule": ["记住", "不要", "必须", "规定"],
            "fact": ["我发现", "原来", "实际上", "事实是"],
        }
        
        extracted_count = 0
        duplicate_count = 0
        
        conn = sqlite3.connect(self.db_path)
        
        for log_path in chat_log_paths:
            if not log_path.exists():
                continue
            
            # 查找最近的日志文件
            log_files = list(log_path.glob(f"*.jsonl")) + list(log_path.glob(f"*-*.jsonl"))
            log_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            
            for log_file in log_files[:days]:
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            try:
                                entry = json.loads(line)
                                text = entry.get('text', '') or entry.get('content', '')
                                if not text:
                                    continue
                                    
                                # 检查关键词
                                for fact_type, kws in keywords.items():
                                    for kw in kws:
                                        if kw in text:
                                            # 提取事实
                                            fact = re.sub(rf'^(.*?{kw})[:：\s]*', '', text).strip()
                                            if fact and len(fact) > 5:
                                                facts_found.append({
                                                    "content": fact,
                                                    "type": fact_type,
                                                    "source": str(log_file.name)
                                                })
                            except json.JSONDecodeError:
                                continue
                except Exception as e:
                    print(f"[提取] 读取日志失败 {log_file}: {e}")
        
        # 写入数据库
        for fact in facts_found:
            try:
                # 检查是否已存在
                existing = conn.execute(
                    "SELECT id FROM memories WHERE content = ?",
                    (fact["content"],)
                ).fetchone()
                
                if existing:
                    duplicate_count += 1
                    continue
                
                # 写入新记忆
                conn.execute("""
                    INSERT INTO memories (content, type, confidence, evidence_count, importance)
                    VALUES (?, ?, 0.6, 1, 0.5)
                """, (fact["content"], fact["type"]))
                extracted_count += 1
                
            except Exception as e:
                print(f"[提取] 写入失败: {e}")
        
        conn.commit()
        conn.close()
        
        return {
            "extracted": extracted_count,
            "duplicates": duplicate_count,
            "facts": facts_found[:10]
        }

    def update_core_summary(self, output_path: str = None) -> Dict[str, Any]:
        """
        更新核心摘要 MEMORY.md
        
        自动生成高置信度记忆摘要，供 Agent 启动时读取。
        
        Args:
            output_path: 输出路径，默认 MEMORY.md
            
        Returns:
            {"written": int, "path": str}
        """
        if output_path is None:
            output_path = self.memory_md_path
        
        if not os.path.exists(self.db_path):
            return {"written": 0, "path": output_path}
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        try:
            memories = conn.execute("""
                SELECT content, confidence, importance, type
                FROM memories
                WHERE confidence > 0.7 AND importance > 0.4
                ORDER BY confidence DESC, importance DESC
                LIMIT 50
            """).fetchall()
            
            conn.close()
            
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write("# 长期记忆\n\n")
                f.write(f"更新于 {datetime.now().isoformat()}\n\n")
                f.write("## 核心记忆\n\n")
                
                for mem in memories:
                    confidence_bar = "█" * int(mem["confidence"] * 10)
                    f.write(f"- [{confidence_bar}] {mem['content']}\n")

            return {"written": len(memories), "path": output_path}
        except Exception as e:
            print(f"[摘要] 更新失败: {e}")
            return {"written": 0, "path": output_path}

    def track_skills(self, skills_path: str = None) -> Dict[str, Any]:
        if skills_path is None:
            skills_path = str(WORKSPACE_ROOT / "skills.md")
        
        if not os.path.exists(self.db_path):
            return {"tracked": 0, "path": skills_path}
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        try:
            skill_keywords = ["学会", "掌握", "能", "会", "擅长", "技能", "能力", "方法"]
            skill_conditions = " OR ".join([f"content LIKE '%{kw}%'" for kw in skill_keywords])
            
            skills = conn.execute(f"""
                SELECT DISTINCT content, confidence, importance
                FROM memories
                WHERE ({skill_conditions})
                AND confidence > 0.6
                ORDER BY confidence DESC, importance DESC
                LIMIT 30
            """).fetchall()
            
            conn.close()
            
            skills_dir = os.path.dirname(skills_path)
            if skills_dir:
                os.makedirs(skills_dir, exist_ok=True)
            
            with open(skills_path, 'w', encoding='utf-8') as f:
                f.write("# 技能清单\n\n")
                f.write(f"更新于 {datetime.now().isoformat()}\n\n")
                
                for skill in skills:
                    content = skill["content"]
                    for kw in ["学会", "掌握", "能", "会", "擅长"]:
                        content = content.replace(kw, "").strip()
                    
                    if content and len(content) > 2:
                        f.write(f"- {content}\n")
            
            return {"tracked": len(skills), "path": skills_path}
            
        except Exception as e:
            print(f"[技能] 追踪失败: {e}")
            return {"tracked": 0, "path": skills_path}

    def daily_maintenance(self) -> dict:
        """执行每日维护"""
        results = {
            "archived": 0,
            "confidence_decayed": 0,
            "synced": False,
        }

        if not os.path.exists(self.db_path):
            return results

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            decay_threshold = (datetime.now() - timedelta(days=30)).isoformat()
            candidates = conn.execute(
                "SELECT id, confidence FROM memories WHERE (last_recall IS NULL OR last_recall < ?) AND confidence > 0.3",
                (decay_threshold,)
            ).fetchall()
            cursor = conn.execute(
                "UPDATE memories SET confidence = MAX(confidence - 0.05, 0.1) "
                "WHERE (last_recall IS NULL OR last_recall < ?) AND confidence > 0.3",
                (decay_threshold,)
            )
            results["confidence_decayed"] = cursor.rowcount

            archived_rows = conn.execute(
                "SELECT id FROM memories WHERE confidence < 0.15"
            ).fetchall()
            cursor = conn.execute(
                "DELETE FROM memories WHERE confidence < 0.15"
            )
            results["archived"] = cursor.rowcount
            conn.commit()
        except Exception as e:
            print(f"[整理] 维护失败: {e}")
        finally:
            conn.close()

        try:
            if candidates:
                for row in candidates:
                    new_confidence = max(0.1, float(row["confidence"] or 0.0) - 0.05)
                    self.memory_api.sync_maintenance_updates(
                        [row["id"]],
                        confidence=new_confidence,
                        lifecycle="aging",
                    )
            if archived_rows:
                self.memory_api.sync_maintenance_updates(
                    [row["id"] for row in archived_rows],
                    lifecycle="archived",
                    promotion_state="archived",
                    remove=True,
                )
        except Exception as e:
            print(f"[整理] schema 同步失败: {e}")

        return results

    def bionic_maintenance(self) -> Dict[str, Any]:
        """
        完整仿生记忆维护流程
        
        执行所有仿生记忆功能：
        1. 相似记忆合并
        2. 高频置信度衰减
        3. 自动事实提取
        4. 核心摘要更新
        5. 技能追踪
        
        Returns:
            各功能执行结果
        """
        results = {}
        
        print("🧠 仿生记忆维护开始...")
        
        print("  1. 相似记忆合并...")
        results["merge"] = self.merge_similar_memories()
        print(f"     合并: {results['merge']['merged']}, 删除: {results['merge']['deleted']}")
        
        print("  2. 置信度衰减...")
        results["decay"] = self.apply_interaction_decay()
        print(f"     衰减: {results['decay']['decayed']}, 遗忘: {results['decay']['forgotten']}")
        
        print("  3. 事实提取...")
        results["extract"] = self.extract_facts_from_conversations()
        print(f"     提取: {results['extract']['extracted']}, 重复: {results['extract']['duplicates']}")
        
        print("  4. 摘要更新...")
        results["summary"] = self.update_core_summary()
        print(f"     写入: {results['summary']['written']} 条")
        
        print("  5. 技能追踪...")
        results["skills"] = self.track_skills()
        print(f"     追踪: {results['skills']['tracked']} 项")
        
        print("✅ 仿生记忆维护完成")
        return results


if __name__ == "__main__":
    print("🧹 忆藏层 · 自动记忆整理器")
    print("=" * 50)
    maintainer = AutoMaintainer()
    result = maintainer.bionic_maintenance()
    print(f"\n汇总: {result}")
