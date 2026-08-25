"""技术指标计算：MA、MACD、RSI、KDJ、BOLL。"""

from typing import Optional

import numpy as np
import pandas as pd


def calc_ma(close_prices: list[float], period: int = 5) -> Optional[float]:
    """计算移动平均线（MA）。

    Args:
        close_prices: 收盘价列表（从旧到新）
        period: 周期（5/10/20/60）

    Returns:
        最新 MA 值，数据不足时返回 None
    """
    if len(close_prices) < period:
        return None
    return float(np.mean(close_prices[-period:]))


def calc_macd(close_prices: list[float],
              fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict[str, float]]:
    """计算 MACD 指标。

    Args:
        close_prices: 收盘价列表（从旧到新）
        fast: 快线周期
        slow: 慢线周期
        signal: 信号线周期

    Returns:
        {"dif": DIF值, "dea": DEA值, "histogram": 柱状图值} 或 None
    """
    if len(close_prices) < slow + signal:
        return None

    series = pd.Series(close_prices)
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    histogram = 2 * (dif - dea)

    return {
        "dif": float(dif.iloc[-1]),
        "dea": float(dea.iloc[-1]),
        "histogram": float(histogram.iloc[-1]),
    }


def calc_rsi(close_prices: list[float], period: int = 14) -> Optional[float]:
    """计算相对强弱指标（RSI）。

    Args:
        close_prices: 收盘价列表（从旧到新）
        period: 周期，默认 14

    Returns:
        RSI 值，数据不足时返回 None
    """
    if len(close_prices) < period + 1:
        return None

    deltas = np.diff(close_prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


def calc_kdj(high_prices: list[float], low_prices: list[float],
             close_prices: list[float], period: int = 9) -> Optional[dict[str, float]]:
    """计算 KDJ 指标。

    Args:
        high_prices: 最高价列表
        low_prices: 最低价列表
        close_prices: 收盘价列表
        period: 周期，默认 9

    Returns:
        {"k": K值, "d": D值, "j": J值} 或 None
    """
    if len(close_prices) < period:
        return None

    # 计算 RSV
    high_n = pd.Series(high_prices).rolling(window=period).max()
    low_n = pd.Series(low_prices).rolling(window=period).min()

    rsv = ((pd.Series(close_prices) - low_n) / (high_n - low_n).replace(0, np.nan)) * 100
    rsv = rsv.fillna(50)

    # KDJ 递推
    k_values = [50.0]
    d_values = [50.0]

    for i in range(1, len(rsv)):
        k = 2 / 3 * k_values[-1] + 1 / 3 * rsv.iloc[i]
        d = 2 / 3 * d_values[-1] + 1 / 3 * k
        k_values.append(k)
        d_values.append(d)

    k = k_values[-1]
    d = d_values[-1]
    j = 3 * k - 2 * d

    return {"k": float(k), "d": float(d), "j": float(j)}


def calc_boll(close_prices: list[float], period: int = 20,
              multiplier: float = 2.0) -> Optional[dict[str, float]]:
    """计算布林带（BOLL）。

    Args:
        close_prices: 收盘价列表
        period: 周期，默认 20
        multiplier: 标准差倍数，默认 2

    Returns:
        {"middle": 中轨, "upper": 上轨, "lower": 下轨} 或 None
    """
    if len(close_prices) < period:
        return None

    series = pd.Series(close_prices)
    ma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()

    middle = float(ma.iloc[-1])
    upper = float(middle + multiplier * std.iloc[-1])
    lower = float(middle - multiplier * std.iloc[-1])

    return {"middle": middle, "upper": upper, "lower": lower}


def calc_all_indicators(close_prices: list[float],
                        high_prices: Optional[list[float]] = None,
                        low_prices: Optional[list[float]] = None) -> dict:
    """计算所有常用技术指标。

    Args:
        close_prices: 收盘价列表
        high_prices: 最高价列表（用于 KDJ）
        low_prices: 最低价列表（用于 KDJ）

    Returns:
        包含各指标计算结果的字典
    """
    result = {}

    # MA 多周期
    for period in [5, 10, 20, 60]:
        val = calc_ma(close_prices, period)
        if val is not None:
            result[f"ma_{period}"] = val

    # MACD
    macd = calc_macd(close_prices)
    if macd:
        result["macd"] = macd

    # RSI
    rsi_val = calc_rsi(close_prices)
    if rsi_val is not None:
        result["rsi_14"] = rsi_val

    # KDJ
    if high_prices and low_prices:
        kdj = calc_kdj(high_prices, low_prices, close_prices)
        if kdj:
            result["kdj"] = kdj

    # BOLL
    boll = calc_boll(close_prices)
    if boll:
        result["boll"] = boll

    return result
