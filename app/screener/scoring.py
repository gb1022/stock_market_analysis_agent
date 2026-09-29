"""多因子综合评分模块。

将多个选股因子（条件契合度、估值、动量、活跃度、规模）按权重加权求和，
得到综合评分（0~100）。因子权重集中配置于 config/default.yaml 的
screener.factor_weights，便于后期调整而无需改代码。
"""

from typing import Optional

# 因子权重默认值（与 config/default.yaml 的 screener.factor_weights 保持一致，
# 此处作为代码内兜底默认值，便于单测无需读文件）
DEFAULT_FACTOR_WEIGHTS: dict[str, float] = {
    "condition_fit": 0.30,   # 条件契合度（matched/total）
    "valuation": 0.30,       # 估值（pe/pb 越低越好）
    "momentum": 0.20,        # 动量（涨跌幅）
    "activity": 0.15,        # 活跃度（换手率）
    "scale": 0.05,           # 规模（市值）
}

# 缺失/异常值时使用的中性分数
NEUTRAL_SCORE = 50.0


def normalize_factor(
    value: Optional[float],
    p_low: float,
    p_high: float,
    higher_is_better: bool = True,
) -> float:
    """按分位数边界 [p_low, p_high] 把原始值线性映射到 0~100。

    Args:
        value: 原始值，None 时返回中性分
        p_low: 低分位边界
        p_high: 高分位边界
        higher_is_better: True 表示值越大分越高，False 表示值越小分越高

    Returns:
        0~100 的因子分
    """
    if value is None:
        return NEUTRAL_SCORE
    if p_high <= p_low:
        return NEUTRAL_SCORE
    clamped = min(max(value, p_low), p_high)
    ratio = (clamped - p_low) / (p_high - p_low)
    score = ratio * 100.0
    return score if higher_is_better else 100.0 - score


def _valuation_score(data: dict, stats: dict) -> float:
    """估值因子分：pe 和 pb 归一化后取平均，负值/缺失取中性。"""
    pe = data.get("pe_ttm")
    pb = data.get("pb")

    if pe is None or pe < 0:
        pe_score = NEUTRAL_SCORE
    else:
        pe_stat = stats.get("pe_ttm", {})
        pe_score = normalize_factor(
            pe, pe_stat.get("p_low", 0), pe_stat.get("p_high", 1),
            higher_is_better=False,
        )

    if pb is None or pb < 0:
        pb_score = NEUTRAL_SCORE
    else:
        pb_stat = stats.get("pb", {})
        pb_score = normalize_factor(
            pb, pb_stat.get("p_low", 0), pb_stat.get("p_high", 1),
            higher_is_better=False,
        )

    return (pe_score + pb_score) / 2.0


def score_stock(
    data: dict,
    market_stats: dict,
    weights: Optional[dict[str, float]] = None,
) -> tuple[float, dict]:
    """对单只股票做多因子加权评分。

    Args:
        data: 股票数据，含 condition_fit、pe_ttm、pb、change_percent、
              turnover_rate、market_cap
        market_stats: 全市场各因子的分位数边界
        weights: 因子权重，默认 DEFAULT_FACTOR_WEIGHTS

    Returns:
        (final_score 0~100, factor_detail 各因子分)
    """
    w = weights if weights is not None else DEFAULT_FACTOR_WEIGHTS

    change_stat = market_stats.get("change_percent", {})
    turnover_stat = market_stats.get("turnover_rate", {})
    cap_stat = market_stats.get("market_cap", {})

    factor_scores = {
        "condition_fit": (data.get("condition_fit") or 0.0) * 100.0,
        "valuation": _valuation_score(data, market_stats),
        "momentum": normalize_factor(
            data.get("change_percent"),
            change_stat.get("p_low", -10),
            change_stat.get("p_high", 10),
            higher_is_better=True,
        ),
        "activity": normalize_factor(
            data.get("turnover_rate"),
            turnover_stat.get("p_low", 0),
            turnover_stat.get("p_high", 20),
            higher_is_better=True,
        ),
        "scale": normalize_factor(
            data.get("market_cap"),
            cap_stat.get("p_low", 1e9),
            cap_stat.get("p_high", 1e11),
            higher_is_better=True,
        ),
    }

    final_score = sum(w.get(k, 0.0) * v for k, v in factor_scores.items())
    return final_score, factor_scores


def load_factor_weights(config: Optional[dict] = None) -> dict:
    """从配置加载因子权重，缺省返回默认权重。

    Args:
        config: 配置 dict，None 时从 config/default.yaml 加载

    Returns:
        因子权重 dict
    """
    if config is None:
        from app.config import load_config
        config = load_config()
    screener = config.get("screener", {}) if config else {}
    return screener.get("factor_weights", DEFAULT_FACTOR_WEIGHTS)
