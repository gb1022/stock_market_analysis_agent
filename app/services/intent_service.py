"""自然语言意图识别服务。

用户通过单一输入框输入需求，由 LLM 识别意图并提取关键信息。
支持：个股分析、智能选股、行情查询。
非股票相关问题返回 UNKNOWN。
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.llm.base import LLMProvider
from app.llm.factory import create_llm_provider
from app.storage.cache import StockCache

logger = logging.getLogger("stock_agent")


class IntentType(str, Enum):
    """支持的意图类型。"""

    ANALYZE = "analyze"  # 个股深度分析
    SCREEN = "screen"    # 智能选股
    QUOTE = "quote"      # 实时行情查询
    UNKNOWN = "unknown"  # 无法识别


@dataclass
class IntentResult:
    """意图识别结果。"""

    intent_type: IntentType
    stock_code: Optional[str] = None    # 个股分析/行情查询用的代码
    stock_name: Optional[str] = None    # 识别到的股票名称
    user_preference: str = ""           # 用户输入中提取的投资偏好/关注点
    position_status: str = ""           # 持仓状态：未买入/已持仓/空（未提及）
    cost_price: Optional[float] = None  # 成本价（已持仓时可选）
    position_quantity: Optional[float] = None  # 持仓数量（股，已持仓时可选）
    confidence: float = 0.0             # 置信度 0~1
    raw_text: str = ""                  # 用户原始输入
    message: str = ""                   # 给用户的提示信息


_INTENT_SYSTEM_PROMPT = """你是一位股票分析助手的意图识别专家。请严格按 JSON 格式输出，不要添加任何解释。

用户输入：{user_input}

请判断用户意图，只输出如下 JSON 结构：
{
  "intent_type": "analyze|screen|quote|unknown",
  "stock_code": "6位数字代码或null",
  "stock_name": "股票名称或null",
  "user_preference": "从用户输入中提取的投资偏好/关注点，如果用户没有提及则填空字符串",
  "position_status": "未买入|已持仓|null",
  "cost_price": "数字或null",
  "position_quantity": "数字或null（单位：股）",
  "confidence": 0.0到1.0之间的数字,
  "message": "给用户的简短提示或null"
}

意图定义：
- analyze：用户想深度分析某只股票（技术面、基本面、资金面等）。
- screen：用户想选股、筛选股票、找符合条件的股票。
- quote：用户只想查询某只股票的实时行情、价格、涨跌幅。
- unknown：用户问题与股票无关，或无法判断。

user_preference 提取规则：
- 从用户输入中提取用户表达的投资偏好、关注方向、选股条件等。
- 例如"分析贵州茅台，重点关注技术面" → user_preference 为 "重点关注技术面"
- 例如"帮我选股，偏好低估值蓝筹股" → user_preference 为 "偏好低估值蓝筹股"
- 例如"看看贵州茅台最近走势" → user_preference 为 ""（没有明确偏好）
- 从用户输入中剥离股票名称和基本操作词（分析/查询/看看/选股等）后的剩余部分。

position_status / cost_price / position_quantity 提取规则：
- 从用户输入中判断用户对该股票的持仓状态。
- 例如"分析贵州茅台，我持有成本1500" → position_status 为 "已持仓"，cost_price 为 1500
- 例如"分析贵州茅台，我持有500股，成本1500" → position_status 为 "已持仓"，cost_price 为 1500，position_quantity 为 500
- 例如"分析平安银行，我还没买，想看看能不能建仓" → position_status 为 "未买入"，cost_price 为 null，position_quantity 为 null
- 例如"帮我深度分析一下贵州茅台"（未提及持仓）→ position_status 为 null，cost_price 为 null，position_quantity 为 null
- 用户未提及任何持仓信息时，position_status、cost_price、position_quantity 都必须为 null。

规则：
1. 如果用户输入的是6位数字股票代码，直接填入 stock_code。
2. 如果只提到股票名称（如"贵州茅台"），填入 stock_name，stock_code 填 null。
3. 如果问题与股票无关（如"今天天气怎么样"、"你好"），必须返回 intent_type="unknown"，confidence 填 0。
4. 严格返回合法 JSON，不要包含 markdown 代码块。
"""


async def classify_intent(
    query: str,
    provider: Optional[LLMProvider] = None,
    cache: Optional[StockCache] = None,
) -> IntentResult:
    """对用户的自然语言输入进行意图识别。

    Args:
        query: 用户输入文本
        provider: LLM Provider（可选，未提供则自动创建）
        cache: 股票列表缓存（可选，用于名称到代码映射）

    Returns:
        IntentResult: 识别结果
    """
    query = query.strip() if query else ""
    if not query:
        logger.info("[意图识别] 输入为空，返回 UNKNOWN")
        return IntentResult(
            intent_type=IntentType.UNKNOWN,
            confidence=0.0,
            raw_text=query,
            message="请输入您的问题",
        )

    # 长度边界
    if len(query) > 500:
        logger.info(f"[意图识别] 输入过长({len(query)}字符)，截断至500字符")
        query = query[:500]

    # 优先尝试规则识别 6 位代码
    rule_code = _extract_code(query)
    if rule_code:
        logger.info(f"[意图识别] 规则提取到代码: {rule_code}")
        # 如果能从缓存验证代码存在，直接判定为个股分析
        if cache and _code_exists(rule_code, cache):
            logger.info(f"[意图识别] 代码 {rule_code} 在缓存中存在，直接返回个股分析")
            return IntentResult(
                intent_type=IntentType.ANALYZE,
                stock_code=rule_code,
                confidence=0.95,
                raw_text=query,
                message=f"已识别股票代码 {rule_code}，正在进入个股分析...",
            )

    # 调用 LLM 识别
    try:
        llm = provider or create_llm_provider()
        # 使用 replace 避免用户输入中的 {} 触发 str.format 异常
        prompt = _INTENT_SYSTEM_PROMPT.replace("{user_input}", query)
        logger.info("[意图识别] 开始调用 LLM 进行意图识别")
        # 同步 LLM 调用转为异步，避免阻塞事件循环
        response = await asyncio.to_thread(llm.invoke, prompt, "")
        logger.info(f"[意图识别] LLM 返回: {response[:200]}")
        parsed = _parse_llm_response(response)
        if parsed:
            logger.info(f"[意图识别] LLM 解析结果: intent_type={parsed.get('intent_type')}, "
                        f"stock_code={parsed.get('stock_code')}, confidence={parsed.get('confidence')}")
        else:
            logger.warning("[意图识别] LLM 返回无法解析的 JSON")
    except Exception as e:
        logger.error(f"[意图识别] LLM 调用失败: {e}", exc_info=True)
        # LLM 失败时降级：如果规则提取到代码仍走分析，否则未知
        if rule_code:
            logger.info(f"[意图识别] LLM 失败，降级使用规则提取的代码: {rule_code}")
            return IntentResult(
                intent_type=IntentType.ANALYZE,
                stock_code=rule_code,
                confidence=0.7,
                raw_text=query,
                message=f"已识别股票代码 {rule_code}，正在进入个股分析...",
            )
        logger.warning("[意图识别] LLM 失败且无规则代码，返回服务不可用")
        return IntentResult(
            intent_type=IntentType.UNKNOWN,
            confidence=0.0,
            raw_text=query,
            message="意图识别服务暂时不可用，请稍后再试",
        )

    # LLM 返回无法解析且规则提取到代码时，降级为个股分析
    if not parsed and rule_code:
        logger.info(f"[意图识别] LLM 未解析，降级使用规则代码: {rule_code}")
        return IntentResult(
            intent_type=IntentType.ANALYZE,
            stock_code=rule_code,
            confidence=0.7,
            raw_text=query,
            message=f"已识别股票代码 {rule_code}，正在进入个股分析...",
        )

    intent_type = _normalize_intent(parsed.get("intent_type", "unknown"))
    stock_code = _normalize_code(parsed.get("stock_code"))
    stock_name = _normalize_text(parsed.get("stock_name"))
    user_preference = _normalize_text(parsed.get("user_preference"), default="")
    position_status = _normalize_position_status(parsed.get("position_status"))
    cost_price = _normalize_cost_price(parsed.get("cost_price"))
    position_quantity = _normalize_position_quantity(parsed.get("position_quantity"))
    confidence = _clamp_confidence(parsed.get("confidence", 0.0))
    message = _normalize_text(parsed.get("message"), default="")

    # 防LLM幻觉验证：只要LLM返回了stock_code，无论intent_type是什么，都需要验证
    # 当stock_name为空时，从缓存查找该代码的真实名称，如果名称和代码都不在用户查询中，则判定为幻觉并拒绝
    if stock_code and not stock_name and cache:
        cached_name = _get_stock_name_by_code(stock_code, cache)
        if cached_name and cached_name not in query and stock_code not in query:
            logger.warning(
                f"[意图识别] LLM返回代码{stock_code}对应「{cached_name}」，"
                f"但用户查询「{query}」中既不包含该名称也不包含该代码，疑似LLM幻觉，拒绝使用"
            )
            return IntentResult(
                intent_type=IntentType.UNKNOWN,
                stock_code=stock_code,
                stock_name=cached_name,
                confidence=0.0,
                raw_text=query,
                message=f"未找到与「{query}」匹配的A股股票，请确认后重新输入需求",
            )

    # 如果 LLM 没给代码但给了名称，尝试本地映射
    if intent_type in (IntentType.ANALYZE, IntentType.QUOTE):
        if not stock_code and stock_name and cache:
            stock_code = _resolve_stock_code(stock_name, cache)
            if stock_code:
                logger.info(f"[意图识别] 通过名称 '{stock_name}' 映射到代码: {stock_code}")
        if not stock_code and not stock_name and rule_code:
            stock_code = rule_code
            logger.info(f"[意图识别] LLM 未返回代码/名称，使用规则提取的代码: {rule_code}")

    # 个股分析 / 行情查询 必须能拿到代码
    if intent_type in (IntentType.ANALYZE, IntentType.QUOTE) and not stock_code:
        # 如果 LLM 识别到了股票名称但无法映射到A股代码，提示不在A股范围内
        if stock_name:
            logger.warning(f"[意图识别] 股票名称 '{stock_name}' 未在A股列表中找到匹配")
            intent_type = IntentType.UNKNOWN
            confidence = 0.0
            message = f"股票「{stock_name}」不在A股分析范围内，请确认后重新输入需求"
        else:
            logger.warning(f"[意图识别] {intent_type.value} 但无股票代码，转为 UNKNOWN")
            intent_type = IntentType.UNKNOWN
            confidence = 0.0
            message = "未能识别到具体的股票代码或名称，请补充股票信息"

    # A股验证：确认股票属于A股范围（同时校验代码与名称是否匹配，防止LLM幻觉）
    if intent_type in (IntentType.ANALYZE, IntentType.QUOTE) and stock_code and cache:
        if not _verify_a_share_stock(stock_code, cache, stock_name=stock_name):
            logger.warning(f"[意图识别] 股票 {stock_code} 不在A股范围内或代码名称不匹配")
            return IntentResult(
                intent_type=IntentType.UNKNOWN,
                stock_code=stock_code,
                stock_name=stock_name,
                confidence=0.0,
                raw_text=query,
                message=f"该股票（{stock_code}）不在A股分析范围内，请确认后重新输入需求",
            )

    # 查询相关性验证：只要LLM返回了stock_code，无论intent_type是什么，都需要验证
    # 确认识别到的股票与用户原始查询相关，防止LLM幻觉
    if stock_code and cache:
        cached_name = _get_stock_name_by_code(stock_code, cache) or ""
        code_in_query = stock_code in query
        name_in_query = (stock_name and stock_name in query) or False
        cached_name_in_query = (cached_name and cached_name in query) or False
        if not code_in_query and not name_in_query and not cached_name_in_query:
            logger.warning(
                f"[意图识别] 查询相关性验证失败: 代码{stock_code}（{cached_name}）"
                f"与用户查询「{query}」无关，疑似LLM幻觉，拒绝使用"
            )
            return IntentResult(
                intent_type=IntentType.UNKNOWN,
                stock_code=stock_code,
                stock_name=cached_name or stock_name,
                confidence=0.0,
                raw_text=query,
                message=f"未找到与「{query}」匹配的A股股票，请确认后重新输入需求",
            )

    # 置信度低于阈值视为未知
    if confidence < 0.6:
        logger.info(f"[意图识别] 置信度({confidence})低于阈值，转为 UNKNOWN")
        intent_type = IntentType.UNKNOWN
        confidence = 0.0
        message = message or "无法识别此问题，请描述与股票相关的需求"

    # LLM 返回 unknown 但规则提取到了有效的股票代码时，降级使用
    if intent_type == IntentType.UNKNOWN and rule_code:
        logger.info(f"[意图识别] LLM 返回 unknown，降级使用规则提取的代码: {rule_code}")
        intent_type = IntentType.ANALYZE
        stock_code = rule_code
        stock_name = stock_name or ""
        confidence = 0.7
        message = f"已识别股票代码 {rule_code}，正在进入个股分析..."

    # LLM 返回 unknown 且无规则代码时，尝试用缓存股票列表匹配用户输入中的股票名称
    if intent_type == IntentType.UNKNOWN and not stock_code and cache:
        matched = _match_stock_name_in_query(query, cache)
        if matched:
            logger.info(f"[意图识别] LLM 返回 unknown，通过名称匹配到股票: {matched}")
            intent_type = IntentType.ANALYZE
            stock_code = matched["code"]
            stock_name = matched["name"]
            confidence = 0.7
            message = f"已识别股票 {matched['name']}（{matched['code']}），正在进入个股分析..."

    # 非股票相关边界
    if intent_type == IntentType.UNKNOWN:
        message = message or "无法识别此问题，请输入股票相关的需求"

    logger.info(f"[意图识别] 最终结果: intent_type={intent_type.value}, "
                f"stock_code={stock_code}, stock_name={stock_name}, "
                f"position_status={position_status}, cost_price={cost_price}, "
                f"position_quantity={position_quantity}, "
                f"confidence={confidence}, message={message}")

    return IntentResult(
        intent_type=intent_type,
        stock_code=stock_code,
        stock_name=stock_name,
        user_preference=user_preference,
        position_status=position_status,
        cost_price=cost_price,
        position_quantity=position_quantity,
        confidence=confidence,
        raw_text=query,
        message=message,
    )


def _extract_code(text: str) -> Optional[str]:
    """从文本中提取 6 位数字股票代码。"""
    # 匹配独立成词的 6 位数字，避免匹配手机号、日期等
    for match in re.finditer(r"(?<![0-9])([0-9]{6})(?![0-9])", text):
        code = match.group(1)
        # 简单过滤：A 股代码以 0/3/6/68/69 开头
        if code[0] in ("0", "3", "6") or code.startswith("68") or code.startswith("69"):
            return code
    return None


def _code_exists(code: str, cache: StockCache) -> bool:
    """判断股票代码是否存在于缓存列表。"""
    stocks = cache.get_stock_list()
    if not stocks:
        return False
    return any(s.get("code") == code for s in stocks)


def _resolve_stock_code(name_or_code: str, cache: StockCache) -> Optional[str]:
    """根据名称或代码在缓存列表中查找最佳匹配。

    如果缓存为空，自动从 AKShare 拉取全量股票列表填充缓存后再查找。
    """
    stocks = cache.get_stock_list()
    if not stocks:
        logger.info("[意图识别-名称映射] 缓存为空，从 AKShare 拉取股票列表")
        try:
            import akshare as ak
            df = ak.stock_info_a_code_name()
            stocks = []
            for _, row in df.iterrows():
                stocks.append({
                    "code": str(row["code"]),
                    "name": str(row["name"]),
                })
            cache.set_stock_list(stocks)
            logger.info(f"[意图识别-名称映射] 拉取完成，共 {len(stocks)} 只股票")
        except Exception as e:
            logger.error(f"[意图识别-名称映射] 拉取股票列表失败: {e}")
            return None

    keyword = name_or_code.strip()
    if not keyword:
        return None

    # 如果是 6 位数字代码，直接匹配
    if re.fullmatch(r"\d{6}", keyword):
        for s in stocks:
            if s.get("code") == keyword:
                return keyword
        return None

    # 名称匹配：优先完整匹配，再前缀匹配，再子串匹配
    candidates = []
    for s in stocks:
        name = s.get("name", "")
        code = s.get("code", "")
        if not name:
            continue
        if name == keyword:
            candidates.append((3, len(name), code, name))
        elif name.startswith(keyword):
            candidates.append((2, len(name), code, name))
        elif keyword in name:
            candidates.append((1, -len(name), code, name))

    if candidates:
        # 按优先级、名称长度排序
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return candidates[0][2]

    return None


def _parse_llm_response(text: str) -> dict:
    """解析 LLM 返回的 JSON，支持 markdown 代码块包裹。"""
    text = text.strip() if text else ""
    if not text:
        return {}

    # 去除 markdown 代码块标记
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    # 尝试提取 {} 内容
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _normalize_intent(value) -> IntentType:
    """归一化意图类型。"""
    if not value:
        return IntentType.UNKNOWN
    value = str(value).strip().lower()
    if value == IntentType.ANALYZE.value:
        return IntentType.ANALYZE
    if value == IntentType.SCREEN.value:
        return IntentType.SCREEN
    if value == IntentType.QUOTE.value:
        return IntentType.QUOTE
    return IntentType.UNKNOWN


def _normalize_code(value) -> Optional[str]:
    """归一化股票代码。"""
    if value is None:
        return None
    code = str(value).strip()
    if code.lower() == "null":
        return None
    if re.fullmatch(r"\d{6}", code):
        return code
    return None


def _normalize_text(value, default: Optional[str] = None) -> Optional[str]:
    """归一化文本字段。"""
    if value is None:
        return default
    text = str(value).strip()
    if text.lower() == "null":
        return default
    return text if text else default


def _normalize_position_status(value) -> str:
    """归一化持仓状态：未买入/已持仓/空字符串（未提及）。"""
    if not value:
        return ""
    text = str(value).strip()
    if text.lower() in ("null", "none", "无"):
        return ""
    # 未买入 / 未持有 / 没有持仓
    if ("未" in text and ("买" in text or "持" in text)) or "没有" in text:
        return "未买入"
    # 已持仓 / 已买入 / 持有中
    if "已" in text or "持仓" in text or "持有" in text:
        return "已持仓"
    return ""


def _normalize_cost_price(value) -> Optional[float]:
    """归一化成本价：非正数或无法解析时返回 None。"""
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return price


def _normalize_position_quantity(value) -> Optional[float]:
    """归一化持仓数量（股）：非正数或无法解析时返回 None。"""
    if value is None:
        return None
    try:
        quantity = float(value)
    except (TypeError, ValueError):
        return None
    if quantity <= 0:
        return None
    return quantity


def _clamp_confidence(value) -> float:
    """将置信度限制在 [0, 1]。"""
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, conf))


def _verify_a_share_stock(code: str, cache: StockCache, stock_name: Optional[str] = None) -> bool:
    """验证股票代码是否属于A股。

    通过缓存的A股列表验证股票代码。如果缓存为空，降级为通过（后续流程会处理）。
    当同时提供 stock_name 时，额外校验代码与名称是否匹配。

    Args:
        code: 6位数字股票代码
        cache: 股票列表缓存
        stock_name: 股票名称（可选），用于校验 code-name 匹配

    Returns:
        True: 是A股股票或无法验证（缓存为空时降级）
        False: 明确不是A股股票，或代码与名称不匹配
    """
    stocks = cache.get_stock_list()
    if not stocks:
        # 缓存为空，无法验证，降级为通过（后续流程会处理）
        logger.warning(f"[意图识别-A股验证] 缓存为空，无法验证代码 {code}，降级为通过")
        return True

    for s in stocks:
        if s.get("code") == code:
            # 代码在A股列表中，进一步校验名称是否匹配
            if stock_name:
                cached_name = s.get("name", "")
                if cached_name and stock_name != cached_name:
                    logger.warning(
                        f"[意图识别-A股验证] 代码 {code} 对应名称为「{cached_name}」，"
                        f"与LLM返回的「{stock_name}」不匹配，疑似LLM幻觉"
                    )
                    return False
            return True

    return False


def _get_stock_name_by_code(code: str, cache: StockCache) -> Optional[str]:
    """根据股票代码从缓存中查找对应的股票名称。"""
    stocks = cache.get_stock_list()
    if not stocks:
        return None
    for s in stocks:
        if s.get("code") == code:
            return s.get("name", "")
    return None


def _match_stock_name_in_query(query: str, cache: StockCache) -> Optional[dict]:
    """在用户输入中搜索缓存股票列表中的股票名称。

    优先完整匹配、再前缀匹配，返回 {code, name} 或 None。
    """
    stocks = cache.get_stock_list()
    if not stocks:
        return None

    query = query.strip()
    if not query:
        return None

    # 按名称长度降序（长名称优先匹配，避免"茅台"先匹配到"茅台股份"之类的误匹配）
    sorted_stocks = sorted(
        stocks,
        key=lambda s: len(s.get("name", "")),
        reverse=True,
    )

    for s in sorted_stocks:
        name = s.get("name", "")
        if not name:
            continue
        if name in query or query.startswith(name):
            return {"code": s.get("code", ""), "name": name}
        # 支持带"股票"后缀的模糊匹配："贵州茅台的股市" → "贵州茅台"
        if name in query.replace("股票", "").replace("行情", "").replace("股价", ""):
            return {"code": s.get("code", ""), "name": name}

    return None
