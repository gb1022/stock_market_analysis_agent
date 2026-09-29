"""v2.10.0 已分析股票侧边栏展示投资建议测试。

需求：主页"已分析过的股票"卡片信息区改为展示该股票的投资建议，
未买入与已持仓两个场景的建议都要展示（保留评分+风险徽章）。
数据来源为决策阶段 selected_plan 中的 recommendation /
not_holding_advice / holding_advice，随分析摘要一并缓存，
并由 /api/cached-stocks 返回给前端。
"""

import pytest


# ---- 公共样例数据 ----

SAMPLE_NOT_HOLDING = {
    "recommendation": "可买入",
    "entry_price_range": "10-12元建仓区间",
    "position_suggestion": "轻仓20%",
    "waiting_condition": "放量突破均线后动手",
}

SAMPLE_HOLDING = {
    "recommendation": "继续持有",
    "cost_analysis": "当前价相对成本价浮盈8%",
    "stop_loss_price": "9.5元止损位",
    "take_profit_price": "14元止盈位",
    "action_detail": "持有为主，跌破止损位离场",
}

SAMPLE_SELECTED_PLAN = {
    "selected_plan_id": "plan_1",
    "recommendation": "买入",
    "position_status": "未提及",
    "not_holding_advice": SAMPLE_NOT_HOLDING,
    "holding_advice": SAMPLE_HOLDING,
}


class TestSaveAnalysisSummaryAdvice:
    """_save_analysis_summary 保存决策建议字段（红→绿）。"""

    @pytest.fixture
    def cache(self, temp_db_path):
        from app.storage.cache import StockCache
        return StockCache(db_path=temp_db_path)

    def _make_result(self, selected_plan):
        """构造带 extended_analysis 与 state.selected_plan 的分析结果。"""
        return {
            "extended_analysis": {
                "investment_value": "具有较高投资价值",
                "investment_value_score": 70,
                "risk_level": "低",
            },
            "state": {
                "stock_name": "测试股票",
                "selected_plan": selected_plan,
            },
            "data_collection": {},
        }

    def test_summary_contains_advice_fields(self, cache):
        """正常路径：摘要保存整体建议与两个场景建议。"""
        from app.web.routes import _save_analysis_summary

        _save_analysis_summary(cache, "600001", self._make_result(SAMPLE_SELECTED_PLAN), max_count=50)
        summary = cache.get_analysis_summary("600001")
        assert summary is not None
        assert summary.get("recommendation") == "买入"
        assert summary.get("not_holding_advice") == SAMPLE_NOT_HOLDING
        assert summary.get("holding_advice") == SAMPLE_HOLDING

    def test_summary_without_selected_plan(self, cache):
        """边界：state 无 selected_plan 时不报错，建议字段为空。"""
        from app.web.routes import _save_analysis_summary

        result = {
            "extended_analysis": {"investment_value_score": 60},
            "state": {"stock_name": "测试股票"},
            "data_collection": {},
        }
        _save_analysis_summary(cache, "600002", result, max_count=50)
        summary = cache.get_analysis_summary("600002")
        assert summary is not None
        assert not summary.get("recommendation")
        assert not summary.get("not_holding_advice")
        assert not summary.get("holding_advice")

    def test_summary_only_not_holding_advice(self, cache):
        """边界：用户已持仓时 holding_advice 为 null，摘要如实保存。"""
        from app.web.routes import _save_analysis_summary

        plan = dict(SAMPLE_SELECTED_PLAN)
        plan["holding_advice"] = None
        _save_analysis_summary(cache, "600003", self._make_result(plan), max_count=50)
        summary = cache.get_analysis_summary("600003")
        assert summary.get("not_holding_advice") == SAMPLE_NOT_HOLDING
        assert summary.get("holding_advice") is None


class TestCachedStocksAdvice:
    """/api/cached-stocks 返回建议字段（红→绿）。"""

    @pytest.fixture
    def client_with_cache(self, temp_db_path):
        """预置真实 StockCache 到 app.state.cache。"""
        from app.main import app
        from app.storage.cache import StockCache
        from httpx import ASGITransport, AsyncClient

        cache = StockCache(db_path=temp_db_path)
        old_cache = getattr(app.state, "cache", None)
        app.state.cache = cache
        transport = ASGITransport(app=app)
        yield AsyncClient(transport=transport, base_url="http://test"), cache
        app.state.cache = old_cache

    @pytest.mark.asyncio
    async def test_cached_stocks_returns_advice(self, client_with_cache):
        """正常路径：接口返回整体建议与两个场景建议。"""
        client, cache = client_with_cache
        cache.set_analysis_summary("600001", {
            "stock_code": "600001",
            "stock_name": "测试股票",
            "investment_value_score": 70,
            "risk_level": "低",
            "recommendation": "买入",
            "not_holding_advice": SAMPLE_NOT_HOLDING,
            "holding_advice": SAMPLE_HOLDING,
        }, max_count=50)

        resp = await client.get("/api/cached-stocks")
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 1
        item = results[0]
        assert item["recommendation"] == "买入"
        assert item["not_holding_advice"] == SAMPLE_NOT_HOLDING
        assert item["holding_advice"] == SAMPLE_HOLDING

    @pytest.mark.asyncio
    async def test_cached_stocks_legacy_summary(self, client_with_cache):
        """边界：历史摘要无建议字段时返回空值，不报错。"""
        client, cache = client_with_cache
        cache.set_analysis_summary("600002", {
            "stock_code": "600002",
            "stock_name": "旧数据股票",
            "investment_value_score": 60,
        }, max_count=50)

        resp = await client.get("/api/cached-stocks")
        assert resp.status_code == 200
        item = resp.json()["results"][0]
        assert item.get("recommendation", "") == ""
        assert item.get("not_holding_advice") is None
        assert item.get("holding_advice") is None


class TestStreamCompleteAdvice:
    """流式分析 complete 事件携带 selected_plan（红→绿）。"""

    def test_complete_event_contains_selected_plan(self):
        """正常路径：complete 事件 data 含 selected_plan。"""
        from unittest.mock import patch

        from app.services import stock_service

        class _FakeWorkflow:
            def stream(self, initial_state):
                yield {"decision": {
                    "selected_plan": SAMPLE_SELECTED_PLAN,
                    "logs": [],
                    "llm_records": [],
                    "current_phase": "report",
                }}
                yield {"report": {
                    "final_report": "ok",
                    "logs": [],
                    "llm_records": [],
                    "current_phase": "completed",
                }}

        with patch.object(stock_service, "create_stock_agent_workflow",
                          return_value=_FakeWorkflow()), \
             patch.object(stock_service, "build_report", return_value="报告"):
            events = list(stock_service.analyze_single_stock_stream("600001"))

        complete = [e for e in events if e["type"] == "complete"]
        assert len(complete) == 1
        data = complete[0]["data"]
        assert data.get("selected_plan") == SAMPLE_SELECTED_PLAN

    def test_complete_event_without_decision(self):
        """边界：分析失败无决策结果时 selected_plan 为 None，不报错。"""
        from unittest.mock import patch

        from app.services import stock_service

        class _FakeWorkflow:
            def stream(self, initial_state):
                yield {"data_collection": {
                    "data_bundle": None,
                    "error": "数据获取失败",
                    "logs": [],
                    "llm_records": [],
                }}

        with patch.object(stock_service, "create_stock_agent_workflow",
                          return_value=_FakeWorkflow()), \
             patch.object(stock_service, "build_report", return_value="报告"):
            events = list(stock_service.analyze_single_stock_stream("600001"))

        complete = [e for e in events if e["type"] == "complete"]
        assert len(complete) == 1
        assert complete[0]["data"].get("selected_plan") is None


class TestSidebarAdviceRender:
    """前端卡片渲染两场景建议（保留评分+风险徽章）。"""

    @pytest.fixture
    def index_html(self):
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "app", "web", "templates", "index.html",
        )
        with open(path, encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _slice_function(html, name):
        """截取指定 JS 函数体（到下一个顶层函数声明为止）。"""
        import re
        marker = "function " + name
        start = html.index(marker)
        nxt = re.search(r"\n(?:async )?function ", html[start + len(marker):])
        end = start + len(marker) + nxt.start() if nxt else len(html)
        return html[start:end]

    def test_render_stock_card_shows_both_advices(self, index_html):
        """正常路径：卡片渲染未买入与已持仓两个场景建议。"""
        body = self._slice_function(index_html, "renderStockCard")
        assert "not_holding_advice" in body
        assert "holding_advice" in body

    def test_render_stock_card_keeps_score_and_risk(self, index_html):
        """正常路径：保留评分与风险徽章。"""
        body = self._slice_function(index_html, "renderStockCard")
        assert "investment_value_score" in body
        assert "risk_level" in body

    def test_render_stock_card_legacy_placeholder(self, index_html):
        """边界：历史数据无建议时展示"暂无建议"占位。"""
        body = self._slice_function(index_html, "renderStockCard")
        assert "暂无建议" in body
