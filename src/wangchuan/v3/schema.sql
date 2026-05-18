-- 忘川 v3.0 - 图谱增强记忆系统
-- 融合 graph-memory 知识图谱 + 忘川温度分层架构

-- ============================================
-- 1. 原始消息层 (对应 graph-memory gm_messages)
-- ============================================
CREATE TABLE IF NOT EXISTS gm_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    message_id TEXT UNIQUE,           -- 外部消息ID
    role TEXT NOT NULL,               -- user / assistant / system
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    token_count INTEGER,              -- 估算token数
    embedding_id INTEGER,             -- 关联到 gm_embeddings
    FOREIGN KEY (embedding_id) REFERENCES gm_embeddings(id)
);

CREATE INDEX IF NOT EXISTS idx_gm_msg_session ON gm_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_gm_msg_time ON gm_messages(timestamp);

-- ============================================
-- 2. 信号检测层 (对应 graph-memory gm_signals)
-- ============================================
CREATE TABLE IF NOT EXISTS gm_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    signal_type TEXT NOT NULL,        -- error / correction / completion / question
    confidence REAL DEFAULT 0.5,      -- 信号置信度
    extracted_text TEXT,              -- 提取的关键文本
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed BOOLEAN DEFAULT FALSE,  -- 是否已处理为三元组
    FOREIGN KEY (message_id) REFERENCES gm_messages(id)
);

CREATE INDEX IF NOT EXISTS idx_gm_sig_type ON gm_signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_gm_sig_processed ON gm_signals(processed);

-- ============================================
-- 3. 知识图谱节点层 (对应 graph-memory gm_nodes)
-- ============================================
CREATE TABLE IF NOT EXISTS gm_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT UNIQUE NOT NULL,     -- 格式: node_<hash>
    node_type TEXT NOT NULL,          -- TASK / SKILL / EVENT / FACT
    name TEXT NOT NULL,               -- 节点名称
    description TEXT,                 -- 节点描述
    content TEXT,                     -- 完整内容
    source_message_ids TEXT,          -- JSON数组: 来源消息ID
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    embedding_id INTEGER,             -- 关联向量
    community_id INTEGER,             -- 所属社区
    pagerank_score REAL DEFAULT 0.0,  -- PageRank分数
    -- 类型特定字段
    task_status TEXT,                 -- TASK: pending/done/failed
    skill_examples TEXT,              -- SKILL: JSON示例数组
    event_severity TEXT,              -- EVENT: low/medium/high
    fact_confidence REAL,             -- FACT: 置信度 0-1
    FOREIGN KEY (embedding_id) REFERENCES gm_embeddings(id),
    FOREIGN KEY (community_id) REFERENCES gm_communities(id)
);

CREATE INDEX IF NOT EXISTS idx_gm_nodes_type ON gm_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_gm_nodes_community ON gm_nodes(community_id);
CREATE INDEX IF NOT EXISTS idx_gm_nodes_pagerank ON gm_nodes(pagerank_score DESC);

-- 全文搜索索引 (FTS5)
CREATE VIRTUAL TABLE IF NOT EXISTS gm_nodes_fts USING fts5(
    name, description, content,
    content=gm_nodes,
    content_rowid=id,
    tokenize='trigram'
);

-- ============================================
-- 4. 知识图谱边层 (对应 graph-memory gm_edges)
-- ============================================
CREATE TABLE IF NOT EXISTS gm_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_id TEXT UNIQUE NOT NULL,     -- 格式: edge_<hash>
    source_node_id TEXT NOT NULL,     -- 头实体
    target_node_id TEXT NOT NULL,     -- 尾实体
    edge_type TEXT NOT NULL,          -- 见下方类型
    weight REAL DEFAULT 1.0,          -- 边权重
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    source_message_ids TEXT,          -- JSON数组
    -- 忘川扩展边类型
    temporal_order INTEGER,           -- 时间顺序
    forget_probability REAL,          -- 遗忘概率
    FOREIGN KEY (source_node_id) REFERENCES gm_nodes(node_id),
    FOREIGN KEY (target_node_id) REFERENCES gm_nodes(node_id)
);

-- 标准边类型: USED_SKILL, SOLVED_BY, REQUIRES, PATCHES, CONFLICTS_WITH
-- 忘川扩展: REMEMBERS, FORGETS, RECALLS, LEADS_TO, SIMILAR_TO

CREATE INDEX IF NOT EXISTS idx_gm_edges_source ON gm_edges(source_node_id);
CREATE INDEX IF NOT EXISTS idx_gm_edges_target ON gm_edges(target_node_id);
CREATE INDEX IF NOT EXISTS idx_gm_edges_type ON gm_edges(edge_type);

-- ============================================
-- 5. 社区检测层 (对应 graph-memory gm_communities)
-- ============================================
CREATE TABLE IF NOT EXISTS gm_communities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    community_id TEXT UNIQUE NOT NULL,
    name TEXT,                        -- 社区名称(自动生成或人工标注)
    description TEXT,
    node_count INTEGER DEFAULT 0,
    dominant_type TEXT,               -- 主导节点类型
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- 社区特征向量(用于快速匹配)
    centroid_embedding BLOB
);

CREATE INDEX IF NOT EXISTS idx_gm_comm_dominant ON gm_communities(dominant_type);

-- ============================================
-- 6. 向量嵌入层 (对应 graph-memory gm_embeddings)
-- ============================================
CREATE TABLE IF NOT EXISTS gm_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    embedding_id TEXT UNIQUE NOT NULL,
    entity_type TEXT NOT NULL,        -- message / node
    entity_id TEXT NOT NULL,          -- 关联实体ID
    model_name TEXT,                  -- 使用的模型
    dimensions INTEGER,               -- 维度数
    embedding BLOB NOT NULL,          -- 二进制存储的向量
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_gm_emb_entity ON gm_embeddings(entity_type, entity_id);

-- ============================================
-- 7. 个性化PageRank缓存 (性能优化)
-- ============================================
CREATE TABLE IF NOT EXISTS gm_ppr_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hash TEXT UNIQUE NOT NULL,  -- 查询文本的hash
    seed_nodes TEXT NOT NULL,         -- JSON: 种子节点ID数组
    ppr_results TEXT NOT NULL,        -- JSON: {node_id: score}
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP              -- 缓存过期时间
);

CREATE INDEX IF NOT EXISTS idx_gm_ppr_expires ON gm_ppr_cache(expires_at);

-- ============================================
-- 8. 与忘川v2的桥接表
-- ============================================
CREATE TABLE IF NOT EXISTS gm_wangchuan_bridge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gm_node_id TEXT NOT NULL,         -- gm_nodes.node_id
    wc_memory_id INTEGER,             -- long_term_memory.id
    bridge_type TEXT NOT NULL,        -- auto / manual
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (gm_node_id) REFERENCES gm_nodes(node_id)
);

CREATE INDEX IF NOT EXISTS idx_gm_wc_bridge_gm ON gm_wangchuan_bridge(gm_node_id);
CREATE INDEX IF NOT EXISTS idx_gm_wc_bridge_wc ON gm_wangchuan_bridge(wc_memory_id);

-- ============================================
-- 9. 系统配置表
-- ============================================
CREATE TABLE IF NOT EXISTS gm_config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 默认配置
INSERT OR IGNORE INTO gm_config (key, value) VALUES
('version', '3.0.0'),
('llm_model', 'gpt-4o-mini'),
('embedding_model', 'text-embedding-3-small'),
('embedding_dimensions', '512'),
('ppr_damping', '0.85'),
('ppr_iterations', '100'),
('community_resolution', '1.0'),
('max_context_nodes', '20'),
('fresh_tail_messages', '10'),
('similarity_threshold', '0.85');
