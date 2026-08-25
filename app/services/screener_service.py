"""选股服务入口。"""

from app.screener.conditions import ConditionGroup
from app.screener.engine import ScreenerEngine
from app.models.screener import ScreenResult
from app.services.stock_service import analyze_single_stock


def screen_stocks(conditions: ConditionGroup, top_n: int = 30) -> list[ScreenResult]:
    """选股服务。

    Args:
        conditions: 筛选条件组
        top_n: 返回的股票数量上限

    Returns:
        符合条件的股票列表
    """
    engine = ScreenerEngine(max_workers=10)
    candidates = engine.scan(conditions, top_n=top_n)
    return candidates


def analyze_screen_results(candidates: list[ScreenResult]) -> list[dict]:
    """对选股结果进行逐一简化分析。

    Args:
        candidates: 候选股票列表

    Returns:
        分析结果列表
    """
    results = []
    for c in candidates[:5]:  # 只分析前 5 只
        try:
            analysis = analyze_single_stock(c.code)
            results.append(analysis)
        except Exception as e:
            results.append({"code": c.code, "error": str(e)})
    return results
