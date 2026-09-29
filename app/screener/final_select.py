# -*- coding: utf-8 -*-
"""阶段三 LLM 精选推荐模块（v2.13.0 / v2.14.0）。

在阶段二精排输出的候选池基础上，调用大模型完成最终精选：
- 将候选池全部股票数据 + 用户选股要求打包为一次 LLM 调用
- 由 LLM 根据用户要求选出恰好 target_count 只最终推荐股票
- 同时为每只入选股票生成 100 字以内的建议（替代 v2.12.0 的 opinion.py）
- v2.14.0：由 LLM 为每只入选股票打分（llm_score，0-100），并按评分降序排序返回
- 提示词注入防虚构规则：仅基于提供的数据，不得编造
- LLM 调用/解析失败时兜底：按原排序取前 target_count 只，opinion 留空、llm_score 为 None
"""

import json
import logging
import re

from app.models.screener import ScreenResult

logger = logging.getLogger("stock_agent")

# 建议最大长度（字）
_OPINION_MAX_LEN = 100

# 阶段二输出配额倍数与上限（v2.13.0）
_RANK_POOL_MULTIPLIER = 3
_RANK_POOL_MAX = 50

# 系统提示词：角色 + 防虚构规则
_SYSTEM_PROMPT = """你是专业的 A 股投资顾问。请根据提供的候选股票数据和用户的选股要求，从中精选出最终推荐的股票，并为每只入选股票生成简明的建议和评分。

严格要求：
1. 仅基于下方提供的数据进行分析与推荐，不得编造或虚构任何未提供的数据
2. 必须严格按照用户要求的数量选出股票，不得多选或少选（候选不足时全部保留）
3. 每只股票的建议不超过 100 字，需包含：核心亮点、关键数据解读、风险提示
4. 为每只入选股票按 0-100 分打分（score），分数越高代表该股票越符合用户要求、数据质量越好
5. 输出纯 JSON 数组，不要附加任何解释文字"""

# 用户提示词模板：注入用户要求 + 候选股票数据 + 目标数量
_USER_PROMPT_TEMPLATE = """用户的选股要求：{user_requirement}

以下是经过两轮筛选后的 {count} 只候选股票及其关键数据：

{stock_data}

请根据用户的选股要求，从上述候选中精选出恰好 {target_count} 只最符合要求的股票作为最终推荐，为每只入选股票生成 100 字以内的建议，并给出 0-100 分的评分。

请按以下 JSON 数组格式输出（不要输出其他内容）：
[{{"code": "股票代码", "score": 0-100的评分, "opinion": "100字以内的建议"}}]"""


def compute_rank_pool_size(target_count: int) -> int:
    """计算阶段二精排的输出配额。

    配额 = 用户要求数量 × 3，上限 50（v2.13.0）。
    扩大候选池是为了让阶段三 LLM 精选有足够挑选空间。

    Args:
        target_count: 用户要求的最终推荐数量

    Returns:
        阶段二输出配额
    """
    return min(target_count * _RANK_POOL_MULTIPLIER, _RANK_POOL_MAX)


def _format_stock_data(candidates: list[ScreenResult]) -> str:
    """将候选股票格式化为提示词中的数据段落。

    Args:
        candidates: 候选股票列表

    Returns:
        格式化后的股票数据文本
    """
    lines = []
    for r in candidates:
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


def _parse_select_json(response: str) -> list[dict]:
    """解析 LLM 返回的精选结果 JSON。

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
        logger.warning(f"[阶段三精选] LLM 返回非数组类型: {type(data).__name__}")
        return []
    except json.JSONDecodeError as e:
        logger.warning(f"[阶段三精选] LLM 返回 JSON 解析失败: {e}, 内容: {text[:200]}")
        return []


def llm_final_select(candidates: list[ScreenResult], provider,
                     target_count: int, user_input: str = "") -> list[ScreenResult]:
    """阶段三：调用 LLM 从候选池中精选最终推荐股票、生成建议并打分排序。

    一次 LLM 调用完成"挑选 + 建议生成 + 评分"。每只入选股票回填 opinion 与
    llm_score（0-100，缺失默认 0），最终按 llm_score 降序排序返回
    （同分时按规则 final_score 降序保持稳定）。调用失败或解析失败时兜底：
    按原排序取前 target_count 只，opinion 留空、llm_score 为 None，
    不抛异常、不阻塞选股结果返回。

    Args:
        candidates: 阶段二精排输出的候选池（已按评分排序）
        provider: LLM Provider 实例（需实现 invoke(system_prompt, user_prompt)）
        target_count: 用户要求的最终推荐数量
        user_input: 用户的选股要求（自然语言，可为空）

    Returns:
        最终推荐的股票列表（数量 <= target_count，按 LLM 评分降序）
    """
    if not candidates:
        return []

    # 候选不足目标数量时，目标数量收敛为候选数量
    effective_target = min(target_count, len(candidates))

    user_requirement = user_input.strip() if user_input else "无特殊要求，按综合评分与数据质量择优推荐"
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        user_requirement=user_requirement,
        count=len(candidates),
        stock_data=_format_stock_data(candidates),
        target_count=effective_target,
    )

    try:
        response = provider.invoke(_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        logger.warning(f"[阶段三精选] LLM 调用失败，兜底取前 {effective_target} 只: {e}")
        return list(candidates[:effective_target])

    selected_list = _parse_select_json(response)
    if not selected_list:
        logger.warning(f"[阶段三精选] LLM 返回解析为空，兜底取前 {effective_target} 只")
        return list(candidates[:effective_target])

    # 按代码建立候选索引，过滤 LLM 返回中不在候选池的股票
    candidate_map = {r.code: r for r in candidates}
    selected: list[ScreenResult] = []
    seen_codes = set()
    for item in selected_list:
        if not (isinstance(item, dict) and "code" in item):
            continue
        code = str(item["code"])
        if code not in candidate_map or code in seen_codes:
            continue
        seen_codes.add(code)
        stock = candidate_map[code].model_copy()
        opinion = str(item.get("opinion", ""))
        # 截断到 100 字以内
        if len(opinion) > _OPINION_MAX_LEN:
            opinion = opinion[:_OPINION_MAX_LEN]
        stock.opinion = opinion
        # LLM 评分（v2.14.0）：缺失时默认 0 分
        score = item.get("score")
        stock.llm_score = float(score) if score is not None else 0.0
        selected.append(stock)
        if len(selected) >= effective_target:
            break

    # LLM 返回的有效选择不足目标数量时，按原排序补齐（兜底）
    if len(selected) < effective_target:
        logger.warning(f"[阶段三精选] LLM 有效选择 {len(selected)} 只，不足目标 {effective_target} 只，按原排序补齐")
        for cand in candidates:
            if cand.code not in seen_codes:
                seen_codes.add(cand.code)
                selected.append(cand.model_copy())
                if len(selected) >= effective_target:
                    break

    # 按 LLM 评分降序排序（v2.14.0）；同分时按规则 final_score 降序保持稳定
    selected.sort(key=lambda r: (
        -(r.llm_score if r.llm_score is not None else 0.0),
        -(r.final_score if r.final_score is not None else 0.0),
    ))

    return selected
