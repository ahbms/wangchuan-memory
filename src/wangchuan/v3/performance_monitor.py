#!/usr/bin/env python3
"""
忘川性能监控模块

功能:
- 操作延迟追踪
- 性能指标统计
- 慢查询告警
"""
from wangchuan.paths import workspace_root as _v3_ws_root

import os
import sys
import time
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime

workspace = _v3_ws_root()
sys.path.insert(0, str(workspace))


class PerformanceMonitor:
    """性能监控器"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = str(workspace / "tiangong" / "wangchuan" / ".index" / "index.sqlite")
        self.db_path = db_path
        self.stats = defaultdict(list)
        self.slow_threshold_ms = 100
    
    def record(self, operation: str, duration_ms: float):
        """记录操作耗时"""
        self.stats[operation].append(duration_ms)
        if duration_ms > self.slow_threshold_ms:
            print(f"⚠️  慢查询 [{operation}]: {duration_ms:.2f}ms")
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        result = {}
        for op, times in self.stats.items():
            if times:
                result[op] = {
                    "count": len(times),
                    "avg_ms": sum(times) / len(times),
                    "min_ms": min(times),
                    "max_ms": max(times)
                }
        return result
    
    def summary(self):
        """打印性能摘要"""
        stats = self.get_stats()
        print("\n" + "=" * 50)
        print("📊 性能监控报告")
        print("=" * 50)
        
        if not stats:
            print("暂无数据")
            return
        
        for op, data in sorted(stats.items(), key=lambda x: x[1]["avg_ms"], reverse=True):
            print(f"\n{op}:")
            print(f"  次数: {data['count']}")
            print(f"  平均: {data['avg_ms']:.2f}ms")
            print(f"  最小: {data['min_ms']:.2f}ms")
            print(f"  最大: {data['max_ms']:.2f}ms")
        
        print("\n" + "=" * 50)
    
    def reset(self):
        """重置统计"""
        self.stats.clear()


def run_benchmark():
    """运行性能基准测试"""
    from wangchuan.memory_api import Memory
    
    monitor = PerformanceMonitor()
    m = Memory()
    
    print("🚀 运行性能基准测试...\n")
    
    for i in range(20):
        start = time.time()
        m.remember(f"性能测试 {i}", importance=0.5)
        monitor.record("remember", (time.time() - start) * 1000)
    
    for i in range(20):
        start = time.time()
        m.recall("测试", limit=5)
        monitor.record("recall", (time.time() - start) * 1000)
    
    for i in range(20):
        start = time.time()
        m.status()
        monitor.record("status", (time.time() - start) * 1000)
    
    monitor.summary()
    return monitor.get_stats()


if __name__ == "__main__":
    run_benchmark()
