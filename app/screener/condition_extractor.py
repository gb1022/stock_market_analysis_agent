"""选股条件抽取器（v2.6.0）。

用户提示词优先：通过 LLM 从用户提示词中抽取结构化筛选条件，
约束在实时行情字段白名单（FIELD_WHITELIST）内，支持单位换算与定性词映射。
抽取不到条件时由调用方回退到预设策略/默认条件。
"""

import json
import logging
import re
from typing import Optional

from app.llm.factory import create_llm_provider
from app.screener.conditions import FieldCondition, FIELD_WHITELIST, OP_WHITELIST

logger = logging.getLogger("stock_agent")

# 推荐数量上限（防止 LLM 抽取到异常大的数量）
MAX_TOP_N = 100

# 字段展示名称与单位（与 engine._FIELD_DISPLAY 保持一致）
FIELD_DISPLAY = {
    "pe_ttm": ("PE", ""),
    "pb": ("PB", ""),
    "turnover_rate": ("换手率", "%"),
    "market_cap": ("市值", "亿"),
    "change_percent": ("涨跌幅", "%"),
    "volume": ("成交量", "手"),
    "amount": ("成交额", "元"),
    "amplitude": ("振幅", "%"),
    "roe": ("ROE", "%"),
    "revenue_growth": ("营收增长", "%"),
    "gross_margin": ("毛利率", "%"),
    "rsi24": ("RSI24", ""),
    "ma20": ("MA20", ""),
}

# LLM 输出值统一按用户可读单位，代码层换算为引擎内部标准单位：
# - market_cap 输入单位：亿元（内部为元，×1e8）
# - amount 输入单位：万元（内部为元，×1e4）
# - volume 输入单位：手（内部一致）
# - 其余百分比/无单位字段直接数值

_SYSTEM_PROMPT = """你是选股条件抽取专家。请从用户的选股需求中抽取结构化筛选条件，只输出 JSON，不要输出任何解释。

字段白名单（只能使用以下字段，其他字段忽略不输出）：
- pe_ttm: 市盈率（TTM），无单位
- pb: 市净率，无单位
- turnover_rate: 换手率，百分数（如 5 表示 5%）
- market_cap: 总市值，单位亿元（如 100 表示 100 亿元）
- change_percent: 涨跌幅，百分数（如 3 表示 3%）
- volume: 成交量，单位手（如 100000 表示 10 万手）
- amount: 成交额，单位万元（如 50000 表示 5 亿元）
- amplitude: 振幅，百分数（如 4 表示 4%）
- roe: 净资产收益率，百分数（如 15 表示 15%）
- revenue_growth: 营业收入同比增长，百分数（如 20 表示 20%）
- gross_margin: 毛利率，百分数（如 30 表示 30%）
- rsi24: 相对强弱指标（24 日），无单位（如 30 表示超卖）
- ma20: 20 日均线，无单位（价格）

操作符白名单：">", "<", ">=", "<=", "==", "between"

定性描述映射规则（用户说以下词时输出对应数值条件）：
- 低估值/价值/白马/蓝筹/稳健/分红 → pe_ttm between [0,20]、pb between [0,3]
- 超跌/反弹/抄底/低吸/回调 → change_percent between [-8,-0.5]、amplitude > 4
- 动量/强势/上涨/突破/放量/大涨/活跃 → change_percent between [3,20]、turnover_rate > 1

输出格式：
{"conditions": [{"field": "pe_ttm", "op": "<", "value": 20}, ...], "top_n": 10}

规则：
1. 数值条件优先于定性映射：用户同时给出具体数值条件和定性词时，只输出数值条件
2. 用户输入与选股无关或没有可抽取的条件时，输出 {"conditions": []}
3. top_n 为用户要求的推荐数量（1-100 的正整数），如"推荐10只/选20支/来5个"；用户未指定数量时省略 top_n 字段或输出 null
4. 严格输出合法 JSON，不要 markdown 代码块
"""


def extract_screen_params(user_input: str, provider=None) -> tuple[list, Optional[int]]:
    """从用户提示词中抽取选股条件与推荐数量。

    Args:
        user_input: 用户提示词
        provider: LLM Provider（可选，未提供则自动创建）

    Returns:
        (conditions, top_n)：条件列表与推荐数量（未指定或失败时 top_n 为 None）
    """
    if not user_input or not user_input.strip():
        logger.info("[条件抽取] 用户输入为空，返回空条件与空数量")
        return [], None
    try:
        llm = provider or create_llm_provider()
        prompt = f"用户的选股需求：{user_input.strip()}"
        logger.info("[条件抽取] 开始调用 LLM 抽取选股条件与数量")
        response = llm.invoke(system_prompt=_SYSTEM_PROMPT, user_prompt=prompt)
        data = _parse_llm_response(response)
        conditions = _parse_conditions_data(data)
        top_n = _parse_top_n(data)
        logger.info(f"[条件抽取] 抽取完成，共 {len(conditions)} 条条件，推荐数量 {top_n}")
        return conditions, top_n
    except Exception as e:
        logger.error(f"[条件抽取] LLM 调用失败: {e}", exc_info=True)
        return [], None


def extract_conditions(user_input: str, provider=None) -> list[FieldCondition]:
    """从用户提示词中抽取选股条件。

    Args:
        user_input: 用户提示词
        provider: LLM Provider（可选，未提供则自动创建）

    Returns:
        抽取到的 FieldCondition 列表，无条件或失败时返回空列表
    """
    conditions, _ = extract_screen_params(user_input, provider)
    return conditions


def parse_conditions_json(text: str) -> list[FieldCondition]:
    """解析 LLM 返回的 JSON，转换为 FieldCondition 列表。

    Args:
        text: LLM 返回文本

    Returns:
        合法条件列表（非法字段/操作符/值会被忽略）
    """
    data = _parse_llm_response(text)
    return _parse_conditions_data(data)


def _parse_conditions_data(data: dict) -> list[FieldCondition]:
    """从解析后的 JSON 字典提取条件列表。"""
    if not data:
        return []
    raw_conditions = data.get("conditions")
    if not raw_conditions or not isinstance(raw_conditions, list):
        return []
    conditions = []
    for item in raw_conditions:
        cond = _to_field_condition(item)
        if cond is not None:
            conditions.append(cond)
    return conditions


def _parse_top_n(data: dict) -> Optional[int]:
    """从解析后的 JSON 字典提取推荐数量（top_n）。

    规则：非正整数或无法解析返回 None；超过 MAX_TOP_N 时截断为 MAX_TOP_N。
    """
    if not isinstance(data, dict):
        return None
    raw = data.get("top_n")
    if raw is None:
        return None
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        logger.warning(f"[条件抽取] top_n 无法解析: {raw}")
        return None
    if n <= 0:
        logger.warning(f"[条件抽取] top_n 非正整数，忽略: {n}")
        return None
    if n > MAX_TOP_N:
        logger.warning(f"[条件抽取] top_n {n} 超过上限 {MAX_TOP_N}，截断")
        return MAX_TOP_N
    return n


def _parse_llm_response(text: str) -> dict:
    """解析 LLM 返回的 JSON，支持 markdown 代码块包裹。"""
    text = text.strip() if text else ""
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        logger.warning("[条件抽取] LLM 返回无法解析的 JSON")
        return {}


def _to_field_condition(item) -> Optional[FieldCondition]:
    """字典转 FieldCondition，非法项返回 None。"""
    if not isinstance(item, dict):
        return None
    field = str(item.get("field", "")).strip()
    op = str(item.get("op", "")).strip()
    value = item.get("value")
    if field not in FIELD_WHITELIST:
        logger.warning(f"[条件抽取] 字段 '{field}' 不在白名单，忽略")
        return None
    if op not in OP_WHITELIST:
        logger.warning(f"[条件抽取] 操作符 '{op}' 不合法，忽略")
        return None
    value = _normalize_value(field, value)
    if value is None:
        logger.warning(f"[条件抽取] 字段 '{field}' 的值无法解析: {item.get('value')}")
        return None
    try:
        return FieldCondition(field=field, op=op, value=value)
    except Exception as e:
        logger.warning(f"[条件抽取] 条件校验失败: {e}")
        return None


def _normalize_value(field: str, value) -> Optional[float | list[float]]:
    """值归一化与单位换算。

    Args:
        field: 字段名
        value: 原始值（数值或 [min, max] 列表）

    Returns:
        内部标准单位的值，无法解析时返回 None
    """
    if isinstance(value, list):
        if len(value) != 2:
            return None
        vals = []
        for v in value:
            nv = _normalize_value(field, v)
            if nv is None:
                return None
            vals.append(nv)
        return vals
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if field == "market_cap":
        return num * 1e8  # 亿元 → 元
    if field == "amount":
        return num * 1e4  # 万元 → 元
    return num


def format_condition(cond: FieldCondition) -> str:
    """单条条件格式化为可读字符串。

    Args:
        cond: 条件对象

    Returns:
        如 "PE 0~20"、"市值 > 50亿"、"换手率 > 0.3%"
    """
    display_name, unit = FIELD_DISPLAY.get(cond.field, (cond.field, ""))
    values = cond.value if isinstance(cond.value, list) else [cond.value]
    if cond.field == "market_cap":
        values = [v / 1e8 for v in values]  # 元 → 亿
    elif cond.field == "amount":
        values = [v / 1e4 for v in values]  # 元 → 万元
    fmt_vals = [_fmt_value(v) for v in values]
    if cond.op == "between" and len(fmt_vals) == 2:
        return f"{display_name} {fmt_vals[0]}~{fmt_vals[1]}{unit}"
    return f"{display_name} {cond.op} {fmt_vals[0]}{unit}"


def _fmt_value(v: float) -> str:
    """数值显示：整数不带小数，小数保留最多 2 位。"""
    r = round(float(v), 2)
    return str(int(r)) if r.is_integer() else str(r)


def describe_conditions(conditions: list[FieldCondition]) -> list[dict]:
    """生成条件展示列表（含 display 可读描述）。

    Args:
        conditions: 条件列表

    Returns:
        [{"field", "op", "value", "display"}, ...]
    """
    return [
        {
            "field": cond.field,
            "op": cond.op,
            "value": cond.value,
            "display": format_condition(cond),
        }
        for cond in conditions
    ]
