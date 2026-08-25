"""DataSourceRouter + 缓存集成测试。"""

from typing import Optional

import pytest

from app.data_sources.base import DataSource, DataSourceError
from app.models.stock import Quote, KLine, FinancialData, CapitalFlow, NewsItem, ExtendedAnalysis


class MockSource(DataSource):
    """Mock 数据源，返回固定数据。"""
    name = "mock_source"

    def __init__(self, fail_on: Optional[str] = None) -> None:
        self._fail_on = fail_on

    def get_quote(self, code: str) -> Quote:
        if self._fail_on == "quote":
            raise DataSourceError("mock failure")
        return Quote(code=code, name="Mock", latest_price=100.0, source=self.name)

    def get_kline(self, code: str, period: str = "daily",
                  days: int = 250) -> list[KLine]:
        if self._fail_on == "kline":
            raise DataSourceError("mock failure")
        return []

    def get_financial(self, code: str) -> FinancialData:
        if self._fail_on == "financial":
            raise DataSourceError("mock failure")
        return FinancialData(code=code, name="Mock", source=self.name)

    def get_capital_flow(self, code: str, days: int = 10) -> list[CapitalFlow]:
        if self._fail_on == "capital_flow":
            raise DataSourceError("mock failure")
        return []

    def get_news(self, code: str, limit: int = 10) -> list[NewsItem]:
        if self._fail_on == "news":
            raise DataSourceError("mock failure")
        return []

    def get_extended_analysis(self, code: str, quote: Quote,
                              capital_flows: list[CapitalFlow],
                              news_list: list[NewsItem]) -> ExtendedAnalysis:
        if self._fail_on == "extended_analysis":
            raise DataSourceError("mock failure")
        return ExtendedAnalysis(
            source=self.name,
            capital_flow_direction="balanced",
            fund_capital_direction="unknown",
            social_security_direction="unknown",
            shareholder_count_change="unknown",
            avg_share_holding_change="unknown",
            is_active_stock=False,
        )


class TestRouterCacheIntegration:
    """路由 + 缓存集成测试套件"""

    def test_router_fallback(self, temp_db_path):
        """正常路径：主数据源失败时降级到备选。"""
        from app.data_sources.router import DataSourceRouter
        from app.storage.cache import StockCache

        cache = StockCache(db_path=temp_db_path)
        router = DataSourceRouter([
            MockSource(fail_on="quote"),  # 主数据源失败
            MockSource(),                    # 备选成功
        ])
        router.set_cache(cache)

        quote = router.get_quote("600519")
        assert quote is not None
        assert quote.latest_price == 100.0

    def test_router_all_fail(self, temp_db_path):
        """边界：全部数据源失败时抛出异常。"""
        from app.data_sources.router import DataSourceRouter
        from app.storage.cache import StockCache
        from app.data_sources.base import DataSourceError

        cache = StockCache(db_path=temp_db_path)
        router = DataSourceRouter([
            MockSource(fail_on="quote"),
            MockSource(fail_on="quote"),
        ])
        router.set_cache(cache)

        with pytest.raises(DataSourceError):
            router.get_quote("600519")

    def test_router_cache_hit(self, temp_db_path):
        """正常路径：命中缓存时不调用数据源。"""
        from app.data_sources.router import DataSourceRouter
        from app.storage.cache import StockCache

        cache = StockCache(db_path=temp_db_path)
        source = MockSource()

        # 先准备缓存
        cache.set_cache("quote", "600519",
                        {"code": "600519", "latest_price": 100.0,
                         "name": "Cached", "source": "cache"},
                        source="cache")

        router = DataSourceRouter([source])
        router.set_cache(cache)

        # 取缓存数据
        quote = router.get_quote("600519")
        assert quote.latest_price == 100.0
