"""Stage 0 数据采集 Mock 测试：模拟 Router 返回值。"""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from app.graph.state import StockAgentState
from app.models.data_bundle import DataBundle
from app.models.stock import Quote


class TestDataCollectionMock:
    """使用 Mock Router 测试数据采集节点"""

    @pytest.fixture
    def mock_router(self, sample_data_bundle):
        """创建一个 Mock 的 DataSourceRouter。"""
        router = Mock()
        router.get_quote.return_value = sample_data_bundle.quote
        router.get_kline.return_value = sample_data_bundle.klines
        router.get_financial.return_value = sample_data_bundle.financial
        router.get_capital_flow.return_value = sample_data_bundle.capital_flow
        router.get_news.return_value = sample_data_bundle.news
        return router

    def test_data_collection_success(self, mock_router, sample_data_bundle):
        """正常路径：数据采集成功，组装正确的 DataBundle。"""
        import app.graph.data_collection as dc

        # 注入 Mock Router
        dc._router = mock_router

        state: StockAgentState = {
            "stock_code": "600519",
            "market": "A",
            "analysis_type": "single",
            "data_bundle": None,
            "perception": None,
            "world_model": None,
            "reasoning_plans": None,
            "selected_plan": None,
            "final_report": None,
            "current_phase": "data_collection",
            "error": None,
        }

        result = dc.data_collection(state)

        # 验证结果
        assert result["current_phase"] == "perception"
        assert result["error"] is None
        assert result["data_bundle"] is not None
        assert result["data_bundle"].stock_code == "600519"
        assert result["data_bundle"].quote is not None

        # 验证 Mock 被调用
        mock_router.get_quote.assert_called_once_with("600519")
        mock_router.get_kline.assert_called_once_with("600519", days=250)
        mock_router.get_news.assert_called_once_with("600519", limit=10)

    def test_data_collection_error(self, mock_router):
        """边界：Router 报错时，current_phase 保持在 data_collection。"""
        import app.graph.data_collection as dc

        mock_router.get_quote.side_effect = Exception("网络超时")
        dc._router = mock_router

        state: StockAgentState = {
            "stock_code": "600519",
            "market": "A",
            "analysis_type": "single",
            "data_bundle": None,
            "perception": None,
            "world_model": None,
            "reasoning_plans": None,
            "selected_plan": None,
            "final_report": None,
            "current_phase": "data_collection",
            "error": None,
        }

        result = dc.data_collection(state)

        assert result["current_phase"] == "data_collection"
        assert "网络超时" in result["error"]
