"""001_baseline - 基线迁移

捕获忘川数据库的完整初始 schema。
使用 CREATE TABLE IF NOT EXISTS 确保幂等性，可在已有数据库上安全执行。

此迁移代表忘川 v3.0 的完整表结构，包含：
- 核心记忆表 (memories, short/medium/long_term_memory, working_memory)
- 图谱节点表 (gm_nodes, gm_edges, gm_communities, gm_signals, gm_messages)
- 向量嵌入表 (gm_embeddings, memory_embeddings)
- 元数据索引表 (memory_schema_index, memory_tags, memory_acl, memory_entities)
- 系统配置表 (gm_config, meta, files, chunks, embedding_cache)
- 反馈与进化表 (feedback, gm_feedback, evolution_rules, evolution_rule_candidates)
- 质量评估表 (quality_scores, error_knowledge, health_alerts)
- 其他辅助表
"""

from __future__ import annotations

description = "001_baseline: 忘川 v3.0 完整基线 schema"

# ─────────────────────────────────────────────
# DDL: 完整建表语句
# ─────────────────────────────────────────────

_BASELINE_DDL = """
-- ============================================
-- 核心记忆表
-- ============================================

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

CREATE TABLE IF NOT EXISTS working_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS short_term_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    time_range TEXT,
    topic TEXT NOT NULL,
    summary TEXT NOT NULL,
    key_facts TEXT,
    emotion TEXT DEFAULT 'neutral',
    importance_score REAL DEFAULT 0.7,
    source_session TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS medium_term_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,
    period_type TEXT CHECK(period_type IN ('week', 'month')),
    user_profile_summary TEXT,
    key_events TEXT,
    lessons_learned TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS long_term_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL CHECK(category IN ('preference', 'habit', 'identity', 'skill', 'aversion', 'fact')),
    fact TEXT NOT NULL,
    confidence REAL DEFAULT 0.7,
    first_seen DATE DEFAULT CURRENT_DATE,
    last_confirmed DATE DEFAULT CURRENT_DATE,
    evidence_count INTEGER DEFAULT 1,
    source TEXT
);

CREATE TABLE IF NOT EXISTS memory_outline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    time_range TEXT,
    topic TEXT NOT NULL,
    summary TEXT,
    key_concepts TEXT,
    emotion TEXT DEFAULT 'neutral',
    source_file TEXT,
    source_type TEXT DEFAULT 'chat',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- 图谱层 (graph-memory)
-- ============================================

CREATE TABLE IF NOT EXISTS gm_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    message_id TEXT UNIQUE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    token_count INTEGER,
    embedding_id INTEGER,
    FOREIGN KEY (embedding_id) REFERENCES gm_embeddings(id)
);

CREATE INDEX IF NOT EXISTS idx_gm_msg_session ON gm_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_gm_msg_time ON gm_messages(timestamp);

CREATE TABLE IF NOT EXISTS gm_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    signal_type TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    extracted_text TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (message_id) REFERENCES gm_messages(id)
);

CREATE INDEX IF NOT EXISTS idx_gm_sig_type ON gm_signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_gm_sig_processed ON gm_signals(processed);

CREATE TABLE IF NOT EXISTS gm_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT UNIQUE NOT NULL,
    node_type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    content TEXT,
    source_message_ids TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    embedding_id INTEGER,
    community_id INTEGER,
    pagerank_score REAL DEFAULT 0.0,
    task_status TEXT,
    skill_examples TEXT,
    event_severity TEXT,
    fact_confidence REAL,
    FOREIGN KEY (embedding_id) REFERENCES gm_embeddings(id),
    FOREIGN KEY (community_id) REFERENCES gm_communities(id)
);

CREATE INDEX IF NOT EXISTS idx_gm_nodes_type ON gm_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_gm_nodes_community ON gm_nodes(community_id);
CREATE INDEX IF NOT EXISTS idx_gm_nodes_pagerank ON gm_nodes(pagerank_score DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS gm_nodes_fts USING fts5(
    name, description, content,
    content=gm_nodes,
    content_rowid=id,
    tokenize='trigram'
);

CREATE TABLE IF NOT EXISTS gm_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_id TEXT UNIQUE NOT NULL,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    source_message_ids TEXT,
    temporal_order INTEGER,
    forget_probability REAL,
    FOREIGN KEY (source_node_id) REFERENCES gm_nodes(node_id),
    FOREIGN KEY (target_node_id) REFERENCES gm_nodes(node_id)
);

CREATE INDEX IF NOT EXISTS idx_gm_edges_source ON gm_edges(source_node_id);
CREATE INDEX IF NOT EXISTS idx_gm_edges_target ON gm_edges(target_node_id);
CREATE INDEX IF NOT EXISTS idx_gm_edges_type ON gm_edges(edge_type);

CREATE TABLE IF NOT EXISTS gm_communities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    community_id TEXT UNIQUE NOT NULL,
    name TEXT,
    description TEXT,
    node_count INTEGER DEFAULT 0,
    dominant_type TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    centroid_embedding BLOB
);

CREATE INDEX IF NOT EXISTS idx_gm_comm_dominant ON gm_communities(dominant_type);

CREATE TABLE IF NOT EXISTS gm_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    embedding_id TEXT UNIQUE NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    model_name TEXT,
    dimensions INTEGER,
    embedding BLOB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_gm_emb_entity ON gm_embeddings(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS gm_ppr_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hash TEXT UNIQUE NOT NULL,
    seed_nodes TEXT NOT NULL,
    ppr_results TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_gm_ppr_expires ON gm_ppr_cache(expires_at);

CREATE TABLE IF NOT EXISTS gm_wangchuan_bridge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gm_node_id TEXT NOT NULL,
    wc_memory_id INTEGER,
    bridge_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (gm_node_id) REFERENCES gm_nodes(node_id)
);

CREATE INDEX IF NOT EXISTS idx_gm_wc_bridge_gm ON gm_wangchuan_bridge(gm_node_id);
CREATE INDEX IF NOT EXISTS idx_gm_wc_bridge_wc ON gm_wangchuan_bridge(wc_memory_id);

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

CREATE TABLE IF NOT EXISTS gm_security_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT UNIQUE,
    rule_type TEXT,
    pattern TEXT,
    action TEXT,
    enabled INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gm_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT,
    feedback_type TEXT,
    weight REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_feedback_created ON gm_feedback(created_at);

CREATE TABLE IF NOT EXISTS gm_tuning_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gm_dag_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dag_hash TEXT,
    summary TEXT,
    node_count INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- 元数据索引与辅助表
-- ============================================

CREATE TABLE IF NOT EXISTS memory_schema_index (
    memory_id INTEGER PRIMARY KEY,
    schema_version TEXT,
    source_layer TEXT,
    source_anchor TEXT,
    source_session TEXT,
    turn_signature TEXT,
    memory_type TEXT,
    user_explicit INTEGER DEFAULT 0,
    is_test_data INTEGER DEFAULT 0,
    promotion_reason TEXT,
    hot_memory_candidate INTEGER DEFAULT 0,
    provenance TEXT,
    lifecycle TEXT,
    dedupe_key TEXT,
    conflict_group TEXT,
    quality_score REAL,
    evidence_level TEXT,
    promotion_state TEXT,
    last_confirmed_at TEXT,
    hotness_score REAL,
    recall_source_type TEXT,
    importance REAL,
    confidence REAL,
    trigger_count INTEGER,
    last_recall TEXT,
    removed_at TEXT,
    updated_at TEXT,
    valid_until TEXT,
    superseded_by INTEGER,
    supersession_chain TEXT,
    valid_from TEXT,
    content_preview TEXT,
    subject_domain TEXT
);

CREATE INDEX IF NOT EXISTS idx_memory_schema_index_promotion_state ON memory_schema_index(promotion_state);
CREATE INDEX IF NOT EXISTS idx_memory_schema_index_lifecycle ON memory_schema_index(lifecycle);
CREATE INDEX IF NOT EXISTS idx_memory_schema_index_dedupe_key ON memory_schema_index(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_memory_schema_index_recall_source_type ON memory_schema_index(recall_source_type);
CREATE INDEX IF NOT EXISTS idx_memory_schema_index_valid_from ON memory_schema_index(valid_from);
CREATE INDEX IF NOT EXISTS idx_memory_schema_index_valid_until ON memory_schema_index(valid_until);
CREATE INDEX IF NOT EXISTS idx_memory_schema_index_superseded_by ON memory_schema_index(superseded_by);

CREATE TABLE IF NOT EXISTS memory_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    memory_id INTEGER,
    created_at TEXT,
    FOREIGN KEY (memory_id) REFERENCES memories(id)
);

CREATE INDEX IF NOT EXISTS idx_entity ON memory_entities(entity);

CREATE TABLE IF NOT EXISTS memory_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_vector TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(memory_id, embedding_model)
);

CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model ON memory_embeddings(embedding_model);

CREATE TABLE IF NOT EXISTS memory_acl (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    memory_id INTEGER NOT NULL,
    permission TEXT DEFAULT 'read',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, memory_id)
);

CREATE INDEX IF NOT EXISTS idx_acl_user ON memory_acl(user_id);

CREATE TABLE IF NOT EXISTS memory_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(memory_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_tags_memory ON memory_tags(memory_id);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON memory_tags(tag);

CREATE TABLE IF NOT EXISTS memory_nodes (
    node_id TEXT PRIMARY KEY,
    node_url TEXT NOT NULL,
    node_name TEXT,
    status TEXT DEFAULT 'active',
    last_seen TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- 系统配置与元数据
-- ============================================

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'memory',
    hash TEXT NOT NULL,
    mtime INTEGER NOT NULL,
    size INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'memory',
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    hash TEXT NOT NULL,
    model TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);

CREATE TABLE IF NOT EXISTS embedding_cache (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    provider_key TEXT NOT NULL,
    hash TEXT NOT NULL,
    embedding TEXT NOT NULL,
    dims INTEGER,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (provider, model, provider_key, hash)
);

CREATE INDEX IF NOT EXISTS idx_embedding_cache_updated_at ON embedding_cache(updated_at);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    id UNINDEXED,
    path UNINDEXED,
    source UNINDEXED,
    model UNINDEXED,
    start_line UNINDEXED,
    end_line UNINDEXED
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_memories USING fts5(
    content
);

-- ============================================
-- 反馈与进化
-- ============================================

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    message_id TEXT,
    feedback_type TEXT,
    weight REAL,
    timestamp TEXT,
    content TEXT,
    context TEXT,
    source TEXT
);

CREATE TABLE IF NOT EXISTS context_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id TEXT UNIQUE NOT NULL,
    parent_context_id TEXT,
    agent_id TEXT NOT NULL,
    task_type TEXT,
    payload TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP
);

CREATE TABLE IF NOT EXISTS circuit_breakers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT UNIQUE NOT NULL,
    state TEXT DEFAULT 'CLOSED',
    failure_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    last_failure_time TIMESTAMP,
    last_success_time TIMESTAMP,
    threshold INTEGER DEFAULT 5,
    recovery_timeout INTEGER DEFAULT 300,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS evolution_rules (
    rule_id TEXT PRIMARY KEY,
    condition TEXT,
    action TEXT,
    scope TEXT,
    confidence REAL,
    importance REAL,
    source_task TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS evolution_rule_candidates (
    candidate_id TEXT PRIMARY KEY,
    condition TEXT,
    action TEXT,
    scope TEXT,
    confidence REAL,
    importance REAL,
    source_task TEXT,
    source_session TEXT,
    source_trace TEXT,
    evidence_json TEXT,
    tags_json TEXT,
    status TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS goal_adjustments (
    adjustment_id TEXT PRIMARY KEY,
    goal TEXT,
    old_value REAL,
    new_value REAL,
    reason TEXT,
    timestamp TEXT,
    reverted INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quality_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    score_type TEXT,
    score_value REAL,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS error_knowledge (
    error_pattern TEXT PRIMARY KEY,
    root_cause TEXT,
    fix_strategy TEXT,
    success_rate REAL DEFAULT 0,
    occurrences INTEGER DEFAULT 0,
    last_updated TEXT
);

CREATE TABLE IF NOT EXISTS failure_analysis_log (
    analysis_id TEXT PRIMARY KEY,
    failure_type TEXT,
    analysis_json TEXT,
    analyzed_at TEXT
);

CREATE TABLE IF NOT EXISTS health_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT UNIQUE,
    timestamp TEXT,
    alert_type TEXT,
    severity TEXT,
    message TEXT,
    evidence TEXT,
    suggested_action TEXT,
    auto_fix TEXT,
    resolved INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS evolution_reports (
    report_id TEXT PRIMARY KEY,
    cycle_number INTEGER,
    generated_at TEXT,
    overall_verdict TEXT,
    evolution_index_before REAL,
    evolution_index_after REAL,
    index_change REAL,
    report_json TEXT
);

CREATE TABLE IF NOT EXISTS dimension_candidates (
    dimension_id TEXT PRIMARY KEY,
    display_name TEXT,
    source TEXT,
    signal_count INTEGER DEFAULT 0,
    first_seen TEXT,
    last_seen TEXT,
    examples TEXT DEFAULT '[]',
    registered INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tuning_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def up(conn) -> None:
    """创建基线表结构。

    使用 CREATE TABLE IF NOT EXISTS 幂等执行。
    可在已有数据库上安全运行。
    """
    # 按语句逐个执行，避免 FTS5 和其他特殊语句在 executemany 中出错
    statements = [s.strip() for s in _BASELINE_DDL.split(";") if s.strip()]
    for stmt in statements:
        # 跳过纯注释行
        lines = [l for l in stmt.split("\n") if not l.strip().startswith("--")]
        clean = "\n".join(lines).strip()
        if not clean:
            continue
        conn.execute(clean)


def down(conn) -> None:
    """回滚基线 - 删除所有基线表。

    ⚠️ 危险操作：会删除数据！仅用于回滚测试。
    """
    tables = [
        "tuning_actions", "dimension_candidates", "evolution_reports",
        "health_alerts", "failure_analysis_log", "error_knowledge",
        "quality_scores", "goal_adjustments", "evolution_rule_candidates",
        "evolution_rules", "circuit_breakers", "context_registry",
        "feedback", "gm_feedback", "gm_security_rules", "gm_tuning_log",
        "gm_dag_summaries", "gm_wangchuan_bridge", "gm_ppr_cache",
        "gm_communities", "gm_edges", "gm_signals", "gm_messages",
        "gm_config",
        # FTS tables
        "gm_nodes_fts", "chunks_fts", "fts_memories",
        "embedding_cache", "chunks", "files", "meta",
        "memory_nodes", "memory_tags", "memory_acl",
        "memory_embeddings", "memory_entities", "memory_schema_index",
        "memory_outline", "long_term_memory", "medium_term_memory",
        "short_term_memory", "working_memory", "memories",
    ]
    for table in tables:
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        except Exception:
            pass
