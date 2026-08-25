"""资金流向分析：主力资金、北向资金等。"""

from typing import Optional

from app.models.stock import CapitalFlow


def calc_main_force_trend(flows: list[CapitalFlow], days: int = 5) -> Optional[dict]:
    """计算主力资金趋势。

    Args:
        flows: 资金流向数据列表（从旧到新）
        days: 分析的天数

    Returns:
        {"net_inflow_total": 净流入合计, "net_inflow_avg": 日均净流入,
         "positive_days": 净流入天数, "trend": 趋势描述}
    """
    if not flows:
        return None

    recent = flows[-days:] if len(flows) > days else flows
    total = sum(
        f.main_net_inflow for f in recent
        if f.main_net_inflow is not None
    )
    positive_days = sum(
        1 for f in recent
        if f.main_net_inflow is not None and f.main_net_inflow > 0
    )

    if total > 0:
        trend = "净流入"
    elif total < 0:
        trend = "净流出"
    else:
        trend = "持平"

    return {
        "net_inflow_total": round(total, 2),
        "net_inflow_avg": round(total / len(recent), 2),
        "positive_days": positive_days,
        "total_days": len(recent),
        "trend": trend,
    }


def calc_large_order_trend(flows: list[CapitalFlow], days: int = 5) -> Optional[dict]:
    """计算大单资金趋势。

    Args:
        flows: 资金流向数据列表（从旧到新）
        days: 分析的天数

    Returns:
        大单资金趋势摘要
    """
    if not flows:
        return None

    recent = flows[-days:] if len(flows) > days else flows
    total = sum(
        f.large_order_net_inflow for f in recent
        if f.large_order_net_inflow is not None
    )

    return {
        "large_order_total": round(total, 2),
        "days": len(recent),
    }


def calc_north_flow_trend(flows: list[CapitalFlow], days: int = 5) -> Optional[dict]:
    """计算北向资金趋势。

    Args:
        flows: 资金流向数据列表（从旧到新）
        days: 分析的天数

    Returns:
        北向资金趋势摘要
    """
    if not flows:
        return None

    recent = flows[-days:] if len(flows) > days else flows
    total = sum(
        f.north_net_inflow for f in recent
        if f.north_net_inflow is not None
    )

    return {
        "north_net_total": round(total, 2),
        "days": len(recent),
    }
