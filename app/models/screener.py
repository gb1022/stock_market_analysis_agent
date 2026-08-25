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
    analyzed_at: Optional[datetime] = Field(None, description="分析时间")
