"""
Query 分析模块 — 分析用户 query，决定召回路由、scope 路由、上下文路由，检测特殊查询类型

从 WangchuanPipeline 中提取的纯函数模块，零外部依赖。
"""

import re
from typing import Dict, List


class QueryProfiler:
    """Query 分析与路由决策器"""

    @staticmethod
    def build_query_preference_profile(query: str) -> Dict[str, object]:
        """分析 query 文本，生成路由偏好（route/scope_route/context_route/preferred_* 等）"""
        text = (query or "").lower()
        raw_markers = [
            "上次怎么说", "上次是怎么说", "原话", "原文", "原始", "完整讨论", "完整对话",
            "当时怎么聊", "证据", "聊天记录", "记录里怎么写"
        ]
        scar_markers = [
            "踩坑", "教训", "经验", "默认怎么判断", "规则", "以前怎么修",
            "之前怎么修", "注意什么", "避坑", "结论"
        ]
        summary_markers = [
            "总结", "摘要", "概况", "概览", "进展", "还剩哪些", "当前状态", "现在到哪了", "总结一下"
        ]
        checkpoint_markers = [
            "下一步", "接下来", "checkpoint", "检查点", "任务状态", "当前步骤", "下一动作", "待做", "blocker", "阻塞", "先做什么", "哪一步", "刚刚那个", "哪块", "主线", "那个呢", "先哪个"
        ]
        handoff_markers = [
            "交接", "handoff", "恢复", "resume", "续上", "接着干", "接上次", "从上次继续"
        ]
        evidence_markers = [
            "证据", "依据", "锚点", "出处", "链接", "哪次", "哪条", "原始记录", "对应记录"
        ]

        profile: Dict[str, object] = {
            "text": text,
            "route": "default",
            "scope_route": "memory",
            "context_route": "default",
            "premise_challenge": False,
            "preferred_layers": ["mixed", "scar", "raw"],
            "preferred_types": [],
            "preferred_domains": [],
            "preferred_evidence": [],
            "topic_tokens": [],
            "preferred_sections": [
                "session_summary", "task_checkpoint", "graph", "episodic",
                "dag_summary", "tail", "handoff_pack", "evidence_pack"
            ],
            "suppressed_sections": [],
        }

        skill_markers = [
            "技能", "skill", "怎么做", "流程", "方法", "命令", "tool", "工具", "capability"
        ]
        resource_markers = [
            "路径", "配置", "config", "文档", "手册", "url", "域名", "端口", "日志", "任务板", "readme"
        ]

        skill_hits = [marker for marker in skill_markers if marker in text]
        resource_hits = [marker for marker in resource_markers if marker in text]

        if skill_hits:
            profile["scope_route"] = "skill"
        if resource_hits and not skill_hits:
            profile["scope_route"] = "resource"

        profile["scope_route_profile"] = {
            "scope_route": profile.get("scope_route", "memory"),
            "matched_skill_markers": skill_hits,
            "matched_resource_markers": resource_hits,
            "fallback_reader": "memory_recall",
            "phase": "p2_scope_route_mainline",
        }

        if any(marker in text for marker in raw_markers):
            profile["route"] = "raw"
            profile["preferred_layers"] = ["raw", "mixed", "scar"]
            profile["preferred_evidence"] = ["raw"]
            profile["preferred_types"] = ["conversation"]
        elif any(marker in text for marker in scar_markers):
            profile["route"] = "scar"
            profile["preferred_layers"] = ["scar", "mixed", "raw"]
            profile["preferred_evidence"] = ["summarized"]
            profile["preferred_types"] = ["lesson", "rule", "decision"]

        if any(marker in text for marker in summary_markers):
            profile["context_route"] = "summary"
            profile["preferred_sections"] = ["session_summary", "task_checkpoint", "dag_summary", "tail", "evidence_pack"]
            profile["suppressed_sections"] = ["handoff_pack"]
        if any(marker in text for marker in checkpoint_markers):
            profile["context_route"] = "checkpoint"
            profile["preferred_sections"] = ["task_checkpoint", "session_summary", "tail", "handoff_pack"]
            profile["suppressed_sections"] = ["dag_summary"]
        if any(marker in text for marker in handoff_markers):
            profile["context_route"] = "handoff"
            profile["preferred_sections"] = ["task_checkpoint", "handoff_pack", "session_summary", "evidence_pack", "tail"]
            profile["suppressed_sections"] = ["graph", "dag_summary"]
        if any(marker in text for marker in evidence_markers):
            profile["context_route"] = "evidence"
            profile["preferred_sections"] = ["evidence_pack", "episodic", "graph", "tail", "session_summary"]
            profile["suppressed_sections"] = ["handoff_pack", "dag_summary"]

        profile["premise_challenge"] = QueryProfiler.is_premise_challenge_query(text)

        domain_rules = [
            ("user", [
                "用户", "偏好", "称呼", "沟通", "回复风格", "分段回复", "少确认",
                "关键节点汇报", "透明黑盒", "执行偏好", "任务板", "实施路线图", "路线图",
                "markdown", "文档扩散"
            ]),
            ("ops", ["网关", "gateway", "服务", "部署", "重启", "systemctl"]),
            ("code", ["代码", "python", "测试", "导入", "函数", "模块", "架构"]),
            ("rule", ["规则", "铁律", "默认", "教训", "踩坑", "经验"]),
        ]
        topic_tokens: List[str] = []
        for domain, tokens in domain_rules:
            matched = [token for token in tokens if token in text]
            if matched:
                cast_domains = profile["preferred_domains"]
                if isinstance(cast_domains, list):
                    cast_domains.append(domain)
                topic_tokens.extend(matched)
                cast_types = profile["preferred_types"]
                if isinstance(cast_types, list):
                    if domain == "rule":
                        cast_types.extend(["rule", "lesson"])
                    elif domain == "user":
                        cast_types.extend(["preference", "identity", "rule"])

        unique_topic_tokens: List[str] = []
        for token in topic_tokens:
            if token not in unique_topic_tokens:
                unique_topic_tokens.append(token)
        profile["topic_tokens"] = unique_topic_tokens
        return profile

    @staticmethod
    def memory_route(query: str) -> str:
        """从 query 推导 memory route（raw/scar/default）"""
        profile = QueryProfiler.build_query_preference_profile(query)
        return str(profile.get("route", "default"))

    @staticmethod
    def is_premise_challenge_query(query_text: str) -> bool:
        """检测"前提挑战型"查询（用户质疑 agent 刚说的内容）"""
        text = str(query_text or "").strip().lower()
        if not text:
            return False

        premise_markers = [
            "你刚不是说", "你刚才说", "按你刚才说的", "按你说的", "如果按你", "不是说先", "不是说",
        ]
        if not any(marker in text for marker in premise_markers):
            return False

        self_correction_markers = ["我是说", "我说的是", "不是那个", "不对，也不是", "也不是"]
        if any(marker in text for marker in self_correction_markers):
            return False

        return True

    @staticmethod
    def is_short_followup_query(query: str) -> bool:
        """检测短追问查询（≤2 词、指代词等）"""
        text = (query or "").strip()
        if not text:
            return True
        compact = re.sub(r"\s+", "", text).lower()
        short_markers = {
            "a", "aa", "ok", "ok了", "继续", "继续吧", "继续查", "继续看", "继续深挖",
            "在吗", "卡了", "咋样了", "好了没", "然后呢", "还有呢", "嗯", "额", "嗨"
        }
        if compact in short_markers:
            return True
        if len(compact) <= 3:
            return True
        if len(compact) <= 6 and all(ch in "继续查看问吗呢啊呀呀了吧啦嘛哦噢嗯额在卡" for ch in compact):
            return True
        return False
