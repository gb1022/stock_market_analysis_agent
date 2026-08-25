"""腾讯财经数据源实现（第三级兜底方案）。

通过腾讯财经公开 HTTP API 获取 A 股数据，使用纯 HTTP 协议，
不依赖 SSL，避免 AKShare 的 SSL 污染问题。
"""

import json
from datetime import datetime
from typing import Optional
from urllib.request import Request, urlopen
import time

from app.data_sources.base import DataSource, DataSourceError
from app.models.stock import Quote, KLine, FinancialData, CapitalFlow, NewsItem, ExtendedAnalysis


class TencentSource(DataSource):
    """腾讯财经数据源。

    通过腾讯财经 HTTP API 获取 A 股数据，作为 AKShare 和东方财富都不可用时的兜底方案。
    使用纯 HTTP 协议，不依赖 SSL/TLS，不受 AKShare SSL 猴子补丁影响。

    实现的接口：get_quote（行情）、get_kline（K 线）、get_financial（从行情推断）
    """

    name = "tencent"

    # 腾讯财经 API
    QUOTE_API = "http://qt.gtimg.cn/q="
    KLINE_API = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://gu.qq.com/",
    }

    def _http_get(self, url: str, max_retries: int = 3) -> str:
        """通过 urllib 发起 HTTP GET 请求，内置重试。

        使用 urllib 而非 requests，避免依赖第三方库且不受 SSL 污染影响。
        腾讯财经 API 使用纯 HTTP，无需 TLS。
        """
        last_exc = None
        for attempt in range(max_retries):
            try:
                req = Request(url)
                for key, value in self._HEADERS.items():
                    req.add_header(key, value)
                with urlopen(req, timeout=10) as resp:
                    # 腾讯财经返回的是 GBK/GB2312 编码
                    raw = resp.read()
                    # 尝试 UTF-8，失败则用 GBK
                    try:
                        return raw.decode("utf-8")
                    except UnicodeDecodeError:
                        return raw.decode("gbk", errors="replace")
            except Exception as e:
                last_exc = e
                if attempt < max_retries - 1:
                    time.sleep(0.5)
        raise DataSourceError(f"腾讯财经 HTTP 请求失败: {last_exc}")

    def _http_get_json(self, url: str, max_retries: int = 3) -> dict:
        """HTTP GET 并解析 JSON 响应。"""
        text = self._http_get(url, max_retries)
        return json.loads(text)

    # ===================== 数据接口实现 =====================

    def get_quote(self, code: str) -> Quote:
        """获取实时行情。

        腾讯财经行情 API 返回格式（~ 分隔）：
        v_<code>="<市场>~<名称>~<代码>~<当前价>~<昨收>~<今开>~<成交量>~..."
        字段索引（0-based）：
          1:名称  3:当前价  4:昨收  5:今开  6:成交量(手)
          32:涨跌额  33:涨跌幅  34:最高  35:最低
          37:成交额(万)  38:换手率  39:市盈率  43:振幅
          44:流通市值  45:总市值  46:市净率
        """
        try:
            tencent_code = self._to_tencent_code(code)
            url = f"{self.QUOTE_API}{tencent_code}"
            resp_text = self._http_get(url)

            # 解析 var 格式: v_sz000001="...~...~..."
            for line in resp_text.split("\n"):
                line = line.strip()
                if not line or "=" not in line:
                    continue
                # 提取引号内的数据
                idx = line.find('"')
                if idx == -1:
                    continue
                data_str = line[idx + 1 : line.rfind('"')]
                if not data_str:
                    continue
                fields = data_str.split("~")
                if len(fields) < 40:
                    continue

                name = fields[1] if fields[1] else ""
                if not name:
                    continue

                return Quote(
                    code=self._clean_code(code),
                    name=name,
                    latest_price=self._safe_float(fields[3]),
                    change_percent=self._safe_float(fields[32]),
                    change_amount=self._safe_float(fields[31]),
                    open_price=self._safe_float(fields[5]),
                    high_price=self._safe_float(fields[33]),
                    low_price=self._safe_float(fields[34]),
                    pre_close=self._safe_float(fields[4]),
                    volume=int(self._safe_float(fields[6]) or 0),
                    amount=self._safe_float(fields[37], 10000),  # 腾讯返回万元，转为元
                    turnover_rate=self._safe_float(fields[38]),
                    amplitude=self._safe_float(fields[43]),
                    pe_ttm=self._safe_float(fields[39], nullable=True),
                    pb=self._safe_float(fields[46], nullable=True),
                    market_cap=self._safe_float(fields[45], nullable=True),
                    circulating_market_cap=self._safe_float(fields[44], nullable=True),
                    source=self.name,
                    fetched_at=datetime.now(),
                )

            raise DataSourceError(f"腾讯财经未找到股票 {code} 的行情数据")

        except DataSourceError:
            raise
        except Exception as e:
            raise DataSourceError(f"腾讯财经获取行情失败: {e}") from e

    def get_kline(self, code: str, period: str = "daily",
                  days: int = 250) -> list[KLine]:
        """获取 K 线数据。

        腾讯财经 K 线 API 返回 JSON：
        {"code":0, "data":{"sz000001":{"qfqday":[[日期,开,收,高,低,量],...]}}}
        """
        try:
            tencent_code = self._to_tencent_code(code)
            period_param = "day"
            if period == "weekly":
                period_param = "week"
            elif period == "monthly":
                period_param = "month"

            url = f"{self.KLINE_API}?param={tencent_code},{period_param},,,{days},qfq"
            data = self._http_get_json(url)

            if data.get("code") != 0:
                raise DataSourceError(f"腾讯财经 K 线接口返回错误: {data.get('msg', '未知错误')}")

            stock_data = data.get("data", {}).get(tencent_code, {})
            kline_key = f"qfq{period_param}"
            raw_klines = stock_data.get(kline_key, [])

            if not raw_klines:
                return []

            klines = []
            for item in raw_klines:
                if len(item) < 6:
                    continue
                try:
                    klines.append(KLine(
                        trade_date=datetime.strptime(item[0], "%Y-%m-%d").date(),
                        open_price=float(item[1]),
                        close=float(item[2]),
                        high=float(item[3]),
                        low=float(item[4]),
                        volume=int(float(item[5])),
                        amount=0.0,  # 腾讯 K 线不返回成交额
                    ))
                except (ValueError, TypeError):
                    continue
            return klines

        except DataSourceError:
            raise
        except Exception as e:
            raise DataSourceError(f"腾讯财经获取 K 线失败: {e}") from e

    def get_financial(self, code: str) -> FinancialData:
        """获取财务数据（从行情数据中提取 PE/PB）。

        腾讯财经不提供详细的财务报表接口，仅返回行情中携带的 PE 和 PB。
        """
        try:
            q = self.get_quote(code)
            return FinancialData(
                code=code,
                name=q.name,
                pe_ttm=q.pe_ttm,
                pb=q.pb,
                source=self.name,
                fetched_at=datetime.now(),
            )
        except Exception as e:
            raise DataSourceError(f"腾讯财经获取财务数据失败: {e}") from e

    def get_capital_flow(self, code: str, days: int = 10) -> list[CapitalFlow]:
        """获取资金流向（腾讯财经不提供此接口，返回空列表）。"""
        return []

    def get_news(self, code: str, limit: int = 10) -> list[NewsItem]:
        """获取新闻/公告（腾讯财经不提供此接口，返回空列表）。"""
        return []

    def get_extended_analysis(self, code: str, quote: Quote,
                              capital_flows: list[CapitalFlow],
                              news_list: list[NewsItem]) -> ExtendedAnalysis:
        """获取扩展分析数据（兜底：仅基于行情和资金流做基础判断）。

        腾讯财经不支持基金持仓、社保、股东户数等定期报告数据和概念板块。
        """
        result = ExtendedAnalysis(source=self.name, fetched_at=datetime.now())

        # 资金流方向
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

        # 基金和社保：不支持
        result.fund_capital_direction = "unknown"
        result.fund_analysis_summary = "当前数据源不支持基金持仓数据"
        result.social_security_direction = "unknown"
        result.social_security_analysis_summary = "当前数据源不支持社保基金持仓数据"

        # 股东户数：不支持
        result.shareholder_count_change = "unknown"
        result.shareholder_analysis_summary = "当前数据源不支持股东户数数据"
        result.avg_share_holding_change = "unknown"

        # 活跃股判断
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

        # 热点题材、风险、投资价值 - 兜底
        result.hot_themes = []
        result.hot_theme_detail = "当前数据源不支持概念板块匹配"
        risks = []
        pe = quote.pe_ttm
        if pe is not None and pe < 0:
            risks.append("公司亏损")
        elif pe is not None and pe > 100:
            risks.append(f"市盈率{pe:.1f}倍偏高")
        result.potential_risks = risks
        result.risk_level = "中" if risks else "低"
        result.risk_analysis_summary = f"发现{len(risks)}项风险" if risks else "未发现显著风险"

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

    # ===================== 辅助方法 =====================

    def _to_tencent_code(self, code: str) -> str:
        """转换为腾讯财经代码格式（sz000001 / sh600519）。"""
        clean = self._clean_code(code)
        if clean.startswith("6") or clean.startswith("9"):
            return f"sh{clean}"
        return f"sz{clean}"

    def _clean_code(self, code: str) -> str:
        """清洗股票代码为 6 位数字。"""
        return code.strip().replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "").zfill(6)

    def _safe_float(self, value, multiplier: float = 1.0, nullable: bool = False) -> Optional[float]:
        """安全转换为 float，支持乘数转换（如万元→元）。"""
        if value is None or value == "" or value == "-":
            return None if nullable else 0.0
        try:
            v = float(value)
            if v != v:  # NaN
                return None if nullable else 0.0
            return v * multiplier
        except (ValueError, TypeError):
            return None if nullable else 0.0
