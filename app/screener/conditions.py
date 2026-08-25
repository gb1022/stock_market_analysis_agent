"""选股条件构建器。

支持链式 API 构建筛选条件，含操作符校验和字段白名单。
"""

from typing import Optional, Literal

from pydantic import BaseModel, Field


class FieldCondition(BaseModel):
    """单条选股条件"""
    field: str = Field(..., description="字段名")
    op: Literal[">", "<", ">=", "<=", "==", "between"] = Field(
        ..., description="操作符"
    )
    value: float | list[float] = Field(..., description="值")


# 字段白名单（仅包含 AKShare 实时行情可提供的字段）
# 财务指标（roe/revenue_growth/eps/bvps等）和技术指标（rsi_14/MA等）待后续版本支持
FIELD_WHITELIST = {
    "pe_ttm", "pb", "turnover_rate", "market_cap",
    "change_percent", "volume", "amount", "amplitude",
}

# 操作符白名单
OP_WHITELIST = {">", "<", ">=", "<=", "==", "between"}


class ConditionGroup:
    """条件组，支持链式 API。

    Examples:
        >>> cg = ConditionGroup()
        >>> cg.add(FieldCondition(field="pe_ttm", op="between", value=[0, 20])) \\
        ...   .add(FieldCondition(field="pb", op="<", value=3))
    """

    def __init__(self, market: Literal["A"] = "A") -> None:
        self._market = market
        self._conditions: list[FieldCondition] = []

    @property
    def market(self) -> str:
        return self._market

    @property
    def conditions(self) -> list[FieldCondition]:
        return list(self._conditions)

    def add(self, condition: FieldCondition) -> "ConditionGroup":
        """添加一个条件。

        Args:
            condition: 条件对象

        Returns:
            self（支持链式调用）

        Raises:
            ValueError: 字段不在白名单或操作符不合法时抛出
        """
        if condition.field not in FIELD_WHITELIST:
            raise ValueError(
                f"字段 '{condition.field}' 不在白名单中。"
                f"可选: {sorted(FIELD_WHITELIST)}"
            )
        if condition.op not in OP_WHITELIST:
            raise ValueError(
                f"操作符 '{condition.op}' 不合法。"
                f"可选: {sorted(OP_WHITELIST)}"
            )
        if condition.op == "between":
            if not isinstance(condition.value, list) or len(condition.value) != 2:
                raise ValueError("between 操作符需要 [min, max] 格式的值")

        self._conditions.append(condition)
        return self

    def to_query(self) -> dict:
        """转换为查询字典。

        Returns:
            {"market": "A", "conditions": [{"field": "pe_ttm", "op": "between", "value": [0, 20]}, ...]}
        """
        return {
            "market": self._market,
            "conditions": [c.model_dump() for c in self._conditions],
        }

    def __len__(self) -> int:
        return len(self._conditions)
