"""LangGraph 状态与路由测试：状态定义 / 阶段流转 / 错误处理。"""


class TestStockAgentState:
    """状态定义测试"""

    def test_initial_state_structure(self):
        """正常路径：初始状态结构完整。"""
        from app.graph.state import StockAgentState

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
        assert state["stock_code"] == "600519"
        assert state["current_phase"] == "data_collection"
        assert state["error"] is None

    def test_state_market_enum(self):
        """边界：market 必须是 Literal['A']。"""
        from app.graph.state import StockAgentState
        state: StockAgentState = {
            "stock_code": "000001",
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
        assert state["market"] == "A"


class TestRouter:
    """阶段路由测试"""

    def test_router_normal_flow(self):
        """正常路径：无错误时 router 透传 current_phase（阶段推进由各节点完成）。"""
        from app.graph.router import router

        base_state = {
            "stock_code": "600519",
            "market": "A",
            "analysis_type": "single",
            "current_phase": "data_collection",
            "error": None,
        }
        # 节点执行后将 current_phase 推进到下一阶段，router 直接返回该阶段名
        for phase in ("data_collection", "perception", "modeling",
                      "reasoning", "decision", "report", "completed"):
            state = {**base_state, "current_phase": phase}
            assert router(state) == phase

    def test_router_stays_on_error(self):
        """边界：有错误且未超重试次数时停留在当前阶段。"""
        from app.graph.router import router

        state = {
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
            "error": "mock error",
            "retry_count": 1,
            "logs": [],
            "llm_records": [],
            "user_input": "",
        }
        assert router(state) == "data_collection"

    def test_router_error_max_retries(self):
        """边界：错误超过3次重试上限时路由到 __end__。"""
        from app.graph.router import router

        state = {
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
            "error": "mock error",
            "retry_count": 3,
            "logs": [],
            "llm_records": [],
            "user_input": "",
        }
        assert router(state) == "__end__"

    def test_router_end_state(self):
        """边界：completed 阶段 router 透传阶段名（由 workflow 条件边映射到 END）。"""
        from app.graph.router import router
        state = {
            "stock_code": "600519",
            "market": "A",
            "analysis_type": "single",
            "current_phase": "completed",
            "error": None,
        }
        assert router(state) == "completed"

    def test_router_unknown_phase(self):
        """边界：未知阶段 router 原样透传（阶段合法性由调用方/状态类型约束保证）。"""
        from app.graph.router import router
        state = {
            "stock_code": "600519",
            "market": "A",
            "analysis_type": "single",
            "current_phase": "unknown",
            "error": None,
        }
        assert router(state) == "unknown"

    def test_workflow_compilation(self):
        """正常路径：工作流能正确编译。"""
        from app.graph.workflow import create_stock_agent_workflow

        workflow = create_stock_agent_workflow()
        assert workflow is not None

        # 检查节点数
        graph = workflow.get_graph()
        nodes = graph.nodes
        assert len(nodes) >= 6  # 6 stages
