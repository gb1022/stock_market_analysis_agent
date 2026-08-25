"""LangGraph 流水线状态定义。"""

from typing import TypedDict, Literal, Optional, Any

from app.models.data_bundle import DataBundle


class LogEntry(TypedDict):
    """一条执行日志"""
    stage: str      # 阶段名称
    message: str    # 日志内容
    type: str       # info / prompt / llm_response / error


class LLMRecord(TypedDict):
    """一次 LLM 调用记录"""
    stage: str          # 调用阶段
    system_prompt: str  # 系统提示词
    user_prompt: str    # 用户提示词
    response: str       # LLM 完整回复


class StockAgentState(TypedDict):
    """股票分析 Agent 状态。

    包含输入参数、各阶段中间输出、控制流字段、日志字段。
    """
    # 输入
    stock_code: str
    market: Literal["A"]
    analysis_type: Literal["single", "screen"]

    # 用户输入的投资意见（可选）
    user_input: str

    # Stage 0: 数据采集
    data_bundle: Optional[DataBundle]

    # Stage 1: 感知
    perception: Optional[dict[str, Any]]

    # Stage 2: 建模
    world_model: Optional[dict[str, Any]]

    # Stage 3: 推理
    reasoning_plans: Optional[list[dict[str, Any]]]

    # Stage 4: 决策
    selected_plan: Optional[dict[str, Any]]

    # Stage 5: 报告
    final_report: Optional[str]

    # 控制流
    current_phase: Literal[
        "data_collection", "perception", "modeling",
        "reasoning", "decision", "report", "completed"
    ]
    error: Optional[str]

    # 重试计数器
    retry_count: int

    # 执行日志和 LLM 调用记录（用于前端展示）
    logs: list[LogEntry]
    llm_records: list[LLMRecord]
