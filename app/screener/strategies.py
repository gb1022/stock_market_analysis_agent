"""预设选股策略。

提供 3 种预设策略：价值选股、动量选股、超跌反弹。
"""

from app.screener.conditions import ConditionGroup, FieldCondition


def value_strategy() -> ConditionGroup:
    """价值选股策略。

    PE 0~20 + PB 0~3（阶段一粗筛硬条件） + ROE > 12%（阶段二精排硬条件）
    ROE 为财务字段，粗筛阶段实时行情缺失时自然跳过，精排阶段用真实 ROE 过滤。
    """
    return (ConditionGroup()
        .add(FieldCondition(field="pe_ttm", op="between", value=[0, 20]))
        .add(FieldCondition(field="pb", op="between", value=[0, 3]))
        .add(FieldCondition(field="roe", op=">", value=12))
    )


def momentum_strategy() -> ConditionGroup:
    """动量选股策略。

    涨跌幅 3~20% + 换手率 > 1% + 成交量 > 100000手
    """
    return (ConditionGroup()
        .add(FieldCondition(field="change_percent", op="between", value=[3, 20]))
        .add(FieldCondition(field="turnover_rate", op=">", value=1))
        .add(FieldCondition(field="volume", op=">", value=100000))
    )


def oversold_rebound_strategy() -> ConditionGroup:
    """超跌反弹策略。

    涨跌幅 -8%~-0.5% + 换手率 > 0.5%（阶段一粗筛） + RSI24 < 30（阶段二精排）
    RSI24 为技术字段，粗筛阶段实时行情缺失时自然跳过，精排阶段用真实 RSI 过滤。
    """
    return (ConditionGroup()
        .add(FieldCondition(field="change_percent", op="between", value=[-8, -0.5]))
        .add(FieldCondition(field="turnover_rate", op=">", value=0.5))
        .add(FieldCondition(field="rsi24", op="<", value=30))
    )


# 策略名称到构造函数的映射
STRATEGY_MAP: dict[str, str] = {
    "value": "价值选股",
    "momentum": "动量选股",
    "oversold_rebound": "超跌反弹",
}


def get_strategy(name: str) -> ConditionGroup:
    """按名称获取预设策略。

    Args:
        name: 策略名称（value / momentum / oversold_rebound）

    Returns:
        对应的 ConditionGroup

    Raises:
        ValueError: 策略名称不存在时抛出
    """
    strategy_map = {
        "value": value_strategy,
        "momentum": momentum_strategy,
        "oversold_rebound": oversold_rebound_strategy,
    }
    if name not in strategy_map:
        raise ValueError(
            f"策略 '{name}' 不存在。可选: {list(strategy_map.keys())}"
        )
    return strategy_map[name]()
