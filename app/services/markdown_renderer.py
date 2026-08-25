"""Markdown 报告渲染器 - 将 LLM 输出的 JSON 分析结论 + 原始数据渲染为 Markdown 报告"""

from datetime import datetime
from typing import Optional

from app.models.data_bundle import DataBundle


def render_markdown_report(
    state: dict,
    llm_analysis: dict,
    bundle: DataBundle
) -> str:
    """渲染完整的 Markdown 分析报告。

    Args:
        state: Agent 状态字典
        llm_analysis: LLM 输出的 JSON 分析结论
        bundle: 原始数据

    Returns:
        Markdown 格式的报告文本
    """
    lines = []

    # 1. 股票基本信息
    lines.append("# 股票分析报告")
    lines.append("")
    lines.append("## 1. 股票基本信息")
    lines.append("")
    lines.append(f"| 项目 | 内容 |")
    lines.append(f"|------|------|")
    lines.append(f"| 股票代码 | {bundle.stock_code} |")
    lines.append(f"| 股票名称 | {bundle.quote.name if bundle.quote else 'N/A'} |")
    lines.append(f"| 市场 | {bundle.market} |")
    lines.append(f"| 数据来源 | {bundle.source} |")
    lines.append(f"| 采集时间 | {bundle.fetched_at.strftime('%Y-%m-%d %H:%M:%S') if bundle.fetched_at else 'N/A'} |")
    lines.append("")

    # 2. 关键数据概览
    lines.append("## 2. 关键数据概览")
    lines.append("")
    if bundle.quote:
        lines.append("### 2.1 行情数据")
        lines.append("")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 最新价 | {bundle.quote.latest_price:.2f} 元 |")
        lines.append(f"| 涨跌幅 | {bundle.quote.change_percent:+.2f}% |")
        lines.append(f"| 涨跌额 | {bundle.quote.change_amount:+.2f} 元 |")
        lines.append(f"| 今开 | {bundle.quote.open_price:.2f} 元 |")
        lines.append(f"| 最高 | {bundle.quote.high_price:.2f} 元 |")
        lines.append(f"| 最低 | {bundle.quote.low_price:.2f} 元 |")
        lines.append(f"| 昨收 | {bundle.quote.pre_close:.2f} 元 |")
        lines.append(f"| 成交量 | {bundle.quote.volume:,} 手 |")
        lines.append(f"| 成交额 | {bundle.quote.amount/100000000:.2f} 亿元 |")
        lines.append(f"| 换手率 | {bundle.quote.turnover_rate:.2f}% |")
        lines.append(f"| 振幅 | {bundle.quote.amplitude:.2f}% |")
        if bundle.quote.market_cap:
            lines.append(f"| 总市值 | {bundle.quote.market_cap/100000000:.2f} 亿元 |")
        if bundle.quote.circulating_market_cap:
            lines.append(f"| 流通市值 | {bundle.quote.circulating_market_cap/100000000:.2f} 亿元 |")
        lines.append("")

    if bundle.financial:
        lines.append("### 2.2 财务数据")
        lines.append("")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        if bundle.financial.pe_ttm is not None:
            lines.append(f"| 市盈率(TTM) | {bundle.financial.pe_ttm:.2f} |")
        if bundle.financial.pb is not None:
            lines.append(f"| 市净率 | {bundle.financial.pb:.2f} |")
        if bundle.financial.roe is not None:
            lines.append(f"| 净资产收益率 | {bundle.financial.roe:.2f}% |")
        if bundle.financial.gross_margin is not None:
            lines.append(f"| 毛利率 | {bundle.financial.gross_margin:.2f}% |")
        if bundle.financial.net_margin is not None:
            lines.append(f"| 净利率 | {bundle.financial.net_margin:.2f}% |")
        if bundle.financial.revenue_growth is not None:
            lines.append(f"| 营收同比增长 | {bundle.financial.revenue_growth:+.2f}% |")
        if bundle.financial.profit_growth is not None:
            lines.append(f"| 净利润同比增长 | {bundle.financial.profit_growth:+.2f}% |")
        if bundle.financial.debt_ratio is not None:
            lines.append(f"| 资产负债率 | {bundle.financial.debt_ratio:.2f}% |")
        if bundle.financial.eps is not None:
            lines.append(f"| 每股收益 | {bundle.financial.eps:.2f} 元 |")
        if bundle.financial.bvps is not None:
            lines.append(f"| 每股净资产 | {bundle.financial.bvps:.2f} 元 |")
        lines.append("")

    if bundle.capital_flow:
        lines.append("### 2.3 资金流向（最近5日）")
        lines.append("")
        lines.append(f"| 日期 | 主力净流入 | 散户净流入 | 北向净流入 |")
        lines.append(f"|------|------------|------------|------------|")
        for cf in bundle.capital_flow[-5:]:
            main_str = f"{cf.main_net_inflow/100000000:+.2f}亿" if cf.main_net_inflow else "N/A"
            retail_str = f"{cf.retail_net_inflow/100000000:+.2f}亿" if cf.retail_net_inflow else "N/A"
            north_str = f"{cf.north_net_inflow/100000000:+.2f}亿" if cf.north_net_inflow else "N/A"
            lines.append(f"| {cf.trade_date} | {main_str} | {retail_str} | {north_str} |")
        lines.append("")

    # 3. 技术分析
    lines.append("## 3. 技术分析")
    lines.append("")

    # 3.1 技术指标数据（来自 stock-sdk-mcp 的 get_kline_with_indicators）
    if bundle.klines:
        latest = bundle.klines[-1]
        has_indicators = any([latest.ma5, latest.dif, latest.rsi6])
        if has_indicators:
            lines.append("### 3.1 技术指标（最新交易日）")
            lines.append("")
            lines.append(f"| 指标 | 数值 |")
            lines.append(f"|------|------|")
            if latest.ma5 is not None:
                lines.append(f"| MA5（5日均线） | {latest.ma5:.2f} |")
            if latest.ma10 is not None:
                lines.append(f"| MA10（10日均线） | {latest.ma10:.2f} |")
            if latest.ma20 is not None:
                lines.append(f"| MA20（20日均线） | {latest.ma20:.2f} |")
            if latest.dif is not None:
                lines.append(f"| DIF | {latest.dif:.2f} |")
            if latest.dea is not None:
                lines.append(f"| DEA | {latest.dea:.2f} |")
            if latest.macd is not None:
                lines.append(f"| MACD柱 | {latest.macd:.2f} |")
            if latest.rsi6 is not None:
                lines.append(f"| RSI(6) | {latest.rsi6:.1f} |")
            if latest.rsi12 is not None:
                lines.append(f"| RSI(12) | {latest.rsi12:.1f} |")
            if latest.rsi24 is not None:
                lines.append(f"| RSI(24) | {latest.rsi24:.1f} |")
            lines.append("")

    # 3.2 LLM 分析结论
    if "technical_analysis" in llm_analysis:
        ta = llm_analysis["technical_analysis"]
        lines.append(f"**趋势判断**: {ta.get('trend_judgment', 'N/A')}")
        lines.append("")
        if "ma_analysis" in ta:
            lines.append(f"**均线分析**: {ta['ma_analysis']}")
            lines.append("")
        if "macd_analysis" in ta:
            lines.append(f"**MACD分析**: {ta['macd_analysis']}")
            lines.append("")
        if "rsi_analysis" in ta:
            lines.append(f"**RSI分析**: {ta['rsi_analysis']}")
            lines.append("")
        if "key_levels" in ta and ta["key_levels"]:
            lines.append("**关键价位**:")
            lines.append("")
            for level in ta["key_levels"]:
                lines.append(f"- {level}")
            lines.append("")

    # 4. 基本面分析
    lines.append("## 4. 基本面分析")
    lines.append("")

    # 4.1 估值指标（来自 stock-sdk-mcp 的 get_a_share_quotes）
    if bundle.quote:
        q = bundle.quote
        has_valuation = any([q.pe_ttm is not None, q.pb is not None, q.market_cap is not None])
        if has_valuation:
            lines.append("### 4.1 估值指标（最新交易日）")
            lines.append("")
            lines.append(f"| 指标 | 数值 |")
            lines.append(f"|------|------|")
            if q.pe_ttm is not None:
                lines.append(f"| PE（TTM） | {q.pe_ttm:.2f} |")
            if q.pb is not None:
                lines.append(f"| PB（市净率） | {q.pb:.2f} |")
            if q.market_cap is not None:
                cap_yi = q.market_cap / 1e8
                lines.append(f"| 总市值 | {cap_yi:.2f}亿 |")
            if q.circulating_market_cap is not None:
                cap_yi = q.circulating_market_cap / 1e8
                lines.append(f"| 流通市值 | {cap_yi:.2f}亿 |")
            lines.append("")

    # 4.2 LLM 分析结论
    if "fundamental_analysis" in llm_analysis:
        fa = llm_analysis["fundamental_analysis"]
        lines.append(f"**估值判断**: {fa.get('valuation_judgment', 'N/A')}")
        lines.append("")
        if "profitability_analysis" in fa:
            lines.append(f"**盈利能力**: {fa['profitability_analysis']}")
            lines.append("")
        if "growth_analysis" in fa:
            lines.append(f"**成长性**: {fa['growth_analysis']}")
            lines.append("")
        if "financial_health" in fa:
            lines.append(f"**财务健康度**: {fa['financial_health']}")
            lines.append("")

    # 5. 资金面分析
    lines.append("## 5. 资金面分析")
    lines.append("")
    if "capital_flow_analysis" in llm_analysis:
        cfa = llm_analysis["capital_flow_analysis"]
        lines.append(f"**主力资金**: {cfa.get('main_force_summary', 'N/A')}")
        lines.append("")
        if "main_force_detail" in cfa:
            lines.append(f"**详细说明**: {cfa['main_force_detail']}")
            lines.append("")
        if "north_fund_analysis" in cfa:
            lines.append(f"**北向资金**: {cfa['north_fund_analysis']}")
            lines.append("")

    # 6. 资金流向判断
    lines.append("## 6. 资金流向判断")
    lines.append("")
    if "capital_direction_analysis" in llm_analysis:
        cda = llm_analysis["capital_direction_analysis"]
        lines.append(f"**整体方向**: {cda.get('overall_direction', 'N/A')}")
        lines.append("")
        lines.append(f"**分析说明**: {cda.get('analysis_detail', 'N/A')}")
        lines.append("")

    # 7. 机构持仓分析
    lines.append("## 7. 机构持仓分析")
    lines.append("")
    if "institutional_analysis" in llm_analysis:
        ia = llm_analysis["institutional_analysis"]
        fund_direction = ia.get('fund_direction', 'N/A')
        social_direction = ia.get('social_security_direction', 'N/A')

        # 当 LLM 返回 unknown 时，回退显示原始数据
        ea = bundle.extended_analysis if bundle else None
        if ea and fund_direction == "unknown" and ea.fund_analysis_summary:
            fund_direction = ea.fund_analysis_summary
        if ea and social_direction == "unknown" and ea.social_security_analysis_summary:
            social_direction = ea.social_security_analysis_summary

        lines.append(f"**基金资金**: {fund_direction}")
        lines.append("")
        if "fund_detail" in ia:
            lines.append(f"**基金持仓说明**: {ia['fund_detail']}")
            lines.append("")
        lines.append(f"**社保基金**: {social_direction}")
        lines.append("")
        if "social_security_detail" in ia:
            lines.append(f"**社保基金说明**: {ia['social_security_detail']}")
            lines.append("")

    # 8. 股东结构分析
    lines.append("## 8. 股东结构分析")
    lines.append("")
    if "shareholder_analysis" in llm_analysis:
        sa = llm_analysis["shareholder_analysis"]
        lines.append(f"**股东户数变化**: {sa.get('shareholder_count_change', 'N/A')}")
        lines.append("")
        if "shareholder_count_detail" in sa:
            lines.append(f"**详细说明**: {sa['shareholder_count_detail']}")
            lines.append("")
        lines.append(f"**平均持股变化**: {sa.get('avg_holding_change', 'N/A')}")
        lines.append("")
        lines.append(f"**筹码集中度**: {sa.get('chip_concentration', 'N/A')}")
        lines.append("")

    # 9. 活跃度分析
    lines.append("## 9. 活跃度分析")
    lines.append("")
    if "activity_analysis" in llm_analysis:
        aa = llm_analysis["activity_analysis"]
        is_active = "是" if aa.get('is_active', False) else "否"
        lines.append(f"**是否活跃股**: {is_active}")
        lines.append("")
        if "activity_score" in aa:
            lines.append(f"**活跃度评分**: {aa['activity_score']}/100")
            lines.append("")
        if "activity_detail" in aa:
            lines.append(f"**分析依据**: {aa['activity_detail']}")
            lines.append("")

    # 10. 题材分析
    lines.append("## 10. 题材分析")
    lines.append("")
    if "theme_analysis" in llm_analysis:
        ta = llm_analysis["theme_analysis"]
        if "themes" in ta and ta["themes"]:
            lines.append("**涉及题材/概念板块**:")
            lines.append("")
            for theme in ta["themes"]:
                lines.append(f"- {theme}")
            lines.append("")
        if "theme_detail" in ta:
            lines.append(f"**题材分析**: {ta['theme_detail']}")
            lines.append("")

    # 11. 风险分析
    lines.append("## 11. 风险分析")
    lines.append("")
    if "risk_analysis" in llm_analysis:
        ra = llm_analysis["risk_analysis"]
        lines.append(f"**风险等级**: {ra.get('risk_level', 'N/A')}")
        lines.append("")
        if "risk_factors" in ra and ra["risk_factors"]:
            lines.append("**潜在风险因素**:")
            lines.append("")
            for risk in ra["risk_factors"]:
                lines.append(f"- {risk}")
            lines.append("")
        if "risk_detail" in ra:
            lines.append(f"**风险分析**: {ra['risk_detail']}")
            lines.append("")

    # 12. 投资价值综合评估
    lines.append("## 12. 投资价值综合评估")
    lines.append("")
    if "investment_value_assessment" in llm_analysis:
        iva = llm_analysis["investment_value_assessment"]
        if "score" in iva:
            lines.append(f"**投资价值评分**: {iva['score']}/100")
            lines.append("")
        lines.append(f"**投资结论**: {iva.get('conclusion', 'N/A')}")
        lines.append("")
        if "reason" in iva:
            lines.append(f"**判断依据**: {iva['reason']}")
            lines.append("")

    # 13. 投资建议
    lines.append("## 13. 投资建议")
    lines.append("")
    if "investment_advice" in llm_analysis:
        ia = llm_analysis["investment_advice"]
        position_status = ia.get("position_status", "未提及")
        lines.append(f"**持仓状态**: {position_status}")
        lines.append("")

        # 未买入场景建议
        not_holding = ia.get("not_holding") or {}
        if not_holding:
            lines.append("### 未买入场景")
            lines.append("")
            lines.append(f"**操作建议**: {not_holding.get('recommendation', 'N/A')}")
            lines.append("")
            if "entry_price_range" in not_holding:
                lines.append(f"**建仓区间**: {not_holding['entry_price_range']}")
                lines.append("")
            if "position_suggestion" in not_holding:
                lines.append(f"**仓位建议**: {not_holding['position_suggestion']}")
                lines.append("")
            if "waiting_condition" in not_holding:
                lines.append(f"**观望触发条件**: {not_holding['waiting_condition']}")
                lines.append("")

        # 已持仓场景建议
        holding = ia.get("holding") or {}
        if holding:
            lines.append("### 已持仓场景")
            lines.append("")
            lines.append(f"**操作建议**: {holding.get('recommendation', 'N/A')}")
            lines.append("")
            if "cost_analysis" in holding:
                lines.append(f"**成本盈亏分析**: {holding['cost_analysis']}")
                lines.append("")
            if "stop_loss_price" in holding:
                lines.append(f"**止损位**: {holding['stop_loss_price']}")
                lines.append("")
            if "take_profit_price" in holding:
                lines.append(f"**止盈位**: {holding['take_profit_price']}")
                lines.append("")
            if "action_detail" in holding:
                lines.append(f"**操作说明**: {holding['action_detail']}")
                lines.append("")

        # 无场景字段时回退渲染单值建议（兼容旧版 LLM 输出）
        if not not_holding and not holding:
            lines.append(f"**操作建议**: {ia.get('recommendation', 'N/A')}")
            lines.append("")

        if "target_price" in ia:
            lines.append(f"**目标价位**: {ia['target_price']}")
            lines.append("")
        if "timeframe" in ia:
            lines.append(f"**投资周期**: {ia['timeframe']}")
            lines.append("")
        if "advice_detail" in ia:
            lines.append(f"**详细说明**: {ia['advice_detail']}")
            lines.append("")

    # 14. 免责声明
    lines.append("## 14. 免责声明")
    lines.append("")
    lines.append("---")
    lines.append("**免责声明**：以上内容仅基于历史数据的技术分析，不构成投资建议。")
    lines.append("")

    return "\n".join(lines)
