#!/usr/bin/env python3
"""
忘川 v3.0 - 记忆压缩
在有限 token 预算内最大化信息密度
"""

import re
from typing import List, Dict


class ContextCompressor:
    """
    上下文压缩器

    策略：
    1. 节点去重（同名合并描述）
    2. 边精简（只保留高权重边）
    3. XML 压缩（缩短标签、去除冗余属性）
    4. 溯源截断（每条消息限长）
    """

    def __init__(self, max_tokens: int = 2000):
        self.max_tokens = max_tokens

    def compress_graph_xml(self, xml: str, max_tokens: int = None) -> str:
        """压缩图谱 XML"""
        budget = max_tokens or self.max_tokens

        if self._estimate_tokens(xml) <= budget:
            return xml

        # 1. 去除 description 如果太长
        xml = re.sub(r'<desc>[^<]{50,}</desc>', '', xml)

        if self._estimate_tokens(xml) <= budget:
            # 还在预算内，做轻量清理后返回
            xml = re.sub(r'\n\s*\n', '\n', xml)
            xml = re.sub(r'>\s+<', '><', xml)
            xml = xml.replace('<graph>', '<g>').replace('</graph>', '</g>')
            xml = xml.replace('<nodes>', '<ns>').replace('</nodes>', '</ns>')
            xml = xml.replace('<node ', '<n ').replace('</node>', '')
            xml = xml.replace('<edges>', '<es>').replace('</edges>', '</es>')
            xml = xml.replace('<edge ', '<e ').replace('/>', '/')
            return xml

        # 2. 截断节点（在压缩标签之前，保留换行结构）
        if self._estimate_tokens(xml) > budget:
            lines = xml.split('\n')
            kept = []
            tokens = 0
            for line in lines:
                line_tokens = self._estimate_tokens(line)
                if tokens + line_tokens > budget * 0.9:
                    break
                kept.append(line)
                tokens += line_tokens
            xml = '\n'.join(kept)
            if '</graph>' not in xml:
                xml += '\n</graph>'
            elif '</nodes>' not in xml and '<edges>' not in xml:
                # 确保结构完整
                pass

        # 3. 缩短标签（最后做，减少最终体积）
        xml = xml.replace('<graph>', '<g>')
        xml = xml.replace('</graph>', '</g>')
        xml = xml.replace('<nodes>', '<ns>')
        xml = xml.replace('</nodes>', '</ns>')
        xml = xml.replace('<node ', '<n ')
        xml = xml.replace('</node>', '')
        xml = xml.replace('<edges>', '<es>')
        xml = xml.replace('</edges>', '</es>')
        xml = xml.replace('<edge ', '<e ')
        xml = xml.replace('/>', '/')

        # 4. 去除空行和多余空格
        xml = re.sub(r'\n\s*\n', '\n', xml)
        xml = re.sub(r'>\s+<', '><', xml)

        return xml

    def compress_episodic(self, episodic_xml: str, max_tokens: int = 500) -> str:
        """压缩溯源 XML"""
        if self._estimate_tokens(episodic_xml) <= max_tokens:
            return episodic_xml

        # 截断每条消息到 100 字符
        xml = re.sub(r'(\[[A-Z]+\]) (.{100})', r'\1 \2...', episodic_xml)

        # 每个 trace 只保留 2 条消息
        def limit_traces(match):
            content = match.group(1)
            lines = content.strip().split('\n')
            if len(lines) > 2:
                lines = lines[:2]
            return (
                f'<trace{match.group(0).split("<trace")[1].split(">")[0]}>\n'
                + '\n'.join(lines) + '\n  </trace>'
            )

        # 用正则匹配每个 trace 块并限制消息数
        xml = re.sub(
            r'<trace([^>]*)>([\s\S]*?)</trace>',
            lambda m: f'<trace{m.group(1)}>\n' +
                      '\n'.join(m.group(2).strip().split('\n')[:2]) +
                      '\n  </trace>',
            xml
        )

        return xml

    def smart_truncate(self, context_parts: List[Dict]) -> str:
        """
        智能裁剪：在 token 预算内保留最有价值的内容

        Args:
            context_parts: [{'content': str, 'priority': int, 'type': str}]
                          priority: 1=最高, 5=最低

        Returns:
            裁剪后的上下文
        """
        # 按优先级排序
        sorted_parts = sorted(context_parts, key=lambda x: x.get('priority', 3))

        result = []
        tokens = 0

        for part in sorted_parts:
            content = part['content']
            part_tokens = self._estimate_tokens(content)

            if tokens + part_tokens <= self.max_tokens:
                result.append(content)
                tokens += part_tokens
            else:
                # 尝试压缩后加入
                remaining = self.max_tokens - tokens
                if remaining > 50:  # 至少还有 50 token 空间
                    truncated = content[:remaining * 3]  # 粗略截断
                    if part['type'] == 'graph':
                        truncated = self.compress_graph_xml(truncated, remaining)
                    result.append(truncated)
                break

        return '\n\n'.join(result)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """估算 token 数"""
        # 中文: ~1.5 token/字, 英文: ~4 char/token
        cn_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        en_chars = len(text) - cn_chars
        return int(cn_chars * 1.5 + en_chars / 4)
