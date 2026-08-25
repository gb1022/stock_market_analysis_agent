"""Stage 5: 报告生成阶段节点。

基于完整分析过程，LLM 输出 JSON 分析结论，程序渲染 Markdown 投资分析报告。
"""

import json
import re

from app.graph.prompts import REPORT_PROMPT, ANTI_FABRICATION_RULE
from app.graph.state import StockAgentState
from app.graph.utils import add_log, add_llm_record
from app.llm.factory import create_llm_provider
from app.services.markdown_renderer import render_markdown_report


def _parse_llm_json(response: str) -> dict:
    """解析 LLM 返回的 JSON 字符串。

    支持处理 LLM 可能返回的 markdown 代码块包裹。

    Args:
        response: LLM 返回的原始文本

    Returns:
        解析后的字典

    Raises:
        ValueError: JSON 解析失败
    """
    text = response.strip()
    # 移除可能的 markdown 代码块包裹
    if text.startswith("```"):
        # 移除开头的 ```json 或 ```
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        # 移除结尾的 ```
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 返回的 JSON 解析失败: {e}\n原始内容:\n{text[:500]}")


def report_generation(state: StockAgentState) -> StockAgentState:
    """报告生成阶段：LLM 输出 JSON 分析结论，程序渲染 Markdown 报告。

    Args:
        state: 当前 Agent 状态（需包含 selected_plan）

    Returns:
        更新后的状态（进入 completed 阶段，或在错误时停留在当前阶段）
    """
    logs = add_log(state, "报告生成", "开始生成最终分析报告...")

    try:
        bundle = state["data_bundle"]
        perception_data = state["perception"]
        world_model = state["world_model"]
        selected_plan = state["selected_plan"]
        if not bundle or not perception_data or not world_model or not selected_plan:
            raise ValueError("缺少决策阶段输出")

        provider = create_llm_provider()

        # 构建数据来源信息
        source_info = (
            f"数据来源: {bundle.source}\n"
            f"采集时间: {bundle.fetched_at.isoformat() if bundle.fetched_at else '未知'}\n"
            f"K线数据: {len(bundle.klines)} 条\n"
            f"资金流数据: {len(bundle.capital_flow)} 条\n"
            f"新闻: {len(bundle.news)} 条"
        )

        user_input = state.get("user_input", "无特殊要求")

        # 获取股票名称（优先从 quote 获取，其次从 financial 获取）
        stock_name = ""
        if bundle.quote and bundle.quote.name:
            stock_name = bundle.quote.name
        elif bundle.financial and bundle.financial.name:
            stock_name = bundle.financial.name

        prompt = REPORT_PROMPT.format(
            stock_code=bundle.stock_code,
            market=bundle.market,
            stock_name=stock_name,
            user_input=user_input,
            anti_fabrication_rule=ANTI_FABRICATION_RULE,
            data_source_info=source_info,
            perception_json=json.dumps(perception_data, ensure_ascii=False, indent=2),
            modeling_json=json.dumps(world_model, ensure_ascii=False, indent=2),
            extended_analysis_json=bundle.extended_analysis.model_dump_json(indent=2, ensure_ascii=False) if bundle.extended_analysis else "暂无扩展分析数据",
            selected_plan_json=json.dumps(selected_plan, ensure_ascii=False, indent=2),
        )

        system_prompt = f"你是一个基于真实数据进行分析的股票分析助手。\n{ANTI_FABRICATION_RULE}"

        logs = add_log(state, "报告生成", "正在调用大模型生成 JSON 分析结论...", log_type="info")

        # 调用 LLM 获取 JSON 分析结论
        llm_response = provider.invoke(system_prompt=system_prompt, user_prompt=prompt)

        # 记录 LLM 调用
        llm_records = add_llm_record(
            state, "报告生成",
            system_prompt, prompt, llm_response,
        )

        # 解析 LLM 返回的 JSON
        llm_analysis = _parse_llm_json(llm_response)

        logs = add_log(state, "报告生成", "JSON 解析成功，正在渲染 Markdown 报告...", log_type="info")

        # 使用渲染函数生成 Markdown 报告
        markdown_report = render_markdown_report(state, llm_analysis, bundle)

        logs = add_log(state, "报告生成", f"报告生成完成，共 {len(markdown_report)} 字符", log_type="info")

        return {
            **state,
            "final_report": markdown_report,
            "current_phase": "completed",
            "logs": logs,
            "llm_records": llm_records,
        }
    except Exception as e:
        retry = state.get("retry_count", 0) + 1
        err_msg = f"报告生成出错: {str(e)}"
        logs = add_log(state, "报告生成", err_msg, log_type="error")
        logs = add_log(state, "报告生成", f"将在第 {retry}/3 次重试", log_type="error")
        return {
            **state,
            "error": err_msg,
            "current_phase": "report",
            "retry_count": retry,
            "logs": logs,
        }
