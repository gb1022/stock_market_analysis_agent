"""LangGraph 工作流构建。

创建 6 阶段流水线：data_collection → perception → modeling → reasoning → decision → report。
"""

from langgraph.graph import StateGraph, END

from app.graph.state import StockAgentState
from app.graph.data_collection import data_collection
from app.graph.perception import perception
from app.graph.modeling import modeling
from app.graph.reasoning import reasoning
from app.graph.decision import decision
from app.graph.report import report_generation
from app.graph.router import router


def create_stock_agent_workflow() -> StateGraph:
    """创建 LangGraph 流水线。

    构建 6 阶段顺序执行的股票分析流程图。

    Returns:
        编译好的 StateGraph 实例
    """
    workflow = StateGraph(StockAgentState)

    # 注册节点
    workflow.add_node("data_collection", data_collection)
    workflow.add_node("perception", perception)
    workflow.add_node("modeling", modeling)
    workflow.add_node("reasoning", reasoning)
    workflow.add_node("decision", decision)
    workflow.add_node("report", report_generation)

    # 设置入口
    workflow.set_entry_point("data_collection")

    # 添加条件边（由 router 决定下一步）
    workflow.add_conditional_edges(
        "data_collection", router,
        {
            "perception": "perception",
            "modeling": "modeling",
            "reasoning": "reasoning",
            "decision": "decision",
            "report": "report",
            "data_collection": "data_collection",  # 错误时重试
            "__end__": END,
        },
    )
    workflow.add_conditional_edges(
        "perception", router,
        {
            "modeling": "modeling",
            "reasoning": "reasoning",
            "decision": "decision",
            "report": "report",
            "perception": "perception",
            "__end__": END,
        },
    )
    workflow.add_conditional_edges(
        "modeling", router,
        {
            "reasoning": "reasoning",
            "decision": "decision",
            "report": "report",
            "modeling": "modeling",
            "__end__": END,
        },
    )
    workflow.add_conditional_edges(
        "reasoning", router,
        {
            "decision": "decision",
            "report": "report",
            "reasoning": "reasoning",
            "__end__": END,
        },
    )
    workflow.add_conditional_edges(
        "decision", router,
        {
            "report": "report",
            "decision": "decision",
            "__end__": END,
        },
    )
    workflow.add_conditional_edges(
        "report", router,
        {
            "__end__": END,
            "completed": END,
            "report": "report",
        },
    )

    return workflow.compile()
