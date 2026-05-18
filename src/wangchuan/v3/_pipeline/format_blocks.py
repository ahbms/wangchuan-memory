"""
格式化模块 — XML tag 格式化和 block 提取逻辑

从 WangchuanPipeline 中提取的纯函数模块，零外部依赖。
"""

import re
from typing import Dict, List


class FormatBlocks:
    """XML / Block 格式化器"""

    @staticmethod
    def extract_block_items(text: str, block_name: str) -> List[str]:
        """从 XML 格式文本提取 block 内容"""
        pattern = rf"<{block_name}>\n(.*?)\n</{block_name}>"
        match = re.search(pattern, text or "", re.S)
        if not match:
            return []
        body = match.group(1)
        items: List[str] = []
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("- "):
                items.append(line[2:].strip())
        return items

    @staticmethod
    def format_memory_recall_block(items: List[Dict], route: str) -> str:
        """格式化 memory recall block"""
        if not items:
            return ""
        block_name = "raw_memory_recall" if route == "raw" else "scar_memory_recall"
        lines = []
        for item in items[:5]:
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            metadata_bits = []
            for key in [
                "source_layer", "memory_type", "subject_domain", "evidence_level",
                "user_explicit", "source_anchor", "turn_signature", "promotion_reason"
            ]:
                value = item.get(key)
                if value not in (None, "", False):
                    metadata_bits.append(f"{key}={value}")
            if metadata_bits:
                lines.append(f"- [{' | '.join(metadata_bits)}] {content[:400]}")
            else:
                lines.append(f"- {content[:400]}")
        if not lines:
            return ""
        return f"<{block_name}>\n" + "\n".join(lines) + f"\n</{block_name}>"

    @staticmethod
    def format_resource_recall_block(items: List[Dict[str, object]]) -> str:
        """格式化 resource recall block"""
        if not items:
            return ""
        lines = []
        for item in items[:4]:
            title = str(item.get("title") or "").strip()
            path = str(item.get("path") or "").strip()
            preview = str(item.get("preview") or "").strip()
            metadata_bits = []
            if title:
                metadata_bits.append(f"title={title}")
            if path:
                metadata_bits.append(f"path={path}")
            if item.get("score") is not None:
                metadata_bits.append(f"score={item.get('score')}")
            if item.get("matched_terms"):
                metadata_bits.append("matched_terms=" + "/".join(str(term) for term in list(item.get("matched_terms") or [])[:4]))
            text = preview[:240] if preview else title or path
            if metadata_bits:
                lines.append(f"- [{' | '.join(metadata_bits)}] {text}")
            elif text:
                lines.append(f"- {text}")
        if not lines:
            return ""
        return "<resource_recall>\n" + "\n".join(lines) + "\n</resource_recall>"

    @staticmethod
    def format_skill_recall_block(items: List[Dict[str, object]]) -> str:
        """格式化 skill recall block"""
        if not items:
            return ""
        lines = []
        for item in items[:4]:
            name = str(item.get("name") or "").strip()
            path = str(item.get("path") or "").strip()
            description = str(item.get("description") or "").strip()
            preview = str(item.get("preview") or "").strip()
            metadata_bits = []
            if name:
                metadata_bits.append(f"name={name}")
            if path:
                metadata_bits.append(f"path={path}")
            if item.get("score") is not None:
                metadata_bits.append(f"score={item.get('score')}")
            if item.get("matched_terms"):
                metadata_bits.append("matched_terms=" + "/".join(str(term) for term in list(item.get("matched_terms") or [])[:4]))
            text = description[:200] if description else preview[:200] if preview else name or path
            if metadata_bits:
                lines.append(f"- [{' | '.join(metadata_bits)}] {text}")
            elif text:
                lines.append(f"- {text}")
        if not lines:
            return ""
        return "<skill_recall>\n" + "\n".join(lines) + "\n</skill_recall>"

    @staticmethod
    def candidate_brief_text(item: Dict[str, object]) -> str:
        """候选摘要文本"""
        for key in ["content", "content_preview", "title", "name", "resonance_reason", "seed_reason"]:
            value = str(item.get(key) or "").strip()
            if value:
                return value[:180]
        return ""

    @classmethod
    def format_resonance_decision_block(cls, view: Dict[str, object]) -> str:
        """格式化共鸣决策 block"""
        if not isinstance(view, dict) or not view:
            return ""

        summary = dict(view.get("summary") or {})
        scope_route = str(view.get("scope_route") or "memory")
        primary_candidates = list(view.get("primary_candidates", []) or [])
        supporting_candidates = list(view.get("supporting_candidates", []) or [])
        evidence_candidates = list(view.get("evidence_candidates", []) or [])

        if scope_route == "memory" and not primary_candidates:
            primary_candidates = supporting_candidates[:1] or evidence_candidates[:1]

        lines = ["<resonance_decision>"]
        lines.append(
            "- summary: "
            f"scope_route={scope_route} | "
            f"primary_kind={summary.get('primary_kind', '')} | "
            f"primary_role={summary.get('primary_role', '')} | "
            f"primary_reason={summary.get('primary_reason', '')}"
        )

        if scope_route == "resource":
            lines.append("- instruction: 先使用 resource 主候选回答，memory 只作背景补充，不得覆盖资源主线。")
        elif scope_route == "skill":
            lines.append("- instruction: 先使用 skill 主候选回答，memory 只作背景补充，不得覆盖技能主线。")
        else:
            lines.append("- instruction: 先依据共振主候选组织回答，再用 supporting/evidence 做校验；history 只能补充，不能覆盖主记忆层。")

        for index, item in enumerate(primary_candidates[:3], start=1):
            lines.append(
                f"- primary#{index}: "
                f"kind={item.get('candidate_kind', '')} | "
                f"role={item.get('decision_role', '')} | "
                f"why={item.get('why_selected', '')} | "
                f"uri={item.get('context_uri', '')} | "
                f"text={cls.candidate_brief_text(item)}"
            )

        for index, item in enumerate(supporting_candidates[:4], start=1):
            lines.append(
                f"- support#{index}: "
                f"kind={item.get('candidate_kind', '')} | "
                f"role={item.get('decision_role', '')} | "
                f"why={item.get('why_selected', '')} | "
                f"uri={item.get('context_uri', '')} | "
                f"text={cls.candidate_brief_text(item)}"
            )

        for index, item in enumerate(evidence_candidates[:3], start=1):
            lines.append(
                f"- evidence#{index}: "
                f"kind={item.get('candidate_kind', '')} | "
                f"memory_type={item.get('memory_type', '')} | "
                f"domain={item.get('subject_domain', '')} | "
                f"uri={item.get('context_uri', '')} | "
                f"text={cls.candidate_brief_text(item)}"
            )

        for index, item in enumerate(list(view.get("pattern_candidates", []) or [])[:3], start=1):
            lines.append(
                f"- pattern#{index}: "
                f"pattern_id={item.get('pattern_id', '')} | "
                f"status={item.get('status', '')} | "
                f"support_count={item.get('support_count', 0)} | "
                f"counter_evidence_count={item.get('counter_evidence_count', 0)}"
            )

        lines.append("</resonance_decision>")
        return "\n".join(lines)

    @staticmethod
    def format_recall_degraded_block(stage: str, reason: str, mode: str) -> str:
        """格式化降级 recall block"""
        reason_text = str(reason or "").strip().replace("\n", " ")[:220]
        return (
            "<recall_degraded>\n"
            f"- stage={stage or 'unknown'}\n"
            f"- mode={mode or 'fallback'}\n"
            f"- reason={reason_text or 'unknown'}\n"
            "</recall_degraded>"
        )
