"""Stage 1: 感知阶段节点。

基于 DataBundle 中的真实数据，用 LLM 识别关键信号（技术/基本面/资金面/新闻）。
"""

import json

from app.graph.prompts import PERCEPTION_PROMPT, ANTI_FABRICATION_RULE
from app.graph.state import StockAgentState
from app.graph.utils import add_log, add_llm_record, extract_json
from app.llm.factory import create_llm_provider


def perception(state: StockAgentState) -> StockAgentState:
    """感知阶段：基于真实数据识别关键信号。

    Args:
        state: 当前 Agent 状态（需包含 data_bundle）

    Returns:
        更新后的状态（进入 modeling 阶段，或在错误时停留在当前阶段）
    """
    logs = add_log(state, "感知分析", "开始识别关键信号...")

    try:
        bundle = state["data_bundle"]
        if not bundle:
            raise ValueError("缺少数据采集阶段输出")

        provider = create_llm_provider()

        # 构建最近的 K 线数据
        recent_klines = bundle.klines[-20:] if len(bundle.klines) > 20 else bundle.klines
        logs = add_log(state, "感知分析", f"构建提示词中，K线数据 {len(recent_klines)} 条", log_type="info")

        user_input = state.get("user_input", "无特殊要求")
        if user_input:
            logs = add_log(state, "感知分析", f"用户关注点: {user_input}", log_type="info")

        prompt = PERCEPTION_PROMPT.format(
            stock_code=bundle.stock_code,
            market=bundle.market,
            fetched_at=bundle.fetched_at.isoformat() if bundle.fetched_at else "未知",
            source=bundle.source,
            user_input=user_input,
            anti_fabrication_rule=ANTI_FABRICATION_RULE,
            quote_json=bundle.quote.model_dump_json(indent=2, ensure_ascii=False) if bundle.quote else "{}",
            klines_recent_json=json.dumps(
                [k.model_dump() for k in recent_klines],
                ensure_ascii=False, indent=2, default=str,
            ),
            financial_json=bundle.financial.model_dump_json(indent=2, ensure_ascii=False) if bundle.financial else "{}",
            capital_flow_json=json.dumps(
                [c.model_dump() for c in bundle.capital_flow],
                ensure_ascii=False, indent=2, default=str,
            ),
            news_json=json.dumps(
                [n.model_dump() for n in bundle.news],
                ensure_ascii=False, indent=2, default=str,
            ),
            extended_analysis_json=bundle.extended_analysis.model_dump_json(indent=2, ensure_ascii=False) if bundle.extended_analysis else "暂无扩展分析数据",
        )

        system_prompt = f"你是一个基于真实数据进行分析的股票分析助手。\n{ANTI_FABRICATION_RULE}"

        logs = add_log(state, "感知分析", "正在调用大模型进行分析...", log_type="info")

        result_json = provider.invoke(system_prompt=system_prompt, user_prompt=prompt)

        # 记录 LLM 调用
        llm_records = add_llm_record(
            state, "感知分析",
            system_prompt, prompt, result_json,
        )

        result = json.loads(extract_json(result_json))
        obs_count = len(result.get("key_observations", []))
        logs = add_log(state, "感知分析", f"大模型返回完成，识别到 {obs_count} 个关键信号", log_type="info")

        return {
            **state,
            "perception": result,
            "current_phase": "modeling",
            "logs": logs,
            "llm_records": llm_records,
        }
    except Exception as e:
        retry = state.get("retry_count", 0) + 1
        err_msg = f"感知阶段出错: {str(e)}"
        logs = add_log(state, "感知分析", err_msg, log_type="error")
        logs = add_log(state, "感知分析", f"将在第 {retry}/3 次重试", log_type="error")
        return {
            **state,
            "error": err_msg,
            "current_phase": "perception",
            "retry_count": retry,
            "logs": logs,
        }
