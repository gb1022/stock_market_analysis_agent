"""阶段路由函数。

根据当前阶段和错误状态决定下一步走向。
限制连续重试最多 3 次，超过则直接结束流程。
"""

from typing import Literal

from app.graph.state import StockAgentState


def router(state: StockAgentState) -> Literal[
    "data_collection", "perception", "modeling",
    "reasoning", "decision", "report", "__end__",
]:
    """阶段路由：根据 current_phase 决定下一步。

    如果有错误且重试次数 >= 3，则结束流程（__end__）；
    如果有错误但未超限，停留在当前阶段重试；
    否则正常进入下一阶段。

    Args:
        state: 当前 Agent 状态

    Returns:
        下一个要执行的节点名称
    """
    retry_count = state.get("retry_count", 0)

    # 有错误且已达最大重试次数 -> 结束
    if state.get("error") and retry_count >= 3:
        print(f"  [Router] 错误已达最大重试次数({retry_count})，结束流程")
        return "__end__"

    # 有错误但未超限 -> 停留在当前阶段重试
    if state.get("error"):
        print(f"  [Router] 检测到错误，重试第 {retry_count}/3 次")
        return state["current_phase"]

    phase = state["current_phase"]

    # 正常流转：节点已将 current_phase 设为下一阶段，直接返回
    print(f"  [Router] -> {phase}")
    return phase
