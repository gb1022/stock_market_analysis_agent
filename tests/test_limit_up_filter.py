"""涨停过滤与排序兜底键修复测试（新增于 2026-08-26, v2.11.0）。

需求背景：
- 选股引擎使用实时行情快照，非交易时段快照中的涨跌幅实际为上一交易日数据，
  叠加"动量因子奖励高涨幅 + 排序兜底键为涨跌幅绝对值"，导致推荐结果被
  前一日涨停股霸榜。
- 方案 A：粗筛阶段过滤已涨停股票（可配置开关，默认开启）。
  涨停判定按板块阈值：主板/中小板 >= 9.8%，创业板(300/301)/科创板(688) >= 19.8%。
- 方案 B：排序兜底键从涨跌幅绝对值改为成交额，消除涨停股霸榜。
"""


class TestLimitUpFilter:
    """方案 A：涨停过滤（可配置开关，默认开启）"""

    @staticmethod
    def _pe_condition():
        from app.screener.conditions import ConditionGroup, FieldCondition
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=30))
        return cg

    def test_limit_up_stock_filtered_by_default(self):
        """正常路径：默认开启涨停过滤，主板涨停股（+10%）被排除。"""
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        stock_data = [
            {"code": "600001", "name": "涨停股", "pe_ttm": 10, "change_percent": 10.0, "amount": 5e8},
            {"code": "600002", "name": "普通股", "pe_ttm": 12, "change_percent": 5.0, "amount": 3e8},
        ]
        results = engine.scan_with_data(self._pe_condition(), stock_data, top_n=10)
        codes = [r.code for r in results]
        assert "600001" not in codes
        assert "600002" in codes

    def test_limit_up_20cm_filtered(self):
        """正常路径：创业板 20cm 涨停股（+20%）被过滤。"""
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        stock_data = [
            {"code": "300001", "name": "创板涨停", "pe_ttm": 10, "change_percent": 20.0, "amount": 5e8},
            {"code": "600002", "name": "普通股", "pe_ttm": 12, "change_percent": 5.0, "amount": 3e8},
        ]
        results = engine.scan_with_data(self._pe_condition(), stock_data, top_n=10)
        codes = [r.code for r in results]
        assert "300001" not in codes
        assert "600002" in codes

    def test_main_board_threshold(self):
        """边界：主板阈值 9.8%——恰好 9.8% 视为涨停过滤，9.79% 保留。"""
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        stock_data = [
            {"code": "600003", "name": "恰涨停", "pe_ttm": 10, "change_percent": 9.8, "amount": 5e8},
            {"code": "600004", "name": "未涨停", "pe_ttm": 12, "change_percent": 9.79, "amount": 3e8},
        ]
        results = engine.scan_with_data(self._pe_condition(), stock_data, top_n=10)
        codes = [r.code for r in results]
        assert "600003" not in codes
        assert "600004" in codes

    def test_gem_star_board_threshold(self):
        """边界：创业板/科创板阈值 19.8%——300/688 开头 19.8% 视为涨停。"""
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        stock_data = [
            {"code": "300002", "name": "创板恰涨停", "pe_ttm": 10, "change_percent": 19.8, "amount": 5e8},
            {"code": "688001", "name": "科创板恰涨停", "pe_ttm": 10, "change_percent": 19.8, "amount": 5e8},
            {"code": "300003", "name": "创板未涨停", "pe_ttm": 12, "change_percent": 19.79, "amount": 3e8},
        ]
        results = engine.scan_with_data(self._pe_condition(), stock_data, top_n=10)
        codes = [r.code for r in results]
        assert "300002" not in codes
        assert "688001" not in codes
        assert "300003" in codes

    def test_near_limit_up_kept(self):
        """边界：接近涨停但未达阈值（主板 9.7% / 创业板 19.7%）不过滤。"""
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        stock_data = [
            {"code": "600005", "name": "主板近涨停", "pe_ttm": 10, "change_percent": 9.7, "amount": 5e8},
            {"code": "300004", "name": "创板近涨停", "pe_ttm": 12, "change_percent": 19.7, "amount": 3e8},
        ]
        results = engine.scan_with_data(self._pe_condition(), stock_data, top_n=10)
        codes = [r.code for r in results]
        assert "600005" in codes
        assert "300004" in codes

    def test_all_limit_up_returns_empty(self):
        """边界：候选全部涨停时被过滤殆尽，返回空列表。"""
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        stock_data = [
            {"code": "600006", "name": "涨停1", "pe_ttm": 10, "change_percent": 10.0, "amount": 5e8},
            {"code": "300005", "name": "涨停2", "pe_ttm": 12, "change_percent": 20.0, "amount": 3e8},
        ]
        results = engine.scan_with_data(self._pe_condition(), stock_data, top_n=10)
        assert results == []

    def test_limit_up_filter_disabled_by_config(self):
        """正常路径：配置关闭涨停过滤（enabled=false）时涨停股保留。"""
        from unittest.mock import patch

        from app.screener.engine import ScreenerEngine

        with patch("app.screener.engine.load_config",
                   return_value={"screener": {"limit_up_filter": {"enabled": False}}}):
            engine = ScreenerEngine()
        stock_data = [
            {"code": "600001", "name": "涨停股", "pe_ttm": 10, "change_percent": 10.0, "amount": 5e8},
            {"code": "600002", "name": "普通股", "pe_ttm": 12, "change_percent": 5.0, "amount": 3e8},
        ]
        results = engine.scan_with_data(self._pe_condition(), stock_data, top_n=10)
        codes = [r.code for r in results]
        assert "600001" in codes
        assert "600002" in codes

    def test_default_enabled_from_config(self):
        """正常路径：配置未显式设置开关时默认开启（兜底为 True）。"""
        from unittest.mock import patch

        from app.screener.engine import ScreenerEngine

        # 空配置（无 limit_up_filter 键）时应默认开启
        with patch("app.screener.engine.load_config", return_value={}):
            engine = ScreenerEngine()
        assert engine.limit_up_filter_enabled is True

        # 真实配置文件中也应为开启状态
        engine2 = ScreenerEngine()
        assert engine2.limit_up_filter_enabled is True

    def test_limit_up_filter_not_affect_rank_candidates(self):
        """边界：涨停过滤仅作用于粗筛，精排 rank_candidates 行为不变。"""
        from app.models.screener import ScreenResult
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        # 候选含"涨停"股票，精排阶段不做涨停过滤（保持既有行为）
        candidates = [
            ScreenResult(code="600001", name="涨停股", score=1.0,
                         matched_conditions=1, total_conditions=1,
                         change_percent=10.0),
            ScreenResult(code="600002", name="普通股", score=1.0,
                         matched_conditions=1, total_conditions=1,
                         change_percent=5.0),
        ]
        results = engine.rank_candidates(candidates, self._pe_condition(), router=None, top_n=10)
        codes = [r.code for r in results]
        assert "600001" in codes
        assert "600002" in codes


class TestSortTieBreakFix:
    """方案 B：排序兜底键从涨跌幅绝对值改为成交额"""

    @staticmethod
    def _pe_condition():
        from app.screener.conditions import ConditionGroup, FieldCondition
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=30))
        return cg

    def test_tie_break_by_amount_not_abs_change(self):
        """正常路径：同分股票按成交额降序，而非涨跌幅绝对值。

        构造两只同分股票：涨停股（+10%）成交额低，普通股（+2%）成交额高。
        旧兜底键（涨跌幅绝对值）会排涨停股在前；新兜底键（成交额）应排普通股在前。
        本测试关闭涨停过滤，单独验证兜底排序键。
        """
        from unittest.mock import patch

        from app.screener.engine import ScreenerEngine

        # 关闭涨停过滤，让涨停股保留以参与兜底排序键比较
        with patch("app.screener.engine.load_config",
                   return_value={"screener": {"limit_up_filter": {"enabled": False}}}):
            engine = ScreenerEngine()
        stock_data = [
            {"code": "600010", "name": "涨停低成交", "pe_ttm": 10,
             "change_percent": 10.0, "amount": 1e8},
            {"code": "600011", "name": "普通高成交", "pe_ttm": 10,
             "change_percent": 2.0, "amount": 9e8},
        ]
        # 固定综合分相同，使排序进入兜底键比较
        fixed = (75.0, {"condition_fit": 100.0, "valuation": 50.0, "momentum": 50.0,
                        "activity": 50.0, "scale": 50.0})
        with patch("app.screener.engine.score_stock", return_value=fixed):
            results = engine.scan_with_data(self._pe_condition(), stock_data, top_n=10)
        assert [r.code for r in results] == ["600011", "600010"]

    def test_result_contains_amount(self):
        """正常路径：结果携带成交额字段，供兜底排序与前端展示使用。"""
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        stock_data = [
            {"code": "600012", "name": "测试股", "pe_ttm": 10,
             "change_percent": 2.0, "amount": 9e8},
        ]
        results = engine.scan_with_data(self._pe_condition(), stock_data, top_n=10)
        assert len(results) == 1
        assert results[0].amount == 9e8

    def test_high_amount_first_when_same_score(self):
        """正常路径：命中率与综合分相同时，成交额高者排前（即使对方涨停）。"""
        from unittest.mock import patch

        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        stock_data = [
            {"code": "600013", "name": "高成交", "pe_ttm": 10,
             "change_percent": -1.0, "amount": 9e8},
            {"code": "600014", "name": "涨停低成交", "pe_ttm": 10,
             "change_percent": 10.0, "amount": 1e8},
        ]
        fixed = (80.0, {"condition_fit": 100.0, "valuation": 50.0, "momentum": 50.0,
                        "activity": 50.0, "scale": 50.0})
        # 关闭涨停过滤，单独验证兜底排序键
        with patch("app.screener.engine.load_config",
                   return_value={"screener": {"limit_up_filter": {"enabled": False}}}):
            engine = ScreenerEngine()
        with patch("app.screener.engine.score_stock", return_value=fixed):
            results = engine.scan_with_data(self._pe_condition(), stock_data, top_n=10)
        assert results[0].code == "600013"
