"""MCP 数据源测试。

覆盖：
- 字段映射（akshare-stock / mcp-eastmoney / stock-api 三种返回风格）
- 两层降级（第一层失败 → 第二层成功；全部失败抛 DataSourceError）
- 不提供的数据类型（资金流/新闻/扩展分析）直接抛错交由 Router 降级
- 配置解析（build_mcp_data_source）
"""

from datetime import date

import pytest

from app.data_sources.base import DataSourceError
from app.data_sources.mcp_source import MCPDataSource, build_mcp_data_source


class FakeMCPClient:
    """测试用假 MCP 客户端：模拟 call_tool 行为，不启动真实子进程。"""

    def __init__(self, name: str, responses=None, errors=None) -> None:
        self.name = name
        self.responses = responses or {}  # {tool_name: raw}
        self.errors = errors or {}        # {tool_name: Exception}
        self.calls: list = []

    def call_tool(self, tool_name: str, arguments: dict):
        self.calls.append((tool_name, arguments))
        if tool_name in self.errors:
            raise self.errors[tool_name]
        if tool_name in self.responses:
            return self.responses[tool_name]
        raise DataSourceError(f"{self.name} 无 {tool_name} 响应")


# ---------- 行情映射 ----------


def test_get_quote_mapping_akshare_style():
    """正常路径：akshare-stock-mcp 风格返回映射为 Quote。"""
    server = FakeMCPClient("akshare-stock", responses={
        "get_stock_realtime": {
            "code": "600519", "name": "贵州茅台", "price": 1700.0,
            "change_pct": 1.23, "change": 20.7,
            "open": 1680.0, "high": 1710.0, "low": 1675.0,
            "prev_close": 1679.3, "volume": 30000, "amount": 5.1e9,
            "turnover_rate": 0.35, "amplitude": 2.1,
            "pe": 30.5, "pb": 8.9, "market_cap": 2.1e12,
            "circ_market_cap": 2.1e12,
        }
    })
    ds = MCPDataSource([server])
    quote = ds.get_quote("600519")

    assert quote.code == "600519"
    assert quote.name == "贵州茅台"
    assert quote.latest_price == 1700.0
    assert quote.change_percent == 1.23
    assert quote.pe_ttm == 30.5
    assert quote.market_cap == 2.1e12
    assert quote.turnover_rate == 0.35
    assert quote.source == "mcp"

    # 校验请求参数：akshare-stock 用 symbol 字段
    tool, args = server.calls[0]
    assert tool == "get_stock_realtime"
    assert args == {"symbol": "600519"}


def test_get_quote_mapping_eastmoney_style():
    """正常路径：mcp-eastmoney 风格返回（amount 为字符串）映射为 Quote。"""
    server = FakeMCPClient("mcp-eastmoney", responses={
        "get_stock_quote": {
            "code": "300750", "name": "宁德时代", "price": 394.85,
            "change_pct": 3.31, "amount": "162.79亿", "pe": 12.65,
        }
    })
    ds = MCPDataSource([server])
    quote = ds.get_quote("300750")

    assert quote.name == "宁德时代"
    assert quote.latest_price == 394.85
    assert quote.source == "mcp"

    # 校验请求参数：mcp-eastmoney 用 code 字段
    tool, args = server.calls[0]
    assert tool == "get_stock_quote"
    assert args == {"code": "300750"}


def test_get_quote_missing_fields_default():
    """边界：返回缺字段时使用默认值。"""
    server = FakeMCPClient("akshare-stock", responses={
        "get_stock_realtime": {"code": "000001", "name": "平安银行"}
    })
    ds = MCPDataSource([server])
    quote = ds.get_quote("000001")

    assert quote.latest_price == 0.0
    assert quote.pe_ttm is None
    assert quote.market_cap is None


def test_get_quote_mapping_stock_api_style():
    """正常路径：stock-api 风格返回（response.stock 嵌套 + percent 小数）映射为 Quote。"""
    server = FakeMCPClient("stock-api", responses={
        "get_stock": {
            "input": {"code": "SH600519"},
            "response": {
                "stock": {
                    "name": "贵州茅台", "code": "SH600519", "now": 1355.29,
                    "low": 1330.0, "high": 1360.0,
                    "percent": 0.0123, "yesterday": 1338.82,
                    "source": "tencent",
                }
            },
        }
    })
    ds = MCPDataSource([server])
    quote = ds.get_quote("600519")

    assert quote.name == "贵州茅台"
    assert quote.latest_price == 1355.29
    assert quote.change_percent == 1.23  # percent 0.0123（小数）→ 1.23%
    assert quote.pre_close == 1338.82
    assert quote.high_price == 1360.0
    assert quote.low_price == 1330.0

    # 校验请求参数：stock-api 需要 SH 前缀代码
    tool, args = server.calls[0]
    assert tool == "get_stock"
    assert args == {"code": "SH600519"}


def test_get_quote_stock_api_sz_code():
    """边界：stock-api 深市代码转 SZ 前缀，percent 负值换算正确。"""
    server = FakeMCPClient("stock-api", responses={
        "get_stock": {
            "response": {"stock": {
                "name": "宁德时代", "code": "SZ300750", "now": 394.85,
                "percent": -0.021, "yesterday": 403.3, "high": 405.0, "low": 392.0,
            }}
        }
    })
    ds = MCPDataSource([server])
    quote = ds.get_quote("300750")

    assert quote.name == "宁德时代"
    assert quote.latest_price == 394.85
    assert quote.change_percent == -2.1
    tool, args = server.calls[0]
    assert args == {"code": "SZ300750"}


# ---------- K 线映射 ----------


def test_get_kline_date_range_mode():
    """正常路径：date_range 模式（akshare-stock）映射 KLine。"""
    server = FakeMCPClient("akshare-stock", responses={
        "get_stock_history": {
            "data": [
                {"date": "2026-07-01", "open": 10.0, "high": 10.5,
                 "low": 9.8, "close": 10.2, "volume": 1000, "amount": 1e7},
                {"date": "2026-07-02", "open": 10.2, "high": 10.8,
                 "low": 10.0, "close": 10.7, "volume": 1200, "amount": 1.2e7},
            ]
        }
    })
    ds = MCPDataSource([server])
    klines = ds.get_kline("600519", days=10)

    assert len(klines) == 2
    assert klines[0].close == 10.2
    assert klines[1].trade_date == date(2026, 7, 2)
    assert klines[0].volume == 1000

    # date_range 模式应传 start_date/end_date/adjust
    tool, args = server.calls[0]
    assert tool == "get_stock_history"
    assert "start_date" in args and "end_date" in args
    assert args["adjust"] == "qfq"
    assert args["period"] == "daily"


def test_get_kline_count_mode():
    """正常路径：count 模式（mcp-eastmoney）映射 KLine。"""
    server = FakeMCPClient("mcp-eastmoney", responses={
        "get_kline": [
            {"date": "2026-07-01", "open": 10.0, "high": 10.5,
             "low": 9.8, "close": 10.2, "volume": 1000},
        ]
    })
    ds = MCPDataSource([server])
    klines = ds.get_kline("300750", days=30)

    assert len(klines) == 1
    assert klines[0].close == 10.2

    # count 模式应传 count
    tool, args = server.calls[0]
    assert tool == "get_kline"
    assert args["count"] == 30


def test_get_kline_stock_api_style():
    """正常路径：stock-api 风格返回（response.klines 嵌套）映射 KLine。"""
    server = FakeMCPClient("stock-api", responses={
        "get_klines": {
            "input": {"code": "SH600519", "period": "day", "count": 30, "adjust": "qfq"},
            "response": {
                "count": 2,
                "klines": [
                    {"date": "2026-07-01", "open": 10.0, "high": 10.5,
                     "low": 9.8, "close": 10.2, "volume": 1000},
                    {"date": "2026-07-02", "open": 10.2, "high": 10.8,
                     "low": 10.0, "close": 10.7, "volume": 1200},
                ],
            },
        }
    })
    ds = MCPDataSource([server])
    klines = ds.get_kline("600519", days=30)

    assert len(klines) == 2
    assert klines[0].close == 10.2
    assert klines[1].trade_date == date(2026, 7, 2)

    # count 模式 + stock-api 应传 SH 前缀、day 周期、qfq 复权
    tool, args = server.calls[0]
    assert tool == "get_klines"
    assert args == {"code": "SH600519", "period": "day", "count": 30, "adjust": "qfq"}


def test_get_kline_stock_api_empty():
    """边界：stock-api K 线为空（response 无 klines）时不报错。"""
    server = FakeMCPClient("stock-api", responses={
        "get_klines": {"response": {"count": 0}}
    })
    ds = MCPDataSource([server])
    assert ds.get_kline("600519") == []


def test_get_kline_returns_empty():
    """边界：K 线返回空列表时不报错。"""
    server = FakeMCPClient("akshare-stock", responses={
        "get_stock_history": {"data": []}
    })
    ds = MCPDataSource([server])
    assert ds.get_kline("600519") == []


# ---------- 财务映射 ----------


def test_get_financial_mapping():
    """正常路径：akshare-stock 财务摘要映射为 FinancialData。"""
    server = FakeMCPClient("akshare-stock", responses={
        "get_stock_financial_abstract": {
            "code": "600519", "name": "贵州茅台",
            "pe": 30.5, "pb": 8.9, "roe": 30.1,
            "revenue_growth": 15.2, "profit_growth": 17.8,
            "eps": 58.9, "net_margin": 52.0,
        }
    })
    ds = MCPDataSource([server])
    fin = ds.get_financial("600519")

    assert fin.name == "贵州茅台"
    assert fin.pe_ttm == 30.5
    assert fin.pb == 8.9
    assert fin.roe == 30.1
    assert fin.revenue_growth == 15.2
    assert fin.source == "mcp"


def test_get_financial_fallback_when_tool_missing():
    """降级：第一层无财务工具时，第二层提供则成功。"""
    primary = FakeMCPClient("akshare-stock")  # 无 financial 工具
    fallback = FakeMCPClient("mcp-eastmoney", responses={
        "get_stock_financial_abstract": {
            "code": "300750", "name": "宁德时代", "pe": 12.65, "pb": 5.0,
        }
    })
    ds = MCPDataSource([primary, fallback])
    fin = ds.get_financial("300750")
    assert fin.name == "宁德时代"
    assert fin.pe_ttm == 12.65


def test_get_financial_stock_api_falls_back():
    """降级：stock-api 不提供财务工具，直接降级到下一层（如 akshare-stock）。"""
    primary = FakeMCPClient("stock-api", responses={
        "get_stock": {"response": {"stock": {
            "name": "贵州茅台", "code": "SH600519", "now": 1355.29,
        }}}
    })
    fallback = FakeMCPClient("akshare-stock", responses={
        "get_stock_financial_abstract": {"code": "600519", "name": "贵州茅台", "pe": 30.5}
    })
    ds = MCPDataSource([primary, fallback])
    fin = ds.get_financial("600519")

    assert fin.name == "贵州茅台"
    assert fin.pe_ttm == 30.5
    # stock-api 未配置财务工具 → 不应调用其任何工具
    assert primary.calls == []
    assert len(fallback.calls) == 1


def test_quote_error_field_fallback_to_second_layer():
    """降级：第一层返回 error 字段（如 ConnectionError）+ 空 data 时视为失败，降级到第二层。"""
    primary = FakeMCPClient("akshare-stock", responses={
        "get_stock_realtime": {
            "error": "ConnectionError: Remote end closed connection without response",
            "data": [],
        }
    })
    fallback = FakeMCPClient("mcp-eastmoney", responses={
        "get_stock_quote": {"code": "600519", "name": "贵州茅台", "price": 1343.0}
    })
    ds = MCPDataSource([primary, fallback])
    quote = ds.get_quote("600519")

    assert quote.name == "贵州茅台"
    assert quote.latest_price == 1343.0
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


def test_financial_long_table_mapping():
    """正常路径：AKShare 财务长表格式（指标 x 报告期）映射为 FinancialData。"""
    server = FakeMCPClient("akshare-stock", responses={
        "get_stock_financial_abstract": {
            "count": 3,
            "data": [
                {"选项": "常用指标", "指标": "净资产收益率(ROE)", "20251231": 34.1, "20241231": 33.0},
                {"选项": "常用指标", "指标": "毛利率", "20251231": 91.5, "20241231": 90.0},
                {"选项": "常用指标", "指标": "营业总收入增长率", "20251231": 15.2, "20241231": 12.0},
                {"选项": "常用指标", "指标": "归属母公司净利润增长率", "20251231": 17.8, "20241231": 14.0},
                {"选项": "常用指标", "指标": "资产负债率", "20251231": 21.3, "20241231": 22.0},
                {"选项": "常用指标", "指标": "基本每股收益", "20251231": 58.9, "20241231": 55.0},
                {"选项": "常用指标", "指标": "每股净资产", "20251231": 190.0, "20241231": 180.0},
            ],
        }
    })
    ds = MCPDataSource([server])
    fin = ds.get_financial("600519")

    assert fin.roe == 34.1
    assert fin.gross_margin == 91.5
    assert fin.revenue_growth == 15.2
    assert fin.profit_growth == 17.8
    assert fin.debt_ratio == 21.3
    assert fin.eps == 58.9
    assert fin.bvps == 190.0
    assert fin.source == "mcp"


def test_quote_eastmoney_pe_field_misaligned_ignored():
    """边界：mcp-eastmoney 的 pe 字段实际为涨跌额时，视为无效置 None。"""
    server = FakeMCPClient("mcp-eastmoney", responses={
        "get_stock_quote": {
            "code": "600519", "name": "贵州茅台", "price": 1343.0,
            "change": -3.5, "change_pct": -0.26, "pe": -3.5,
        }
    })
    ds = MCPDataSource([server])
    quote = ds.get_quote("600519")

    assert quote.latest_price == 1343.0
    assert quote.change_amount == -3.5
    assert quote.pe_ttm is None


def test_financial_long_table_empty():
    """边界：财务长表无报告期列时按扁平 dict 处理（返回空值）。"""
    server = FakeMCPClient("akshare-stock", responses={
        "get_stock_financial_abstract": {"count": 0, "data": []}
    })
    ds = MCPDataSource([server])
    fin = ds.get_financial("600519")
    assert fin.roe is None
    assert fin.name == ""


# ---------- 两层降级 ----------


def test_two_layer_fallback():
    """正常路径：第一层失败 → 第二层成功。"""
    primary = FakeMCPClient("akshare-stock", errors={
        "get_stock_realtime": DataSourceError("子进程超时")
    })
    fallback = FakeMCPClient("mcp-eastmoney", responses={
        "get_stock_quote": {"code": "300750", "name": "宁德时代", "price": 394.85}
    })
    ds = MCPDataSource([primary, fallback])
    quote = ds.get_quote("300750")

    assert quote.name == "宁德时代"
    assert quote.source == "mcp"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


def test_all_servers_fail_raise():
    """边界：两层都失败 → DataSourceError。"""
    primary = FakeMCPClient("akshare-stock", errors={
        "get_stock_realtime": DataSourceError("子进程超时")
    })
    fallback = FakeMCPClient("mcp-eastmoney", errors={
        "get_stock_quote": DataSourceError("连接失败")
    })
    ds = MCPDataSource([primary, fallback])
    with pytest.raises(DataSourceError):
        ds.get_quote("300750")


def test_no_servers_raise():
    """边界：无可用 server → DataSourceError。"""
    ds = MCPDataSource([])
    with pytest.raises(DataSourceError):
        ds.get_quote("600519")


# ---------- 财务指标列表映射（china-stock-mcp） ----------


def test_get_financial_metric_rows_mapping():
    """正常路径：china-stock-mcp 指标列表（output_format=json）映射 FinancialData。"""
    raw = [
        {"选项": "常用指标", "指标": "净资产收益率(ROE)", "20260331": 12.34, "20251231": 30.5},
        {"选项": "常用指标", "指标": "总资产报酬率(ROA)", "20260331": 10.1},
        {"选项": "常用指标", "指标": "毛利率", "20260331": 91.5},
        {"选项": "常用指标", "指标": "销售净利率", "20260331": 51.2},
        {"选项": "成长能力", "指标": "营业总收入增长率", "20260331": 10.3},
        {"选项": "成长能力", "指标": "归属母公司净利润增长率", "20260331": 15.4},
        {"选项": "偿债能力", "指标": "资产负债率", "20260331": 20.1},
        {"选项": "每股指标", "指标": "基本每股收益", "20260331": 21.68},
        {"选项": "每股指标", "指标": "每股净资产", "20260331": 180.5},
    ]
    ds = MCPDataSource([FakeMCPClient("china-stock-mcp")])
    fin = ds._to_financial("600519", raw)

    assert fin.roe == 12.34
    assert fin.roa == 10.1
    assert fin.gross_margin == 91.5
    assert fin.net_margin == 51.2
    assert fin.revenue_growth == 10.3
    assert fin.profit_growth == 15.4
    assert fin.debt_ratio == 20.1
    assert fin.eps == 21.68
    assert fin.bvps == 180.5
    assert fin.source == "mcp"


def test_get_financial_metric_rows_takes_latest_non_empty():
    """边界：指标行最新期为 null/空时回退到前一期，空行跳过。"""
    raw = [
        {"指标": "基本每股收益", "20260331": None, "20251231": 68.42},
        {"指标": "毛利率", "20260331": ""},
    ]
    ds = MCPDataSource([FakeMCPClient("china-stock-mcp")])
    fin = ds._to_financial("600519", raw)

    assert fin.eps == 68.42
    assert fin.gross_margin is None


def test_get_financial_adds_output_format_for_china_stock():
    """正常路径：china-stock-mcp 调用 get_financial_metrics 时附加 output_format=json。"""
    server = FakeMCPClient("china-stock-mcp", responses={
        "get_financial_metrics": [{"指标": "毛利率", "20260331": 91.5}]
    })
    ds = MCPDataSource([server])
    fin = ds.get_financial("600519")

    assert fin.gross_margin == 91.5
    tool, args = server.calls[0]
    assert tool == "get_financial_metrics"
    assert args == {"symbol": "600519", "output_format": "json"}


# ---------- 资金流映射（stock-sdk-mcp） ----------


def test_get_capital_flow_stock_sdk_style():
    """正常路径：stock-sdk-mcp 返回结构映射 CapitalFlow，散户=小单+中单。"""
    raw = {
        "data": [
            {
                "date": "2026-08-13", "mainNetInflow": 123456.0,
                "mainNetInflowPercent": 1.5,
                "smallNetInflow": 100.0, "mediumNetInflow": 200.0,
                "smallNetInflowPercent": 0.1, "mediumNetInflowPercent": 0.2,
                "largeNetInflow": 300.0, "largeNetInflowPercent": 0.3,
                "close": 1355.29,
            },
            {
                "date": "2026-08-12", "mainNetInflow": -1000.0,
                "mainNetInflowPercent": -0.5,
                "smallNetInflow": 0.0, "mediumNetInflow": 0.0,
                "smallNetInflowPercent": 0.0, "mediumNetInflowPercent": 0.0,
                "largeNetInflow": 0.0, "largeNetInflowPercent": 0.0,
                "close": 1340.0,
            },
        ]
    }
    ds = MCPDataSource([FakeMCPClient("stock-sdk-mcp")])
    flows = ds._to_capital_flows("600519", raw)

    assert len(flows) == 2
    assert flows[0].trade_date == date(2026, 8, 13)
    assert flows[0].main_net_inflow == 123456.0
    assert flows[0].main_net_inflow_rate == 1.5
    assert flows[0].retail_net_inflow == 300.0  # 小单100 + 中单200
    assert flows[0].retail_net_inflow_rate == pytest.approx(0.3)
    assert flows[0].large_order_net_inflow == 300.0
    assert flows[1].main_net_inflow == -1000.0


def test_get_capital_flow_takes_latest_days():
    """正常路径：get_capital_flow 通过 MCP 调用并截取最近 days 条。"""
    server = FakeMCPClient("stock-sdk-mcp", responses={
        "get_stock_fund_flow_history": {
            "data": [
                {"date": f"2026-08-{d:02d}", "mainNetInflow": float(d)}
                for d in range(1, 6)
            ]
        }
    })
    ds = MCPDataSource([server])
    flows = ds.get_capital_flow("600519", days=2)

    assert len(flows) == 2
    assert flows[-1].main_net_inflow == 5.0
    tool, args = server.calls[0]
    assert tool == "get_stock_fund_flow_history"
    assert args == {"symbol": "600519", "period": "daily"}


# ---------- 新闻映射（china-stock-mcp） ----------


def test_get_news_china_stock_style():
    """正常路径：china-stock-mcp 新闻返回（毫秒时间戳）映射 NewsItem。"""
    server = FakeMCPClient("china-stock-mcp", responses={
        "get_news_data": [
            {
                "keyword": "600519",
                "title": "贵州茅台发布2026年半年报",
                "content": "公司实现营业收入同比增长15.4%",
                "publish_time": 1786611540000,
                "source": "证券时报网",
                "url": "https://example.com/news/1",
            }
        ]
    })
    ds = MCPDataSource([server])
    news = ds.get_news("600519", limit=10)

    assert len(news) == 1
    assert news[0].title == "贵州茅台发布2026年半年报"
    assert news[0].summary == "公司实现营业收入同比增长15.4%"
    assert news[0].source == "证券时报网"
    assert news[0].url == "https://example.com/news/1"
    assert news[0].date is not None and news[0].date.year == 2026
    tool, args = server.calls[0]
    assert tool == "get_news_data"
    assert args == {"symbol": "600519", "output_format": "json"}


def test_get_news_limits_count():
    """边界：get_news 按 limit 截断条数。"""
    server = FakeMCPClient("china-stock-mcp", responses={
        "get_news_data": [
            {"title": f"新闻{i}", "content": "内容", "source": "来源"}
            for i in range(15)
        ]
    })
    ds = MCPDataSource([server])
    news = ds.get_news("600519", limit=5)

    assert len(news) == 5


# ---------- 不提供的数据类型 ----------


def test_capital_flow_not_supported():
    """资金流：MCP 源不提供，抛 DataSourceError 交由 Router 降级。"""
    ds = MCPDataSource([])
    with pytest.raises(DataSourceError):
        ds.get_capital_flow("600519", days=10)


def test_news_not_supported():
    """新闻：MCP 源不提供，抛 DataSourceError 交由 Router 降级。"""
    ds = MCPDataSource([])
    with pytest.raises(DataSourceError):
        ds.get_news("600519", limit=10)


def test_extended_analysis_not_supported():
    """扩展分析：MCP 源不提供，抛 DataSourceError 交由 Router 降级。"""
    ds = MCPDataSource([])
    with pytest.raises(DataSourceError):
        ds.get_extended_analysis("600519", None, [], [])


# ---------- 配置解析 ----------


def test_build_mcp_data_source_from_config():
    """正常路径：从配置 dict 构建两层 MCP 数据源。"""
    cfg = {
        "enabled": True,
        "timeout": 30,
        "servers": [
            {
                "name": "akshare-stock",
                "command": "python",
                "args": ["-m", "akshare_stock_mcp.server"],
                "enabled": True,
                "code_param": "symbol",
                "tools": {
                    "quote": "get_stock_realtime",
                    "kline": "get_stock_history",
                    "kline_mode": "date_range",
                    "financial": "get_stock_financial_abstract",
                },
            },
            {
                "name": "mcp-eastmoney",
                "command": "uvx",
                "args": ["mcp-eastmoney"],
                "enabled": True,
                "code_param": "code",
                "tools": {
                    "quote": "get_stock_quote",
                    "kline": "get_kline",
                    "kline_mode": "count",
                    "financial": None,
                },
            },
        ],
    }
    ds = build_mcp_data_source(cfg)

    assert isinstance(ds, MCPDataSource)
    assert len(ds.servers) == 2
    assert ds.servers[0].name == "akshare-stock"
    assert ds.servers[1].name == "mcp-eastmoney"


def test_build_mcp_data_source_disabled():
    """边界：enabled=false 返回 None。"""
    assert build_mcp_data_source({"enabled": False}) is None


# ---------- MCPClient.list_tools ----------


class _FakeTool:
    """模拟 MCP 工具对象。"""

    def __init__(self, name, description, input_schema):
        self.name = name
        self.description = description
        self.inputSchema = input_schema


class _FakeListToolsResult:
    """模拟 session.list_tools() 返回。"""

    def __init__(self, tools):
        self.tools = tools


class _FakeSession:
    """模拟 MCP ClientSession，仅实现 list_tools。"""

    def __init__(self, tools):
        self._tools = tools

    async def list_tools(self):
        return _FakeListToolsResult(self._tools)


def test_list_tools_returns_tool_schemas():
    """正常路径：list_tools 返回工具名/描述/入参 schema。"""
    import asyncio
    import threading

    from app.data_sources.mcp_source import MCPClient

    client = MCPClient("fake-server", "python", ["-m", "x"])
    client._loop = asyncio.new_event_loop()
    client._thread = threading.Thread(target=client._loop.run_forever, daemon=True)
    client._thread.start()
    try:
        client._session = _FakeSession([
            _FakeTool("get_stock", "实时行情", {
                "type": "object", "properties": {"code": {"type": "string"}},
            }),
        ])
        tools = client.list_tools()

        assert len(tools) == 1
        assert tools[0]["name"] == "get_stock"
        assert tools[0]["description"] == "实时行情"
        assert tools[0]["input_schema"]["properties"]["code"]["type"] == "string"
    finally:
        client._loop.call_soon_threadsafe(client._loop.stop)
        client._thread.join(timeout=5)


def test_build_mcp_data_source_skip_disabled_server():
    """边界：单个 server 禁用时被跳过。"""
    cfg = {
        "enabled": True,
        "servers": [
            {"name": "akshare-stock", "command": "python",
             "args": ["-m", "akshare_stock_mcp.server"], "enabled": False,
             "code_param": "symbol",
             "tools": {"quote": "get_stock_realtime", "kline": "get_stock_history",
                       "kline_mode": "date_range", "financial": None}},
            {"name": "mcp-eastmoney", "command": "uvx",
             "args": ["mcp-eastmoney"], "enabled": True,
             "code_param": "code",
             "tools": {"quote": "get_stock_quote", "kline": "get_kline",
                       "kline_mode": "count", "financial": None}},
        ],
    }
    ds = build_mcp_data_source(cfg)
    assert len(ds.servers) == 1
    assert ds.servers[0].name == "mcp-eastmoney"


def test_build_mcp_data_source_stock_api_config():
    """正常路径：配置新增 stock-api（sh_sz 代码格式），旧 server 禁用时被跳过。"""
    cfg = {
        "enabled": True,
        "timeout": 30,
        "servers": [
            {
                "name": "akshare-stock",
                "command": "python",
                "args": ["-m", "akshare_stock_mcp.server"],
                "enabled": False,
                "code_param": "symbol",
                "tools": {
                    "quote": "get_stock_realtime",
                    "kline": "get_stock_history",
                    "kline_mode": "date_range",
                    "financial": "get_stock_financial_abstract",
                },
            },
            {
                "name": "stock-api",
                "command": "node",
                "args": ["C:\\stock-api\\dist\\cli.js", "mcp"],
                "enabled": True,
                "code_param": "code",
                "code_style": "sh_sz",
                "tools": {
                    "quote": "get_stock",
                    "kline": "get_klines",
                    "kline_mode": "count",
                    "financial": None,
                },
            },
        ],
    }
    ds = build_mcp_data_source(cfg)

    assert len(ds.servers) == 1
    assert ds.servers[0].name == "stock-api"
    # code_style 需传递给运行时，供代码格式转换使用
    assert ds._configs[0]["code_style"] == "sh_sz"
