#!/usr/bin/env python3
"""
忘川 v3.0 Hook 激活脚本
"""
from wangchuan.paths import workspace_root as _v3_ws_root

import os
import sys
import shutil
from pathlib import Path
import sqlite3

WORKSPACE_ROOT = _v3_ws_root()
HOOK_ROOT = Path.home() / ".openclaw" / "hooks" / "wangchuan-v3"
DB_PATH = WORKSPACE_ROOT / "tiangong" / "wangchuan" / ".index" / "index.sqlite"

def activate():
    """激活Hook"""
    hook_dir = HOOK_ROOT
    
    # 检查Hook是否存在
    if not (hook_dir / "handler.py").exists():
        print("❌ Hook文件不存在")
        return False
    
    # 设置环境变量
    env_vars = """
# 忘川v3 Hook环境变量
export LLM_BASE_URL="https://ark.cn-beijing.volces.com/api/coding/v3"
export LLM_MODEL="kimi-k2.5"
export EMBEDDING_BASE_URL="https://maas-api.cn-huabei-1.xf-yun.com/v2"
export EMBEDDING_MODEL="xop3qwen8bembedding"
"""
    
    # 写入.bashrc
    bashrc_path = os.path.expanduser("~/.bashrc")
    with open(bashrc_path, 'a') as f:
        f.write(env_vars)
    
    print("✅ 忘川v3 Hook已激活！")
    print("\n📋 状态:")
    msg_count = 0
    signal_count = 0
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM gm_messages")
            msg_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM gm_signals")
            signal_count = cur.fetchone()[0]
            conn.close()
        except Exception:
            pass
    print(f"  Hook路径: {hook_dir}")
    print(f"  数据库: {DB_PATH}")
    print(f"  当前消息数: {msg_count}")
    print(f"  当前信号数: {signal_count}")
    
    print("\n🚀 现在每次对话都会自动记录到忘川v3！")
    print("\n💡 查看状态:")
    print("  python3 -c \"from handler import get_status; print(get_status())\"")
    
    return True

if __name__ == "__main__":
    activate()
