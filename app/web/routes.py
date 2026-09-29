"""Web 路由定义。

| 路径 | 功能 |
|------|------|
| GET / | 首页 |
| GET /screener | 选股页 |
| POST /api/screen | 选股 API |
| POST /api/analyze/{code} | 个股分析 API |
| GET /api/stock/search?q= | 股票搜索补全 |
| GET /api/workflow/graph | Mermaid 流程图 |
"""

import json
import logging
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.data_sources.base import DataSourceError
from app.graph.workflow import create_stock_agent_workflow
from app.screener.conditions import ConditionGroup, FieldCondition
from app.screener.condition_extractor import describe_conditions, extract_screen_params
from app.screener.strategies import get_strategy, STRATEGY_MAP
from app.services.intent_service import classify_intent
from app.services.stock_service import analyze_single_stock, analyze_single_stock_stream
from app.web.deps import get_cache, get_data_source_router

import os

logger = logging.getLogger("stock_agent")

# 模板目录
_templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_templates_dir)

router = APIRouter()


class ScreenRequest(BaseModel):
    """选股请求体"""
    conditions: list[dict] = []
    strategy: Optional[str] = None
    top_n: int = 30
    user_input: str = ""  # 用户的投资意见


class AnalyzeRequest(BaseModel):
    """分析请求体"""
    user_input: str = ""  # 用户的投资意见/关注点
    position_status: str = ""  # 持仓状态：未买入/已持仓（可选）
    cost_price: Optional[float] = None  # 成本价（已持仓时可选）
    position_quantity: Optional[float] = None  # 持仓数量（股，已持仓时可选）
    force_refresh: bool = False  # 是否强制刷新（跳过缓存）


def _fmt_number(value) -> str:
    """数字格式化：整数去掉小数部分，如 500.0 → "500"，1500.5 → "1500.5"。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(f)) if f.is_integer() else str(f)


def _compose_user_input(user_input: str = "", position_status: str = "",
                        cost_price: Optional[float] = None,
                        position_quantity: Optional[float] = None) -> str:
    """将持仓状态/成本价/持仓数量与用户关注点合并为传给 LLM 的 user_input。

    无持仓信息时原样透传 user_input（兼容旧行为）。

    Args:
        user_input: 用户填写的关注点/投资意见
        position_status: 持仓状态（未买入/已持仓/空字符串）
        cost_price: 成本价（已持仓时可填）
        position_quantity: 持仓数量（股，已持仓时可填）

    Returns:
        合并后的 user_input 文本（如"【持仓状态】已持仓，成本价1500元，持仓500股；【关注点】重点关注技术面"）
    """
    if not position_status:
        return user_input
    details = []
    if cost_price:
        details.append(f"成本价{_fmt_number(cost_price)}元")
    if position_quantity:
        details.append(f"持仓{_fmt_number(position_quantity)}股")
    detail_text = f"，{'，'.join(details)}" if details else ""
    position_part = f"【持仓状态】{position_status}{detail_text}"
    if user_input:
        return f"{position_part}；【关注点】{user_input}"
    return position_part


class IntentRequest(BaseModel):
    """自然语言意图识别请求体"""
    query: str = ""  # 用户原始输入


# ---- 页面路由 ----

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """首页。"""
    try:
        graph_mermaid = _get_mermaid_graph()
    except Exception:
        graph_mermaid = ""

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "mermaid_graph": graph_mermaid,
        },
    )


@router.get("/screener", response_class=HTMLResponse)
async def screener_page(request: Request):
    """选股页面。"""
    return templates.TemplateResponse(
        request,
        "screener.html",
        {
            "strategies": STRATEGY_MAP,
        },
    )


@router.get("/analysis/{code}", response_class=HTMLResponse)
async def analysis_page(code: str, request: Request):
    """个股分析页面。

    Args:
        code: 股票代码（6 位数字，如 600519）
        request: 请求对象
    """
    return templates.TemplateResponse(
        request,
        "analysis.html",
        {
            "code": code,
        },
    )


# ---- API 路由 ----

@router.post("/api/screen")
async def api_screen(req: ScreenRequest, request: Request):
    """选股 API。

    条件构建优先级（v2.6.0）：
      自定义条件(conditions) > 用户提示词抽取的条件 > 预设策略(strategy) > 默认条件
    用户提示词优先，抽取不到相关条件时才降级使用默认条件。

    筛选执行（v2.13.0）：
      三阶段筛选——阶段一用实时行情字段全市场粗筛得候选池（coarse_pool_size），
      阶段二经 DataSourceRouter 拉取财务/技术指标对候选池精排，输出配额为
      用户要求数量 × 3（上限 50），阶段三调用 LLM 从精排候选池中按用户要求
      精选最终推荐股票并生成建议。
    """
    logger.info(f"[选股] 开始执行, strategy={req.strategy}, top_n={req.top_n}, user_input={req.user_input[:100] if req.user_input else ''}")
    try:
        conditions: Optional[ConditionGroup] = None
        condition_source = ""
        source_label = ""
        prompt_top_n: Optional[int] = None

        # 优先级 1：自定义条件
        if req.conditions:
            logger.info(f"[选股] 使用自定义条件: {len(req.conditions)} 条")
            conditions = ConditionGroup()
            for c in req.conditions:
                conditions.add(FieldCondition(
                    field=c["field"],
                    op=c["op"],
                    value=c["value"],
                ))
            condition_source = "custom"
            source_label = "自定义条件"

        # 优先级 2：用户提示词抽取的条件
        if condition_source == "" and req.user_input and req.user_input.strip():
            extracted, prompt_top_n = extract_screen_params(req.user_input)
            if extracted:
                logger.info(f"[选股] 从提示词抽取条件: {len(extracted)} 条, top_n={prompt_top_n}")
                conditions = ConditionGroup()
                for cond in extracted:
                    conditions.add(cond)
                condition_source = "prompt_extracted"
                source_label = "来自你的提示词"
            else:
                logger.info("[选股] 提示词未抽取到相关条件，继续降级")

        # 优先级 3：预设策略
        if condition_source == "" and req.strategy:
            if req.strategy not in STRATEGY_MAP:
                logger.warning(f"[选股] 未知策略: {req.strategy}")
                raise HTTPException(status_code=400, detail=f"未知策略: {req.strategy}")
            conditions = get_strategy(req.strategy)
            condition_source = "strategy"
            source_label = f"预设策略（{STRATEGY_MAP[req.strategy]}）"
            logger.info(f"[选股] 使用预设策略: {req.strategy}")

        # 优先级 4：默认条件（用户提交了提示词但无相关条件时）
        if condition_source == "" and req.user_input and req.user_input.strip():
            logger.info("[选股] 无可用条件，使用默认价值策略")
            conditions = get_strategy("value")
            condition_source = "default"
            source_label = "默认条件"

        if conditions is None:
            raise HTTPException(status_code=400, detail="请提供选股条件或选择预设策略")

        # 执行两阶段筛选（v2.9.0）；推荐数量优先级：提示词数量 > 请求参数 > 默认 30
        from app.config import load_config
        from app.screener.engine import ScreenerEngine
        screener_cfg = load_config().get("screener", {})
        coarse_pool_size = int(screener_cfg.get("coarse_pool_size", 100))
        max_workers = int(screener_cfg.get("max_workers", 10))
        effective_top_n = prompt_top_n or req.top_n or 30

        engine = ScreenerEngine(max_workers=max_workers)
        # 阶段一：实时行情全市场粗筛，得到候选池
        coarse_results = engine.scan(conditions, top_n=coarse_pool_size)
        # 阶段二：经 DataSourceRouter 拉取财务/技术指标后精排（无精排字段时原样截断）
        # v2.13.0：阶段二输出配额 = 用户要求数量 × 3，上限 50，为阶段三精选提供挑选空间
        from app.screener.final_select import compute_rank_pool_size, llm_final_select
        rank_pool_size = compute_rank_pool_size(effective_top_n)
        router = get_data_source_router(request)
        rank_pool = engine.rank_candidates(coarse_results, conditions, router, top_n=rank_pool_size)
        logger.info(f"[选股] 执行完成, 粗筛 {len(coarse_results)} 只候选, 精排返回 {len(rank_pool)} 只, 配额={rank_pool_size}")

        # 阶段三：LLM 精选推荐（v2.13.0）：从精排候选池中按用户要求精选最终推荐并生成建议
        # 替代 v2.12.0 的 opinion.py 意见生成；失败时兜底取前 effective_top_n 只，不阻塞返回
        results = rank_pool
        if rank_pool:
            import asyncio
            from app.llm.factory import create_llm_provider
            try:
                llm_provider = create_llm_provider()
                results = await asyncio.to_thread(
                    llm_final_select, rank_pool, llm_provider,
                    effective_top_n, req.user_input,
                )
            except Exception as e:
                logger.warning(f"[选股] 阶段三 LLM 精选失败，降级返回精排前 {effective_top_n} 只: {e}")
                results = rank_pool[:effective_top_n]

        return {
            "results": [r.model_dump() for r in results],
            "total": len(results),
            "strategy": req.strategy or "custom",
            "user_input": req.user_input,
            "condition_source": condition_source,
            "source_label": source_label,
            "conditions": describe_conditions(conditions.conditions),
        }

    except ValueError as e:
        logger.error(f"[选股] 参数错误: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[选股] 执行异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"选股失败: {str(e)}")


@router.post("/api/analyze/{code}")
async def api_analyze(code: str, req: AnalyzeRequest = None, request: Request = None):
    """个股分析 API。

    支持传入用户投资意见（可选），返回分析报告 + 执行日志 + LLM 调用记录。
    支持 force_refresh 参数强制跳过缓存重新拉取数据。
    """
    user_input = _compose_user_input(
        user_input=(req.user_input if req else ""),
        position_status=(req.position_status if req else ""),
        cost_price=(req.cost_price if req else None),
        position_quantity=(req.position_quantity if req else None),
    )
    force_refresh = req.force_refresh if req else False
    logger.info(f"[个股分析] 开始分析, code={code}, user_input={user_input[:100] if user_input else ''}, force_refresh={force_refresh}")
    try:
        # 强制刷新：清除该股票的所有缓存数据
        cache = get_cache(request) if request else None
        if force_refresh and cache:
            _clear_stock_cache(cache, code)
            logger.info(f"[个股分析] 已清除 {code} 的缓存数据，将重新拉取")

        result = analyze_single_stock(code, user_input=user_input)
        report_len = len(result.get("report", ""))
        log_count = len(result.get("logs", []))
        llm_count = len(result.get("llm_records", []))
        logger.info(f"[个股分析] 分析完成, code={code}, 报告长度={report_len}, 日志条数={log_count}, LLM调用次数={llm_count}")

        # 保存分析结果摘要到缓存（永久存储）
        if cache and result.get("extended_analysis"):
            try:
                from app.config import load_config
                cfg = load_config()
                max_count = cfg.get("cache", {}).get("analysis_summary_max_count", 50)
                _save_analysis_summary(cache, code, result, max_count)
                logger.info(f"[个股分析] 已保存 {code} 的分析摘要到缓存")
            except Exception as e:
                logger.warning(f"[个股分析] 保存分析摘要失败: {e}")

        return result
    except Exception as e:
        logger.error(f"[个股分析] 分析异常, code={code}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"分析失败: {str(e)}")


@router.post("/api/analyze-stream/{code}")
async def api_analyze_stream(code: str, req: AnalyzeRequest = None, request: Request = None):
    """流式个股分析 API（SSE）。

    通过 Server-Sent Events 实时推送分析进度、日志和最终报告。
    分析完成后保存摘要到缓存（永久存储）。
    """
    user_input = _compose_user_input(
        user_input=(req.user_input if req else ""),
        position_status=(req.position_status if req else ""),
        cost_price=(req.cost_price if req else None),
        position_quantity=(req.position_quantity if req else None),
    )
    logger.info(f"[流式分析] SSE 连接建立, code={code}, user_input={user_input[:100] if user_input else ''}")

    def event_generator():
        # 获取缓存实例和配置
        cache = get_cache(request) if request else None
        max_count = 100
        if cache:
            try:
                from app.config import load_config
                cfg = load_config()
                max_count = cfg.get("cache", {}).get("analysis_summary_max_count", 100)
            except Exception:
                pass

        for event in analyze_single_stock_stream(code, user_input=user_input):
            yield f"event: {event['type']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

            # 分析完成后保存摘要到缓存（使用流式分析返回的数据，避免重复调用 LLM）
            if event['type'] == 'complete' and cache:
                try:
                    ea_data = event['data'].get('extended_analysis')
                    if ea_data:
                        # 构造与 _save_analysis_summary 兼容的结果结构
                        result = {
                            "extended_analysis": ea_data,
                            "state": {
                                "stock_name": "",
                                # 决策结果（含两场景投资建议），随摘要一并缓存
                                "selected_plan": event['data'].get("selected_plan"),
                            },
                            "data_collection": {},
                        }
                        _save_analysis_summary(cache, code, result, max_count)
                        logger.info(f"[流式分析] 已保存 {code} 的分析摘要到缓存")
                except Exception as e:
                    logger.warning(f"[流式分析] 保存分析摘要失败: {e}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/api/stock/search")
async def api_stock_search(request: Request, q: str = Query("", description="搜索关键字")):
    """股票搜索补全（优先使用缓存，减少 AKShare 全量拉取）。"""
    if not q or len(q) < 1:
        return {"results": []}

    logger.info(f"[股票搜索] 关键字: {q}")
    try:
        # 从缓存获取全量股票列表
        cache = getattr(request.app.state, "cache", None)
        all_stocks = cache.get_stock_list() if cache else None

        # 缓存未命中，从 AKShare 拉取并缓存
        if all_stocks is None:
            logger.info(f"[股票搜索] 缓存未命中, 从 AKShare 拉取全量股票列表")
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            all_stocks = []
            for _, row in df.iterrows():
                all_stocks.append({
                    "code": str(row["代码"]),
                    "name": str(row["名称"]),
                })
            # 写入缓存
            if cache:
                cache.set_stock_list(all_stocks)
            logger.info(f"[股票搜索] AKShare 拉取完成, 共 {len(all_stocks)} 只股票")

        # 在前端搜索（不重复拉取）
        results = [s for s in all_stocks if q in s["code"] or q in s["name"]]
        logger.info(f"[股票搜索] 匹配结果: {len(results[:10])} 条")
        return {"results": results[:10]}
    except Exception as e:
        logger.error(f"[股票搜索] 异常: {e}", exc_info=True)
        return {"results": []}


@router.get("/api/workflow/graph")
async def api_workflow_graph():
    """返回 LangGraph 流水线的 Mermaid 流程图。"""
    try:
        graph_mermaid = _get_mermaid_graph()
        return {"graph": graph_mermaid}
    except Exception as e:
        return {"graph": "", "error": str(e)}


@router.post("/api/intent")
async def api_intent(req: IntentRequest, request: Request):
    """自然语言意图识别入口。

    支持：个股分析、智能选股、行情查询。
    非股票相关问题返回无法识别。
    """
    logger.info(f"[意图识别] 收到用户输入: {req.query[:200]}")
    try:
        cache = get_cache(request)
        result = await classify_intent(req.query, cache=cache)
        logger.info(f"[意图识别] 识别结果: intent_type={result.intent_type.value}, stock_code={result.stock_code}, "
                    f"stock_name={result.stock_name}, confidence={result.confidence}, "
                    f"message={result.message}")
        return {
            "intent_type": result.intent_type.value,
            "stock_code": result.stock_code,
            "stock_name": result.stock_name,
            "user_preference": result.user_preference,
            "position_status": result.position_status,
            "cost_price": result.cost_price,
            "position_quantity": result.position_quantity,
            "confidence": result.confidence,
            "message": result.message,
            "raw_text": result.raw_text,
        }
    except Exception as e:
        logger.error(f"[意图识别] 异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"意图识别失败: {str(e)}")


@router.get("/api/quote/{code}")
async def api_quote(code: str, request: Request):
    """实时行情查询接口。"""
    if not re.fullmatch(r"\d{6}", code):
        logger.warning(f"[行情查询] 无效股票代码: {code}")
        raise HTTPException(status_code=400, detail="股票代码必须是 6 位数字")
    logger.info(f"[行情查询] 查询股票: {code}")
    try:
        router = get_data_source_router(request)
        quote = router.get_quote(code)
        logger.info(f"[行情查询] 结果: {quote.name}({quote.code}), 最新价={quote.latest_price}, 涨跌幅={quote.change_percent}%")
        return {
            "code": quote.code,
            "name": quote.name,
            "latest_price": quote.latest_price,
            "change_percent": quote.change_percent,
            "change_amount": quote.change_amount,
            "open_price": quote.open_price,
            "high_price": quote.high_price,
            "low_price": quote.low_price,
            "pre_close": quote.pre_close,
            "volume": quote.volume,
            "amount": quote.amount,
            "turnover_rate": quote.turnover_rate,
            "pe_ttm": quote.pe_ttm,
            "pb": quote.pb,
            "source": quote.source,
        }
    except DataSourceError as e:
        logger.error(f"[行情查询] 数据源异常, code={code}: {e}")
        raise HTTPException(status_code=503, detail=f"行情获取失败: {str(e)}")
    except Exception as e:
        logger.error(f"[行情查询] 异常, code={code}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"行情查询失败: {str(e)}")


@router.get("/api/health")
async def api_health():
    """健康检查。"""
    return {"status": "ok"}


@router.get("/api/cached-stocks")
async def api_cached_stocks(request: Request):
    """获取已分析过的股票列表（用于主页展示）。

    返回最近有分析摘要缓存的股票，按分析时间倒序排列。
    """
    try:
        cache = get_cache(request) if request else None
        if not cache:
            return {"results": []}

        # 获取全量股票列表（用于补全股票名称）
        stock_list = cache.get_stock_list()
        stock_name_map = {}
        if stock_list:
            stock_name_map = {s["code"]: s["name"] for s in stock_list if "code" in s}

        # 查询所有有分析摘要缓存的股票（按分析时间倒序，最多 20 条）
        cursor = cache._conn.execute(
            "SELECT DISTINCT stock_code, data_json, fetched_at FROM stock_cache "
            "WHERE data_type = 'analysis_summary' "
            "ORDER BY fetched_at DESC LIMIT 20"
        )
        rows = cursor.fetchall()

        results = []
        for row in rows:
            stock_code = row[0]
            fetched_at = row[2]
            # 从全量股票列表中补全名称
            stock_name = stock_name_map.get(stock_code, "")
            try:
                data = json.loads(row[1] or "{}")
                results.append({
                    "code": stock_code,
                    "name": data.get("stock_name") or stock_name,
                    "fetched_at": fetched_at,
                    "investment_value": data.get("investment_value", ""),
                    "investment_value_score": data.get("investment_value_score"),
                    "risk_level": data.get("risk_level", ""),
                    "capital_flow_direction": data.get("capital_flow_direction", ""),
                    "is_active_stock": data.get("is_active_stock", False),
                    "hot_themes": data.get("hot_themes", []),
                    # 投资建议（v2.10.0）：整体建议 + 未买入/已持仓两场景建议
                    "recommendation": data.get("recommendation", ""),
                    "not_holding_advice": data.get("not_holding_advice"),
                    "holding_advice": data.get("holding_advice"),
                })
            except Exception:
                results.append({"code": stock_code, "name": stock_name, "fetched_at": fetched_at})

        return {"results": results}
    except Exception as e:
        logger.error(f"[已缓存列表] 查询失败: {e}", exc_info=True)
        return {"results": []}


# ---- 内部方法 ----


def _save_analysis_summary(cache, code: str, result: dict, max_count: int = 50) -> None:
    """从分析结果中提取摘要并保存到缓存（永久存储）。

    Args:
        cache: 缓存实例
        code: 股票代码
        result: analyze_single_stock 的返回结果
        max_count: 缓存数量上限
    """
    ea = result.get("extended_analysis")
    if not ea:
        return

    # 获取股票名称
    state = result.get("state", {})
    stock_name = state.get("stock_name", "")
    # 尝试从 data_collection 中获取名称
    dc = result.get("data_collection", {})
    if not stock_name and dc:
        stock_name = dc.get("quote", {}).get("name", "")

    # 提取决策阶段的投资建议（整体建议 + 未买入/已持仓两场景建议，v2.10.0）
    selected_plan = state.get("selected_plan") or {}

    summary = {
        "stock_code": code,
        "stock_name": stock_name,
        "investment_value": ea.get("investment_value", ""),
        "investment_value_score": ea.get("investment_value_score"),
        "investment_value_reason": ea.get("investment_value_reason", ""),
        "risk_level": ea.get("risk_level", ""),
        "risk_analysis_summary": ea.get("risk_analysis_summary", ""),
        "capital_flow_direction": ea.get("capital_flow_direction", ""),
        "capital_flow_summary": ea.get("capital_flow_summary", ""),
        "fund_capital_direction": ea.get("fund_capital_direction", ""),
        "fund_analysis_summary": ea.get("fund_analysis_summary", ""),
        "social_security_direction": ea.get("social_security_direction", ""),
        "social_security_analysis_summary": ea.get("social_security_analysis_summary", ""),
        "shareholder_count_change": ea.get("shareholder_count_change", ""),
        "shareholder_analysis_summary": ea.get("shareholder_analysis_summary", ""),
        "is_active_stock": ea.get("is_active_stock", False),
        "active_stock_score": ea.get("active_stock_score"),
        "active_stock_reason": ea.get("active_stock_reason", ""),
        "hot_themes": ea.get("hot_themes", []),
        "potential_risks": ea.get("potential_risks", []),
        "user_input": state.get("user_input", ""),
        # 投资建议（v2.10.0）：整体建议 + 未买入/已持仓两场景建议
        "recommendation": selected_plan.get("recommendation", ""),
        "not_holding_advice": selected_plan.get("not_holding_advice"),
        "holding_advice": selected_plan.get("holding_advice"),
    }

    cache.set_analysis_summary(code, summary, max_count=max_count)


def _get_mermaid_graph() -> str:
    """生成 Mermaid 流程图。"""
    try:
        workflow = create_stock_agent_workflow()
        return workflow.get_graph().draw_mermaid()
    except Exception:
        return ""


def _clear_stock_cache(cache, code: str) -> None:
    """清除指定股票的缓存数据（包括所有数据类型）。"""
    try:
        deleted = cache.clear_stock_cache(code)
        logger.info(f"[缓存清理] 已删除 {code} 的 {deleted} 条缓存记录")
    except Exception as e:
        logger.error(f"[缓存清理] 清除 {code} 缓存失败: {e}")
