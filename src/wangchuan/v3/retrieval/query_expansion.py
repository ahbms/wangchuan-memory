#!/usr/bin/env python3
"""
忘川 v3.0 - 查询扩展
将用户查询扩展为多个同义/相关查询，提升召回率
"""

import re
from typing import List, Dict


NON_EXECUTION_PATTERNS = [
    r"只是举个例子",
    r"只是举例",
    r"只是讨论",
    r"只是想聊",
    r"只是想听",
    r"先不要",
    r"不用真的",
    r"不要真的",
    r"不要用",
    r"别帮我",
    r"别生成",
    r"别发",
    r"不需要执行",
    r"不是要.*是要",
]


class QueryExpander:
    """
    查询扩展器

    策略：
    1. 同义词扩展（预定义词表）
    2. 领域关联扩展（Docker→容器→镜像）
    3. 拆词扩展（"Python安装" → "Python" + "安装"）
    """

    # 同义词/关联词表
    SYNONYM_MAP: Dict[str, List[str]] = {
        # Docker 生态
        "Docker": ["容器", "镜像", "docker", "dockerfile", "container"],
        "容器": ["Docker", "镜像", "container", "docker"],
        "docker-compose": ["compose", "编排", "多容器"],
        "镜像": ["image", "Docker", "容器"],

        # Python 生态
        "Python": ["python", "py", "pip", "conda", "虚拟环境"],
        "pip": ["Python", "包管理", "安装包"],
        "conda": ["Python", "虚拟环境", "环境管理"],
        "虚拟环境": ["venv", "conda", "Python"],

        # Linux
        "Linux": ["linux", "ubuntu", "centos", "debian", "命令行"],
        "权限": ["permission", "chmod", "chown", "usermod", "sudo"],
        "sudo": ["权限", "超级用户", "root"],

        # Web
        "nginx": ["web服务器", "反向代理", "负载均衡", "代理"],
        "反向代理": ["nginx", "proxy", "代理服务器"],

        # DevOps
        "CI/CD": ["持续集成", "持续部署", "GitHub Actions", "流水线", "自动化部署"],
        "部署": ["deploy", "上线", "发布", "CI/CD"],

        # 通用
        "安装": ["install", "setup", "配置", "部署"],
        "配置": ["config", "设置", "配置文件"],
        "错误": ["error", "报错", "失败", "异常", "bug"],
        "解决": ["修复", "fix", "搞定", "处理"],
    }

    def __init__(self):
        # 构建反向索引
        self._reverse_map: Dict[str, set] = {}
        for key, synonyms in self.SYNONYM_MAP.items():
            all_terms = {key.lower()} | {s.lower() for s in synonyms}
            for term in all_terms:
                if term not in self._reverse_map:
                    self._reverse_map[term] = set()
                self._reverse_map[term].update(all_terms)

    def expand(self, query: str, max_expansions: int = 5) -> List[str]:
        """
        扩展查询

        Returns:
            [原始查询, 扩展查询1, 扩展查询2, ...]
        """
        if self.should_freeze_expansion(query):
            return [query]

        expansions = [query]  # 始终包含原始查询
        query_lower = query.lower()

        # 1. 同义词扩展
        seen = {query_lower}
        for term, related in self._reverse_map.items():
            if term in query_lower:
                for r in related:
                    if r not in seen and r != term:
                        seen.add(r)
                        # 用同义词替换查询中的词
                        expanded = query_lower.replace(term, r)
                        if expanded != query_lower:
                            expansions.append(expanded)
                        else:
                            expansions.append(r)

        # 2. 拆词扩展（对中文有效）
        words = self._split_chinese(query)
        if len(words) >= 2:
            for word in words:
                if len(word) >= 2 and word.lower() not in seen:
                    expansions.append(word)
                    seen.add(word.lower())

        return expansions[:max_expansions + 1]

    def should_freeze_expansion(self, query: str) -> bool:
        text = (query or "").strip().lower()
        if not text:
            return True
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in NON_EXECUTION_PATTERNS)

    def _split_chinese(self, text: str) -> List[str]:
        """简单中文分词（按标点和空格）"""
        parts = re.split(r'[\s,，。、；;！!？?\-]+', text)
        return [p.strip() for p in parts if len(p.strip()) >= 2]

    def expand_for_vector(self, query: str) -> str:
        """
        为向量搜索扩展查询（拼接同义词提升语义覆盖）
        """
        expansions = self.expand(query, max_expansions=3)
        if len(expansions) <= 1:
            return query

        # 拼接前几个扩展，权重递减
        parts = [expansions[0]]
        for e in expansions[1:4]:
            if e not in parts[0]:
                parts.append(e)

        return " ".join(parts)
