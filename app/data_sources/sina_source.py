"""新浪财经数据源实现（资金流数据）。

通过新浪财经公开 HTTP API 获取 A 股资金流数据。
当前网络环境下东财接口（AKShare / stock-sdk-mcp 底层）不可达，
新浪资金流接口可作为主力资金流数据的替代来源。

仅实现 get_capital_flow，其余接口抛出 DataSourceError 交由 Router 降级。
"""

import json
from datetime import datetime
from typing import Optional
from urllib.request import Request, urlopen
import time

from app.data_sources.base import DataSource, DataSourceError
from app.models.stock import Quote, KLine, FinancialData, CapitalFlow, NewsItem, ExtendedAnalysis


class SinaSource(DataSource):
    """新浪财经数据源（仅资金流）。

    通过新浪财经资金流 API 获取个股资金流历史数据，
    返回超大单/大单/中单/小单的流入流出净额。
    """

    name = "sina"

    # 新浪个股资金流历史 API（按日期倒序返回）
    FUND_FLOW_API = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                     "MoneyFlow.ssl_qsfx_lscjfb")

    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://finance.sina.com.cn/",
    }

    def _http_get_text(self, url: str, max_retries: int = 3) -> str:
        """HTTP GET 请求，内置重试。

        使用 urllib 而非 requests，避免第三方依赖，且不受 AKShare SSL 补丁影响。
        """
        last_exc = None
        for attempt in range(max_retries):
            try:
                req = Request(url)
                for key, value in self._HEADERS.items():
                    req.add_header(key, value)
                with urlopen(req, timeout=10) as resp:
                    raw = resp.read()
                    return raw.decode("utf-8", errors="replace")
            except Exception as e:
                last_exc = e
                if attempt < max_retries - 1:
                    time.sleep(0.5)
        raise DataSourceError(f"新浪财经 HTTP 请求失败: {last_exc}")

    # ===================== 数据接口实现 =====================

    def get_capital_flow(self, code: str, days: int = 10) -> list[CapitalFlow]:
        """获取资金流向数据。

        新浪资金流接口返回（按日期倒序）：
        [
          {"opendate": "2026-08-18", "trade": "当日收盘价", "changeratio": "涨跌幅",
           "netamount": "净流入总额", "ratioamount": "净流入占比",
           "r0": "超大单流入", "r1": "大单流入", "r2": "中单流入", "r3": "小单流入",
           "r0_net": "超大单净流入", "r1_net": "大单净流入",
           "r2_net": "中单净流入", "r3_net": "小单净流入"},
          ...
        ]
        金额字段单位均为元；主力 = 超大单(r0) + 大单(r1)；散户 = 中单(r2) + 小单(r3)。
        接口不提供成交额字段，故净流入占比（main_net_inflow_rate）置为 None。
        """
        try:
            sina_code = self._to_sina_code(code)
            # 多取一些再截断，避免 num 限制导致不足 days 条
            url = f"{self.FUND_FLOW_API}?page=1&num={max(days * 2, 60)}&sort=opendate&asc=0&daima={sina_code}"
            text = self._http_get_text(url).strip()
            if not text:
                return []
            items = json.loads(text)
            if not isinstance(items, list):
                return []

            flows = []
            for row in items:
                if not isinstance(row, dict):
                    continue
                r0 = self._safe_float(row.get("r0_net"))
                r1 = self._safe_float(row.get("r1_net"))
                r2 = self._safe_float(row.get("r2_net"))
                r3 = self._safe_float(row.get("r3_net"))
                flows.append(CapitalFlow(
                    trade_date=datetime.strptime(row["opendate"], "%Y-%m-%d").date(),
                    code=code,
                    main_net_inflow=r0 + r1,
                    main_net_inflow_rate=None,
                    retail_net_inflow=r2 + r3,
                    large_order_net_inflow=r1,
                    source=self.name,
                ))
            # 接口按日期倒序，转为升序后取最近 days 条
            flows.sort(key=lambda f: f.trade_date)
            return flows[-days:] if days > 0 else flows
        except DataSourceError:
            raise
        except Exception as e:
            raise DataSourceError(f"新浪财经获取资金流失败: {e}") from e

    # ---- 不支持的数据接口 ----

    def get_quote(self, code: str) -> Quote:
        raise DataSourceError("新浪数据源不支持行情接口")

    def get_kline(self, code: str, period: str = "daily",
                  days: int = 250) -> list[KLine]:
        raise DataSourceError("新浪数据源不支持K线接口")

    def get_financial(self, code: str) -> FinancialData:
        raise DataSourceError("新浪数据源不支持财务接口")

    def get_news(self, code: str, limit: int = 10) -> list[NewsItem]:
        raise DataSourceError("新浪数据源不支持新闻接口")

    def get_extended_analysis(self, code: str, quote: Quote,
                              capital_flows: list[CapitalFlow],
                              news_list: list[NewsItem]) -> ExtendedAnalysis:
        raise DataSourceError("新浪数据源不支持扩展分析")

    # ===================== 辅助方法 =====================

    def _to_sina_code(self, code: str) -> str:
        """转换为新浪代码格式（sh600519 / sz000001）。"""
        clean = self._clean_code(code)
        if clean.startswith("6") or clean.startswith("9"):
            return f"sh{clean}"
        return f"sz{clean}"

    def _clean_code(self, code: str) -> str:
        """清洗股票代码为 6 位数字。"""
        return code.strip().replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "").zfill(6)

    def _safe_float(self, value) -> float:
        """安全转换为 float，失败返回 0.0。"""
        if value is None or value == "" or value == "-":
            return 0.0
        try:
            v = float(value)
            if v != v:  # NaN
                return 0.0
            return v
        except (ValueError, TypeError):
            return 0.0
