"""Stage 2: 建模阶段节点。

基于感知阶段的分析结果，构建市场内部模型（趋势/风险/机会）。
"""

import json

from app.graph.prompts import MODELING_PROMPT, ANTI_FABRICATION_RULE
from app.graph.state import StockAgentState
from app.graph.utils import add_log, add_llm_record, extract_json
from app.llm.factory import create_llm_provider


def modeling(state: StockAgentState) -> StockAgentState:
    """建模阶段：构建市场内部模型。

    Args:
        state: 当前 Agent 状态（需包含 perception 输出）

    Returns:
        更新后的状态（进入 reasoning 阶段，或在错误时停留在当前阶段）
    """
    logs = add_log(state, "建模分析", "开始构建市场内部模型...")

    try:
        bundle = state["data_bundle"]
        perception_data = state["perception"]
        if not bundle or not perception_data:
            raise ValueError("缺少感知阶段输出")

        provider = create_llm_provider()

        user_input = state.get("user_input", "无特殊要求")

        prompt = MODELING_PROMPT.format(
            stock_code=bundle.stock_code,
            market=bundle.market,
            user_input=user_input,
            anti_fabrication_rule=ANTI_FABRICATION_RULE,
            perception_json=json.dumps(perception_data, ensure_ascii=False, indent=2),
        )

        system_prompt = f"你是一个基于真实数据进行分析的股票分析助手。\n{ANTI_FABRICATION_RULE}"

        logs = add_log(state, "建模分析", "正在调用大模型进行市场建模...", log_type="info")

        result_json = provider.invoke(system_prompt=system_prompt, user_prompt=prompt)

        # 记录 LLM 调用
        llm_records = add_llm_record(
            state, "建模分析",
            system_prompt, prompt, result_json,
        )

        result = json.loads(extract_json(result_json))
        logs = add_log(state, "建模分析", f"建模完成，趋势判断: {result.get('trend_judgment', '未知')}", log_type="info")

        return {
            **state,
            "world_model": result,
            "current_phase": "reasoning",
            "logs": logs,
            "llm_records": llm_records,
        }
    except Exception as e:
        retry = state.get("retry_count", 0) + 1
        err_msg = f"建模阶段出错: {str(e)}"
        logs = add_log(state, "建模分析", err_msg, log_type="error")
        logs = add_log(state, "建模分析", f"将在第 {retry}/3 次重试", log_type="error")
        return {
            **state,
            "error": err_msg,
            "current_phase": "modeling",
            "retry_count": retry,
            "logs": logs,
        }
