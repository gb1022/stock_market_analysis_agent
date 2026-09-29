# -*- coding: utf-8 -*-
"""选股意见生成模块（v2.12.0）。

[已废弃] 自 v2.13.0 起，本模块不再被选股流程调用。
意见生成职责已合并至阶段三 LLM 精选模块（app/screener/final_select.py），
由 llm_final_select() 在一次调用中同时完成"精选最终推荐 + 生成建议"。
本文件保留仅供历史参考，请勿在新代码中引用。

批量调用 LLM 为选股结果生成选择意见：
- 一次 LLM 调用覆盖全部股票（避免逐条调用导致耗时过长）
- 提示词注入防虚构规则：仅基于提供的数据生成意见
- LLM 调用失败时 opinion 留空，不阻塞选股结果返回
"""

import json
import logging
import re

from app.models.screener import ScreenResult

logger = logging.getLogger("stock_agent")

# 意见最大长度（字）
_OPINION_MAX_LEN = 100

# 系统提示词：角色 + 防虚构规则
_SYSTEM_PROMPT = """你是专业的 A 股投资顾问。请根据提供的选股结果数据，为每只股票生成简明的选择意见。

严格要求：
1. 仅基于下方提供的数据生成意见，不得编造或虚构任何未提供的数据
2. 每只股票的意见不超过 100 字
3. 意见需包含：核心亮点（基于评分和命中条件）、关键数据解读、风险提示
4. 输出纯 JSON 数组，不要附加任何解释文字"""

# 用户提示词模板：注入全部股票的关键数据
_USER_PROMPT_TEMPLATE = """以下是本次选股筛选出的 {count} 只股票及其关键数据，请为每只股票生成 100 字以内的选择意见。

{stock_data}

请按以下 JSON 数组格式输出（不要输出其他内容）：
[{{"code": "股票代码", "opinion": "100字以内的选择意见"}}]"""


def _format_stock_data(results: list[ScreenResult]) -> str:
    """将选股结果格式化为提示词中的数据段落。

    Args:
        results: 选股结果列表

    Returns:
        格式化后的股票数据文本
    """
    lines = []
    for r in results:
        parts = [f"代码: {r.code}", f"名称: {r.name}"]
        if r.final_score is not None:
            parts.append(f"综合评分: {r.final_score:.1f}")
        if r.reason:
            parts.append(f"入选理由: {r.reason}")
        if r.change_percent is not None:
            parts.append(f"涨跌幅: {r.change_percent:.2f}%")
        if r.pe_ttm is not None:
            parts.append(f"PE(动): {r.pe_ttm:.2f}")
        if r.pb is not None:
            parts.append(f"PB: {r.pb:.2f}")
        if r.turnover_rate is not None:
            parts.append(f"换手率: {r.turnover_rate:.2f}%")
        if r.market_cap is not None:
            parts.append(f"总市值: {r.market_cap / 1e8:.1f}亿")
        if r.amount is not None:
            parts.append(f"成交额: {r.amount / 1e8:.2f}亿")
        if r.volume is not None:
            parts.append(f"成交量: {r.volume:.0f}手")
        if r.amplitude is not None:
            parts.append(f"振幅: {r.amplitude:.2f}%")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _parse_opinion_json(response: str) -> list[dict]:
    """解析 LLM 返回的意见 JSON。

    支持 markdown 代码块包裹。

    Args:
        response: LLM 返回的原始文本

    Returns:
        解析后的列表；解析失败返回空列表
    """
    text = response.strip()
    # 移除可能的 markdown 代码块包裹
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        logger.warning(f"[选股意见] LLM 返回非数组类型: {type(data).__name__}")
        return []
    except json.JSONDecodeError as e:
        logger.warning(f"[选股意见] LLM 返回 JSON 解析失败: {e}, 内容: {text[:200]}")
        return []


def generate_opinions(results: list[ScreenResult], provider) -> None:
    """批量调用 LLM 为选股结果生成选择意见，回填到各结果的 opinion 字段。

    一次 LLM 调用覆盖全部股票。调用失败或解析失败时 opinion 保持为空，
    不抛异常、不阻塞选股结果返回。

    Args:
        results: 选股结果列表（原地修改 opinion 字段）
        provider: LLM Provider 实例（需实现 invoke(system_prompt, user_prompt)）
    """
    if not results:
        return

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        count=len(results),
        stock_data=_format_stock_data(results),
    )

    try:
        response = provider.invoke(_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        logger.warning(f"[选股意见] LLM 调用失败，意见留空: {e}")
        return

    opinion_list = _parse_opinion_json(response)
    # 按代码建立索引，回填到对应结果
    opinion_map = {}
    for item in opinion_list:
        if isinstance(item, dict) and "code" in item and "opinion" in item:
            opinion_map[str(item["code"])] = str(item["opinion"])

    for r in results:
        opinion = opinion_map.get(r.code, "")
        # 截断到 100 字以内
        if len(opinion) > _OPINION_MAX_LEN:
            opinion = opinion[:_OPINION_MAX_LEN]
        r.opinion = opinion
