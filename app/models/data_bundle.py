"""数据采集阶段输出：DataBundle"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.stock import Quote, KLine, FinancialData, CapitalFlow, NewsItem, ExtendedAnalysis


class DataBundle(BaseModel):
    """数据采集阶段输出，包含全部原始数据"""
    stock_code: str = Field(..., description="股票代码")
    market: str = Field(default="A", description="市场")
    quote: Optional[Quote] = Field(None, description="实时行情")
    klines: list[KLine] = Field(default_factory=list, description="K 线数据列表")
    financial: Optional[FinancialData] = Field(None, description="财务数据")
    capital_flow: list[CapitalFlow] = Field(default_factory=list, description="资金流向列表")
    news: list[NewsItem] = Field(default_factory=list, description="新闻列表")
    extended_analysis: Optional[ExtendedAnalysis] = Field(None, description="扩展分析数据")
    fetched_at: Optional[datetime] = Field(None, description="采集时间")
    source: str = Field("", description="数据来源")
