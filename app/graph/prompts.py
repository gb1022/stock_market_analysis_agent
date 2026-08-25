"""Prompt 模板：全部含防虚构约束。

每个 LLM 节点的 Prompt 都以 ANTI_FABRICATION_RULE 开头，
确保 LLM 基于真实数据分析，不编造数据。
支持 {user_input} 占位，用于注入用户的投资意见。
"""

ANTI_FABRICATION_RULE = """
【数据真实性铁律 - 违反将导致输出作废】
1. 你只能基于"下方提供的真实数据"进行分析，不得使用你的先验知识编造任何数字、事件、新闻
2. 对未来走势的判断必须标注为"基于历史数据的技术分析推测，非确定性预测"
3. 每条结论后用 [数据来源:xxx, 时间:xxx] 标注依据
4. 投资意见必须区分"基于数据的客观描述"和"主观建议"
5. 必须附风险提示
"""

PERCEPTION_PROMPT = """你是专业的股票分析助手，请基于以下真实数据识别关键信号。

【研究对象】{stock_code} ({market})
【数据更新时间】{fetched_at}
【数据来源】{source}

【用户的投资意见/关注点（请重点分析）】
{user_input}

{anti_fabrication_rule}

【真实行情数据】
{quote_json}
（注：行情数据中 pe_ttm 为市盈率（TTM），pb 为市净率，market_cap 为总市值（元），
  circulating_market_cap 为流通市值（元）。若字段值为 null 表示该数据未获取到。）

【K线最近20日】
{klines_recent_json}
（注：K线数据中 ma5/ma10/ma20 为均线值，dif/dea/macd 为 MACD 指标，rsi6/rsi12/rsi24 为 RSI 指标。
  若字段值为 null 表示该日指标未计算完成，请基于有值的字段进行分析，不要自行计算。）

【财务数据】
{financial_json}

【资金流数据】
{capital_flow_json}

【相关新闻】
{news_json}

【扩展分析数据（资金方向/基金/社保/股东户数/活跃度/题材/风险/投资价值）】
{extended_analysis_json}

请按以下 JSON 格式输出（对应 PerceptionOutput）：
{{
    "key_observations": ["关键观察1", "关键观察2"],
    "technical_signals": {{"ma_5": "多头排列", "macd": "金叉"}},
    "fundamental_signals": {{"pe_ttm": "低于行业均值"}},
    "capital_flow_signals": {{"main_force": "连续3日净流入"}},
    "news_impact": ["新闻影响1"]
}}
"""

MODELING_PROMPT = """你是专业的股票分析助手，请基于感知阶段的分析结果构建市场内部模型。

【研究对象】{stock_code} ({market})

【用户的投资意见/关注点（请重点分析）】
{user_input}

{anti_fabrication_rule}

【感知阶段输出】
{perception_json}

请按以下 JSON 格式输出（对应 ModelingOutput）：
{{
    "market_state": "市场状态描述",
    "trend_judgment": "上升/震荡/下降",
    "risk_factors": ["风险1", "风险2"],
    "opportunity_factors": ["机会1", "机会2"],
    "sentiment": "乐观/中性/悲观"
}}
"""

REASONING_PROMPT = """你是专业的股票分析助手，请基于已有分析生成 3 个差异化的分析方案。

【研究对象】{stock_code} ({market})

【用户的投资意见/关注点（请重点参考）】
{user_input}

{anti_fabrication_rule}

【感知阶段输出】
{perception_json}

【建模阶段输出】
{modeling_json}

请输出包含 3 个方案的 JSON 数组，每个方案格式如下：
{{
    "plan_id": "plan_1",
    "hypothesis": "假设描述",
    "approach": "分析方法",
    "expected_outcome": "预期结果",
    "confidence": 0.75,
    "pros": ["优势1"],
    "cons": ["劣势1"]
}}
"""

DECISION_PROMPT = """你是专业的股票分析助手，请从 3 个方案中选择最优方案并进行风险评估。

【研究对象】{stock_code} ({market})

【用户的投资意见/关注点（请重点参考）】
{user_input}

{anti_fabrication_rule}

【感知阶段输出】
{perception_json}

【建模阶段输出】
{modeling_json}

【3个分析方案】
{plans_json}

请按以下 JSON 格式输出（对应 DecisionOutput）：
{{
    "selected_plan_id": "plan_1",
    "investment_thesis": "投资论点",
    "supporting_evidence": ["证据1"],
    "risk_assessment": "风险评估",
    "recommendation": "买入/持有/卖出/观望",
    "position_status": "未买入/已持仓/未提及",
    "not_holding_advice": {{
        "recommendation": "可买入/暂缓买入/不建议买入",
        "entry_price_range": "XX-YY元建仓区间",
        "position_suggestion": "建议仓位（如轻仓20%）",
        "waiting_condition": "观望触发条件（出现什么信号才动手）"
    }},
    "holding_advice": {{
        "recommendation": "继续持有/加仓/减仓/卖出",
        "cost_analysis": "当前价相对成本价的盈亏分析",
        "stop_loss_price": "XX元止损位",
        "take_profit_price": "XX元止盈位",
        "action_detail": "具体操作说明"
    }},
    "target_price_range": "XX-YY元",
    "timeframe": "短期/中期/长期"
}}

持仓场景输出规则：
1. position_status 取值为 未买入/已持仓/未提及：用户明确未持有填"未买入"，明确已持有填"已持仓"，用户未提及持仓状态填"未提及"
2. 用户已持仓（尤其提供了成本价时）：必须输出 holding_advice，结合成本价计算盈亏并给出止损/止盈位，not_holding_advice 填 null
3. 用户未买入：必须输出 not_holding_advice，给出建仓区间和仓位建议，holding_advice 填 null
4. 用户未提及持仓状态：not_holding_advice 和 holding_advice 两个场景都要输出
5. recommendation 字段给出整体方向判断（买入/持有/卖出/观望）
"""

REPORT_PROMPT = """你是专业的股票分析助手，请基于完整分析过程输出结构化的 JSON 分析结论。

【研究对象】{stock_code} ({market}) - {stock_name}

【用户的投资意见/关注点（请在分析中重点回应）】
{user_input}

{anti_fabrication_rule}

【数据来源】
{data_source_info}

【感知阶段输出】
{perception_json}

【建模阶段输出】
{modeling_json}

【扩展分析数据（资金方向/基金/社保/股东户数/活跃度/题材/风险/投资价值）】
{extended_analysis_json}

【选中的分析方案】
{selected_plan_json}

请严格按照以下 JSON 格式输出分析结论（不要输出 Markdown，只输出纯 JSON）：
{{
    "technical_analysis": {{
        "trend_judgment": "趋势判断（上升/震荡/下降）",
        "ma_analysis": "均线分析结论（包含具体MA数值）",
        "macd_analysis": "MACD分析结论（包含DIF、DEA、MACD柱数值）",
        "rsi_analysis": "RSI分析结论（包含RSI数值）",
        "key_levels": ["支撑位: XX元", "压力位: XX元"]
    }},
    "fundamental_analysis": {{
        "valuation_judgment": "估值判断（包含PE、PB具体数值及行业对比）",
        "profitability_analysis": "盈利能力分析（包含ROE、毛利率、净利率具体数值）",
        "growth_analysis": "成长性分析（包含营收增长率、净利润增长率具体数值）",
        "financial_health": "财务健康度分析（包含资产负债率具体数值）"
    }},
    "capital_flow_analysis": {{
        "main_force_summary": "主力资金流向总结（包含具体净流入金额）",
        "main_force_detail": "主力资金详细说明",
        "north_fund_analysis": "北向资金分析（包含具体净流入金额）"
    }},
    "capital_direction_analysis": {{
        "overall_direction": "整体资金方向（流入/流出/平衡）",
        "analysis_detail": "资金流向详细分析说明"
    }},
    "institutional_analysis": {{
        "fund_direction": "基金资金流入/流出（包含具体持股比例变化）",
        "fund_detail": "基金持仓详细分析",
        "social_security_direction": "社保基金流入/流出（包含具体持股比例）",
        "social_security_detail": "社保基金详细分析"
    }},
    "shareholder_analysis": {{
        "shareholder_count_change": "股东户数变化（增加/减少/不变，包含具体数值和变化率）",
        "shareholder_count_detail": "股东户数详细分析",
        "avg_holding_change": "平均持股变化（增加/减少/不变，包含具体数值）",
        "chip_concentration": "筹码集中度分析结论"
    }},
    "activity_analysis": {{
        "is_active": true,
        "activity_score": 75,
        "activity_detail": "活跃度分析依据（包含换手率、振幅、成交量具体数值）"
    }},
    "theme_analysis": {{
        "themes": ["题材1", "题材2"],
        "theme_detail": "题材详细分析"
    }},
    "risk_analysis": {{
        "risk_level": "低/中/高",
        "risk_factors": ["风险因素1", "风险因素2"],
        "risk_detail": "风险分析详细说明"
    }},
    "investment_value_assessment": {{
        "score": 70,
        "conclusion": "投资价值结论",
        "reason": "判断依据（包含具体指标支撑）"
    }},
    "investment_advice": {{
        "position_status": "未买入/已持仓/未提及",
        "not_holding": {{
            "recommendation": "可买入/暂缓买入/不建议买入",
            "entry_price_range": "XX-YY元建仓区间",
            "position_suggestion": "建议仓位（如轻仓20%）",
            "waiting_condition": "观望触发条件（出现什么信号才动手）"
        }},
        "holding": {{
            "recommendation": "继续持有/加仓/减仓/卖出",
            "cost_analysis": "当前价相对成本价的盈亏分析",
            "stop_loss_price": "XX元止损位",
            "take_profit_price": "XX元止盈位",
            "action_detail": "具体操作说明"
        }},
        "target_price": "目标价位区间",
        "timeframe": "短期/中期/长期",
        "advice_detail": "投资建议详细说明（需回应用户关注点）"
    }}
}}

注意：
1. 所有分析必须包含具体的指标数值，不要只给模糊描述
2. 只输出纯 JSON，不要用 ```json``` 包裹
3. 所有数值必须基于提供的真实数据，不得虚构
4. 每条分析结论后标注数据来源 [数据来源:xxx]
5. 投资建议需按持仓状态区分输出：
   - 用户明确已持仓（尤其提供了成本价）时：只输出 holding 场景，结合成本价计算盈亏并给出止损/止盈位，not_holding 填 null
   - 用户明确未买入时：只输出 not_holding 场景，给出建仓区间和仓位建议，holding 填 null
   - 用户未提及持仓状态时：position_status 填"未提及"，not_holding 和 holding 两个场景都要输出
"""

# Prompt 模板映射表（用于测试验证所有模板必含 ANTI_FABRICATION_RULE）
ALL_PROMPTS = {
    "PERCEPTION_PROMPT": PERCEPTION_PROMPT,
    "MODELING_PROMPT": MODELING_PROMPT,
    "REASONING_PROMPT": REASONING_PROMPT,
    "DECISION_PROMPT": DECISION_PROMPT,
    "REPORT_PROMPT": REPORT_PROMPT,
}
