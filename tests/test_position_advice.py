"""v2.5.0 持仓场景化投资建议测试。

覆盖：决策输出模型、Prompt 结构、报告渲染、user_input 合成。
"""

import pytest

from app.graph.models import DecisionOutput
from app.graph.prompts import DECISION_PROMPT, REPORT_PROMPT


class TestDecisionOutputModel:
    """决策输出模型：持仓场景化字段"""

    def test_has_position_status_field(self):
        """正常路径：DecisionOutput 包含 position_status 字段，默认未提及。"""
        d = DecisionOutput()
        assert hasattr(d, "position_status")
        assert d.position_status == "未提及"

    def test_has_not_holding_advice_field(self):
        """正常路径：DecisionOutput 包含未买入场景建议字段。"""
        d = DecisionOutput(not_holding_advice={"recommendation": "可买入"})
        assert d.not_holding_advice["recommendation"] == "可买入"

    def test_has_holding_advice_field(self):
        """正常路径：DecisionOutput 包含持仓场景建议字段。"""
        d = DecisionOutput(holding_advice={"recommendation": "继续持有"})
        assert d.holding_advice["recommendation"] == "继续持有"

    def test_position_status_invalid_value(self):
        """边界：position_status 传入非法值应抛校验异常。"""
        with pytest.raises(Exception):
            DecisionOutput(position_status="已卖出")


class TestPromptScenarioStructure:
    """Prompt 模板：场景化建议结构"""

    def test_decision_prompt_has_position_status(self):
        """正常路径：DECISION_PROMPT 包含 position_status 字段。"""
        assert "position_status" in DECISION_PROMPT

    def test_decision_prompt_has_not_holding_advice(self):
        """正常路径：DECISION_PROMPT 包含未买入场景建议字段。"""
        assert "not_holding_advice" in DECISION_PROMPT

    def test_decision_prompt_has_holding_advice(self):
        """正常路径：DECISION_PROMPT 包含持仓场景建议字段。"""
        assert "holding_advice" in DECISION_PROMPT

    def test_report_prompt_has_position_status(self):
        """正常路径：REPORT_PROMPT 的 investment_advice 包含 position_status。"""
        assert "position_status" in REPORT_PROMPT

    def test_report_prompt_has_not_holding_scenario(self):
        """正常路径：REPORT_PROMPT 包含未买入场景（建仓区间/仓位建议）。"""
        assert "not_holding" in REPORT_PROMPT
        assert "entry_price_range" in REPORT_PROMPT
        assert "position_suggestion" in REPORT_PROMPT
        assert "waiting_condition" in REPORT_PROMPT

    def test_report_prompt_has_holding_scenario(self):
        """正常路径：REPORT_PROMPT 包含持仓场景（止损位/止盈位）。"""
        assert "holding" in REPORT_PROMPT
        assert "cost_analysis" in REPORT_PROMPT
        assert "stop_loss_price" in REPORT_PROMPT
        assert "take_profit_price" in REPORT_PROMPT


class TestMarkdownRenderScenario:
    """报告渲染：按持仓场景分节渲染"""

    def _render(self, investment_advice: dict, sample_data_bundle) -> str:
        from app.services.markdown_renderer import render_markdown_report
        llm_analysis = {"investment_advice": investment_advice}
        return render_markdown_report({}, llm_analysis, sample_data_bundle)

    def test_render_not_holding_scenario(self, sample_data_bundle):
        """正常路径：position_status=未买入 时渲染未买入场景。"""
        report = self._render({
            "position_status": "未买入",
            "not_holding": {
                "recommendation": "可买入",
                "entry_price_range": "1800-1850元",
                "position_suggestion": "建议轻仓20%",
                "waiting_condition": "站稳MA20后再加仓",
            },
            "target_price": "2000-2100元",
            "timeframe": "中期",
        }, sample_data_bundle)
        assert "**持仓状态**: 未买入" in report
        assert "### 未买入场景" in report
        assert "**操作建议**: 可买入" in report
        assert "**建仓区间**: 1800-1850元" in report
        assert "**仓位建议**: 建议轻仓20%" in report
        assert "**观望触发条件**: 站稳MA20后再加仓" in report
        assert "### 已持仓场景" not in report

    def test_render_holding_scenario(self, sample_data_bundle):
        """正常路径：position_status=已持仓 时渲染持仓场景。"""
        report = self._render({
            "position_status": "已持仓",
            "holding": {
                "recommendation": "继续持有",
                "cost_analysis": "成本1500元，当前价1888元，浮盈约25.9%",
                "stop_loss_price": "1700元",
                "take_profit_price": "2100元",
                "action_detail": "跌破1700元止损，冲高至2100元可减仓",
            },
            "target_price": "2000-2100元",
            "timeframe": "中期",
        }, sample_data_bundle)
        assert "**持仓状态**: 已持仓" in report
        assert "### 已持仓场景" in report
        assert "**操作建议**: 继续持有" in report
        assert "**成本盈亏分析**: 成本1500元" in report
        assert "**止损位**: 1700元" in report
        assert "**止盈位**: 2100元" in report
        assert "**操作说明**: 跌破1700元止损" in report
        assert "### 未买入场景" not in report

    def test_render_both_scenarios_when_unknown(self, sample_data_bundle):
        """边界：position_status=未提及 时两个场景都渲染。"""
        report = self._render({
            "position_status": "未提及",
            "not_holding": {
                "recommendation": "暂缓买入",
                "entry_price_range": "1750-1800元",
                "position_suggestion": "建议轻仓10%",
                "waiting_condition": "放量突破1850元再介入",
            },
            "holding": {
                "recommendation": "减仓",
                "cost_analysis": "成本1600元，当前价1888元",
                "stop_loss_price": "1650元",
                "take_profit_price": "1950元",
                "action_detail": "建议在1900元上方分批减仓",
            },
        }, sample_data_bundle)
        assert "**持仓状态**: 未提及" in report
        assert "### 未买入场景" in report
        assert "### 已持仓场景" in report

    def test_render_legacy_recommendation_fallback(self, sample_data_bundle):
        """边界：无场景字段时回退渲染单值 recommendation。"""
        report = self._render({
            "recommendation": "观望",
            "target_price": "1800-1900元",
            "timeframe": "短期",
            "advice_detail": "当前观望",
        }, sample_data_bundle)
        assert "**操作建议**: 观望" in report
        assert "**目标价位**: 1800-1900元" in report
        assert "**投资周期**: 短期" in report


class TestComposeUserInput:
    """user_input 合成：持仓状态/成本价/持仓数量 + 关注点"""

    def _compose(self, user_input: str = "", position_status: str = "",
                 cost_price=None, position_quantity=None) -> str:
        from app.web.routes import _compose_user_input
        return _compose_user_input(user_input, position_status, cost_price, position_quantity)

    def test_holding_with_cost_price(self):
        """正常路径：已持仓+成本价 与关注点合并。"""
        text = self._compose("重点关注技术面", "已持仓", 1500.0)
        assert "已持仓" in text
        assert "成本价1500" in text
        assert "重点关注技术面" in text

    def test_holding_with_cost_price_and_quantity(self):
        """正常路径：已持仓+成本价+持仓数量 与关注点合并。"""
        text = self._compose("重点关注技术面", "已持仓", 1500.0, 500.0)
        assert "已持仓" in text
        assert "成本价1500" in text
        assert "持仓500股" in text
        assert "重点关注技术面" in text

    def test_holding_with_quantity_no_cost(self):
        """正常路径：已持仓+持仓数量 无成本价。"""
        text = self._compose("想了解何时加仓", "已持仓", None, 300.0)
        assert "已持仓" in text
        assert "持仓300股" in text
        assert "成本价" not in text

    def test_holding_with_cost_no_quantity(self):
        """正常路径：已持仓+成本价 无持仓数量。"""
        text = self._compose("关注风险", "已持仓", 1500.0, None)
        assert "成本价1500" in text
        assert "股" not in text  # 无持仓数量时不应出现数量表述

    def test_not_holding_without_cost(self):
        """正常路径：未买入 无成本价。"""
        text = self._compose("想了解建仓时机", "未买入", None)
        assert "未买入" in text
        assert "成本价" not in text
        assert "想了解建仓时机" in text

    def test_empty_all(self):
        """边界：全部为空返回空字符串。"""
        assert self._compose("", "", None) == ""
        assert self._compose() == ""

    def test_only_preference(self):
        """边界：只有关注点，无持仓信息。"""
        text = self._compose("长期投资", "", None)
        assert text == "长期投资"
