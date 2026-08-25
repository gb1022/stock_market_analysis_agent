"""Stage 3: 推理阶段节点。

基于已有分析生成 3 个差异化的分析方案。
"""

import json

from app.graph.prompts import REASONING_PROMPT, ANTI_FABRICATION_RULE
from app.graph.state import StockAgentState
from app.graph.utils import add_log, add_llm_record, extract_json
from app.llm.factory import create_llm_provider


def reasoning(state: StockAgentState) -> StockAgentState:
    """推理阶段：生成 3 个差异化分析方案。

    Args:
        state: 当前 Agent 状态（需包含 perception 和 world_model）

    Returns:
        更新后的状态（进入 decision 阶段，或在错误时停留在当前阶段）
    """
    logs = add_log(state, "推理分析", "开始生成差异化分析方案...")

    try:
        bundle = state["data_bundle"]
        perception_data = state["perception"]
        world_model = state["world_model"]
        if not bundle or not perception_data or not world_model:
            raise ValueError("缺少建模阶段输出")

        provider = create_llm_provider()

        user_input = state.get("user_input", "无特殊要求")

        prompt = REASONING_PROMPT.format(
            stock_code=bundle.stock_code,
            market=bundle.market,
            user_input=user_input,
            anti_fabrication_rule=ANTI_FABRICATION_RULE,
            perception_json=json.dumps(perception_data, ensure_ascii=False, indent=2),
            modeling_json=json.dumps(world_model, ensure_ascii=False, indent=2),
        )

        system_prompt = f"你是一个基于真实数据进行分析的股票分析助手。\n{ANTI_FABRICATION_RULE}"

        logs = add_log(state, "推理分析", "正在调用大模型生成分析方案...", log_type="info")

        result_json = provider.invoke(system_prompt=system_prompt, user_prompt=prompt)

        # 记录 LLM 调用
        llm_records = add_llm_record(
            state, "推理分析",
            system_prompt, prompt, result_json,
        )

        result = json.loads(extract_json(result_json))

        # 确保结果是列表
        if isinstance(result, dict):
            result = [result]
        elif not isinstance(result, list):
            result = [{"plan_id": "plan_1", "hypothesis": str(result)}]

        logs = add_log(state, "推理分析", f"推理完成，生成了 {len(result)} 个分析方案", log_type="info")

        return {
            **state,
            "reasoning_plans": result,
            "current_phase": "decision",
            "logs": logs,
            "llm_records": llm_records,
        }
    except Exception as e:
        retry = state.get("retry_count", 0) + 1
        err_msg = f"推理阶段出错: {str(e)}"
        logs = add_log(state, "推理分析", err_msg, log_type="error")
        logs = add_log(state, "推理分析", f"将在第 {retry}/3 次重试", log_type="error")
        return {
            **state,
            "error": err_msg,
            "current_phase": "reasoning",
            "retry_count": retry,
            "logs": logs,
        }
