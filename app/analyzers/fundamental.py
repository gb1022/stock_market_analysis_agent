"""财务指标计算：PE、PB、ROE、营收同比等。"""

from typing import Optional

from app.models.stock import FinancialData


def calc_pe(price: float, eps: Optional[float]) -> Optional[float]:
    """计算市盈率（PE）。

    Args:
        price: 当前股价
        eps: 每股收益

    Returns:
        PE 值，数据不足时返回 None
    """
    if eps is None or eps == 0:
        return None
    return round(price / eps, 2)


def calc_pb(price: float, bvps: Optional[float]) -> Optional[float]:
    """计算市净率（PB）。

    Args:
        price: 当前股价
        bvps: 每股净资产

    Returns:
        PB 值，数据不足时返回 None
    """
    if bvps is None or bvps == 0:
        return None
    return round(price / bvps, 2)


def calc_roe(profit: Optional[float], equity: Optional[float]) -> Optional[float]:
    """计算净资产收益率（ROE）。

    Args:
        profit: 净利润
        equity: 净资产

    Returns:
        ROE 百分比值，数据不足时返回 None
    """
    if profit is None or equity is None or equity == 0:
        return None
    return round((profit / equity) * 100, 2)


def calc_revenue_growth(current_revenue: Optional[float],
                        previous_revenue: Optional[float]) -> Optional[float]:
    """计算营业收入同比增长率。

    Args:
        current_revenue: 本期营业收入
        previous_revenue: 上年同期营业收入

    Returns:
        同比增长率（%），数据不足时返回 None
    """
    if current_revenue is None or previous_revenue is None or previous_revenue == 0:
        return None
    return round(((current_revenue - previous_revenue) / previous_revenue) * 100, 2)


def extract_key_metrics(financial: FinancialData) -> dict:
    """从 FinancialData 中提取关键财务指标。

    Args:
        financial: 财务数据对象

    Returns:
        关键指标字典
    """
    metrics = {}
    if financial.pe_ttm is not None:
        metrics["pe_ttm"] = financial.pe_ttm
    if financial.pb is not None:
        metrics["pb"] = financial.pb
    if financial.roe is not None:
        metrics["roe"] = financial.roe
    if financial.revenue_growth is not None:
        metrics["revenue_growth"] = financial.revenue_growth
    if financial.eps is not None:
        metrics["eps"] = financial.eps
    if financial.bvps is not None:
        metrics["bvps"] = financial.bvps
    if financial.debt_ratio is not None:
        metrics["debt_ratio"] = financial.debt_ratio
    if financial.gross_margin is not None:
        metrics["gross_margin"] = financial.gross_margin
    return metrics
