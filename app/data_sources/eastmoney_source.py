"""东方财富直连数据源实现（兜底方案）。

通过东方财富公开 API 直连获取数据，不依赖 AKShare。
"""

import json
from datetime import datetime, date, timedelta
from typing import Optional

from app.data_sources.base import DataSource, DataSourceError
from app.models.stock import Quote, KLine, FinancialData, CapitalFlow, NewsItem, ExtendedAnalysis


class EastmoneySource(DataSource):
    """东方财富直连数据源。

    通过东方财富 HTTP API 直接获取 A 股数据，作为 AKShare 不可用时的兜底方案。
    当前实现：get_quote（必备）、get_kline（必备），其余返回空数据。
    """

    name = "eastmoney"

    # 东方财富 API 基础 URL
    QUOTE_API = "https://push2.eastmoney.com/api/qt/stock/get"
    QUOTE_BATCH_API = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    KLINE_API = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    # 统一的请求头
    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    def _request(self, url: str, params: dict) -> dict:
        """统一的 HTTP GET 请求，通过外部 curl 进程彻底隔离 akshare 的 SSL 污染。

        东方财富 API 在 akshare 导入后，Python 进程内的所有 TLS 连接都会受影响
        （包括 http.client 和 requests），唯独外部进程不受影响。使用 curl.exe
        发起请求，完全隔离。内置 5 次重试。
        """
        import subprocess
        import time
        from urllib.parse import urlencode

        # 构建完整 URL
        full_url = url
        if params:
            full_url += "?" + urlencode(params)

        # 构建 curl 请求头
        header_args = []
        for key, value in self._HEADERS.items():
            header_args.extend(["-H", f"{key}: {value}"])

        last_exc = None
        for attempt in range(5):
            try:
                result = subprocess.run(
                    ["curl.exe", "-s", "--max-time", "15", full_url] + header_args,
                    capture_output=True, timeout=20,
                )
                if result.returncode != 0:
                    stderr_text = result.stderr.decode("utf-8", errors="replace")[:200] if result.stderr else ""
                    raise RuntimeError(f"curl 退出码 {result.returncode}: {stderr_text}")
                stdout_text = result.stdout.decode("utf-8", errors="replace")
                return json.loads(stdout_text)
            except Exception as e:
                last_exc = e
                if attempt < 4:
                    time.sleep(0.3)
        raise last_exc  # type: ignore

    def get_quote(self, code: str) -> Quote:
        """获取实时行情（使用批量 API 获取单只股票，更稳定）。"""
        try:
            secid = self._get_secid(code)
            params = {
                "secids": secid,
                "fltt": "2",
                "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f12,f14,f15,f16,f17,f18,f20,f21,f23",
                "ut": "fa5fd1943c7b386f172d6893dbfd32bb",
            }
            data = self._request(self.QUOTE_BATCH_API, params)

            diff = (data.get("data") or {}).get("diff") or []
            if not diff:
                raise DataSourceError(f"东方财富未找到股票 {code} 的行情数据")
            d = diff[0]

            return Quote(
                code=self._clean_code(code),
                name=str(d.get("f14", "")),
                latest_price=float(d.get("f2", 0)),
                change_percent=float(d.get("f3", 0)),
                change_amount=float(d.get("f4", 0)),
                open_price=float(d.get("f17", 0)),
                high_price=float(d.get("f15", 0)),
                low_price=float(d.get("f16", 0)),
                pre_close=float(d.get("f18", 0)),
                volume=int(d.get("f5", 0)),
                amount=float(d.get("f6", 0)),
                turnover_rate=float(d.get("f8", 0)),
                amplitude=float(d.get("f7", 0)),
                pe_ttm=self._safe_float(d.get("f9")),
                pb=self._safe_float(d.get("f23")),
                market_cap=self._safe_float(d.get("f20")),
                circulating_market_cap=self._safe_float(d.get("f21")),
                source=self.name,
                fetched_at=datetime.now(),
            )
        except DataSourceError:
            raise
        except Exception as e:
            raise DataSourceError(f"东方财富获取行情失败: {e}") from e

    def get_kline(self, code: str, period: str = "daily",
                  days: int = 250) -> list[KLine]:
        """获取 K 线数据。"""
        try:
            secid = self._get_secid(code)
            period_map = {
                "daily": "101",
                "weekly": "102",
                "monthly": "103",
            }
            klt = period_map.get(period, "101")
            end_date = date.today().strftime("%Y%m%d")
            start_date = (date.today() - timedelta(days=days)).strftime("%Y%m%d")

            params = {
                "secid": secid,
                "ut": "fa5fd1943c7b386f172d6893dbfd32bb",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": klt,
                "fqt": "1",  # 前复权
                "beg": start_date,
                "end": end_date,
            }
            data = self._request(self.KLINE_API, params)

            if data.get("data") is None or data["data"].get("klines") is None:
                return []

            klines = []
            for item in data["data"]["klines"]:
                parts = item.split(",")
                if len(parts) < 6:
                    continue
                klines.append(KLine(
                    trade_date=datetime.strptime(parts[0], "%Y-%m-%d").date(),
                    open_price=float(parts[1]),
                    close=float(parts[2]),
                    high=float(parts[3]),
                    low=float(parts[4]),
                    volume=int(float(parts[5])),
                    amount=float(parts[6]) if len(parts) > 6 else 0.0,
                ))
            return klines
        except Exception as e:
            raise DataSourceError(f"东方财富获取 K 线失败: {e}") from e

    def get_financial(self, code: str) -> FinancialData:
        """获取财务数据（兜底返回空对象）。"""
        q = self.get_quote(code)
        return FinancialData(
            code=code,
            name=q.name,
            pe_ttm=q.pe_ttm,
            pb=q.pb,
            source=self.name,
            fetched_at=datetime.now(),
        )

    def get_capital_flow(self, code: str, days: int = 10) -> list[CapitalFlow]:
        """获取资金流向（兜底返回空列表）。"""
        return []

    def get_news(self, code: str, limit: int = 10) -> list[NewsItem]:
        """获取新闻（兜底返回空列表）。"""
        return []

    def get_extended_analysis(self, code: str, quote: Quote,
                              capital_flows: list[CapitalFlow],
                              news_list: list[NewsItem]) -> ExtendedAnalysis:
        """获取扩展分析数据（兜底：仅基于可用数据做基础判断）。

        东方财富直连不支持基金持仓、社保、股东户数等定期报告数据。
        仅基于行情和资金流数据做活跃度和风险评估。
        """
        result = ExtendedAnalysis(source=self.name, fetched_at=datetime.now())

        # 1. 资金流方向
        if capital_flows:
            recent = capital_flows[-5:] if len(capital_flows) >= 5 else capital_flows
            total = sum((cf.main_net_inflow or 0) for cf in recent)
            if total > 1e7:
                result.capital_flow_direction = "inflow"
                result.capital_flow_summary = f"近{len(recent)}日主力净流入{total/1e8:.2f}亿"
            elif total < -1e7:
                result.capital_flow_direction = "outflow"
                result.capital_flow_summary = f"近{len(recent)}日主力净流出{abs(total)/1e8:.2f}亿"
            else:
                result.capital_flow_direction = "balanced"
                result.capital_flow_summary = f"近{len(recent)}日资金流向平衡"

        # 2-3. 基金和社保：兜底返回 unknown
        result.fund_capital_direction = "unknown"
        result.fund_analysis_summary = "当前数据源不支持基金持仓数据"
        result.social_security_direction = "unknown"
        result.social_security_analysis_summary = "当前数据源不支持社保基金持仓数据"

        # 4-5. 股东户数：兜底返回 unknown
        result.shareholder_count_change = "unknown"
        result.shareholder_analysis_summary = "当前数据源不支持股东户数数据"
        result.avg_share_holding_change = "unknown"

        # 6. 活跃股判断
        score = 0.0
        reasons = []
        turnover = quote.turnover_rate or 0
        if turnover > 10:
            score += 40
            reasons.append(f"换手率{turnover:.1f}%极高")
        elif turnover > 5:
            score += 30
            reasons.append(f"换手率{turnover:.1f}%较高")
        elif turnover > 3:
            score += 20
            reasons.append(f"换手率{turnover:.1f}%略高")
        elif turnover > 1:
            score += 10

        amplitude = quote.amplitude or 0
        if amplitude > 10:
            score += 30
        elif amplitude > 5:
            score += 20
        elif amplitude > 3:
            score += 10

        result.active_stock_score = round(score, 1)
        result.is_active_stock = score >= 40
        result.active_stock_reason = "；".join(reasons) if reasons else "交易指标正常"

        # 7. 热点题材：兜底
        result.hot_themes = []
        result.hot_theme_detail = "当前数据源不支持概念板块匹配"

        # 8. 潜在风险
        risks = []
        pe = quote.pe_ttm
        if pe is not None and pe < 0:
            risks.append("公司亏损")
        elif pe is not None and pe > 100:
            risks.append(f"市盈率{pe:.1f}倍偏高")
        if turnover > 15:
            risks.append("换手率过高")
        result.potential_risks = risks
        result.risk_level = "中" if risks else "低"
        result.risk_analysis_summary = f"发现{len(risks)}项风险" if risks else "未发现显著风险"

        # 9. 投资价值（兜底基础评估）
        score_val = 50.0
        if pe is not None and 10 < pe < 30:
            score_val += 10
        if result.capital_flow_direction == "inflow":
            score_val += 10
        score_val -= len(risks) * 5
        score_val = max(0, min(100, score_val))
        result.investment_value_score = round(score_val, 1)
        if score_val >= 60:
            result.investment_value = "具有一定投资价值"
        elif score_val >= 40:
            result.investment_value = "投资价值一般"
        else:
            result.investment_value = "投资价值偏低"
        result.investment_value_reason = "基于有限数据的初步评估"

        return result

    # ---- helper methods ----

    def _get_secid(self, code: str) -> str:
        """获取东方财富 secid 格式。"""
        clean = self._clean_code(code)
        if clean.startswith("6") or clean.startswith("9"):
            return f"1.{clean}"
        return f"0.{clean}"

    def _clean_code(self, code: str) -> str:
        """清洗股票代码。"""
        return code.strip().replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "").zfill(6)

    def _safe_float(self, value) -> Optional[float]:
        """安全转换为 float。"""
        if value is None:
            return None
        try:
            v = float(value)
            return None if v != v else v  # NaN 检查
        except (ValueError, TypeError):
            return None
