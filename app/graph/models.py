"""LangGraph 各阶段的 Pydantic 输出模型。

参考 deliberative_research_langgraph.py 的 4 个模型结构，
但字段全部按股票业务重写。
"""

from typing import Optional, Literal

from pydantic import BaseModel, Field


class PerceptionOutput(BaseModel):
    """感知阶段：对真实数据的解读"""
    key_observations: list[str] = Field(
        default_factory=list,
        description="关键观察",
    )
    technical_signals: dict[str, str] = Field(
        default_factory=dict,
        description="技术信号（如 ma_5: 多头排列）",
    )
    fundamental_signals: dict[str, str] = Field(
        default_factory=dict,
        description="基本面信号（如 pe_ttm: 低于行业均值）",
    )
    capital_flow_signals: dict[str, str] = Field(
        default_factory=dict,
        description="资金面信号",
    )
    news_impact: list[str] = Field(
        default_factory=list,
        description="新闻影响",
    )


class ModelingOutput(BaseModel):
    """建模阶段：构建市场内部模型"""
    market_state: str = Field(
        default="",
        description="市场状态描述",
    )
    trend_judgment: str = Field(
        default="",
        description="趋势判断（上升/震荡/下降）",
    )
    risk_factors: list[str] = Field(
        default_factory=list,
        description="风险因素",
    )
    opportunity_factors: list[str] = Field(
        default_factory=list,
        description="机会因素",
    )
    sentiment: str = Field(
        default="",
        description="市场情绪（乐观/中性/悲观）",
    )


class ReasoningPlan(BaseModel):
    """推理阶段：单个分析方案"""
    plan_id: str = Field(default="", description="方案标识")
    hypothesis: str = Field(default="", description="假设")
    approach: str = Field(default="", description="分析方法")
    expected_outcome: str = Field(default="", description="预期结果")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="置信度")
    pros: list[str] = Field(default_factory=list, description="优势")
    cons: list[str] = Field(default_factory=list, description="劣势")


class DecisionOutput(BaseModel):
    """决策阶段：最终决策输出"""
    selected_plan_id: str = Field(default="", description="选中的方案 ID")
    investment_thesis: str = Field(default="", description="投资论点")
    supporting_evidence: list[str] = Field(
        default_factory=list,
        description="支撑证据",
    )
    risk_assessment: str = Field(default="", description="风险评估")
    recommendation: Literal["买入", "持有", "卖出", "观望"] = Field(
        default="观望",
        description="投资建议",
    )
    position_status: Literal["未买入", "已持仓", "未提及"] = Field(
        default="未提及",
        description="持仓状态",
    )
    not_holding_advice: Optional[dict] = Field(
        None,
        description="未买入场景建议（recommendation/entry_price_range/position_suggestion/waiting_condition）",
    )
    holding_advice: Optional[dict] = Field(
        None,
        description="持仓场景建议（recommendation/cost_analysis/stop_loss_price/take_profit_price/action_detail）",
    )
    target_price_range: Optional[str] = Field(None, description="目标价区间")
    timeframe: Literal["短期", "中期", "长期"] = Field(
        default="中期",
        description="投资时间框架",
    )
