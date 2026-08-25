"""共享 Fixtures。"""

from datetime import datetime, date, timedelta
from typing import Any

import pytest

from app.models.stock import Quote, KLine, FinancialData, CapitalFlow, NewsItem
from app.models.data_bundle import DataBundle


@pytest.fixture
def sample_quote() -> Quote:
    return Quote(
        code="600519",
        name="贵州茅台",
        latest_price=1888.0,
        change_percent=1.25,
        change_amount=23.5,
        open_price=1870.0,
        high_price=1900.0,
        low_price=1865.0,
        pre_close=1864.5,
        volume=2500000,
        amount=4700000000.0,
        turnover_rate=0.42,
        amplitude=1.88,
        pe_ttm=32.5,
        pb=9.8,
        market_cap=23700000000000.0,
        circulating_market_cap=23700000000000.0,
        source="akshare",
        fetched_at=datetime.now(),
    )


@pytest.fixture
def sample_klines() -> list[KLine]:
    today = date.today()
    klines = []
    for i in range(250):
        klines.append(KLine(
            trade_date=today - timedelta(days=i),
            open_price=1800.0 + i * 0.5,
            high=1820.0 + i * 0.5,
            low=1790.0 + i * 0.5,
            close=1810.0 + i * 0.5,
            volume=2000000 + i * 1000,
            amount=3600000000.0,
        ))
    return klines


@pytest.fixture
def sample_financial() -> FinancialData:
    return FinancialData(
        code="600519",
        name="贵州茅台",
        pe_ttm=32.5,
        pb=9.8,
        ps=15.2,
        pcf=28.1,
        roe=30.5,
        roa=18.2,
        gross_margin=91.5,
        net_margin=52.3,
        revenue_growth=18.5,
        profit_growth=22.3,
        debt_ratio=15.2,
        eps=58.2,
        bvps=192.5,
        source="akshare",
        fetched_at=datetime.now(),
    )


@pytest.fixture
def sample_capital_flows() -> list[CapitalFlow]:
    today = date.today()
    flows = []
    for i in range(10):
        flows.append(CapitalFlow(
            trade_date=today - timedelta(days=i),
            code="600519",
            main_net_inflow=100000000.0 + i * 1000000,
            main_net_inflow_rate=5.2 + i * 0.1,
            retail_net_inflow=-50000000.0 - i * 500000,
            retail_net_inflow_rate=-2.8 - i * 0.1,
            large_order_net_inflow=80000000.0 + i * 500000,
            large_order_net_inflow_rate=4.1 + i * 0.1,
            source="akshare",
        ))
    return flows


@pytest.fixture
def sample_news() -> list[NewsItem]:
    return [
        NewsItem(
            title="贵州茅台发布2024年年度报告",
            date=datetime.now(),
            summary="净利润同比增长22.3%",
            source="东方财富",
            sentiment="positive",
        ),
        NewsItem(
            title="茅台出厂价上调20%",
            date=datetime.now() - timedelta(days=1),
            summary="飞天茅台出厂价上调至1499元",
            source="证券时报",
            sentiment="positive",
        ),
    ]


@pytest.fixture
def sample_data_bundle(sample_quote, sample_klines, sample_financial,
                       sample_capital_flows, sample_news) -> DataBundle:
    return DataBundle(
        stock_code="600519",
        market="A",
        quote=sample_quote,
        klines=sample_klines,
        financial=sample_financial,
        capital_flow=sample_capital_flows,
        news=sample_news,
        fetched_at=datetime.now(),
        source="akshare",
    )


@pytest.fixture
def temp_db_path(tmp_path) -> str:
    """临时 SQLite 数据库路径。"""
    return str(tmp_path / "test_cache.db")
