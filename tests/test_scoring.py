"""多因子综合评分测试：归一化 / 加权求和 / 权重配置（v2.8.0）。"""

import pytest


class TestNormalizeFactor:
    """归一化函数测试套件"""

    def test_missing_returns_neutral(self):
        """边界：值为 None 时返回中性分 50。"""
        from app.screener.scoring import normalize_factor, NEUTRAL_SCORE
        assert normalize_factor(None, 0, 100) == NEUTRAL_SCORE

    def test_higher_is_better_midpoint(self):
        """正常路径：越高越好方向，值在中点得 50 分。"""
        from app.screener.scoring import normalize_factor
        assert normalize_factor(50, 0, 100, higher_is_better=True) == 50.0

    def test_lower_is_better(self):
        """正常路径：越低越好方向，低值得高分、高值得低分。"""
        from app.screener.scoring import normalize_factor
        assert normalize_factor(0, 0, 100, higher_is_better=False) == 100.0
        assert normalize_factor(100, 0, 100, higher_is_better=False) == 0.0

    def test_clamp_bounds(self):
        """边界：超出 [p_low, p_high] 时截断到 0 或 100。"""
        from app.screener.scoring import normalize_factor
        assert normalize_factor(200, 0, 100, higher_is_better=True) == 100.0
        assert normalize_factor(-50, 0, 100, higher_is_better=True) == 0.0

    def test_invalid_range_returns_neutral(self):
        """边界：p_high <= p_low 时返回中性分。"""
        from app.screener.scoring import normalize_factor, NEUTRAL_SCORE
        assert normalize_factor(50, 100, 100) == NEUTRAL_SCORE


class TestScoreStock:
    """多因子加权评分测试套件"""

    def _market_stats(self) -> dict:
        """构造全市场分位数边界。"""
        return {
            "pe_ttm": {"p_low": 0, "p_high": 60},
            "pb": {"p_low": 0, "p_high": 10},
            "change_percent": {"p_low": -10, "p_high": 10},
            "turnover_rate": {"p_low": 0, "p_high": 20},
            "market_cap": {"p_low": 1e9, "p_high": 1e11},
        }

    def test_weighted_sum(self):
        """正常路径：全因子满分为 100，加权求和结果正确。"""
        from app.screener.scoring import score_stock
        data = {
            "condition_fit": 1.0,
            "pe_ttm": 0,
            "pb": 0,
            "change_percent": 10,
            "turnover_rate": 20,
            "market_cap": 1e11,
        }
        final, detail = score_stock(data, self._market_stats())
        assert final == pytest.approx(100.0)
        assert detail["condition_fit"] == pytest.approx(100.0)
        assert detail["valuation"] == pytest.approx(100.0)
        assert detail["momentum"] == pytest.approx(100.0)
        assert detail["activity"] == pytest.approx(100.0)
        assert detail["scale"] == pytest.approx(100.0)

    def test_neutral_when_missing(self):
        """边界：pe/pb 缺失时估值取中性 50，不抛异常。"""
        from app.screener.scoring import score_stock
        data = {
            "condition_fit": 1.0,
            "change_percent": 10,
            "turnover_rate": 20,
            "market_cap": 1e11,
        }
        final, detail = score_stock(data, self._market_stats())
        assert detail["valuation"] == pytest.approx(50.0)

    def test_negative_pe_returns_neutral(self):
        """边界：pe 为负（亏损股）时估值取中性 50，不抛异常。"""
        from app.screener.scoring import score_stock
        data = {
            "condition_fit": 1.0,
            "pe_ttm": -5,
            "pb": 1,
            "change_percent": 0,
            "turnover_rate": 1,
            "market_cap": 1e9,
        }
        final, detail = score_stock(data, self._market_stats())
        # pe=-5 取中性 50，pb=1 归一化 90，估值 = (50+90)/2 = 70
        assert detail["valuation"] == pytest.approx(70.0)


class TestFactorWeights:
    """权重配置测试套件"""

    def test_default_weights_sum_to_one(self):
        """边界：默认权重之和为 1。"""
        from app.screener.scoring import DEFAULT_FACTOR_WEIGHTS
        assert sum(DEFAULT_FACTOR_WEIGHTS.values()) == pytest.approx(1.0)

    def test_load_weights_from_config(self):
        """正常路径：从配置 dict 读取自定义权重。"""
        from app.screener.scoring import load_factor_weights
        custom = {
            "condition_fit": 0.4,
            "valuation": 0.3,
            "momentum": 0.15,
            "activity": 0.1,
            "scale": 0.05,
        }
        weights = load_factor_weights({"screener": {"factor_weights": custom}})
        assert weights == custom

    def test_load_weights_default_when_missing(self):
        """边界：配置缺失 factor_weights 时返回默认权重。"""
        from app.screener.scoring import load_factor_weights, DEFAULT_FACTOR_WEIGHTS
        weights = load_factor_weights({"screener": {}})
        assert weights == DEFAULT_FACTOR_WEIGHTS


class TestScoreDifferentiation:
    """评分区分度测试套件（v2.12.1 补充）。

    背景：用户反馈"推荐的都是 100 分"。根因是前端评分列误显示条件命中率
    （matched/total*100）而非多因子综合评分 final_score，且排序第一优先级
    为命中率，满分命中股票全部置顶。本套件验证 final_score 本身具备区分度：
    命中率（condition_fit）不同时综合评分应不同，而非恒为 100。
    """

    def _market_stats(self) -> dict:
        """构造全市场分位数边界。"""
        return {
            "pe_ttm": {"p_low": 0, "p_high": 60},
            "pb": {"p_low": 0, "p_high": 10},
            "change_percent": {"p_low": -10, "p_high": 10},
            "turnover_rate": {"p_low": 0, "p_high": 20},
            "market_cap": {"p_low": 1e9, "p_high": 1e11},
        }

    def _perfect_factors(self, condition_fit: float) -> dict:
        """构造除 condition_fit 外其余因子均满分的数据。"""
        return {
            "condition_fit": condition_fit,
            "pe_ttm": 0,           # 估值满分（越低越好）
            "pb": 0,
            "change_percent": 10,  # 动量满分（越高越好）
            "turnover_rate": 20,   # 活跃度满分
            "market_cap": 1e11,    # 规模满分
        }

    def test_full_condition_fit_full_score(self):
        """正常路径：命中率 1.0 且其余因子满分时综合评分为 100。"""
        from app.screener.scoring import score_stock
        final, detail = score_stock(self._perfect_factors(1.0), self._market_stats())
        assert final == pytest.approx(100.0)

    def test_partial_condition_fit_reduces_score(self):
        """正常路径：命中率 0.5 时综合评分为 85，而非 100（区分度来源）。

        计算：0.30*50 + 0.30*100 + 0.20*100 + 0.15*100 + 0.05*100 = 85
        """
        from app.screener.scoring import score_stock
        final, detail = score_stock(self._perfect_factors(0.5), self._market_stats())
        assert detail["condition_fit"] == pytest.approx(50.0)
        assert final == pytest.approx(85.0)

    def test_different_condition_fit_different_scores(self):
        """正常路径：命中率不同的两只股票综合评分不同（不会都是 100）。"""
        from app.screener.scoring import score_stock
        stats = self._market_stats()
        full, _ = score_stock(self._perfect_factors(1.0), stats)
        partial, _ = score_stock(self._perfect_factors(0.5), stats)
        assert full != partial
        assert full > partial

    def test_condition_fit_weight_isolation(self):
        """正常路径：仅命中率变化时，评分差值 = 命中率差 * 权重 0.30 * 100。"""
        from app.screener.scoring import score_stock
        stats = self._market_stats()
        score_high, _ = score_stock(self._perfect_factors(1.0), stats)
        score_low, _ = score_stock(self._perfect_factors(0.6), stats)
        # 命中率差 0.4，权重 0.30 → 评分差 0.4 * 0.30 * 100 = 12
        assert score_high - score_low == pytest.approx(12.0)

    def test_all_midpoint_gives_50(self):
        """正常路径：所有因子均处中点时综合评分恰为 50。"""
        from app.screener.scoring import score_stock
        data = {
            "condition_fit": 0.5,     # 50 分
            "pe_ttm": 30,             # 估值中点（0~60）→ 50
            "pb": 5,                  # 估值中点（0~10）→ 50
            "change_percent": 0,      # 动量中点（-10~10）→ 50
            "turnover_rate": 10,      # 活跃度中点（0~20）→ 50
            "market_cap": 5.05e10,    # 规模中点（1e9~1e11）→ 50
        }
        final, detail = score_stock(data, self._market_stats())
        assert final == pytest.approx(50.0)

    def test_zero_condition_fit_not_100(self):
        """边界：命中率 0 时综合评分低于 100（其余因子满分也仅 70）。"""
        from app.screener.scoring import score_stock
        final, _ = score_stock(self._perfect_factors(0.0), self._market_stats())
        # 0.30*0 + 0.30*100 + 0.20*100 + 0.15*100 + 0.05*100 = 70
        assert final == pytest.approx(70.0)
        assert final < 100.0
