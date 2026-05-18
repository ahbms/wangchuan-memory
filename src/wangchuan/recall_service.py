"""
忘川主 recall 语义化入口（稳定职责名入口）

作用：
- 为当前生产主 recall 主链提供不带版本号的语义化入口
- 当前实现暂时转发到 `wangchuan.v3.pipeline_v3.WangchuanPipeline`
- 后续可在不影响调用方的前提下，逐步把 v3 目录内实现迁移到职责化目录
- 主模块只保留当前 recall 主链语义入口；legacy fallback 兼容入口已迁到 `wangchuan.compat` 命名空间

注意：
- 这是主链入口别名，不是 legacy 兼容链
- 运行日志应显示为 `【WangChuan】`，不要暴露 `v3` 到表层认知
- legacy fallback 若仍需使用，优先通过 `wangchuan.compat`，不要直接从 `chat_memory.py` 开始读
"""

from .v3.pipeline_v3 import WangchuanPipeline


__all__ = ["WangchuanPipeline"]
