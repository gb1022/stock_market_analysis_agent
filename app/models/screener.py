"""选股相关数据模型。"""

from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel, Field


class StockBasicInfo(BaseModel):
    """股票基本信息"""
    code: str = Field(..., description="股票代码")
    name: str = Field("", description="股票名称")
    market: str = Field("A", description="市场")


class ConditionItem(BaseModel):
    """单条选股条件"""
    field: str = Field(..., description="字段名")
    op: Literal[">", "<", ">=", "<=", "==", "between"] = Field(..., description="操作符")
    value: float | list[float] = Field(..., description="值（between 时为 [min, max]）")


class StrategyPreset(BaseModel):
    """预设策略"""
    name: str = Field(..., description="策略名称")
    display_name: str = Field(..., description="显示名称")
    description: str = Field("", description="策略描述")
    conditions: list[ConditionItem] = Field(default_factory=list, description="条件列表")


class ScreenRequest(BaseModel):
    """选股请求"""
    conditions: list[ConditionItem] = Field(default_factory=list, description="自定义条件列表")
    strategy: Optional[str] = Field(None, description="预设策略名称")
    top_n: int = Field(30, description="返回股票数量上限", ge=1, le=100)


class ScreenResult(BaseModel):
    """选股结果"""
    code: str = Field(..., description="股票代码")
    name: str = Field(..., description="股票名称")
    score: float = Field(0.0, description="综合评分")
    matched_conditions: int = Field(0, description="命中的条件数")
    total_conditions: int = Field(0, description="总条件数")
    reason: str = Field("", description="入选理由摘要")
    is_partial: bool = Field(False, description="是否部分匹配（动态补齐）")
    change_percent: Optional[float] = Field(None, description="涨跌幅，用于稳定排序")
    amount: Optional[float] = Field(None, description="成交额（元），用于稳定排序兜底键（v2.11.0）")
    final_score: Optional[float] = Field(None, description="多因子综合评分（v2.8.0）")
    factor_detail: dict = Field(default_factory=dict, description="各因子分详情")
    # ---- 关键行情字段（v2.12.0）：供前端展示与 LLM 意见生成使用 ----
    pe_ttm: Optional[float] = Field(None, description="市盈率（动态）")
    pb: Optional[float] = Field(None, description="市净率")
    turnover_rate: Optional[float] = Field(None, description="换手率（%）")
    market_cap: Optional[float] = Field(None, description="总市值（元）")
    volume: Optional[float] = Field(None, description="成交量（手）")
    amplitude: Optional[float] = Field(None, description="振幅（%）")
    opinion: str = Field("", description="LLM 生成的选择意见（100字以内，v2.12.0）")
    llm_score: Optional[float] = Field(None, description="LLM 综合评分（0-100，v2.14.0），阶段三精选后按此评分降序排序")
    analyzed_at: Optional[datetime] = Field(None, description="分析时间")
