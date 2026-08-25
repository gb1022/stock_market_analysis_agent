"""Stage 4: 决策阶段节点。

从 3 个方案中选择最优方案并进行风险评估。
"""

import json

from app.graph.prompts import DECISION_PROMPT, ANTI_FABRICATION_RULE
from app.graph.state import StockAgentState
from app.graph.utils import add_log, add_llm_record, extract_json
from app.llm.factory import create_llm_provider


def decision(state: StockAgentState) -> StockAgentState:
    """决策阶段：选择最优方案并进行风险评估。

    Args:
        state: 当前 Agent 状态（需包含 reasoning_plans）

    Returns:
        更新后的状态（进入 report 阶段，或在错误时停留在当前阶段）
    """
    logs = add_log(state, "决策分析", "开始选择最优方案并进行风险评估...")

    try:
        bundle = state["data_bundle"]
        perception_data = state["perception"]
        world_model = state["world_model"]
        plans = state["reasoning_plans"]
        if not bundle or not perception_data or not world_model or not plans:
            raise ValueError("缺少推理阶段输出")

        provider = create_llm_provider()

        user_input = state.get("user_input", "无特殊要求")

        prompt = DECISION_PROMPT.format(
            stock_code=bundle.stock_code,
            market=bundle.market,
            user_input=user_input,
            anti_fabrication_rule=ANTI_FABRICATION_RULE,
            perception_json=json.dumps(perception_data, ensure_ascii=False, indent=2),
            modeling_json=json.dumps(world_model, ensure_ascii=False, indent=2),
            plans_json=json.dumps(plans, ensure_ascii=False, indent=2),
        )

        system_prompt = f"你是一个基于真实数据进行分析的股票分析助手。\n{ANTI_FABRICATION_RULE}"

        logs = add_log(state, "决策分析", "正在调用大模型进行方案选择...", log_type="info")

        result_json = provider.invoke(system_prompt=system_prompt, user_prompt=prompt)

        # 记录 LLM 调用
        llm_records = add_llm_record(
            state, "决策分析",
            system_prompt, prompt, result_json,
        )

        result = json.loads(extract_json(result_json))
        logs = add_log(state, "决策分析",
                       f"决策完成，推荐方案: {result.get('selected_plan_id', '未知')}, "
                       f"建议: {result.get('recommendation', '未知')}",
                       log_type="info")

        return {
            **state,
            "selected_plan": result,
            "current_phase": "report",
            "logs": logs,
            "llm_records": llm_records,
        }
    except Exception as e:
        retry = state.get("retry_count", 0) + 1
        err_msg = f"决策阶段出错: {str(e)}"
        logs = add_log(state, "决策分析", err_msg, log_type="error")
        logs = add_log(state, "决策分析", f"将在第 {retry}/3 次重试", log_type="error")
        return {
            **state,
            "error": err_msg,
            "current_phase": "decision",
            "retry_count": retry,
            "logs": logs,
        }
