"""数据源路由器：按优先级降级 + 缓存集成。"""

from datetime import datetime
from typing import Optional

from app.data_sources.base import DataSource, DataSourceError
from app.models.stock import Quote, KLine, FinancialData, CapitalFlow, NewsItem, ExtendedAnalysis


class DataSourceRouter:
    """数据源路由器。

    按优先级依次尝试各数据源，当前数据源失败时自动降级到下一个。
    如果设置了缓存层，则自动查缓存 → 回写缓存。
    """

    def __init__(self, sources: list[DataSource]) -> None:
        """初始化。

        Args:
            sources: 按优先级排序的数据源列表（主数据源在前）
        """
        self._sources = sources
        self._cache = None  # 由 set_cache 注入，避免循环导入

    def set_cache(self, cache) -> None:
        """设置缓存实例（由外部注入）。"""
        self._cache = cache

    def get_quote(self, code: str) -> Quote:
        """获取实时行情。"""
        # 查缓存
        if self._cache:
            cached = self._cache.get_cache("quote", code)
            if cached is not None:
                return Quote(**cached)

        # 拉取数据
        last_error: Optional[Exception] = None
        for source in self._sources:
            try:
                print(f"  [DataSource] 尝试 {source.name} 获取 quote({code})...")
                quote = source.get_quote(code)
                quote.source = source.name
                quote.fetched_at = datetime.now()
                print(f"  [DataSource] {source.name} 获取 quote 成功")
                # 回写缓存
                if self._cache:
                    self._cache.set_cache("quote", code, quote.model_dump(), source.name)
                return quote
            except Exception as e:
                print(f"  [DataSource] {source.name} 获取 quote 失败: {e}")
                last_error = e
                continue
        raise DataSourceError(f"所有数据源均失败: {last_error}")

    def get_kline(self, code: str, period: str = "daily",
                  days: int = 250) -> list[KLine]:
        """获取 K 线数据。"""
        cache_key = f"kline_{period}_{days}"
        if self._cache:
            cached = self._cache.get_cache(cache_key, code)
            if cached is not None:
                return [KLine(**k) for k in cached]

        last_error: Optional[Exception] = None
        for source in self._sources:
            try:
                klines = source.get_kline(code, period, days)
                if self._cache:
                    self._cache.set_cache(cache_key, code, [k.model_dump() for k in klines], source.name)
                return klines
            except Exception as e:
                last_error = e
                continue
        raise DataSourceError(f"所有数据源均失败: {last_error}")

    def get_financial(self, code: str) -> FinancialData:
        """获取财务数据。"""
        if self._cache:
            cached = self._cache.get_cache("financial", code)
            if cached is not None:
                return FinancialData(**cached)

        last_error: Optional[Exception] = None
        for source in self._sources:
            try:
                data = source.get_financial(code)
                data.source = source.name
                data.fetched_at = datetime.now()
                if self._cache:
                    self._cache.set_cache("financial", code, data.model_dump(), source.name)
                return data
            except Exception as e:
                last_error = e
                continue
        raise DataSourceError(f"所有数据源均失败: {last_error}")

    def get_capital_flow(self, code: str, days: int = 10) -> list[CapitalFlow]:
        """获取资金流向数据。"""
        cache_key = f"capital_flow_{days}"
        if self._cache:
            cached = self._cache.get_cache(cache_key, code)
            if cached is not None:
                return [CapitalFlow(**c) for c in cached]

        last_error: Optional[Exception] = None
        for source in self._sources:
            try:
                flows = source.get_capital_flow(code, days)
                if not flows:
                    # 空数据视为失败，继续尝试下一个数据源（避免空结果中断降级链）
                    last_error = DataSourceError(f"{source.name} 返回空资金流数据")
                    continue
                if self._cache:
                    self._cache.set_cache(cache_key, code, [f.model_dump() for f in flows], source.name)
                return flows
            except Exception as e:
                last_error = e
                continue
        raise DataSourceError(f"所有数据源均失败: {last_error}")

    def get_news(self, code: str, limit: int = 10) -> list[NewsItem]:
        """获取新闻/公告。"""
        cache_key = f"news_{limit}"
        if self._cache:
            cached = self._cache.get_cache(cache_key, code)
            if cached is not None:
                return [NewsItem(**n) for n in cached]

        last_error: Optional[Exception] = None
        for source in self._sources:
            try:
                news = source.get_news(code, limit)
                if self._cache:
                    self._cache.set_cache(cache_key, code, [n.model_dump() for n in news], source.name)
                return news
            except Exception as e:
                last_error = e
                continue
        raise DataSourceError(f"所有数据源均失败: {last_error}")

    def get_extended_analysis(self, code: str, quote: Quote,
                              capital_flows: list[CapitalFlow],
                              news_list: list[NewsItem]) -> ExtendedAnalysis:
        """获取扩展分析数据。

        按优先级依次尝试各数据源，带缓存支持。
        """
        cache_key = "extended_analysis"
        if self._cache:
            cached = self._cache.get_cache(cache_key, code)
            if cached is not None:
                print(f"  [DataSource] extended_analysis 命中缓存({code})")
                # 过滤缓存内部字段，保留真实数据
                data = {k: v for k, v in cached.items() if not k.startswith("_")}
                data["from_cache"] = True
                return ExtendedAnalysis(**data)

        last_error: Optional[Exception] = None
        for source in self._sources:
            try:
                print(f"  [DataSource] 尝试 {source.name} 获取 extended_analysis({code})...")
                analysis = source.get_extended_analysis(code, quote, capital_flows, news_list)
                analysis.source = source.name
                analysis.fetched_at = datetime.now()
                print(f"  [DataSource] {source.name} 获取 extended_analysis 成功")
                if self._cache:
                    self._cache.set_cache(cache_key, code, analysis.model_dump(), source.name)
                return analysis
            except Exception as e:
                print(f"  [DataSource] {source.name} 获取 extended_analysis 失败: {e}")
                last_error = e
                continue
        raise DataSourceError(f"所有数据源均失败: {last_error}")
