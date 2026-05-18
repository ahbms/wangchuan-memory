#!/usr/bin/env python3
"""
忘川 v3.0 - 三元组提取模块 (Extract)
使用LLM从消息中提取知识图谱三元组
"""

import logging
import sqlite3
import json
import hashlib
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class Triple:
    """知识图谱三元组"""
    subject: str           # 头实体
    predicate: str         # 关系
    object: str            # 尾实体
    subject_type: str      # TASK / SKILL / EVENT / FACT
    object_type: str       # TASK / SKILL / EVENT / FACT
    confidence: float = 0.8
    source_message_ids: List[int] = None
    
    def __post_init__(self):
        if self.source_message_ids is None:
            self.source_message_ids = []
    
    def to_node_edge(self) -> Tuple[Dict, Dict]:
        """转换为节点和边"""
        subject_node = {
            'node_id': self._generate_node_id(self.subject, self.subject_type),
            'node_type': self.subject_type,
            'name': self.subject,
            'description': f"{self.subject_type}: {self.subject}"
        }
        
        object_node = {
            'node_id': self._generate_node_id(self.object, self.object_type),
            'node_type': self.object_type,
            'name': self.object,
            'description': f"{self.object_type}: {self.object}"
        }
        
        edge = {
            'edge_id': self._generate_edge_id(self.subject, self.predicate, self.object),
            'source_node_id': subject_node['node_id'],
            'target_node_id': object_node['node_id'],
            'edge_type': self.predicate,
            'weight': self.confidence
        }
        
        return subject_node, object_node, edge
    
    def _generate_node_id(self, name: str, node_type: str) -> str:
        """生成节点ID"""
        content = f"{node_type}:{name}"
        hash_val = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"node_{node_type.lower()}_{hash_val}"
    
    def _generate_edge_id(self, subj: str, pred: str, obj: str) -> str:
        """生成边ID"""
        content = f"{subj}|{pred}|{obj}"
        hash_val = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"edge_{hash_val}"

class ExtractEngine:
    """三元组提取引擎"""
    
    # 关系映射表
    RELATION_MAPPINGS = {
        # 标准 graph-memory 关系
        '使用了': 'USED_SKILL',
        '解决了': 'SOLVED_BY',
        '需要': 'REQUIRES',
        '修复了': 'PATCHES',
        '冲突': 'CONFLICTS_WITH',
        '依赖于': 'DEPENDS_ON',
        '导致': 'CAUSES',
        '属于': 'BELONGS_TO',
        
        # 忘川扩展关系
        '记住': 'REMEMBERS',
        '遗忘': 'FORGETS',
        '回忆': 'RECALLS',
        '导致': 'LEADS_TO',
        '相似': 'SIMILAR_TO',
        '相关': 'RELATED_TO',
        
        # 英文映射
        'used': 'USED_SKILL',
        'solved': 'SOLVED_BY',
        'requires': 'REQUIRES',
        'fixed': 'PATCHES',
        'conflicts': 'CONFLICTS_WITH',
        'depends': 'DEPENDS_ON',
        'causes': 'CAUSES',
        'belongs': 'BELONGS_TO'
    }
    
    def __init__(self, db_path: str, llm_config=None):
        self.db_path = db_path
        self.llm_config = llm_config
    
    def extract_from_signal(self, signal: Dict) -> List[Triple]:
        """
        从信号中提取三元组
        
        如果LLM可用，使用LLM提取；否则使用规则提取
        """
        if self.llm_config and self.llm_config.api_key:
            return self._extract_with_llm(signal)
        else:
            return self._extract_with_rules(signal)
    
    def _extract_with_llm(self, signal: Dict) -> List[Triple]:
        """使用LLM提取三元组"""
        # 构建提示
        prompt = self._build_extraction_prompt(signal)
        
        # 调用LLM (简化版，实际应使用OpenAI/Anthropic API)
        try:
            response = self._call_llm(prompt)
            triples = self._parse_llm_response(response, signal)
            return triples
        except Exception as e:
            logger.warning("【WangChuan】[Extract][LLM] extraction failed; fallback to rules: %s", e)
            return self._extract_with_rules(signal)
    
    def _build_extraction_prompt(self, signal: Dict) -> str:
        """构建提取提示"""
        return f"""从以下对话内容中提取知识图谱三元组。

对话角色: {signal.get('role', 'unknown')}
信号类型: {signal.get('signal_type', 'unknown')}
内容:
{signal.get('content', '')}

请提取所有相关的三元组，格式为 JSON:
[
  {{
    "subject": "头实体名称",
    "predicate": "关系",
    "object": "尾实体名称",
    "subject_type": "TASK|SKILL|EVENT|FACT",
    "object_type": "TASK|SKILL|EVENT|FACT",
    "confidence": 0.9
  }}
]

实体类型说明:
- TASK: 执行的任务 (如"安装Docker", "修复bug")
- SKILL: 技能/方法 (如"使用pip", "调试技巧")
- EVENT: 发生的事件 (如"报错", "成功")
- FACT: 事实/知识 (如"Python3.8支持", "用户偏好")

关系示例: 使用了, 解决了, 需要, 修复了, 冲突, 依赖于, 导致

只返回JSON数组，不要其他解释。"""
    
    def _call_llm(self, prompt: str) -> str:
        """调用LLM API (真实实现 - 豆包/火山引擎)"""
        import urllib.request
        import urllib.error
        import os
        
        api_key = os.environ.get('LLM_API_KEY') or (self.llm_config.api_key if self.llm_config else None)
        base_url = os.environ.get('LLM_BASE_URL') or (self.llm_config.base_url if self.llm_config else 'https://api.openai.com/v1')
        model = os.environ.get('LLM_MODEL') or (self.llm_config.model if self.llm_config else 'gpt-4o-mini')
        
        if not api_key:
            raise Exception("LLM API Key未配置")
        
        url = f"{base_url}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是一个知识提取助手。从对话中提取知识图谱三元组，只返回JSON数组格式。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 1000
        }
        
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers=headers,
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode('utf-8'))
                
                # 解析响应
                if 'choices' in result and len(result['choices']) > 0:
                    content = result['choices'][0]['message']['content']
                    # 提取JSON部分
                    import re
                    json_match = re.search(r'\[.*?\]', content, re.DOTALL)
                    if json_match:
                        return json_match.group(0)
                    return content
                
                raise Exception(f"API响应格式错误: {result}")
                
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            raise Exception(f"API错误 {e.code}: {error_body[:500]}")
        except Exception as e:
            raise Exception(f"LLM调用失败: {e}")
    
    def _parse_llm_response(self, response: str, signal: Dict) -> List[Triple]:
        """解析LLM响应"""
        try:
            data = json.loads(response)
            triples = []
            
            for item in data:
                triple = Triple(
                    subject=item.get('subject', ''),
                    predicate=self._normalize_relation(item.get('predicate', '')),
                    object=item.get('object', ''),
                    subject_type=item.get('subject_type', 'FACT'),
                    object_type=item.get('object_type', 'FACT'),
                    confidence=item.get('confidence', 0.8),
                    source_message_ids=[signal.get('message_id')]
                )
                triples.append(triple)
            
            return triples
        except json.JSONDecodeError:
            return []
    
    def _extract_with_rules(self, signal: Dict) -> List[Triple]:
        """基于规则的三元组提取 (零LLM fallback)"""
        triples = []
        content = signal.get('content', '')
        signal_type = signal.get('signal_type', '')
        
        # 根据信号类型使用不同的提取规则
        if signal_type == 'error':
            triples.extend(self._extract_error_triples(content, signal))
        elif signal_type == 'correction':
            triples.extend(self._extract_correction_triples(content, signal))
        elif signal_type == 'completion':
            triples.extend(self._extract_completion_triples(content, signal))
        else:
            triples.extend(self._extract_generic_triples(content, signal))
        
        return triples
    
    def _extract_error_triples(self, content: str, signal: Dict) -> List[Triple]:
        """提取错误相关三元组"""
        triples = []
        
        # 提取错误类型
        error_patterns = [
            r'(\w+Error):\s*(.+?)(?:\n|$)',
            r'错误[:：]\s*(.+?)(?:\n|$)',
            r'报错[:：]\s*(.+?)(?:\n|$)'
        ]
        
        for pattern in error_patterns:
            matches = re.finditer(pattern, content, re.IGNORECASE)
            for match in matches:
                error_name = match.group(1) if match.groups() else "未知错误"
                error_detail = match.group(2) if len(match.groups()) > 1 else match.group(0)
                
                triple = Triple(
                    subject=error_name,
                    predicate='CONFLICTS_WITH',
                    object='正常运行',
                    subject_type='EVENT',
                    object_type='TASK',
                    confidence=0.75,
                    source_message_ids=[signal.get('message_id')]
                )
                triples.append(triple)
        
        return triples
    
    def _extract_correction_triples(self, content: str, signal: Dict) -> List[Triple]:
        """提取修正相关三元组"""
        triples = []
        
        # 提取"X 改为 Y"模式
        correction_patterns = [
            r'["\']([^"\']+)["\']\s*改为\s*["\']([^"\']+)["\']',
            r'(\S+)\s*改为\s*(\S+)',
            r'fix(?:ed)?:?\s*(.+?)(?:\s+to\s+|\s+as\s+)(.+)'
        ]
        
        for pattern in correction_patterns:
            matches = re.finditer(pattern, content, re.IGNORECASE)
            for match in matches:
                old_val = match.group(1)
                new_val = match.group(2)
                
                triple = Triple(
                    subject=new_val,
                    predicate='PATCHES',
                    object=old_val,
                    subject_type='SKILL',
                    object_type='EVENT',
                    confidence=0.8,
                    source_message_ids=[signal.get('message_id')]
                )
                triples.append(triple)
        
        return triples
    
    def _extract_completion_triples(self, content: str, signal: Dict) -> List[Triple]:
        """提取完成相关三元组"""
        triples = []
        
        # 提取完成的任务
        task_patterns = [
            r'完成[了了]?\s*["\']?([^"\']+)["\']?',
            r'解决[了了]?\s*["\']?([^"\']+)["\']?',
            r'(?:done|completed|fixed)\s*["\']?([^"\']+)["\']?'
        ]
        
        for pattern in task_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                task_name = match.group(1)
                
                triple = Triple(
                    subject=task_name,
                    predicate='SOLVED_BY',
                    object='当前会话',
                    subject_type='TASK',
                    object_type='EVENT',
                    confidence=0.85,
                    source_message_ids=[signal.get('message_id')]
                )
                triples.append(triple)
        
        return triples
    
    def _extract_generic_triples(self, content: str, signal: Dict) -> List[Triple]:
        """通用三元组提取"""
        triples = []
        
        # 简单的"主语 谓语 宾语"模式
        # 如: "我使用了Docker" -> (我, 使用了, Docker)
        generic_pattern = r'([我你他她它]|\w+)\s*(使用|安装|配置|修复|解决|完成|需要)\s*["\']?([^"\']+)["\']?'
        
        matches = re.finditer(generic_pattern, content)
        for match in matches:
            subject = match.group(1)
            predicate = self._normalize_relation(match.group(2))
            obj = match.group(3)
            
            triple = Triple(
                subject=subject,
                predicate=predicate,
                object=obj,
                subject_type='TASK',
                object_type='SKILL',
                confidence=0.6,
                source_message_ids=[signal.get('message_id')]
            )
            triples.append(triple)
        
        return triples
    
    def _normalize_relation(self, relation: str) -> str:
        """标准化关系名称"""
        relation = relation.lower().strip()
        return self.RELATION_MAPPINGS.get(relation, relation.upper())
    
    def save_triples(self, triples: List[Triple]) -> int:
        """
        保存三元组到数据库
        
        Args:
            triples: 三元组列表
        
        Returns:
            保存的三元组数量
        """
        if not triples:
            return 0
        
        saved_count = 0
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            for triple in triples:
                subject_node, object_node, edge = triple.to_node_edge()
                
                # 保存头实体节点
                cursor.execute("""
                    INSERT OR IGNORE INTO gm_nodes 
                    (node_id, node_type, name, description, first_seen, last_accessed)
                    VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                """, (subject_node['node_id'], subject_node['node_type'], 
                      subject_node['name'], subject_node['description']))
                
                # 保存尾实体节点
                cursor.execute("""
                    INSERT OR IGNORE INTO gm_nodes 
                    (node_id, node_type, name, description, first_seen, last_accessed)
                    VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                """, (object_node['node_id'], object_node['node_type'],
                      object_node['name'], object_node['description']))
                
                # 保存边
                cursor.execute("""
                    INSERT OR IGNORE INTO gm_edges 
                    (edge_id, source_node_id, target_node_id, edge_type, weight, first_seen)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                """, (edge['edge_id'], edge['source_node_id'], 
                      edge['target_node_id'], edge['edge_type'], edge['weight']))
                
                saved_count += 1
            
            conn.commit()
        
        return saved_count
