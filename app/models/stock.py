"""股票相关数据模型。"""

from datetime import datetime, date
from typing import Optional

from pydantic import BaseModel, Field


class Quote(BaseModel):
    """实时行情数据"""
    code: str = Field(..., description="股票代码")
    name: str = Field(default="", description="股票名称")
    latest_price: float = Field(0.0, description="最新价")
    change_percent: float = Field(0.0, description="涨跌幅（%）")
    change_amount: float = Field(0.0, description="涨跌额")
    open_price: float = Field(0.0, description="今开")
    high_price: float = Field(0.0, description="最高")
    low_price: float = Field(0.0, description="最低")
    pre_close: float = Field(0.0, description="昨收")
    volume: int = Field(0, description="成交量（手）")
    amount: float = Field(0.0, description="成交额（元）")
    turnover_rate: float = Field(0.0, description="换手率（%）")
    amplitude: float = Field(0.0, description="振幅（%）")
    pe_ttm: Optional[float] = Field(None, description="市盈率（TTM）")
    pb: Optional[float] = Field(None, description="市净率")
    market_cap: Optional[float] = Field(None, description="总市值（元）")
    circulating_market_cap: Optional[float] = Field(None, description="流通市值（元）")
    source: str = Field("", description="数据来源")
    fetched_at: Optional[datetime] = Field(None, description="采集时间")


class KLine(BaseModel):
    """K 线数据"""
    trade_date: date = Field(..., description="日期")
    open_price: float = Field(0.0, description="开盘价")
    high: float = Field(0.0, description="最高价")
    low: float = Field(0.0, description="最低价")
    close: float = Field(0.0, description="收盘价")
    volume: int = Field(0, description="成交量（手）")
    amount: float = Field(0.0, description="成交额（元）")
    # 技术指标（由 stock-sdk-mcp 的 get_kline_with_indicators 提供，其他数据源为 None）
    ma5: Optional[float] = Field(None, description="5日均线")
    ma10: Optional[float] = Field(None, description="10日均线")
    ma20: Optional[float] = Field(None, description="20日均线")
    dif: Optional[float] = Field(None, description="MACD DIF")
    dea: Optional[float] = Field(None, description="MACD DEA")
    macd: Optional[float] = Field(None, description="MACD柱")
    rsi6: Optional[float] = Field(None, description="RSI(6)")
    rsi12: Optional[float] = Field(None, description="RSI(12)")
    rsi24: Optional[float] = Field(None, description="RSI(24)")


class FinancialData(BaseModel):
    """财务数据"""
    code: str = Field(..., description="股票代码")
    name: str = Field(default="", description="股票名称")
    pe: Optional[float] = Field(None, description="市盈率")
    pe_ttm: Optional[float] = Field(None, description="市盈率（TTM）")
    pb: Optional[float] = Field(None, description="市净率")
    ps: Optional[float] = Field(None, description="市销率")
    pcf: Optional[float] = Field(None, description="市现率")
    roe: Optional[float] = Field(None, description="净资产收益率（%）")
    roa: Optional[float] = Field(None, description="总资产收益率（%）")
    gross_margin: Optional[float] = Field(None, description="毛利率（%）")
    net_margin: Optional[float] = Field(None, description="净利率（%）")
    revenue_growth: Optional[float] = Field(None, description="营业收入同比增长（%）")
    profit_growth: Optional[float] = Field(None, description="净利润同比增长（%）")
    debt_ratio: Optional[float] = Field(None, description="资产负债率（%）")
    eps: Optional[float] = Field(None, description="每股收益")
    bvps: Optional[float] = Field(None, description="每股净资产")
    source: str = Field("", description="数据来源")
    fetched_at: Optional[datetime] = Field(None, description="采集时间")


class CapitalFlow(BaseModel):
    """资金流向数据"""
    trade_date: date = Field(..., description="日期")
    code: str = Field(..., description="股票代码")
    main_net_inflow: Optional[float] = Field(None, description="主力净流入（元）")
    main_net_inflow_rate: Optional[float] = Field(None, description="主力净流入占比（%）")
    retail_net_inflow: Optional[float] = Field(None, description="散户净流入（元）")
    retail_net_inflow_rate: Optional[float] = Field(None, description="散户净流入占比（%）")
    north_net_inflow: Optional[float] = Field(None, description="北向资金净流入（元）")
    north_net_inflow_rate: Optional[float] = Field(None, description="北向资金净流入占比（%）")
    large_order_net_inflow: Optional[float] = Field(None, description="大单净流入（元）")
    large_order_net_inflow_rate: Optional[float] = Field(None, description="大单净流入占比（%）")
    source: str = Field("", description="数据来源")


class NewsItem(BaseModel):
    """新闻/公告数据"""
    title: str = Field(..., description="标题")
    date: Optional[datetime] = Field(None, description="发布时间")
    summary: Optional[str] = Field(None, description="摘要")
    source: str = Field("", description="来源")
    url: Optional[str] = Field(None, description="链接")
    sentiment: Optional[str] = Field(None, description="情感倾向（positive/negative/neutral）")


class ExtendedAnalysis(BaseModel):
    """扩展分析数据 - 包含资金/股东/题材/风险/价值等9个维度。

    用于数据采集阶段收集原始扩展数据，再由 LLM 分析阶段做深度解读。
    """
    # 资金流方向判断（基于原始资金流数据汇总）
    capital_flow_direction: str = Field(default="", description="资金整体流入/流出方向（inflow/outflow/balanced）")
    capital_flow_summary: str = Field(default="", description="资金流分析摘要")

    # 基金资金流向（来自定期报告中的基金持仓变化）
    fund_capital_direction: str = Field(default="", description="基金资金流入/流出（inflow/outflow/unchanged/unknown）")
    fund_holding_change: Optional[float] = Field(None, description="基金持股比例变化（百分点）")
    fund_holding_count: Optional[int] = Field(None, description="持仓基金数量")
    fund_analysis_summary: str = Field(default="", description="基金持仓分析摘要")

    # 社保基金流向
    social_security_direction: str = Field(default="", description="社保基金流入/流出（inflow/outflow/unchanged/unknown）")
    social_security_holding: Optional[float] = Field(None, description="社保基金持股比例（%）")
    social_security_analysis_summary: str = Field(default="", description="社保基金分析摘要")

    # 股东户数变化
    shareholder_count_change: str = Field(default="", description="股东户数变化（increase/decrease/unchanged/unknown）")
    shareholder_count_latest: Optional[int] = Field(None, description="最新股东户数")
    shareholder_count_previous: Optional[int] = Field(None, description="上期股东户数")
    shareholder_change_rate: Optional[float] = Field(None, description="股东户数变化率（%）")
    shareholder_analysis_summary: str = Field(default="", description="股东户数分析摘要")

    # 股东平均持股数量变化
    avg_share_holding_change: str = Field(default="", description="股东平均持股变化（increase/decrease/unchanged/unknown）")
    avg_share_holding_latest: Optional[float] = Field(None, description="最新股东平均持股（股/人）")
    avg_share_holding_change_rate: Optional[float] = Field(None, description="平均持股变化率（%）")

    # 活跃股判断（基于换手率、成交量、振幅等多指标）
    is_active_stock: bool = Field(default=False, description="是否活跃股")
    active_stock_reason: str = Field(default="", description="活跃股判断依据")
    active_stock_score: Optional[float] = Field(None, description="活跃度评分（0-100）")

    # 热点题材（概念板块匹配 + 新闻题材分析）
    hot_themes: list[str] = Field(default_factory=list, description="匹配的概念板块/热点题材列表")
    hot_theme_detail: str = Field(default="", description="热点题材详细分析")

    # 潜在风险
    potential_risks: list[str] = Field(default_factory=list, description="潜在风险因素列表")
    risk_level: str = Field(default="", description="风险等级（低/中/高）")
    risk_analysis_summary: str = Field(default="", description="风险分析摘要")

    # 投资价值评估
    investment_value: str = Field(default="", description="投资价值评估结论")
    investment_value_score: Optional[float] = Field(None, description="投资价值评分（0-100）")
    investment_value_reason: str = Field(default="", description="投资价值判断依据")

    # 元数据
    source: str = Field(default="", description="数据来源")
    fetched_at: Optional[datetime] = Field(None, description="采集时间")
    from_cache: bool = Field(default=False, description="是否来自缓存")
