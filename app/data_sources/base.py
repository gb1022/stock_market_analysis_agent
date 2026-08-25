"""数据源抽象基类与异常定义。"""

from abc import ABC, abstractmethod
from typing import Optional

from app.models.stock import Quote, KLine, FinancialData, CapitalFlow, NewsItem, ExtendedAnalysis


class DataSourceError(Exception):
    """数据源错误"""
    pass


class DataSource(ABC):
    """数据源抽象基类"""

    name: str = ""

    @abstractmethod
    def get_quote(self, code: str) -> Quote:
        """获取实时行情。

        Args:
            code: 股票代码

        Returns:
            Quote 对象

        Raises:
            DataSourceError: 获取失败时抛出
        """
        pass

    @abstractmethod
    def get_kline(self, code: str, period: str = "daily",
                  days: int = 250) -> list[KLine]:
        """获取 K 线数据。

        Args:
            code: 股票代码
            period: 周期（daily/weekly/monthly）
            days: 获取的天数

        Returns:
            KLine 列表

        Raises:
            DataSourceError: 获取失败时抛出
        """
        pass

    @abstractmethod
    def get_financial(self, code: str) -> FinancialData:
        """获取财务数据。

        Args:
            code: 股票代码

        Returns:
            FinancialData 对象

        Raises:
            DataSourceError: 获取失败时抛出
        """
        pass

    @abstractmethod
    def get_capital_flow(self, code: str, days: int = 10) -> list[CapitalFlow]:
        """获取资金流向数据。

        Args:
            code: 股票代码
            days: 获取的天数

        Returns:
            CapitalFlow 列表

        Raises:
            DataSourceError: 获取失败时抛出
        """
        pass

    @abstractmethod
    def get_news(self, code: str, limit: int = 10) -> list[NewsItem]:
        """获取新闻/公告。

        Args:
            code: 股票代码
            limit: 返回条数上限

        Returns:
            NewsItem 列表

        Raises:
            DataSourceError: 获取失败时抛出
        """
        pass

    @abstractmethod
    def get_extended_analysis(self, code: str, quote: Quote,
                              capital_flows: list[CapitalFlow],
                              news_list: list[NewsItem]) -> ExtendedAnalysis:
        """获取扩展分析数据。

        包含：资金流方向、基金持仓、社保持仓、股东户数、活跃度、
        热点题材、潜在风险、投资价值等9个维度。

        Args:
            code: 股票代码
            quote: 实时行情（用于活跃度判断等）
            capital_flows: 资金流向列表（用于资金流方向判断）
            news_list: 新闻列表（用于题材分析）

        Returns:
            ExtendedAnalysis 对象

        Raises:
            DataSourceError: 获取失败时抛出
        """
        pass
