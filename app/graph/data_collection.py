"""Stage 0: 数据采集节点。

通过 DataSourceRouter 拉取真实行情、K线、财务、资金流、新闻数据，
组装为 DataBundle 输出。
不调用 LLM，纯 API 采集。
"""

from datetime import datetime

from app.data_sources.router import DataSourceRouter
from app.graph.state import StockAgentState
from app.graph.utils import add_log
from app.models.data_bundle import DataBundle


# 全局 Router 实例（由外部注入）
_router: DataSourceRouter | None = None


def set_data_source_router(router: DataSourceRouter) -> None:
    """设置数据源路由器（启动时注入）。"""
    global _router
    _router = router


def get_data_source_router() -> DataSourceRouter:
    """获取数据源路由器。"""
    if _router is None:
        raise RuntimeError("DataSourceRouter 未初始化，请先调用 set_data_source_router()")
    return _router


def data_collection(state: StockAgentState) -> StockAgentState:
    """数据采集阶段：采集真实数据（不虚构）。

    从 DataSourceRouter 拉取 5 类数据，组装为 DataBundle，
    标记数据来源和采集时间。

    Args:
        state: 当前 Agent 状态

    Returns:
        更新后的状态（进入 perception 阶段，或在错误时停留在当前阶段）
    """
    logs = add_log(state, "数据采集", "开始采集真实市场数据...")
    print(f"  [数据采集] 股票代码: {state['stock_code']}")

    try:
        router = get_data_source_router()
        code = state["stock_code"]
        market = state["market"]

        logs = add_log(state, "数据采集", "正在获取实时行情...", log_type="info")
        print(f"  [数据采集] 调用 router.get_quote({code})")
        quote = router.get_quote(code)
        logs = add_log(state, "数据采集", f"行情获取成功: {quote.name} ({quote.latest_price})", log_type="info")

        logs = add_log(state, "数据采集", "正在获取 K 线数据（250日）...", log_type="info")
        klines = router.get_kline(code, days=250)
        logs = add_log(state, "数据采集", f"K 线获取成功，共 {len(klines)} 条记录", log_type="info")

        logs = add_log(state, "数据采集", "正在获取财务数据...", log_type="info")
        financial = router.get_financial(code)
        logs = add_log(state, "数据采集", "财务数据获取成功", log_type="info")

        logs = add_log(state, "数据采集", "正在获取资金流向数据...", log_type="info")
        capital_flow = router.get_capital_flow(code, days=10)
        logs = add_log(state, "数据采集", f"资金流向获取成功，共 {len(capital_flow)} 条记录", log_type="info")

        logs = add_log(state, "数据采集", "正在获取新闻/公告...", log_type="info")
        news = router.get_news(code, limit=10)
        logs = add_log(state, "数据采集", f"新闻获取成功，共 {len(news)} 条", log_type="info")

        # 扩展分析数据采集（基金/社保/股东户数/活跃度/题材/风险/价值）
        logs = add_log(state, "数据采集", "正在采集扩展分析数据（基金/社保/股东户数/题材/风险/价值）...", log_type="info")
        try:
            extended_analysis = router.get_extended_analysis(code, quote, capital_flow, news)
            logs = add_log(state, "数据采集",
                           f"扩展分析完成：资金[{extended_analysis.capital_flow_direction}], "
                           f"基金[{extended_analysis.fund_capital_direction}], "
                           f"社保[{extended_analysis.social_security_direction}], "
                           f"股东户数[{extended_analysis.shareholder_count_change}], "
                           f"活跃[{extended_analysis.is_active_stock}], "
                           f"题材{len(extended_analysis.hot_themes)}个, "
                           f"风险{len(extended_analysis.potential_risks)}项, "
                           f"投资价值[{extended_analysis.investment_value}]",
                           log_type="info")
        except Exception as ext_err:
            logs = add_log(state, "数据采集", f"扩展分析数据获取失败（不影响主流程）: {ext_err}", log_type="warn")
            extended_analysis = None

        bundle = DataBundle(
            stock_code=code,
            market=market,
            quote=quote,
            klines=klines,
            financial=financial,
            capital_flow=capital_flow,
            news=news,
            extended_analysis=extended_analysis,
            fetched_at=datetime.now(),
            source=quote.source,
        )
        logs = add_log(state, "数据采集", "数据采集完成，所有数据已装入 DataBundle", log_type="info")
        return {
            **state,
            "data_bundle": bundle,
            "current_phase": "perception",
            "logs": logs,
        }
    except Exception as e:
        retry = state.get("retry_count", 0) + 1
        err_msg = f"数据采集失败: {e}"
        logs = add_log(state, "数据采集", err_msg, log_type="error")
        logs = add_log(state, "数据采集", f"将在第 {retry}/3 次重试", log_type="error")
        return {
            **state,
            "data_bundle": None,
            "error": err_msg,
            "current_phase": "data_collection",
            "retry_count": retry,
            "logs": logs,
        }
