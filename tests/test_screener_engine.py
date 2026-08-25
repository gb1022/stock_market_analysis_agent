"""选股引擎测试：策略 / 扫描。"""


class TestScreenerStrategies:
    """预设策略测试套件"""

    def test_value_strategy(self):
        """正常路径：价值选股策略包含 4 个条件。"""
        from app.screener.strategies import value_strategy
        cg = value_strategy()
        assert len(cg) == 4

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

    def test_get_strategy_by_name(self):
        """正常路径：按名称获取策略。"""
        from app.screener.strategies import get_strategy
        cg = get_strategy("value")
        assert len(cg) == 4

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
