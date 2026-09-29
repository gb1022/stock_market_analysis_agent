"""选股引擎测试：策略 / 扫描。"""


class TestScreenerStrategies:
    """预设策略测试套件"""

    def test_value_strategy(self):
        """正常路径：价值选股策略包含 3 个条件（PE/PB 粗筛 + ROE 精排）。"""
        from app.screener.strategies import value_strategy
        cg = value_strategy()
        assert len(cg) == 3

    def test_value_strategy_uses_roe(self):
        """正常路径：价值策略用真实 ROE > 12 替换市值+换手率。"""
        from app.screener.strategies import value_strategy
        cg = value_strategy()
        fields = {c.field for c in cg.conditions}
        assert "roe" in fields
        assert "market_cap" not in fields
        assert "turnover_rate" not in fields
        roe_cond = next(c for c in cg.conditions if c.field == "roe")
        assert roe_cond.op == ">"
        assert roe_cond.value == 12
        assert roe_cond.required is True

    def test_momentum_strategy(self):
        """正常路径：动量选股策略包含 3 个条件。"""
        from app.screener.strategies import momentum_strategy
        cg = momentum_strategy()
        assert len(cg) == 3

    def test_oversold_rebound_strategy(self):
        """正常路径：超跌反弹策略包含 3 个条件。"""
        from app.screener.strategies import oversold_rebound_strategy
        cg = oversold_rebound_strategy()
        assert len(cg) == 3

    def test_oversold_strategy_uses_rsi24(self):
        """正常路径：超跌策略用真实 RSI24 < 30 替换振幅。"""
        from app.screener.strategies import oversold_rebound_strategy
        cg = oversold_rebound_strategy()
        fields = {c.field for c in cg.conditions}
        assert "rsi24" in fields
        assert "amplitude" not in fields
        rsi_cond = next(c for c in cg.conditions if c.field == "rsi24")
        assert rsi_cond.op == "<"
        assert rsi_cond.value == 30

    def test_get_strategy_by_name(self):
        """正常路径：按名称获取策略。"""
        from app.screener.strategies import get_strategy
        cg = get_strategy("value")
        assert len(cg) == 3

    def test_get_strategy_invalid_name(self):
        """边界：不存在的策略名称抛出 ValueError。"""
        from app.screener.strategies import get_strategy
        try:
            get_strategy("not_exist")
            assert False, "应抛出 ValueError"
        except ValueError:
            pass

    def test_strategy_map_keys(self):
        """正常路径：STRATEGY_MAP 包含全部策略。"""
        from app.screener.strategies import STRATEGY_MAP
        assert "value" in STRATEGY_MAP
        assert "momentum" in STRATEGY_MAP
        assert "oversold_rebound" in STRATEGY_MAP


class TestScreenerEngine:
    """选股引擎测试套件"""

    def test_scan_empty(self):
        """边界：所有数据源均失败时扫描返回空列表。"""
        from unittest.mock import patch

        from app.screener.engine import ScreenerEngine
        from app.screener.strategies import value_strategy

        engine = ScreenerEngine()
        with patch.object(engine, "_fetch_via_akshare", return_value=[]), \
             patch.object(engine, "_fetch_via_eastmoney", return_value=[]), \
             patch.object(engine, "_fetch_via_tencent", return_value=[]):
            results = engine.scan(value_strategy(), top_n=10)
        assert isinstance(results, list)
        assert len(results) == 0  # 所有数据源失败时为空

    def test_scan_with_data(self):
        """正常路径：用已有数据扫描。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="between", value=[0, 30]))

        stock_data = [
            {"code": "600519", "name": "茅台", "pe_ttm": 32, "pb": 9.8},
            {"code": "000001", "name": "平安", "pe_ttm": 10, "pb": 1.2},
            {"code": "002415", "name": "海康", "pe_ttm": 25, "pb": 4.5},
        ]

        results = engine.scan_with_data(cg, stock_data, top_n=2)
        assert len(results) == 2  # 平安 + 海康
        assert results[0].code == "000001"
        assert results[0].matched_conditions == 1

    def test_scan_with_data_no_match(self):
        """边界：无匹配时返回空列表。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=0))

        stock_data = [
            {"code": "600519", "pe_ttm": 32},
            {"code": "000001", "pe_ttm": 10},
        ]

        results = engine.scan_with_data(cg, stock_data, top_n=10)
        assert len(results) == 0


class TestScanWithDataPartialMatch:
    """部分匹配 / 动态补齐 / 硬软条件测试套件（新增于 2026-08-25, v2.7.0）"""

    def test_partial_match_included(self):
        """正常路径：硬条件满足、软条件部分命中的股票入选，score 按命中率计算。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=30, required=True))
        cg.add(FieldCondition(field="pb", op="<", value=5, required=False))

        stock_data = [
            {"code": "600519", "name": "茅台", "pe_ttm": 32, "pb": 9.8, "change_percent": 1.0},
            {"code": "000001", "name": "平安", "pe_ttm": 10, "pb": 6.0, "change_percent": 2.0},
            {"code": "002415", "name": "海康", "pe_ttm": 25, "pb": 4.5, "change_percent": 3.0},
        ]

        results = engine.scan_with_data(cg, stock_data, top_n=10)
        # 海康软条件全命中（score=1.0），平安软条件未命中（score=0.5，部分匹配），茅台硬条件不满足（过滤）
        assert len(results) == 2
        assert results[0].code == "002415"
        assert results[0].score == 1.0
        assert results[0].is_partial is False
        assert results[1].code == "000001"
        assert results[1].score == 0.5
        assert results[1].is_partial is True

    def test_zero_match_filtered(self):
        """边界：命中 0 个条件的股票被过滤。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=0))

        stock_data = [
            {"code": "600519", "pe_ttm": 32},
            {"code": "000001", "pe_ttm": 10},
        ]

        results = engine.scan_with_data(cg, stock_data, top_n=10)
        assert len(results) == 0

    def test_partial_fill_topn(self):
        """正常路径：全匹配不足 top_n 时，部分匹配按 score 降序补齐。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=30, required=True))
        cg.add(FieldCondition(field="pb", op="<", value=5, required=False))

        stock_data = [
            {"code": "000001", "name": "平安", "pe_ttm": 10, "pb": 1.2, "change_percent": 1.0},
            {"code": "000002", "name": "万科", "pe_ttm": 20, "pb": 6.0, "change_percent": 2.0},
            {"code": "000003", "name": "招商", "pe_ttm": 25, "pb": 4.5, "change_percent": 3.0},
        ]

        results = engine.scan_with_data(cg, stock_data, top_n=3)
        assert len(results) == 3
        # 全匹配两只排前，部分匹配补齐
        assert results[0].is_partial is False
        assert results[1].is_partial is False
        assert results[2].is_partial is True
        assert results[2].code == "000002"

    def test_hard_condition_required(self):
        """边界：硬条件（required=True）不满足时，股票被排除。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=30, required=True))
        cg.add(FieldCondition(field="pb", op="<", value=5, required=False))

        stock_data = [
            {"code": "000001", "name": "平安", "pe_ttm": 35, "pb": 1.2},
            {"code": "000002", "name": "万科", "pe_ttm": 20, "pb": 1.0},
        ]

        results = engine.scan_with_data(cg, stock_data, top_n=10)
        # 平安硬条件 pe<30 不满足，被排除
        assert len(results) == 1
        assert results[0].code == "000002"

    def test_soft_condition_bonus(self):
        """正常路径：软条件（required=False）不满足时不排除，仅影响 score。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=30, required=True))
        cg.add(FieldCondition(field="pb", op="<", value=5, required=False))

        stock_data = [
            {"code": "000001", "name": "平安", "pe_ttm": 20, "pb": 6.0},
            {"code": "000002", "name": "万科", "pe_ttm": 25, "pb": 2.0},
        ]

        results = engine.scan_with_data(cg, stock_data, top_n=10)
        # 平安软条件 pb 不满足但不排除（score=0.5），万科全满足（score=1.0）
        assert len(results) == 2
        assert results[0].code == "000002"
        assert results[0].score == 1.0
        assert results[1].code == "000001"
        assert results[1].score == 0.5
        assert results[1].is_partial is True

    def test_score_priority_over_final_score(self):
        """边界（v2.8.0）：命中率（score）优先于 final_score，部分匹配不因高因子分越级。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=30, required=True))
        cg.add(FieldCondition(field="pb", op="<", value=5, required=False))

        stock_data = [
            # 全匹配（score=1.0）但估值/动量/活跃/规模均较差，final_score 较低
            {"code": "000001", "name": "全匹配低质", "pe_ttm": 28, "pb": 4.9,
             "change_percent": -8, "turnover_rate": 0.1, "market_cap": 1e8},
            # 部分匹配（pb 软条件不满足，score=0.5）但其余因子极佳，final_score 更高
            # 注：涨跌幅取 9.5（低于主板涨停阈值 9.8），避免被涨停过滤（v2.11.0）
            {"code": "000002", "name": "部分匹配高质", "pe_ttm": 5, "pb": 9,
             "change_percent": 9.5, "turnover_rate": 25, "market_cap": 1e12},
        ]

        results = engine.scan_with_data(cg, stock_data, top_n=10)
        # 全匹配（score=1.0）优先于部分匹配（score=0.5），即使后者 final_score 更高
        assert len(results) == 2
        assert results[0].code == "000001"
        assert results[0].score == 1.0
        assert results[1].code == "000002"
        assert results[1].score == 0.5


class TestScanWithDataMultiFactor:
    """多因子评分引擎集成测试套件（新增于 2026-08-25, v2.8.0）"""

    def test_result_has_final_score_and_factor_detail(self):
        """正常路径：结果包含 final_score（0~100）和 factor_detail。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=30))

        stock_data = [
            {"code": "000001", "name": "平安", "pe_ttm": 10, "pb": 1.0,
             "change_percent": 2.0, "turnover_rate": 3.0, "market_cap": 5e10},
            {"code": "000002", "name": "万科", "pe_ttm": 20, "pb": 2.0,
             "change_percent": 1.0, "turnover_rate": 2.0, "market_cap": 2e10},
        ]

        results = engine.scan_with_data(cg, stock_data, top_n=10)
        assert len(results) == 2
        for r in results:
            assert r.final_score is not None
            assert 0 <= r.final_score <= 100
            assert isinstance(r.factor_detail, dict)
            assert "valuation" in r.factor_detail

    def test_sort_prefers_higher_final_score_when_same_match(self):
        """正常路径：同命中率（全匹配）时，final_score 高者排前。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=30))

        stock_data = [
            # 背景股票（pe >= 30 不匹配，提供分位数参考）
            {"code": "000001", "name": "背景1", "pe_ttm": 40, "pb": 5, "change_percent": 0, "turnover_rate": 2, "market_cap": 1e10},
            {"code": "000002", "name": "背景2", "pe_ttm": 50, "pb": 6, "change_percent": 1, "turnover_rate": 3, "market_cap": 2e10},
            {"code": "000003", "name": "背景3", "pe_ttm": 60, "pb": 7, "change_percent": 2, "turnover_rate": 4, "market_cap": 3e10},
            # 全匹配股票
            {"code": "000004", "name": "高估值", "pe_ttm": 28, "pb": 4, "change_percent": -5, "turnover_rate": 0.5, "market_cap": 1e9},
            {"code": "000005", "name": "低估值", "pe_ttm": 5, "pb": 1, "change_percent": 8, "turnover_rate": 15, "market_cap": 5e10},
        ]

        results = engine.scan_with_data(cg, stock_data, top_n=10)
        # 000004、000005 都全匹配（score=1.0），低估值 000005 的 final_score 应更高
        assert len(results) == 2
        assert results[0].code == "000005"
        assert results[0].final_score > results[1].final_score


class FakeRouter:
    """Mock 数据源路由器，用于 rank_candidates 精排测试。"""

    def __init__(self, financials=None, klines=None, financial_error_codes=None):
        self.financials = financials or {}
        self.klines = klines or {}
        self.financial_error_codes = set(financial_error_codes or [])
        self.financial_calls = []

    def get_financial(self, code):
        self.financial_calls.append(code)
        if code in self.financial_error_codes:
            from app.data_sources.base import DataSourceError
            raise DataSourceError(f"{code} 财务拉取失败")
        return self.financials.get(code)

    def get_kline(self, code):
        return self.klines.get(code)


class TestRankCandidates:
    """阶段二精排 rank_candidates 测试套件（新增于 2026-08-25, v2.9.0）"""

    @staticmethod
    def _candidate(code):
        from app.models.screener import ScreenResult
        return ScreenResult(code=code, name=code, score=1.0,
                            matched_conditions=2, total_conditions=2)

    @staticmethod
    def _roe_conditions():
        from app.screener.conditions import ConditionGroup, FieldCondition
        cg = ConditionGroup()
        cg.add(FieldCondition(field="roe", op=">", value=12))
        return cg

    def test_rank_filters_by_roe(self):
        """正常路径：ROE 硬条件过滤，不满足的候选被剔除。"""
        from app.models.stock import FinancialData
        router = FakeRouter(financials={
            "000001": FinancialData(code="000001", roe=15),
            "000002": FinancialData(code="000002", roe=5),
            "000003": FinancialData(code="000003", roe=20),
        })
        from app.screener.engine import ScreenerEngine
        engine = ScreenerEngine()
        candidates = [self._candidate(c) for c in ["000001", "000002", "000003"]]
        results = engine.rank_candidates(candidates, self._roe_conditions(), router=router)
        assert [r.code for r in results] == ["000001", "000003"]

    def test_rank_skips_missing_financial(self):
        """边界：某股财务拉取失败时降级保留，不阻塞精排。"""
        from app.models.stock import FinancialData
        router = FakeRouter(
            financials={"000002": FinancialData(code="000002", roe=20)},
            financial_error_codes=["000001"],
        )
        from app.screener.engine import ScreenerEngine
        engine = ScreenerEngine()
        candidates = [self._candidate(c) for c in ["000001", "000002"]]
        results = engine.rank_candidates(candidates, self._roe_conditions(), router=router)
        assert [r.code for r in results] == ["000001", "000002"]

    def test_rank_returns_top_n(self):
        """正常路径：精排后按 top_n 截断。"""
        from app.models.stock import FinancialData
        router = FakeRouter(financials={
            f"00000{i}": FinancialData(code=f"00000{i}", roe=15) for i in range(1, 6)
        })
        from app.screener.engine import ScreenerEngine
        engine = ScreenerEngine()
        candidates = [self._candidate(f"00000{i}") for i in range(1, 6)]
        results = engine.rank_candidates(candidates, self._roe_conditions(), router=router, top_n=2)
        assert len(results) == 2

    def test_rank_no_rank_fields_unchanged(self):
        """边界：无条件涉及精排字段时，原样返回且不拉取财务。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=30))
        router = FakeRouter()
        from app.screener.engine import ScreenerEngine
        engine = ScreenerEngine()
        candidates = [self._candidate(c) for c in ["000001", "000002", "000003"]]
        results = engine.rank_candidates(candidates, cg, router=router)
        assert len(results) == 3
        assert router.financial_calls == []

    def test_rank_filters_by_rsi24(self):
        """正常路径：RSI24 技术条件过滤，K 线指标缺失时降级保留。"""
        from datetime import date
        from app.models.stock import FinancialData, KLine
        from app.screener.conditions import ConditionGroup, FieldCondition
        cg = ConditionGroup()
        cg.add(FieldCondition(field="rsi24", op="<", value=30))
        router = FakeRouter(
            klines={
                "000001": [KLine(trade_date=date(2026, 8, 25), rsi24=20)],
                "000002": [KLine(trade_date=date(2026, 8, 25), rsi24=45)],
            },
            financials={},
        )
        from app.screener.engine import ScreenerEngine
        engine = ScreenerEngine()
        candidates = [self._candidate(c) for c in ["000001", "000002", "000003"]]
        results = engine.rank_candidates(candidates, cg, router=router)
        # 000001 rsi24=20 满足；000002 rsi24=45 不满足被剔除；000003 无 K 线降级保留
        assert [r.code for r in results] == ["000001", "000003"]
