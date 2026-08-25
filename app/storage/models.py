"""存储层数据模型与 TTL 定义。"""

from dataclasses import dataclass


@dataclass
class CachePolicy:
    """每种数据类型的缓存策略"""
    ttl_seconds: int
    max_stale_seconds: int = 0  # 0 = 过期即失效


# 全局缓存策略表
# TTL 可按需调整；运行时可通过 StockCache.update_policy() 覆盖
CACHE_POLICIES: dict[str, CachePolicy] = {
    "quote":              CachePolicy(ttl_seconds=300),        # 5 分钟
    "kline":              CachePolicy(ttl_seconds=86400),      # 1 天
    "financial":          CachePolicy(ttl_seconds=604800),     # 7 天
    "capital_flow":       CachePolicy(ttl_seconds=86400),      # 1 天
    "news":               CachePolicy(ttl_seconds=21600),      # 6 小时
    "stock_list":         CachePolicy(ttl_seconds=604800),    # 7 天（全量股票列表，增量更新）
    "extended_analysis":  CachePolicy(ttl_seconds=86400),      # 1 天（基于定期报告数据）
    "analysis_summary":   CachePolicy(ttl_seconds=3153600000), # 100 年（永久存储分析结果摘要）
}
