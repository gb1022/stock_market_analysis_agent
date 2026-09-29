# -*- coding: utf-8 -*-
"""选股意见生成测试（v2.12.0）。

覆盖：
- 意见生成正常路径（Mock LLM 返回合法 JSON）
- LLM 调用失败时 opinion 为空但选股结果正常
- LLM 返回 JSON 缺少某只股票时该股票 opinion 为空
- 提示词包含防虚构规则
- ScreenResult 新增行情字段透传
"""

import json
from unittest.mock import MagicMock, patch


class TestScreenResultNewFields:
    """ScreenResult 新增行情字段透传测试"""

    def test_scan_with_data_returns_pe_ttm(self):
        """正常路径：scan_with_data 返回结果包含 pe_ttm 字段。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=30))

        stock_data = [
            {"code": "000001", "name": "平安", "pe_ttm": 10, "pb": 1.2,
             "turnover_rate": 2.5, "market_cap": 3000e8,
             "change_percent": 1.5, "amount": 5e9, "volume": 100000, "amplitude": 3.2},
        ]

        results = engine.scan_with_data(cg, stock_data, top_n=10)
        assert len(results) == 1
        r = results[0]
        assert r.pe_ttm == 10
        assert r.pb == 1.2
        assert r.turnover_rate == 2.5
        assert r.market_cap == 3000e8
        assert r.volume == 100000
        assert r.amplitude == 3.2

    def test_scan_with_data_missing_fields_none(self):
        """边界：数据中缺失的字段在结果中为 None。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        from app.screener.engine import ScreenerEngine

        engine = ScreenerEngine()
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="<", value=30))

        stock_data = [
            {"code": "000001", "name": "平安", "pe_ttm": 10},
        ]

        results = engine.scan_with_data(cg, stock_data, top_n=10)
        assert len(results) == 1
        r = results[0]
        assert r.pe_ttm == 10
        assert r.pb is None
        assert r.turnover_rate is None
        assert r.market_cap is None
        assert r.volume is None
        assert r.amplitude is None

    def test_screen_result_has_opinion_field(self):
        """正常路径：ScreenResult 模型包含 opinion 字段，默认为空字符串。"""
        from app.models.screener import ScreenResult

        r = ScreenResult(code="000001", name="平安")
        assert r.opinion == ""


class TestOpinionGeneration:
    """批量 LLM 意见生成测试"""

    def _make_results(self):
        """构造测试用 ScreenResult 列表。"""
        from app.models.screener import ScreenResult
        return [
            ScreenResult(
                code="000001", name="平安银行", score=1.0,
                matched_conditions=3, total_conditions=3,
                reason="PE 10 低估 + PB 1.2 低估",
                change_percent=1.5, amount=5e9,
                pe_ttm=10, pb=1.2, turnover_rate=2.5,
                market_cap=3000e8, volume=100000, amplitude=3.2,
                final_score=85.0,
            ),
            ScreenResult(
                code="600519", name="贵州茅台", score=0.67,
                matched_conditions=2, total_conditions=3,
                reason="PE 25 + 换手率 1.5%",
                change_percent=-0.8, amount=8e9,
                pe_ttm=25, pb=8.5, turnover_rate=1.5,
                market_cap=20000e8, volume=50000, amplitude=2.1,
                final_score=72.0,
            ),
        ]

    def test_opinion_normal(self):
        """正常路径：Mock LLM 返回合法 JSON，各股票 opinion 正确回填。"""
        from app.screener.opinion import generate_opinions

        llm_response = json.dumps([
            {"code": "000001", "opinion": "估值偏低，基本面稳健，适合中长期配置。"},
            {"code": "600519", "opinion": "品牌护城河深，短期回调提供布局机会。"},
        ], ensure_ascii=False)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = llm_response

        results = self._make_results()
        generate_opinions(results, mock_provider)

        assert results[0].opinion == "估值偏低，基本面稳健，适合中长期配置。"
        assert results[1].opinion == "品牌护城河深，短期回调提供布局机会。"

    def test_opinion_llm_failure(self):
        """边界：LLM 调用抛异常时，opinion 为空但结果列表不受影响。"""
        from app.screener.opinion import generate_opinions

        mock_provider = MagicMock()
        mock_provider.invoke.side_effect = Exception("LLM 服务不可用")

        results = self._make_results()
        generate_opinions(results, mock_provider)

        # 不抛异常，opinion 保持为空
        assert results[0].opinion == ""
        assert results[1].opinion == ""
        assert len(results) == 2

    def test_opinion_partial_missing(self):
        """边界：LLM 返回的 JSON 缺少某只股票，该股票 opinion 为空。"""
        from app.screener.opinion import generate_opinions

        # 只返回第一只股票的意见
        llm_response = json.dumps([
            {"code": "000001", "opinion": "估值偏低，适合配置。"},
        ], ensure_ascii=False)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = llm_response

        results = self._make_results()
        generate_opinions(results, mock_provider)

        assert results[0].opinion == "估值偏低，适合配置。"
        assert results[1].opinion == ""

    def test_opinion_invalid_json(self):
        """边界：LLM 返回非法 JSON 时，opinion 为空但不报错。"""
        from app.screener.opinion import generate_opinions

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = "这不是JSON内容"

        results = self._make_results()
        generate_opinions(results, mock_provider)

        assert results[0].opinion == ""
        assert results[1].opinion == ""

    def test_opinion_empty_results(self):
        """边界：空结果列表调用不报错。"""
        from app.screener.opinion import generate_opinions

        mock_provider = MagicMock()
        generate_opinions([], mock_provider)
        # 空列表不应调用 LLM
        mock_provider.invoke.assert_not_called()

    def test_opinion_prompt_contains_anti_fabrication(self):
        """正常路径：提示词包含防虚构规则。"""
        from app.screener.opinion import generate_opinions

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = "[]"

        results = self._make_results()
        generate_opinions(results, mock_provider)

        # 验证调用时传入的提示词包含防虚构相关约束
        call_args = mock_provider.invoke.call_args
        system_prompt = call_args[0][0] if call_args[0] else call_args[1].get("system_prompt", "")
        user_prompt = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("user_prompt", "")
        combined = system_prompt + user_prompt
        assert "不得编造" in combined or "仅基于" in combined or "虚构" in combined

    def test_opinion_prompt_contains_stock_data(self):
        """正常路径：提示词中包含股票的关键数据。"""
        from app.screener.opinion import generate_opinions

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = "[]"

        results = self._make_results()
        generate_opinions(results, mock_provider)

        call_args = mock_provider.invoke.call_args
        user_prompt = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("user_prompt", "")
        # 验证包含股票代码和名称
        assert "000001" in user_prompt
        assert "平安银行" in user_prompt
        assert "600519" in user_prompt

    def test_opinion_truncated_to_100_chars(self):
        """边界：LLM 返回超过 100 字的意见时截断到 100 字。"""
        from app.screener.opinion import generate_opinions

        long_opinion = "这是一段非常长的意见" * 20  # 超过 100 字
        llm_response = json.dumps([
            {"code": "000001", "opinion": long_opinion},
            {"code": "600519", "opinion": "短意见"},
        ], ensure_ascii=False)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = llm_response

        results = self._make_results()
        generate_opinions(results, mock_provider)

        assert len(results[0].opinion) <= 100
        assert results[1].opinion == "短意见"

    def test_opinion_markdown_wrapped_json(self):
        """边界：LLM 返回 markdown 代码块包裹的 JSON 也能正确解析。"""
        from app.screener.opinion import generate_opinions

        llm_response = '```json\n[{"code": "000001", "opinion": "测试意见"}]\n```'

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = llm_response

        results = self._make_results()
        generate_opinions(results, mock_provider)

        assert results[0].opinion == "测试意见"
