"""个股分析服务入口。"""

import json
import logging
from typing import Generator

from app.graph.state import StockAgentState
from app.graph.workflow import create_stock_agent_workflow
from app.services.report_service import build_report
from app.graph.utils import extract_json

logger = logging.getLogger("stock_agent")


def _extract_data_collection_summary(result: dict) -> dict:
    """从 workflow 结果中提取数据采集阶段的摘要信息。"""
    bundle = result.get("data_bundle")
    summary = {}
    if bundle is not None:
        try:
            q = bundle.quote
            if q:
                summary["quote"] = {
                    "name": q.name or "",
                    "latest_price": q.latest_price or 0,
                    "change_percent": q.change_percent or 0,
                    "volume": q.volume or 0,
                    "amount": q.amount or 0,
                    "turnover_rate": q.turnover_rate or 0,
                    "amplitude": q.amplitude or 0,
                    "pe_ttm": q.pe_ttm,
                    "pb": q.pb,
                    "market_cap": q.market_cap,
                }
            summary["klines_count"] = len(bundle.klines)
            # K线数据：最近20日（数据按日期升序排列，取最后20条）
            summary["klines_recent"] = [
                {
                    "date": k.trade_date.strftime("%Y-%m-%d"),
                    "open": k.open_price,
                    "high": k.high,
                    "low": k.low,
                    "close": k.close,
                    "volume": k.volume,
                    "ma5": k.ma5,
                    "ma10": k.ma10,
                    "ma20": k.ma20,
                }
                for k in bundle.klines[-20:]
            ]
            if bundle.financial:
                summary["financial"] = {
                    "pe_ttm": bundle.financial.pe_ttm,
                    "pb": bundle.financial.pb,
                }
            summary["capital_flow_count"] = len(bundle.capital_flow)
            summary["news_count"] = len(bundle.news)
            summary["source"] = bundle.source
            summary["fetched_at"] = bundle.fetched_at.isoformat() if bundle.fetched_at else ""
            # 扩展分析缓存状态
            if bundle.extended_analysis is not None:
                summary["extended_analysis_from_cache"] = getattr(bundle.extended_analysis, "from_cache", False)
                summary["extended_analysis_fetched_at"] = bundle.extended_analysis.fetched_at.isoformat() if bundle.extended_analysis.fetched_at else ""
        except Exception:
            pass
    return summary


def _extract_extended_analysis(result: dict) -> dict | None:
    """从 workflow 结果中提取扩展分析数据。"""
    bundle = result.get("data_bundle")
    if bundle is not None and bundle.extended_analysis is not None:
        try:
            ea = bundle.extended_analysis
            return {
                "capital_flow_direction": ea.capital_flow_direction,
                "capital_flow_summary": ea.capital_flow_summary,
                "fund_capital_direction": ea.fund_capital_direction,
                "fund_analysis_summary": ea.fund_analysis_summary,
                "fund_holding_change": ea.fund_holding_change,
                "fund_holding_count": ea.fund_holding_count,
                "social_security_direction": ea.social_security_direction,
                "social_security_holding": ea.social_security_holding,
                "social_security_analysis_summary": ea.social_security_analysis_summary,
                "shareholder_count_change": ea.shareholder_count_change,
                "shareholder_count_latest": ea.shareholder_count_latest,
                "shareholder_count_previous": ea.shareholder_count_previous,
                "shareholder_change_rate": ea.shareholder_change_rate,
                "shareholder_analysis_summary": ea.shareholder_analysis_summary,
                "avg_share_holding_change": ea.avg_share_holding_change,
                "is_active_stock": ea.is_active_stock,
                "active_stock_reason": ea.active_stock_reason,
                "active_stock_score": ea.active_stock_score,
                "hot_themes": ea.hot_themes,
                "hot_theme_detail": ea.hot_theme_detail,
                "potential_risks": ea.potential_risks,
                "risk_level": ea.risk_level,
                "risk_analysis_summary": ea.risk_analysis_summary,
                "investment_value": ea.investment_value,
                "investment_value_score": ea.investment_value_score,
                "investment_value_reason": ea.investment_value_reason,
                # 缓存元数据
                "from_cache": getattr(ea, "from_cache", False),
                "fetched_at": ea.fetched_at.isoformat() if ea.fetched_at else "",
                "source": ea.source or "",
            }
        except Exception:
            pass
    return None


def analyze_single_stock(code: str, market: str = "A",
                         user_input: str = "") -> dict:
    """单股分析入口（阻塞式，一次性返回结果）。

    Args:
        code: 股票代码
        market: 市场（默认 A 股）
        user_input: 用户的投资意见/关注点（可选）

    Returns:
        {"report": "...", "state": {...}, "logs": [...], "llm_records": [...]}
    """
    logger.info(f"[个股分析服务] 开始分析: code={code}, market={market}, user_input={user_input[:100] if user_input else ''}")
    workflow = create_stock_agent_workflow()

    initial_state: StockAgentState = {
        "stock_code": code,
        "market": market,
        "analysis_type": "single",
        "user_input": user_input,
        "data_bundle": None,
        "perception": None,
        "world_model": None,
        "reasoning_plans": None,
        "selected_plan": None,
        "final_report": None,
        "current_phase": "data_collection",
        "error": None,
        "retry_count": 0,
        "logs": [],
        "llm_records": [],
    }

    result = workflow.invoke(initial_state)
    logger.info(f"[个股分析服务] workflow 执行完成, error={result.get('error')}, final_phase={result.get('current_phase')}")
    report = build_report(result)

    logs = result.get("logs", [])
    llm_records = result.get("llm_records", [])

    # 提取数据采集阶段摘要
    data_collection_summary = _extract_data_collection_summary(result)

    # 提取扩展分析数据（9维度）
    extended_analysis = _extract_extended_analysis(result)

    return {
        "report": report,
        "state": {k: v for k, v in result.items() if k not in ("data_bundle", "logs", "llm_records")},
        "data_collection": data_collection_summary,
        "extended_analysis": extended_analysis,
        "logs": logs,
        "llm_records": llm_records,
    }


def analyze_single_stock_stream(code: str, market: str = "A",
                                user_input: str = "") -> Generator[dict, None, None]:
    """单股分析流式入口（通过 Generator 产出 SSE 事件）。

    事件类型：
    - phase: 阶段开始/完成状态
    - data_collection: 数据采集结果（完整数据）
    - extended_analysis: 9维度扩展分析（完整数据）
    - perception: 感知分析结果（完整数据）
    - modeling: 建模分析结果（完整数据）
    - reasoning: 推理分析结果（完整数据）
    - decision: 决策结果（完整数据）
    - log: 执行日志
    - llm_call: LLM调用记录
    - report: 最终报告
    - error: 错误信息
    - complete: 完成事件

    Args:
        code: 股票代码
        market: 市场（默认 A 股）
        user_input: 用户的投资意见/关注点（可选）
    """
    logger.info(f"[流式分析] 开始分析: code={code}, user_input={user_input[:100] if user_input else ''}")
    workflow = create_stock_agent_workflow()

    initial_state: StockAgentState = {
        "stock_code": code,
        "market": market,
        "analysis_type": "single",
        "user_input": user_input,
        "data_bundle": None,
        "perception": None,
        "world_model": None,
        "reasoning_plans": None,
        "selected_plan": None,
        "final_report": None,
        "current_phase": "data_collection",
        "error": None,
        "retry_count": 0,
        "logs": [],
        "llm_records": [],
    }

    last_log_count = 0
    last_llm_count = 0
    final_state = None
    extended_analysis = None

    # 发送启动事件
    yield {"type": "phase", "data": {"phase": "data_collection", "status": "started"}}

    try:
        for node_output in workflow.stream(initial_state):
            for node_name, state in node_output.items():
                if state.get("error"):
                    yield {"type": "error", "data": {
                        "stage": node_name,
                        "message": state["error"],
                    }}
                    continue

                # 阶段完成事件
                yield {"type": "phase", "data": {"phase": node_name, "status": "completed"}}

                # 新增日志事件
                logs = state.get("logs", [])
                for i in range(last_log_count, len(logs)):
                    yield {"type": "log", "data": logs[i]}
                last_log_count = len(logs)

                # 新增 LLM 调用记录事件
                llm_records = state.get("llm_records", [])
                for i in range(last_llm_count, len(llm_records)):
                    yield {"type": "llm_call", "data": llm_records[i]}
                last_llm_count = len(llm_records)

                # 发送完整阶段数据（而非摘要）
                if node_name == "data_collection" and state.get("data_bundle"):
                    # 数据采集结果
                    dc_summary = _extract_data_collection_summary(state)
                    yield {"type": "data_collection", "data": dc_summary}

                    # 9维度扩展分析（数据采集阶段同时产出）
                    if state["data_bundle"].extended_analysis:
                        ea_data = _extract_extended_analysis(state)
                        yield {"type": "extended_analysis", "data": ea_data}

                elif node_name == "perception" and state.get("perception"):
                    # 感知分析结果
                    yield {"type": "perception", "data": state["perception"]}

                elif node_name == "modeling" and state.get("world_model"):
                    # 建模分析结果
                    yield {"type": "modeling", "data": state["world_model"]}

                elif node_name == "reasoning" and state.get("reasoning_plans"):
                    # 推理分析结果
                    yield {"type": "reasoning", "data": state["reasoning_plans"]}

                elif node_name == "decision" and state.get("selected_plan"):
                    # 决策结果
                    yield {"type": "decision", "data": state["selected_plan"]}

                # 记录下一个阶段的开始
                next_phase = state.get("current_phase")
                if next_phase and next_phase != node_name and next_phase != "completed":
                    yield {"type": "phase", "data": {"phase": next_phase, "status": "started"}}

                final_state = state

        # 发送最终报告
        if final_state:
            report = build_report(final_state)
            yield {"type": "report", "data": {"content": report}}

            # 提取扩展分析数据，供 complete 事件使用
            extended_analysis = _extract_extended_analysis(final_state)

    except Exception as e:
        logger.error(f"[流式分析] 异常: {e}", exc_info=True)
        yield {"type": "error", "data": {"message": str(e)}}
        extended_analysis = None

    yield {"type": "complete", "data": {"extended_analysis": extended_analysis}}


def _summarize_perception(data: dict) -> str:
    """从感知结果中提取关键摘要。"""
    try:
        signals = data.get("key_signals", [])
        if not signals:
            signals = data.get("signals", [])
        signal_text = f"识别到 {len(signals)} 个关键信号" if signals else "无显著信号"
        trend = data.get("trend_assessment", {}).get("overall_trend", "未知")
        return f"趋势判断: {trend}；{signal_text}"
    except Exception:
        return "感知分析完成"


def _summarize_modeling(data: dict) -> str:
    """从建模结果中提取关键摘要。"""
    try:
        risk = data.get("overall_risk_level", "未知")
        scenarios = data.get("scenarios", [])
        scenario_text = f"包含 {len(scenarios)} 个情景" if scenarios else ""
        return f"风险等级: {risk}；{scenario_text}"
    except Exception:
        return "建模分析完成"
