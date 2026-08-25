"""AKShare 数据源实现。"""

import logging
import sys
import time
from datetime import datetime, date
from typing import Optional

import akshare as ak
import pandas as pd
import requests

from app.data_sources.base import DataSource, DataSourceError
from app.models.stock import Quote, KLine, FinancialData, CapitalFlow, NewsItem, ExtendedAnalysis

logger = logging.getLogger("stock_agent")


class AKShareSource(DataSource):
    """AKShare 数据源实现。

    使用 AKShare 库获取 A 股市场数据。
    """

    name = "akshare"
    
    _spot_cache = None
    _spot_cache_time = None
    _SPOT_CACHE_TTL = 300

    # 全市场最新股东户数缓存（方案 A），TTL 1 小时
    _gdhs_latest_cache = None
    _gdhs_latest_cache_time = None
    _GDHS_CACHE_TTL = 3600

    def _get_spot_data(self) -> pd.DataFrame:
        """获取全市场实时行情数据（带内存缓存）。
        
        在同一次请求中多次调用时，避免重复拉取全市场数据。
        """
        now = datetime.now()
        if (self._spot_cache is not None and 
            self._spot_cache_time is not None and
            (now - self._spot_cache_time).seconds < self._SPOT_CACHE_TTL):
            return self._spot_cache
        
        self._spot_cache = ak.stock_zh_a_spot_em()
        self._spot_cache_time = now
        return self._spot_cache

    def get_quote(self, code: str) -> Quote:
        """获取实时行情。"""
        try:
            # 处理股票代码格式：补齐 6 位
            code_fmt = self._format_code(code)
            df = self._get_spot_data()
            row = df[df["代码"] == code_fmt]
            if row.empty:
                raise DataSourceError(f"AKShare 未找到股票 {code} 的行情数据")

            row = row.iloc[0]
            return Quote(
                code=code,
                name=str(row.get("名称", "")),
                latest_price=float(row.get("最新价", 0)),
                change_percent=float(row.get("涨跌幅", 0)),
                change_amount=float(row.get("涨跌额", 0)),
                open_price=float(row.get("今开", 0)),
                high_price=float(row.get("最高", 0)),
                low_price=float(row.get("最低", 0)),
                pre_close=float(row.get("昨收", 0)),
                volume=int(row.get("成交量", 0)),
                amount=float(row.get("成交额", 0)),
                turnover_rate=float(row.get("换手率", 0)),
                amplitude=float(row.get("振幅", 0)),
                pe_ttm=self._safe_float(row.get("市盈率-动态")),
                pb=self._safe_float(row.get("市净率")),
                market_cap=self._safe_float(row.get("总市值")),
                circulating_market_cap=self._safe_float(row.get("流通市值")),
                source=self.name,
                fetched_at=datetime.now(),
            )
        except DataSourceError:
            raise
        except Exception as e:
            raise DataSourceError(f"AKShare 获取行情失败: {e}") from e

    def get_kline(self, code: str, period: str = "daily",
                  days: int = 250) -> list[KLine]:
        """获取 K 线数据。"""
        try:
            code_fmt = self._format_code(code)
            # AKShare 股票代码格式：sh600519 / sz000001
            akshare_code = self._to_akshare_code(code_fmt)
            # period 映射
            period_map = {
                "daily": "daily",
                "weekly": "weekly",
                "monthly": "monthly",
            }
            adj = period_map.get(period, "daily")
            df = ak.stock_zh_a_hist(
                symbol=akshare_code,
                period=adj,
                start_date=self._calc_start_date(days),
                adjust="qfq",  # 前复权
            )
            if df is None or df.empty:
                return []

            klines = []
            for _, row in df.iterrows():
                klines.append(KLine(
                    trade_date=pd.to_datetime(row["日期"]).date(),
                    open_price=float(row["开盘"]),
                    high=float(row["最高"]),
                    low=float(row["最低"]),
                    close=float(row["收盘"]),
                    volume=int(row["成交量"]),
                    amount=float(row["成交额"]),
                ))
            return klines
        except Exception as e:
            raise DataSourceError(f"AKShare 获取 K 线失败: {e}") from e

    def get_financial(self, code: str) -> FinancialData:
        """获取财务数据。"""
        try:
            code_fmt = self._format_code(code)
            akshare_code = self._to_akshare_code(code_fmt)

            fin = FinancialData(code=code, source=self.name, fetched_at=datetime.now())

            # 获取实时财务指标（复用内存缓存的全市场数据）
            try:
                df_indicator = self._get_spot_data()
                row = df_indicator[df_indicator["代码"] == code_fmt]
                if not row.empty:
                    row = row.iloc[0]
                    fin.pe_ttm = self._safe_float(row.get("市盈率-动态"))
                    fin.pb = self._safe_float(row.get("市净率"))
                    fin.name = str(row.get("名称", ""))
            except Exception:
                pass

            return fin
        except Exception as e:
            raise DataSourceError(f"AKShare 获取财务数据失败: {e}") from e

    def get_capital_flow(self, code: str, days: int = 10) -> list[CapitalFlow]:
        """获取资金流向数据。"""
        try:
            code_fmt = self._format_code(code)
            akshare_code = self._to_akshare_code(code_fmt)

            df = ak.stock_individual_fund_flow(
                stock=akshare_code,
                market="sh" if akshare_code.startswith("sh") else "sz",
            )
            if df is None or df.empty:
                return []

            flows = []
            for _, row in df.iterrows():
                flows.append(CapitalFlow(
                    date=pd.to_datetime(row["日期"]).date(),
                    code=code,
                    main_net_inflow=self._safe_float(row.get("主力净流入-净额")),
                    main_net_inflow_rate=self._safe_float(row.get("主力净流入-净占比")),
                    retail_net_inflow=self._safe_float(row.get("散户净流入-净额")),
                    retail_net_inflow_rate=self._safe_float(row.get("散户净流入-净占比")),
                    source=self.name,
                ))
            return flows[-days:] if len(flows) > days else flows
        except Exception as e:
            raise DataSourceError(f"AKShare 获取资金流失败: {e}") from e

    def get_news(self, code: str, limit: int = 10) -> list[NewsItem]:
        """获取新闻/公告。"""
        try:
            code_fmt = self._format_code(code)
            df = ak.stock_news_notice(symbol=code_fmt)

            if df is None or df.empty:
                return []

            news_list = []
            for _, row in df.iterrows():
                news_list.append(NewsItem(
                    title=str(row.get("标题", "")),
                    date=pd.to_datetime(row.get("发布时间")) if pd.notna(row.get("发布时间")) else None,
                    summary=str(row.get("摘要", "")),
                    source=str(row.get("文章来源", "")),
                    url=str(row.get("链接", "")),
                ))
            return news_list[:limit]
        except Exception as e:
            raise DataSourceError(f"AKShare 获取新闻失败: {e}") from e

    def get_extended_analysis(self, code: str, quote: Quote,
                              capital_flows: list[CapitalFlow],
                              news_list: list[NewsItem]) -> ExtendedAnalysis:
        """获取扩展分析数据。

        包含9个维度的分析：
        1. 资金整体流向判断
        2. 基金资金流向（来自定期报告持仓变化）
        3. 社保基金流向
        4. 股东户数变化
        5. 股东平均持股变化
        6. 活跃股判断（多指标综合）
        7. 热点题材（概念板块匹配）
        8. 潜在风险识别
        9. 投资价值评估
        """
        code_fmt = self._format_code(code)
        result = ExtendedAnalysis(source=self.name, fetched_at=datetime.now())

        # 1. 资金整体流向判断（基于已有的资金流数据）
        self._analyze_capital_flow_direction(result, capital_flows)

        # 2. 基金资金流向（从定期报告持仓数据）
        self._analyze_fund_holdings(result, code_fmt)

        # 3. 社保基金流向
        self._analyze_social_security(result, code_fmt)

        # 4. 股东户数变化
        self._analyze_shareholder_count(result, code_fmt)

        # 5. 股东平均持股变化（基于总股本和股东户数）
        self._analyze_avg_share_holding(result, code_fmt)

        # 6. 活跃股判断（多指标综合）
        self._analyze_active_stock(result, quote, capital_flows)

        # 7. 热点题材（概念板块匹配）
        self._analyze_hot_themes(result, code_fmt)

        # 8. 潜在风险识别
        self._analyze_risks(result, quote)

        # 9. 投资价值综合评估
        self._analyze_investment_value(result, quote, capital_flows)

        return result

    # ---- 扩展分析辅助方法 ----

    def _analyze_capital_flow_direction(self, result: ExtendedAnalysis,
                                        capital_flows: list[CapitalFlow]) -> None:
        """分析资金整体流向。"""
        if not capital_flows:
            result.capital_flow_direction = "unknown"
            result.capital_flow_summary = "无资金流数据"
            return

        total_main_inflow = sum(
            (cf.main_net_inflow or 0) for cf in capital_flows
        )
        recent_main_inflow = sum(
            (cf.main_net_inflow or 0) for cf in capital_flows[-5:]
        ) if len(capital_flows) >= 5 else total_main_inflow

        if recent_main_inflow > 1e7:
            result.capital_flow_direction = "inflow"
            result.capital_flow_summary = f"近{min(5, len(capital_flows))}日主力净流入{recent_main_inflow/1e8:.2f}亿，资金呈流入态势"
        elif recent_main_inflow < -1e7:
            result.capital_flow_direction = "outflow"
            result.capital_flow_summary = f"近{min(5, len(capital_flows))}日主力净流出{abs(recent_main_inflow)/1e8:.2f}亿，资金呈流出态势"
        else:
            result.capital_flow_direction = "balanced"
            result.capital_flow_summary = f"近{min(5, len(capital_flows))}日主力净流入{recent_main_inflow/1e8:.2f}亿，资金流向平衡"

    def _analyze_fund_holdings(self, result: ExtendedAnalysis, code_fmt: str) -> None:
        """分析基金持仓变化（从定期报告数据）。

        stock_report_fund_hold(symbol="基金持仓", date="YYYYMMDD") 返回全市场
        基金持仓汇总数据，列名：序号/股票代码/股票简称/持有基金家数/持股总数/
        持股市值/持股变化/持股变动数值/持股变动比例。
        需要按股票代码筛选目标股票。
        """
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                # symbol 是分类名而非股票代码，date 格式为 YYYYMMDD
                df = ak.stock_report_fund_hold(symbol="基金持仓", date="20250331")
            if df is None or df.empty:
                result.fund_capital_direction = "unknown"
                result.fund_analysis_summary = "暂无基金持仓定期报告数据"
                return

            # 按股票代码筛选目标股票
            pure_code = code_fmt.strip().lower().replace("sh", "").replace("sz", "")
            stock_rows = df[df["股票代码"].astype(str) == pure_code]
            if stock_rows.empty:
                result.fund_capital_direction = "unknown"
                result.fund_analysis_summary = "暂无基金持仓定期报告数据"
                return

            row = stock_rows.iloc[0]
            fund_count = int(row["持有基金家数"]) if row["持有基金家数"] else None
            # 持股变动比例是百分比字符串（如 "-3.65"），直接转浮点
            change_pct = self._safe_float(row.get("持股变动比例"))

            result.fund_holding_count = fund_count
            result.fund_holding_change = round(change_pct, 2) if change_pct is not None else None

            direction = "unchanged"
            if change_pct is not None and change_pct > 0.5:
                direction = "inflow"
            elif change_pct is not None and change_pct < -0.5:
                direction = "outflow"
            result.fund_capital_direction = direction

            change_desc = f"持股变动比例{change_pct:.2f}%" if change_pct is not None else "持股变动比例未知"
            result.fund_analysis_summary = (
                f"最新报告期基金持仓：共{fund_count or '?'}只基金持仓，"
                f"{change_desc}"
            )
            return

        except Exception as e:
            logger.warning(f"[AKShare] 基金持仓分析失败: {e}")
            result.fund_capital_direction = "unknown"
            result.fund_analysis_summary = "暂无基金持仓定期报告数据"
            return

    def _analyze_social_security(self, result: ExtendedAnalysis, code_fmt: str) -> None:
        """分析社保基金持仓（从十大股东中识别）。

        stock_main_stock_holder 返回股东明细，列名：
        编号/股东名称/持股数量/持股比例/股本性质/截至日期/公告日期/股东说明/股东总数/平均持股数。
        筛选股东名称含"社保"的记录即为社保基金持仓。
        """
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                # stock_main_stock_holder 需要纯数字代码
                pure_code = code_fmt.strip().lower().replace("sh", "").replace("sz", "")
                df = ak.stock_main_stock_holder(stock=pure_code)
            if df is None or df.empty:
                result.social_security_direction = "unknown"
                result.social_security_analysis_summary = "暂无社保基金持仓数据"
                return

            # 筛选社保基金相关股东
            holder_col = "股东名称"
            if holder_col not in df.columns:
                result.social_security_direction = "unknown"
                result.social_security_analysis_summary = "暂无社保基金持仓数据"
                return

            social_rows = df[
                df[holder_col].astype(str).str.contains("社保|全国社保|社会保险", na=False)
            ]
            if social_rows.empty:
                result.social_security_direction = "unchanged"
                result.social_security_analysis_summary = "最新报告期未发现社保基金持仓"
                return

            # 计算社保基金总持股比例
            pct_col = "持股比例" if "持股比例" in df.columns else None

            total_pct = 0.0
            sec_names = []
            for _, row in social_rows.iterrows():
                name = row.get(holder_col, "")
                pct = self._safe_float(row.get(pct_col, 0)) if pct_col else 0
                total_pct += pct or 0
                sec_names.append(str(name))

            result.social_security_holding = round(total_pct, 2)
            direction = "inflow" if total_pct > 0 else "unchanged"
            result.social_security_direction = direction
            result.social_security_analysis_summary = (
                f"社保基金持股比例约{total_pct:.2f}%，"
                f"涉及：{', '.join(sec_names[:3])}"
            )
            return

        except Exception as e:
            logger.warning(f"[AKShare] 社保基金分析失败: {e}")
            result.social_security_direction = "unknown"
            result.social_security_analysis_summary = "暂无社保基金持仓数据"
            return

    def _analyze_shareholder_count(self, result: ExtendedAnalysis,
                                   code_fmt: str) -> None:
        """分析股东户数变化。

        股东户数数据走双通道：
        - 方案 B（单股分析）：东财 RPT_HOLDERNUMLATEST 按 SECURITY_CODE 直查，1 次请求
        - 方案 A（批量分析）：拉全市场最新股东户数（symbol='最新'）并缓存，按代码过滤
        批量判定：60 秒窗口内 >= 3 只不同股票视为批量。
        """
        try:
            data = self._query_shareholder_two_periods(code_fmt)
        except Exception as e:
            logger.debug("[AKShare] 股东户数查询失败(%s): %s", code_fmt, e)
            data = None

        if not data:
            result.shareholder_count_change = "unknown"
            result.shareholder_analysis_summary = "暂无股东户数数据"
            return

        latest_count, prev_count, latest_date, _prev_date = data
        result.shareholder_count_latest = latest_count

        if prev_count is not None and prev_count > 0:
            change_rate = (latest_count - prev_count) / prev_count * 100
            result.shareholder_count_previous = prev_count
            result.shareholder_change_rate = round(change_rate, 2)

            if change_rate > 5:
                result.shareholder_count_change = "increase"
                result.shareholder_analysis_summary = (
                    f"股东户数从{prev_count:,}户增至{latest_count:,}户（{change_rate:+.1f}%），"
                    f"筹码趋于分散（截止{latest_date}）"
                )
            elif change_rate < -5:
                result.shareholder_count_change = "decrease"
                result.shareholder_analysis_summary = (
                    f"股东户数从{prev_count:,}户减少至{latest_count:,}户（{change_rate:+.1f}%），"
                    f"筹码趋于集中（截止{latest_date}）"
                )
            else:
                result.shareholder_count_change = "unchanged"
                result.shareholder_analysis_summary = (
                    f"股东户数{latest_count:,}户，环比变化不大（{change_rate:+.1f}%）（截止{latest_date}）"
                )
        else:
            # 仅有一期数据，无法对比
            result.shareholder_count_change = "unknown"
            result.shareholder_analysis_summary = (
                f"最新股东户数{latest_count:,}户"
                f"（截止{latest_date}，仅一期数据无法对比变化）"
            )

    def _query_shareholder_two_periods(self, code_fmt: str) -> Optional[tuple]:
        """获取股东户数最近两期数据。

        按场景选择通道：
        - 批量分析（60 秒内 >= 3 只不同股票）：方案 A 全市场缓存过滤
        - 单股分析：方案 B 直查

        Returns:
            (最新户数, 上期户数, 最新截止日期, 上期截止日期)，上期户数可为 None
        """
        if self._use_batch_shareholder_mode(code_fmt):
            return self._shareholder_from_latest_all(code_fmt)
        return self._shareholder_from_single_query(code_fmt)

    def _use_batch_shareholder_mode(self, code: str) -> bool:
        """判定是否处于批量分析模式。

        维护 60 秒滑动窗口，窗口内不同股票数 >= 3 判定为批量；
        单只股票重复请求不计入不同股票数。
        """
        now = time.time()
        window = getattr(self, "_shareholder_window", [])
        window = [(t, c) for t, c in window if now - t < 60]
        window.append((now, code))
        self._shareholder_window = window
        return len({c for _, c in window}) >= 3

    def _shareholder_from_single_query(self, code_fmt: str) -> Optional[tuple]:
        """方案 B：东财 RPT_HOLDERNUMLATEST 按 SECURITY_CODE 单股直查。

        该报表每只股票返回最新一期股东户数（含上期户数 PRE_HOLDER_NUM），
        一次请求即可完成两期对比。
        """
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "sortColumns": "END_DATE",
            "sortTypes": "-1",
            "pageSize": "50",
            "pageNumber": "1",
            "reportName": "RPT_HOLDERNUMLATEST",
            "columns": "SECURITY_CODE,END_DATE,HOLDER_NUM,PRE_HOLDER_NUM",
            "source": "WEB",
            "client": "WEB",
            "filter": f'(SECURITY_CODE="{code_fmt}")',
        }
        resp = requests.get(url, params=params, timeout=15)
        payload = resp.json()
        rows = (payload.get("result") or {}).get("data") or []
        if not rows:
            return None
        latest = rows[0]
        latest_count = self._safe_int(latest.get("HOLDER_NUM"))
        prev_count = self._safe_int(latest.get("PRE_HOLDER_NUM"))
        return (latest_count, prev_count, latest.get("END_DATE"), None)

    def _shareholder_from_latest_all(self, code_fmt: str) -> Optional[tuple]:
        """方案 A：从全市场最新股东户数缓存中按代码过滤。"""
        df = self._get_gdhs_latest_all()
        if df is None or df.empty:
            return None
        row_df = df[df["代码"] == code_fmt]
        if row_df.empty:
            return None
        row = row_df.iloc[0]
        latest_count = self._safe_int(row.get("股东户数-本次"))
        prev_count = self._safe_int(row.get("股东户数-上次"))
        latest_date = row.get("股东户数统计截止日-本次")
        prev_date = row.get("股东户数统计截止日-上次")
        return (latest_count, prev_count, latest_date, prev_date)

    def _get_gdhs_latest_all(self) -> Optional[pd.DataFrame]:
        """拉取全市场最新股东户数（方案 A，带 1 小时缓存）。

        抑制 stock_zh_a_gdhs 内部的 tqdm 进度条输出，避免污染日志。
        """
        now = time.time()
        if (self._gdhs_latest_cache is not None and
                self._gdhs_latest_cache_time is not None and
                now - self._gdhs_latest_cache_time < self._GDHS_CACHE_TTL):
            return self._gdhs_latest_cache

        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()  # 抑制 akshare 内部 tqdm 进度条
        try:
            df = ak.stock_zh_a_gdhs(symbol="最新")
        except Exception as e:
            logger.debug("[AKShare] 全市场股东户数拉取失败: %s", e)
            df = None
        finally:
            sys.stdout = old_stdout

        self._gdhs_latest_cache = df
        self._gdhs_latest_cache_time = now
        return df

    @staticmethod
    def _safe_int(value) -> Optional[int]:
        """安全转 int（NaN/None 返回 None）。"""
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _analyze_avg_share_holding(self, result: ExtendedAnalysis,
                                   code_fmt: str) -> None:
        """分析股东平均持股数量变化。

        优先使用 stock_main_stock_holder 返回的实际平均持股数据，
        若失败则基于股东户数变化率推算。
        """
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                pure_code = code_fmt.strip().lower().replace("sh", "").replace("sz", "")
                df = ak.stock_main_stock_holder(stock=pure_code)
            if df is not None and not df.empty and "平均持股数" in df.columns:
                # 取最新一期（第一行）的平均持股数
                avg_shares = self._safe_float(df.iloc[0].get("平均持股数"))
                if avg_shares and avg_shares > 0:
                    result.avg_share_holding_latest = round(avg_shares, 2)
                    # 基于股东户数变化率推算平均持股变化
                    if result.shareholder_count_change == "decrease":
                        result.avg_share_holding_change = "increase"
                        if result.shareholder_change_rate:
                            result.avg_share_holding_change_rate = round(abs(result.shareholder_change_rate), 2)
                    elif result.shareholder_count_change == "increase":
                        result.avg_share_holding_change = "decrease"
                        if result.shareholder_change_rate:
                            result.avg_share_holding_change_rate = round(-result.shareholder_change_rate, 2)
                    else:
                        result.avg_share_holding_change = "unchanged"
                    return
        except Exception as e:
            logger.debug(f"[AKShare] 平均持股数据获取失败: {e}")

        # 降级：仅基于股东户数变化率推算
        if result.shareholder_count_change == "decrease":
            result.avg_share_holding_change = "increase"
            if result.shareholder_change_rate:
                result.avg_share_holding_change_rate = round(abs(result.shareholder_change_rate), 2)
        elif result.shareholder_count_change == "increase":
            result.avg_share_holding_change = "decrease"
            if result.shareholder_change_rate:
                result.avg_share_holding_change_rate = round(-result.shareholder_change_rate, 2)
        else:
            result.avg_share_holding_change = "unknown"

    def _analyze_active_stock(self, result: ExtendedAnalysis, quote: Quote,
                              capital_flows: list[CapitalFlow]) -> None:
        """活跃股判断（多指标综合：换手率、成交量变化、振幅）。"""
        score = 0.0
        reasons = []

        # 换手率评分（0-40分）
        turnover = quote.turnover_rate or 0
        if turnover > 10:
            score += 40
            reasons.append(f"换手率{round(turnover, 1)}%极高")
        elif turnover > 5:
            score += 30
            reasons.append(f"换手率{round(turnover, 1)}%较高")
        elif turnover > 3:
            score += 20
            reasons.append(f"换手率{round(turnover, 1)}%略高")
        elif turnover > 1:
            score += 10
            reasons.append(f"换手率{round(turnover, 1)}%正常")

        # 振幅评分（0-30分）
        amplitude = quote.amplitude or 0
        if amplitude > 10:
            score += 30
            reasons.append(f"振幅{round(amplitude, 1)}%极大")
        elif amplitude > 5:
            score += 20
            reasons.append(f"振幅{round(amplitude, 1)}%较大")
        elif amplitude > 3:
            score += 10
            reasons.append(f"振幅{round(amplitude, 1)}%适中")

        # 成交量变化评分（0-30分）
        if len(capital_flows) >= 2:
            avg_recent_vol = sum(
                cf.main_net_inflow or 0 for cf in capital_flows[-3:]
            ) / 3 if len(capital_flows) >= 3 else (
                capital_flows[-1].main_net_inflow or 0
            )
            avg_prev_vol = sum(
                cf.main_net_inflow or 0 for cf in capital_flows[:-3]
            ) / max(len(capital_flows) - 3, 1) if len(capital_flows) > 3 else avg_recent_vol
            if avg_prev_vol != 0 and abs(avg_recent_vol) > abs(avg_prev_vol) * 2:
                score += 20
                reasons.append("近期资金量显著放大")
            elif avg_prev_vol != 0 and abs(avg_recent_vol) > abs(avg_prev_vol) * 1.5:
                score += 10
                reasons.append("近期资金量有所放大")

        result.active_stock_score = round(score, 1)
        result.is_active_stock = score >= 40
        result.active_stock_reason = "；".join(reasons) if reasons else "当前交易指标处于正常范围"

    def _analyze_hot_themes(self, result: ExtendedAnalysis, code_fmt: str) -> None:
        """分析热点题材（概念板块匹配）。"""
        themes = []
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                # 获取所有概念板块
                concept_df = ak.stock_board_concept_name_em()
            if concept_df is not None and not concept_df.empty:
                # 只检查前200个活跃概念以节省时间
                concept_names = concept_df["板块名称"].head(200).tolist()
                stock_name = ""
                try:
                    spot_df = self._get_spot_data()
                    stock_row = spot_df[spot_df["代码"] == code_fmt]
                    if not stock_row.empty:
                        stock_name = str(stock_row.iloc[0].get("名称", ""))
                except Exception:
                    pass

                for concept_name in concept_names[:100]:  # 限制到前100个
                    try:
                        cons_df = ak.stock_board_concept_cons_em(symbol=concept_name)
                        if cons_df is not None and not cons_df.empty:
                            # 匹配股票代码
                            code_col = None
                            for col_name in ["代码", "成分券代码", "股票代码"]:
                                if col_name in cons_df.columns:
                                    code_col = col_name
                                    break
                            if code_col:
                                matched = cons_df[cons_df[code_col].astype(str) == code_fmt]
                                if not matched.empty:
                                    themes.append(str(concept_name))
                                if len(themes) >= 10:  # 最多匹配10个
                                    break
                    except Exception:
                        continue
        except Exception as e:
            pass

        result.hot_themes = themes
        if themes:
            result.hot_theme_detail = f"该股票涉及 {len(themes)} 个概念板块：{', '.join(themes[:8])}"
        else:
            result.hot_theme_detail = "未匹配到热点概念板块（或未检索到概念数据）"

    def _analyze_risks(self, result: ExtendedAnalysis, quote: Quote) -> None:
        """识别潜在风险。"""
        risks = []

        # 基于财务指标的风险判断
        pe = quote.pe_ttm
        if pe is not None:
            if pe < 0:
                risks.append("公司处于亏损状态（市盈率为负）")
            elif pe > 100:
                risks.append(f"市盈率{pe:.1f}倍，估值偏高")

        pb = quote.pb
        if pb is not None and pb > 10:
            risks.append(f"市净率{pb:.1f}倍，资产估值较高")

        turnover = quote.turnover_rate or 0
        if turnover > 15:
            risks.append("换手率过高，短期炒作风险较大")

        amplitude = quote.amplitude or 0
        if amplitude > 10:
            risks.append(f"振幅{amplitude:.1f}%，股价波动剧烈")

        change_pct = quote.change_percent or 0
        if change_pct > 9.5:
            risks.append("今日涨幅接近涨停，追高风险较大")
        elif change_pct < -9.5:
            risks.append("今日跌幅接近跌停，存在继续下跌风险")

        if quote.market_cap and quote.market_cap < 1e10:
            risks.append(f"总市值{quote.market_cap/1e8:.1f}亿，小盘股流动性风险较高")

        result.potential_risks = risks
        if len(risks) >= 3:
            result.risk_level = "高"
            result.risk_analysis_summary = f"存在{len(risks)}项风险因素，风险等级较高"
        elif len(risks) >= 1:
            result.risk_level = "中"
            result.risk_analysis_summary = f"存在{len(risks)}项风险因素，需要关注"
        else:
            result.risk_level = "低"
            result.risk_analysis_summary = "未发现显著风险因素"

    def _analyze_investment_value(self, result: ExtendedAnalysis, quote: Quote,
                                  capital_flows: list[CapitalFlow]) -> None:
        """投资价值综合评估。"""
        score = 50.0  # 基础分
        reasons = []

        # PE 评估（0-20分）
        pe = quote.pe_ttm
        if pe is not None:
            if 10 < pe < 30:
                score += 15
                reasons.append(f"市盈率{pe:.1f}倍处于合理区间")
            elif 0 < pe <= 10:
                score += 20
                reasons.append(f"市盈率{pe:.1f}倍估值较低")
            elif pe > 50:
                score -= 10
                reasons.append(f"市盈率{pe:.1f}倍估值偏高")

        # 资金流向评估
        if capital_flows:
            inflow_direction = result.capital_flow_direction
            if inflow_direction == "inflow":
                score += 10
                reasons.append("主力资金持续流入")
            elif inflow_direction == "outflow":
                score -= 10
                reasons.append("主力资金持续流出")

        # 股东户数评估
        if result.shareholder_count_change == "decrease":
            score += 10
            reasons.append("股东户数减少，筹码趋于集中")
        elif result.shareholder_count_change == "increase":
            score -= 5
            reasons.append("股东户数增加，筹码趋于分散")

        # 基金持仓评估
        if result.fund_capital_direction == "inflow":
            score += 10
            reasons.append("基金加仓中")

        # 社保持仓评估
        if result.social_security_direction == "inflow":
            score += 10
            reasons.append("社保基金持有")

        # 活跃度评估
        if result.is_active_stock:
            score += 5
            reasons.append("交易活跃，流动性好")

        # 风险扣分
        risk_count = len(result.potential_risks)
        score -= risk_count * 5

        # 限制分数范围
        score = max(0, min(100, score))
        result.investment_value_score = round(score, 1)

        if score >= 70:
            result.investment_value = "具有较高投资价值"
        elif score >= 50:
            result.investment_value = "具有一定投资价值，需关注风险"
        elif score >= 30:
            result.investment_value = "投资价值一般，风险收益比不理想"
        else:
            result.investment_value = "投资价值较低，风险较大"

        result.investment_value_reason = "；".join(reasons) if reasons else "综合评估"

    # ---- helper methods ----

    def _format_code(self, code: str) -> str:
        """补齐股票代码为 6 位数字。"""
        code_clean = code.strip().replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")
        return code_clean.zfill(6)

    def _to_akshare_code(self, code: str) -> str:
        """转为 AKShare 格式。"""
        if code.startswith("6") or code.startswith("9"):
            return f"sh{code}"
        return f"sz{code}"

    def _calc_start_date(self, days: int) -> str:
        """计算起始日期。"""
        from datetime import timedelta
        d = date.today() - timedelta(days=days)
        return d.strftime("%Y%m%d")

    def _safe_float(self, value) -> Optional[float]:
        """安全转换为 float。"""
        if value is None:
            return None
        try:
            v = float(value)
            return None if pd.isna(v) else v
        except (ValueError, TypeError):
            return None
