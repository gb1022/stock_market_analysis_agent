"""FastAPI 应用入口。"""

import json
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.config import load_config
from app.data_sources.akshare_source import AKShareSource
from app.data_sources.eastmoney_source import EastmoneySource
from app.data_sources.sina_source import SinaSource
from app.data_sources.tencent_source import TencentSource
from app.data_sources.mcp_source import build_mcp_data_source
from app.data_sources.router import DataSourceRouter
from app.graph.data_collection import set_data_source_router
from app.storage.cache import StockCache

# ---- 日志配置 ----
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(filename)s:%(funcName)s:%(lineno)d - %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("stock_agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。

    启动时：初始数据源、缓存、注入 Router。
    关闭时：清理资源。
    """
    # 启动
    cfg = load_config()

    # 初始化缓存
    db_path = cfg.get("storage", {}).get("db_path", "data/stock_cache.db")
    cache = StockCache(db_path=db_path)

    # 覆盖缓存策略（从配置读取）
    cache_cfg = cfg.get("cache", {})
    stock_list_ttl = cache_cfg.get("stock_list_ttl_seconds")
    if stock_list_ttl:
        cache.update_policy("stock_list", int(stock_list_ttl))

    # 读取分析摘要缓存数量上限配置
    analysis_summary_max_count = cache_cfg.get("analysis_summary_max_count", 50)

    # 初始化数据源：MCP 优先（默认开启，位于降级链首级）
    # MCP 两层（akshare-stock-mcp → mcp-eastmoney）均失败/超时时，
    # Router 自动降级到下方 AKShare / Eastmoney / Tencent 链路
    sources = []
    mcp_cfg = cfg.get("mcp", {})
    mcp_source = build_mcp_data_source(mcp_cfg) if mcp_cfg else None
    if mcp_source is not None:
        logger.info("[MCP] 启用 MCP 数据源（第一层优先，位于降级链首级）")
        sources.append(mcp_source)

    sources.append(AKShareSource())

    # 按优先级初始化数据源（akshare -> eastmoney -> sina -> tencent）
    # sina 提供资金流（新浪接口，东财不可达时可用）；
    # tencent 资金流返回空列表会中断降级链，故必须放在 sina 之后
    priority = cfg.get("data_sources", {}).get("priority", ["akshare", "eastmoney", "sina", "tencent"])
    if "eastmoney" in priority:
        sources.append(EastmoneySource())
    if "sina" in priority:
        sources.append(SinaSource())
    if "tencent" in priority:
        sources.append(TencentSource())

    router = DataSourceRouter(sources)
    router.set_cache(cache)

    # 注入到 data_collection 节点
    set_data_source_router(router)

    app.state.router = router
    app.state.cache = cache

    # 预热缓存：提前拉取全量股票列表（名称→代码映射用）
    _warm_stock_cache(cache)

    yield

    # 关闭
    if hasattr(app.state, "cache") and app.state.cache:
        app.state.cache._conn.close()
    # 关闭 MCP 数据源（子进程/事件循环清理）
    if hasattr(app.state, "router") and app.state.router:
        for source in getattr(app.state.router, "_sources", []):
            close = getattr(source, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as e:
                    logger.warning(f"[MCP] 关闭数据源失败: {e}")


def _warm_stock_cache(cache) -> None:
    """启动时预热股票列表缓存。

    策略：
    1. 优先使用缓存（即使 TTL 过期）——不阻塞启动
    2. 后台做增量更新：拉取最新列表，只合并新增的股票
    3. 完全没有缓存时才全量拉取
    """
    # 第一步：尝试使用现有缓存（即使过期）
    stale_list = cache.get_stock_list_stale()
    if stale_list is not None:
        logger.info(f"[缓存预热] 使用缓存数据（{len(stale_list)} 只股票），后台增量更新...")
        try:
            _incremental_update_stock_list(cache)
        except Exception as e:
            logger.warning(f"[缓存预热] 增量更新失败（缓存数据仍可用）: {e}")
        return

    # 第二步：完全没有缓存 → 全量拉取
    try:
        logger.info("[缓存预热] 无缓存，开始全量拉取股票列表...")
        import akshare as ak
        import time
        t0 = time.time()
        df = ak.stock_info_a_code_name()
        stocks = []
        for _, row in df.iterrows():
            stocks.append({
                "code": str(row["code"]),
                "name": str(row["name"]),
            })
        cache.set_stock_list(stocks)
        elapsed = time.time() - t0
        logger.info(f"[缓存预热] 完成，共 {len(stocks)} 只股票，耗时 {elapsed:.1f} 秒")
    except Exception as e:
        logger.warning(f"[缓存预热] 全量拉取失败（首次请求时自动重试）: {e}")


def _incremental_update_stock_list(cache) -> None:
    """增量更新股票列表缓存。

    拉取最新全量列表，与缓存对比，只合并新增股票。
    不阻塞启动流程。
    """
    import time
    t0 = time.time()
    import akshare as ak
    df = ak.stock_info_a_code_name()
    new_stocks = []
    for _, row in df.iterrows():
        new_stocks.append({
            "code": str(row["code"]),
            "name": str(row["name"]),
        })
    added = cache.merge_stock_list(new_stocks)
    elapsed = time.time() - t0
    if added > 0:
        logger.info(f"[缓存预热] 增量更新完成，新增 {added} 只股票，耗时 {elapsed:.1f}秒")
    else:
        logger.info(f"[缓存预热] 股票列表无变化，刷新过期时间，耗时 {elapsed:.1f}秒")


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例。"""
    app = FastAPI(
        title="智能股票分析助手",
        description="基于 LangGraph 多阶段推理的 A 股智能分析系统",
        version="1.0.0",
        lifespan=lifespan,
    )

    # ---- 全局请求日志中间件 ----
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """记录所有 HTTP 请求的入参和路径。"""
        # 收集请求信息
        method = request.method
        path = request.url.path
        query = str(request.url.query) if request.url.query else ""
        client_ip = request.client.host if request.client else "unknown"

        # 跳过静态文件请求，避免刷屏
        if path.startswith("/static/"):
            return await call_next(request)

        # 读取请求 body（用于 POST/PUT），读取后需重置
        body_bytes = await request.body()
        body_str = ""
        if body_bytes and method in ("POST", "PUT"):
            try:
                body_str = json.dumps(json.loads(body_bytes), ensure_ascii=False)
            except (json.JSONDecodeError, UnicodeDecodeError):
                body_str = body_bytes.decode("utf-8", errors="replace")[:500]

        # 打印请求日志
        log_parts = [f"[请求] {method} {path}"]
        if query:
            log_parts.append(f"查询参数: {query}")
        if body_str:
            # body 过长时截断
            display_body = body_str if len(body_str) <= 1000 else body_str[:1000] + "...(截断)"
            log_parts.append(f"请求体: {display_body}")
        log_parts.append(f"客户端: {client_ip}")
        logger.info(" | ".join(log_parts))

        # 执行请求
        response = await call_next(request)

        # 记录响应状态
        logger.info(f"[响应] {method} {path} -> {response.status_code}")
        return response

    # 注册路由（延迟导入避免循环依赖）
    from app.web.routes import router as web_router
    app.include_router(web_router)

    # 挂载静态文件
    import os
    static_dir = os.path.join(os.path.dirname(__file__), "web", "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


app = create_app()
