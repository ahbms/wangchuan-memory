#!/usr/bin/env python3
"""
忘川加密密钥生成工具

用法:
    python generate_encryption_key.py          # 生成新密钥
    python generate_encryption_key.py --show   # 显示当前密钥
    python generate_encryption_key.py --export # 输出当前 shell 可用的 export 命令
"""

import os
import sys
from cryptography.fernet import Fernet


def generate_key() -> str:
    """生成新的 Fernet 密钥"""
    return Fernet.generate_key().decode()


def show_current_key():
    """显示当前配置的密钥（隐藏部分）"""
    key = os.getenv("MEMORY_ENCRYPTION_KEY")
    if key:
        print(f"当前密钥: {key[:20]}...{key[-10:]}")
    else:
        print("未设置密钥 (MEMORY_ENCRYPTION_KEY)")


def emit_export_command(key: str):
    """输出当前 shell 会话可直接使用的 export 命令。"""
    print(f'export MEMORY_ENCRYPTION_KEY="{key}"')


def main():
    if "--show" in sys.argv:
        show_current_key()
        return

    if "--export" in sys.argv:
        key = generate_key()
        emit_export_command(key)
        return

    key = generate_key()
    print("=" * 50)
    print("🔐 忘川加密密钥")
    print("=" * 50)
    print(f"\n密钥: {key}")
    print("\n使用方法:")
    print("  1. 当前 shell 会话中设置环境变量:")
    print(f'     export MEMORY_ENCRYPTION_KEY="{key}"')
    print("\n  2. 或通过你的进程管理器 / 密钥管理器注入同名环境变量")
    print("\n  3. 如需只输出 export 命令，可运行:")
    print("     python generate_encryption_key.py --export")
    print("=" * 50)


if __name__ == "__main__":
    main()
