"""技术指标计算测试：MA/MACD/RSI/KDJ/BOLL 及边界。"""

import pytest


class TestTechnicalIndicators:
    """技术指标函数测试套件"""

    def test_calc_ma_basic(self):
        """正常路径：计算 5 日均线。"""
        from app.analyzers.technical import calc_ma
        prices = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
        result = calc_ma(prices, period=5)
        assert result is not None
        assert result == 17.0  # (15+16+17+18+19)/5

    def test_calc_ma_insufficient_data(self):
        """边界：数据不足时返回 None。"""
        from app.analyzers.technical import calc_ma
        prices = [10, 11, 12]
        result = calc_ma(prices, period=5)
        assert result is None

    def test_calc_macd_basic(self):
        """正常路径：计算 MACD。"""
        from app.analyzers.technical import calc_macd
        prices = [float(i) for i in range(50)]
        result = calc_macd(prices)
        assert result is not None
        assert "dif" in result
        assert "dea" in result
        assert "histogram" in result

    def test_calc_macd_insufficient_data(self):
        """边界：数据不足时返回 None。"""
        from app.analyzers.technical import calc_macd
        prices = [1, 2, 3]
        result = calc_macd(prices, fast=12, slow=26, signal=9)
        assert result is None

    def test_calc_rsi_basic(self):
        """正常路径：计算 RSI(14)。"""
        from app.analyzers.technical import calc_rsi
        prices = [float(i) for i in range(30)]
        result = calc_rsi(prices, period=14)
        assert result is not None
        assert 0 <= result <= 100

    def test_calc_rsi_insufficient_data(self):
        """边界：数据不足时返回 None。"""
        from app.analyzers.technical import calc_rsi
        prices = [1, 2, 3]
        result = calc_rsi(prices, period=14)
        assert result is None

    def test_calc_rsi_all_up(self):
        """边界：持续上涨时 RSI=100。"""
        from app.analyzers.technical import calc_rsi
        prices = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25]
        result = calc_rsi(prices, period=14)
        assert result == 100.0

    def test_calc_kdj_basic(self):
        """正常路径：计算 KDJ。"""
        from app.analyzers.technical import calc_kdj
        highs = [float(i) for i in range(30, 60)]
        lows = [float(i) for i in range(10, 40)]
        closes = [float(i) for i in range(20, 50)]

        result = calc_kdj(highs, lows, closes, period=9)
        assert result is not None
        assert "k" in result
        assert "d" in result
        assert "j" in result

    def test_calc_kdj_insufficient_data(self):
        """边界：数据不足时返回 None。"""
        from app.analyzers.technical import calc_kdj
        result = calc_kdj([1, 2], [1, 2], [1, 2], period=9)
        assert result is None

    def test_calc_boll_basic(self):
        """正常路径：计算 BOLL。"""
        from app.analyzers.technical import calc_boll
        prices = [float(i) for i in range(30)]
        result = calc_boll(prices, period=20)
        assert result is not None
        assert "middle" in result
        assert "upper" in result
        assert "lower" in result
        assert result["upper"] >= result["middle"] >= result["lower"]

    def test_calc_boll_insufficient_data(self):
        """边界：数据不足时返回 None。"""
        from app.analyzers.technical import calc_boll
        prices = [1, 2, 3]
        result = calc_boll(prices, period=20)
        assert result is None

    def test_calc_all_indicators(self):
        """正常路径：综合计算所有指标。"""
        from app.analyzers.technical import calc_all_indicators
        closes = [float(i) for i in range(100)]
        highs = [float(i + 5) for i in range(100)]
        lows = [float(i - 5) for i in range(100)]

        result = calc_all_indicators(closes, highs, lows)
        assert "ma_5" in result
        assert "ma_10" in result
        assert "ma_20" in result
        assert "macd" in result
        assert "rsi_14" in result
        assert "kdj" in result
        assert "boll" in result
