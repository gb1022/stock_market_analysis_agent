"""基于现成 A股 MCP Server 的数据源实现。

多层降级管理：
- 当前优先：stock-api（Node 版，腾讯/新浪/东财多源自动兜底，实时行情 / 历史K线）
- 旧备选（默认禁用）：akshare-stock-mcp（实时行情 / 历史K线 / 财务摘要）、mcp-eastmoney（实时行情 / 历史K线）

任一请求按配置顺序尝试各 server，出错或超时自动降级到下一层；
全部失败时抛 DataSourceError，交由 Router 继续降级到 AKShare 链路。

资金流、新闻、扩展分析三个方法 MCP 源不提供（社区 MCP 无个股级工具），
直接抛 DataSourceError 交由 Router 降级。
"""

import asyncio
import json
import logging
import os
import re
import sys
import threading
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

import anyio
import mcp.types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.message import SessionMessage

# Windows 下官方 stdio_client 的 TextReceiveStream 存在兼容问题
# （子进程 stdout 中文 JSON 解码失败 / 握手挂起），改用自定义 transport
if sys.platform == "win32":
    from mcp.os.win32.utilities import create_windows_process, terminate_windows_process_tree

from app.data_sources.base import DataSource, DataSourceError
from app.models.stock import Quote, KLine, FinancialData, CapitalFlow, NewsItem, ExtendedAnalysis

logger = logging.getLogger("stock_agent")


# 已知 MCP Server 的默认工具映射（未配置时按 name 推断）
DEFAULT_SERVER_TOOLS: dict[str, dict] = {
    "akshare-stock": {
        "code_param": "symbol",
        "kline_mode": "date_range",
        "tools": {
            "quote": "get_stock_realtime",
            "kline": "get_stock_history",
            "financial": "get_stock_financial_abstract",
        },
    },
    "mcp-eastmoney": {
        "code_param": "code",
        "kline_mode": "count",
        "tools": {
            "quote": "get_stock_quote",
            "kline": "get_kline",
            "financial": "get_stock_financial_abstract",
        },
    },
    # stock-api：腾讯/新浪/东财多源自动兜底，行情与 K 线稳定
    "stock-api": {
        "code_param": "code",
        "code_style": "sh_sz",  # 代码需转换为 SH600519 / SZ000651 格式
        "kline_mode": "count",
        "tools": {
            "quote": "get_stock",
            "kline": "get_klines",
            "financial": None,  # 财务数据由 Router 降级到 AKShare 链路
        },
    },
    # china-stock-mcp：财务指标 + 新闻（akshare-one 多源故障切换）
    "china-stock-mcp": {
        "code_param": "symbol",
        "output_format": "json",  # 显式 json 输出便于结构化解析（默认 markdown 表格）
        "tools": {
            "quote": None,
            "kline": None,
            "financial": "get_financial_metrics",
            "capital_flow": None,
            "news": "get_news_data",
        },
    },
    # stock-sdk-mcp：行情（PE/PB）+ 带指标K线 + 资金流向（多源自动兜底，不依赖东财）
    "stock-sdk-mcp": {
        "code_param": "codes",
        "code_style": "sh_sz_lower",
        "tools": {
            "quote": "get_a_share_quotes",
            "kline": "get_kline_with_indicators",
            "financial": None,
            "capital_flow": "get_stock_fund_flow_history",
            "news": None,
        },
    },
}


class MCPClient:
    """单个 MCP Server 的客户端（线程 + 事件循环 + Session 生命周期管理）。

    通过 stdio 启动 MCP Server 子进程，事件循环运行在独立线程中，
    同步调用方通过 run_coroutine_threadsafe 桥接异步 Session。
    """

    def __init__(self, name: str, command: str, args: list[str],
                 timeout: float = 30.0) -> None:
        """初始化。

        Args:
            name: server 名称
            command: 启动命令（如 python / uvx）
            args: 启动参数（如 ["-m", "akshare_stock_mcp.server"]）
            timeout: 调用超时秒数
        """
        self.name = name
        # Windows 下子进程 stdout 默认 GBK 编码 + 管道全缓冲，
        # 会导致 MCP 中文 JSON 解码失败、JSON-RPC 响应不 flush 使握手挂起。
        # 强制子进程 UTF-8 输出且无缓冲（需保留父进程环境，否则 PATH 丢失）
        self._params = StdioServerParameters(
            command=command,
            args=args,
            env={
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
            },
        )
        self._timeout = timeout
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session: Optional[ClientSession] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._process: Optional[Any] = None
        self._ready = threading.Event()
        self._start_error: Optional[Exception] = None

    def start(self) -> None:
        """启动子进程并建立 MCP Session。

        Raises:
            DataSourceError: 启动超时或失败时抛出
        """
        if self._loop is not None and self._loop.is_running():
            return
        self._ready.clear()
        self._start_error = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name=f"mcp-{self.name}", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=self._timeout):
            logger.error(f"[MCP] server {self.name} 启动超时（{self._timeout}s）")
            raise DataSourceError(f"MCP server {self.name} 启动超时（{self._timeout}s）")
        if self._start_error is not None:
            logger.error(f"[MCP] server {self.name} 启动失败: {self._start_error}")
            raise DataSourceError(f"MCP server {self.name} 启动失败: {self._start_error}")

    def _run_loop(self) -> None:
        """事件循环线程入口。"""
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._bootstrap())
        except Exception as e:
            logger.error(f"[MCP] server {self.name} 事件循环异常: {e}")
            self._start_error = e
            self._ready.set()
        finally:
            self._loop.close()

    async def _bootstrap(self) -> None:
        """建立 stdio 连接与 Session，等待 stop 信号后优雅关闭。

        Windows 下官方 stdio_client 的 TextReceiveStream 存在兼容问题
        （中文 JSON 解码失败 / 握手挂起），因此 Windows 走自定义 transport；
        其余平台使用官方 stdio_client。
        """
        if sys.platform == "win32":
            await self._bootstrap_windows()
        else:
            await self._bootstrap_stdio()

    async def _bootstrap_stdio(self) -> None:
        """非 Windows 平台：使用官方 stdio_client。"""
        async with stdio_client(self._params) as (read_stream, write_stream):
            session = ClientSession(read_stream, write_stream)
            await session.initialize()
            self._session = session
            self._ready.set()
            self._shutdown_event = asyncio.Event()
            await self._shutdown_event.wait()
            try:
                await session.aclose()
            finally:
                self._session = None

    async def _bootstrap_windows(self) -> None:
        """Windows 平台：自定义 stdio transport。

        使用 create_windows_process 启动子进程（UTF-8 环境 + 无缓冲），
        手动读取 stdout 按行解析 JSON-RPC、写入 stdin，
        规避官方 stdio_client 在 Windows 上的解码/缓冲兼容问题。
        """
        process = await create_windows_process(
            self._params.command,
            self._params.args,
            env=self._params.env,
            errlog=sys.stderr,
            cwd=str(self._params.cwd) if self._params.cwd else None,
        )
        self._process = process

        read_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_reader = anyio.create_memory_object_stream(0)

        async def stdout_reader() -> None:
            buffer = b""
            while True:
                try:
                    chunk = await process.stdout.receive()
                except (anyio.ClosedResourceError, anyio.EndOfStream):
                    break
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = types.JSONRPCMessage.model_validate_json(line)
                    except Exception:
                        # 部分 MCP Server（如 FastMCP 3.x）会把日志/banner 打印到 stdout，
                        # 非 JSON-RPC 行直接跳过，避免 TaskGroup 异常导致启动失败
                        logger.debug(
                            "[MCP] server %s 忽略非 JSON-RPC stdout 行: %.200s",
                            self.name, line.decode("utf-8", "replace"),
                        )
                        continue
                    await read_writer.send(SessionMessage(msg))

        async def stdin_writer() -> None:
            async for sm in write_reader:
                data = (
                    sm.message.model_dump_json(by_alias=True, exclude_none=True)
                    + "\n"
                ).encode("utf-8")
                await process.stdin.send(data)

        async with anyio.create_task_group() as tg:
            tg.start_soon(stdout_reader)
            tg.start_soon(stdin_writer)
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                self._session = session
                self._ready.set()
                self._shutdown_event = asyncio.Event()
                await self._shutdown_event.wait()
            tg.cancel_scope.cancel()
        # 关闭子进程：先终止进程树（job object 一并清理子进程），再关闭流
        try:
            await terminate_windows_process_tree(process, timeout_seconds=5)
        except Exception as e:
            logger.warning(f"[MCP] server {self.name} 子进程终止失败: {e}")
        try:
            await process.aclose()
        except Exception as e:
            logger.warning(f"[MCP] server {self.name} 子进程流关闭失败: {e}")
        finally:
            self._process = None
            self._session = None

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """同步调用 MCP 工具（线程安全，带超时）。

        Args:
            tool_name: MCP 工具名
            arguments: 工具入参

        Returns:
            解析后的 JSON 数据（dict / list / str）

        Raises:
            DataSourceError: 未初始化、调用失败或超时时抛出
        """
        if self._session is None:
            raise DataSourceError(f"MCP server {self.name} 未初始化")
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._session.call_tool(tool_name, arguments), self._loop
            )
            result = future.result(timeout=self._timeout)
            return self._parse_result(result, tool_name)
        except DataSourceError:
            raise
        except Exception as e:
            logger.warning(f"[MCP] server {self.name} 调用 {tool_name} 失败: {e}")
            raise DataSourceError(
                f"MCP server {self.name} 调用 {tool_name} 失败: {e}"
            ) from e

    @staticmethod
    def _parse_result(result: Any, tool_name: str) -> Any:
        """解析 CallToolResult，提取文本内容并尝试解析 JSON。

        Args:
            result: MCP call_tool 返回值
            tool_name: 工具名（用于错误提示）

        Returns:
            dict / list / str

        Raises:
            DataSourceError: 工具返回错误或内容为空时抛出
        """
        if getattr(result, "isError", False):
            raise DataSourceError(f"MCP tool {tool_name} 返回错误")
        texts = []
        for content in getattr(result, "content", []) or []:
            if getattr(content, "type", "") == "text":
                texts.append(getattr(content, "text", ""))
        text = "\n".join(texts).strip()
        if not text:
            raise DataSourceError(f"MCP tool {tool_name} 返回空内容")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def list_tools(self) -> list[dict]:
        """同步列出 MCP Server 提供的工具（含入参 schema）。

        Returns:
            工具列表：[{"name", "description", "input_schema"}]

        Raises:
            DataSourceError: 未初始化或列出失败时抛出
        """
        if self._session is None:
            raise DataSourceError(f"MCP server {self.name} 未初始化")
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._session.list_tools(), self._loop
            )
            result = future.result(timeout=self._timeout)
        except DataSourceError:
            raise
        except Exception as e:
            logger.warning(f"[MCP] server {self.name} 列出工具失败: {e}")
            raise DataSourceError(
                f"MCP server {self.name} 列出工具失败: {e}"
            ) from e
        tools = []
        for t in getattr(result, "tools", []) or []:
            tools.append({
                "name": getattr(t, "name", ""),
                "description": getattr(t, "description", "") or "",
                "input_schema": getattr(t, "inputSchema", {}) or {},
            })
        return tools

    def stop(self) -> None:
        """优雅关闭 Session、子进程和事件循环。"""
        if self._loop is None or not self._loop.is_running():
            return

        async def _signal_shutdown() -> None:
            if self._shutdown_event is not None:
                self._shutdown_event.set()

        try:
            asyncio.run_coroutine_threadsafe(_signal_shutdown(), self._loop).result(timeout=5)
        except Exception as e:
            logger.warning(f"[MCP] server {self.name} 关闭信号发送失败: {e}")
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._loop = None
        self._thread = None
        self._shutdown_event = None


class MCPDataSource(DataSource):
    """基于现成 A股 MCP Server 的数据源（按配置顺序多层降级）。

    当前优先：stock-api（多源自动兜底）；旧备选默认禁用（akshare-stock / mcp-eastmoney）。
    覆盖：实时行情 / 历史K线 / 财务摘要。
    资金流、新闻、扩展分析不提供，抛 DataSourceError 交由 Router 降级。
    """

    name = "mcp"

    def __init__(self, servers: Optional[list] = None,
                 timeout: float = 30.0,
                 server_configs: Optional[list[dict]] = None) -> None:
        """初始化。

        Args:
            servers: MCP 客户端列表（按优先级排序，第一层在前）
            timeout: 调用超时秒数
            server_configs: 与 servers 一一对应的工具配置；为空时按 name 推断
        """
        self.servers = servers or []
        self._timeout = timeout
        if server_configs is None:
            server_configs = [
                DEFAULT_SERVER_TOOLS.get(
                    s.name, DEFAULT_SERVER_TOOLS["akshare-stock"]
                )
                for s in self.servers
            ]
        self._configs = server_configs
        self._started = False

    # ---- 生命周期 ----

    def _ensure_started(self) -> None:
        """惰性启动所有 MCP server（单个失败不阻塞，交由调用降级）。"""
        if self._started:
            return
        for client in self.servers:
            start = getattr(client, "start", None)
            if not callable(start):
                continue
            try:
                start()
            except Exception as e:
                logger.warning(f"[MCP] server {client.name} 启动失败: {e}")
        self._started = True

    def close(self) -> None:
        """关闭所有 MCP server 连接。"""
        for client in self.servers:
            stop = getattr(client, "stop", None)
            if not callable(stop):
                continue
            try:
                stop()
            except Exception as e:
                logger.warning(f"[MCP] 关闭 {client.name} 失败: {e}")

    # ---- 通用调用 ----

    def _call_any(self, data_type: str,
                  build_args: Callable[[dict], dict]) -> Any:
        """按序尝试各 server 调用指定数据类型的工具，直到成功。

        Args:
            data_type: 数据类型（quote / kline / financial）
            build_args: 根据 server 配置构造调用参数的函数

        Returns:
            工具返回的原始数据（dict / list）

        Raises:
            DataSourceError: 所有 server 均失败时抛出
        """
        self._ensure_started()
        last_error: Optional[Exception] = None
        for client, cfg in zip(self.servers, self._configs):
            tool_name = (cfg.get("tools") or {}).get(data_type)
            if not tool_name:
                last_error = DataSourceError(f"{client.name} 不提供 {data_type} 工具")
                continue
            try:
                raw = client.call_tool(tool_name, build_args(cfg))
                if raw is None:
                    raise DataSourceError(f"{client.name} 返回空数据")
                # akshare-stock-mcp 失败时返回 {"error": "...", "data": []}，
                # 含 error 字段视为失败，继续降级到下一层
                if isinstance(raw, dict) and raw.get("error"):
                    raise DataSourceError(f"{client.name} 返回错误: {raw['error']}")
                return raw
            except Exception as e:
                logger.warning(f"[MCP] {client.name} 获取 {data_type} 失败: {e}")
                last_error = e
        raise DataSourceError(f"MCP 获取 {data_type} 失败: {last_error}")

    # ---- DataSource 接口 ----

    def get_quote(self, code: str) -> Quote:
        """获取实时行情。"""
        def build_args(cfg: dict) -> dict:
            formatted = self._format_code(cfg, code)
            # stock-sdk-mcp 的 get_a_share_quotes 参数为 codes（列表）
            if cfg.get("tools", {}).get("quote") == "get_a_share_quotes":
                return {"codes": [formatted]}
            return {cfg["code_param"]: formatted}

        raw = self._call_any("quote", build_args)
        return self._to_quote(code, raw)

    def get_kline(self, code: str, period: str = "daily",
                  days: int = 250) -> list[KLine]:
        """获取 K 线数据。"""
        def build_args(cfg: dict) -> dict:
            tool_name = (cfg.get("tools") or {}).get("kline")
            if tool_name == "get_kline_with_indicators":
                # stock-sdk-mcp：symbol 需要纯数字代码（无前缀），用日期范围控制条数
                # 注意：code_param 是 quote 工具的参数名（如 codes），kline 工具固定用 symbol
                clean_code = code.strip().lower().replace("sh", "").replace("sz", "")
                args = {"symbol": clean_code}
                # 多取一些天数以确保指标计算有足够历史数据（MA20 需要至少 20 条）
                start = (date.today() - timedelta(days=int(days * 1.5))).strftime("%Y%m%d")
                end = date.today().strftime("%Y%m%d")
                args.update({
                    "period": "daily",
                    "startDate": start,
                    "endDate": end,
                    "adjust": "qfq",
                    "indicators": {
                        "ma": {"periods": [5, 10, 20]},
                        "macd": True,
                        "rsi": True,
                    },
                })
                return args
            args = {cfg["code_param"]: self._format_code(cfg, code)}
            mode = cfg.get("kline_mode", "date_range")
            if mode == "count":
                # stock-api / mcp-eastmoney：按数量取最近 N 条
                args.update({
                    "period": self._kline_period(cfg, period),
                    "count": days,
                })
                if cfg.get("code_style") == "sh_sz":
                    # stock-api 默认不复权，显式指定前复权与 akshare-stock 保持一致
                    args["adjust"] = "qfq"
                return args
            # akshare-stock：按日期范围取，前复权
            start = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
            end = date.today().strftime("%Y%m%d")
            args.update({
                "period": period,
                "start_date": start,
                "end_date": end,
                "adjust": "qfq",
            })
            return args

        raw = self._call_any("kline", build_args)
        return self._to_klines(raw)

    def get_financial(self, code: str) -> FinancialData:
        """获取财务数据。"""
        def build_args(cfg: dict) -> dict:
            args = {cfg["code_param"]: self._format_code(cfg, code)}
            fmt = cfg.get("output_format")
            if fmt:
                # china-stock-mcp 等支持显式指定输出格式，json 便于结构化解析
                args["output_format"] = fmt
            return args

        raw = self._call_any("financial", build_args)
        return self._to_financial(code, raw)

    @staticmethod
    def _format_code(cfg: dict, code: str) -> str:
        """按 server 的代码格式要求转换股票代码。

        - sh_sz:     大写前缀，如 SH600519（stock-api）
        - sh_sz_lower: 小写前缀，如 sh600519（stock-sdk-mcp 的 get_a_share_quotes）
        - 无前缀:    纯数字，如 600519（stock-sdk-mcp 的 kline/capital_flow）
        """
        style = cfg.get("code_style")
        if style not in ("sh_sz", "sh_sz_lower"):
            return code
        clean = code.strip().lower().replace("sh", "").replace("sz", "")
        prefix = "SH" if clean.startswith("6") or clean.startswith("9") else "SZ"
        if style == "sh_sz_lower":
            prefix = prefix.lower()
        return f"{prefix}{clean}"

    @staticmethod
    def _kline_period(cfg: dict, period: str) -> str:
        """按 server 的 K 线周期命名转换（stock-api 用 day/week/month，其余用 daily 等）。"""
        if cfg.get("code_style") != "sh_sz":
            return period
        return {"daily": "day", "weekly": "week", "monthly": "month"}.get(period, period)

    def get_capital_flow(self, code: str, days: int = 10) -> list[CapitalFlow]:
        """获取资金流向数据（优先 stock-sdk-mcp，失败交由 Router 降级）。"""
        def build_args(cfg: dict) -> dict:
            if (cfg.get("tools") or {}).get("capital_flow") == "get_stock_fund_flow_history":
                # stock-sdk-mcp：该工具参数名为 symbol（非 code_param），
                # 需要纯数字代码（无前缀，与 get_kline 保持一致），按日返回，默认全部交易日
                clean_code = code.strip().lower().replace("sh", "").replace("sz", "")
                return {"symbol": clean_code, "period": "daily"}
            return {cfg["code_param"]: self._format_code(cfg, code)}

        raw = self._call_any("capital_flow", build_args)
        flows = self._to_capital_flows(code, raw)
        # 返回数据按日期升序排列，取最近 days 条
        return flows[-days:] if days > 0 else flows

    def get_news(self, code: str, limit: int = 10) -> list[NewsItem]:
        """获取新闻/公告（优先 china-stock-mcp，失败交由 Router 降级）。"""
        def build_args(cfg: dict) -> dict:
            args = {cfg["code_param"]: self._format_code(cfg, code)}
            fmt = cfg.get("output_format")
            if fmt:
                args["output_format"] = fmt
            return args

        raw = self._call_any("news", build_args)
        news = self._to_news(raw)
        return news[:limit] if limit > 0 else news

    def get_extended_analysis(self, code: str, quote: Quote,
                              capital_flows: list[CapitalFlow],
                              news_list: list[NewsItem]) -> ExtendedAnalysis:
        """获取扩展分析数据（MCP 源不提供，交由 Router 降级）。"""
        raise DataSourceError("MCP 数据源不提供扩展分析数据，交由 Router 降级")

    # ---- 字段映射（宽松解析，兼容不同 server 返回风格） ----

    @staticmethod
    def _pick(raw: dict, *keys: str, default: Any = None) -> Any:
        """从 dict 中按候选 key 取第一个非空值。"""
        for key in keys:
            if key in raw and raw[key] is not None:
                return raw[key]
        return default

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> Optional[float]:
        """安全转换为 float，解析失败返回默认值。"""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        """安全转换为 int。"""
        if value is None:
            return default
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _to_date(value: Any) -> date:
        """解析日期字符串为 date，兼容多种格式，失败返回今天。"""
        if value is None:
            return date.today()
        if isinstance(value, date):
            return value
        text = str(value)[:19]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except (ValueError, TypeError):
                continue
        return date.today()

    def _to_quote(self, code: str, raw: Any) -> Quote:
        """MCP 行情返回 → Quote。"""
        # stock-sdk-mcp 返回 list，取第一个元素
        if isinstance(raw, list):
            if not raw:
                raise DataSourceError("行情返回空列表")
            raw = raw[0]
        if not isinstance(raw, dict):
            raise DataSourceError(f"行情返回格式异常: {type(raw)}")
        # stock-api 返回 {"input":..., "response": {"stock": {...}}}，先解包到行情对象
        resp = raw.get("response")
        if isinstance(resp, dict):
            raw = resp.get("stock") if isinstance(resp.get("stock"), dict) else resp
        change_amount = self._to_float(self._pick(raw, "change", "涨跌额"))
        change_percent = self._to_float(self._pick(raw, "change_pct", "change_percent", "changePercent", "涨跌幅"))
        if change_percent == 0.0 and "percent" in raw and raw["percent"] is not None:
            # stock-api 的 percent 为小数（0.01 表示 1%），需换算为百分数
            change_percent = self._to_float(raw["percent"]) * 100
        pe_ttm = self._to_float(self._pick(raw, "pe", "pe_ttm", "市盈率"), default=None)
        # mcp-eastmoney 的 get_stock_quote 存在字段错位：pe 字段实际填入涨跌额。
        # 当 pe 与涨跌额完全一致时判定为错位数据，置 None 以保证数据真实性
        if pe_ttm is not None and change_amount != 0 and abs(pe_ttm - change_amount) < 0.01:
            pe_ttm = None
        # stock-sdk-mcp 的市值单位为亿元（如 16164.68 表示 16164.68 亿），需转换为元
        market_cap = self._to_float(self._pick(raw, "market_cap", "totalMarketCap", "总市值"), default=None)
        if market_cap is not None and market_cap < 1e10:
            # 小于 100 亿视为亿元单位，转换为元
            market_cap = market_cap * 1e8
        circulating_market_cap = self._to_float(
            self._pick(raw, "circulating_market_cap", "circ_market_cap", "circulatingMarketCap", "流通市值"),
            default=None,
        )
        if circulating_market_cap is not None and circulating_market_cap < 1e10:
            circulating_market_cap = circulating_market_cap * 1e8
        return Quote(
            code=code,
            name=self._pick(raw, "name", "股票名称", "名称", default=""),
            latest_price=self._to_float(self._pick(raw, "price", "latest", "latest_price", "最新价", "close", "now")),
            change_percent=change_percent,
            change_amount=change_amount,
            open_price=self._to_float(self._pick(raw, "open", "open_price", "今开")),
            high_price=self._to_float(self._pick(raw, "high", "high_price", "最高")),
            low_price=self._to_float(self._pick(raw, "low", "low_price", "最低")),
            pre_close=self._to_float(self._pick(raw, "prev_close", "pre_close", "昨收", "yesterday")),
            volume=self._to_int(self._pick(raw, "volume", "成交量")),
            amount=self._to_float(self._pick(raw, "amount", "成交额")),
            turnover_rate=self._to_float(self._pick(raw, "turnoverRate", "turnover_rate", "换手率")),
            amplitude=self._to_float(self._pick(raw, "amplitude", "振幅")),
            pe_ttm=pe_ttm,
            pb=self._to_float(self._pick(raw, "pb", "市净率"), default=None),
            market_cap=market_cap,
            circulating_market_cap=circulating_market_cap,
            source=self.name,
            fetched_at=datetime.now(),
        )

    def _to_klines(self, raw: Any) -> list[KLine]:
        """MCP K 线返回（dict 含 data 字段或 list）→ KLine 列表。"""
        # stock-api 返回 {"input":..., "response": {"count": n, "klines": [...]}}，先解包 response
        if isinstance(raw, dict) and isinstance(raw.get("response"), dict):
            raw = raw["response"]
        items = raw
        if isinstance(raw, dict):
            for key in ("data", "klines", "records", "list", "items"):
                if isinstance(raw.get(key), list):
                    items = raw[key]
                    break
            else:
                # dict 但无列表字段 → 视为空
                return []
        if not isinstance(items, list):
            return []

        klines = []
        for row in items:
            if not isinstance(row, dict):
                continue
            # 解析技术指标（stock-sdk-mcp 的 get_kline_with_indicators 返回）
            ma_data = row.get("ma", {}) or {}
            macd_data = row.get("macd", {}) or {}
            rsi_data = row.get("rsi", {}) or {}
            
            klines.append(KLine(
                trade_date=self._to_date(self._pick(row, "date", "trade_date", "日期", "day", "时间")),
                open_price=self._to_float(self._pick(row, "open", "open_price", "开盘", "o")),
                high=self._to_float(self._pick(row, "high", "high_price", "最高", "h")),
                low=self._to_float(self._pick(row, "low", "low_price", "最低", "l")),
                close=self._to_float(self._pick(row, "close", "close_price", "收盘", "c")),
                volume=self._to_int(self._pick(row, "volume", "成交量", "vol", "v")),
                amount=self._to_float(self._pick(row, "amount", "成交额")),
                # 技术指标（保持 None 表示未提供，避免 0.0 被误认为有效值）
                ma5=self._to_float(ma_data.get("ma5"), default=None),
                ma10=self._to_float(ma_data.get("ma10"), default=None),
                ma20=self._to_float(ma_data.get("ma20"), default=None),
                dif=self._to_float(macd_data.get("dif"), default=None),
                dea=self._to_float(macd_data.get("dea"), default=None),
                macd=self._to_float(macd_data.get("macd"), default=None),
                rsi6=self._to_float(rsi_data.get("rsi6"), default=None),
                rsi12=self._to_float(rsi_data.get("rsi12"), default=None),
                rsi24=self._to_float(rsi_data.get("rsi24"), default=None),
            ))
        return klines

    def _to_capital_flows(self, code: str, raw: Any) -> list[CapitalFlow]:
        """MCP 资金流返回 → CapitalFlow 列表。

        stock-sdk-mcp 返回 {"data": [{"date", "mainNetInflow", "mainNetInflowPercent",
        "smallNetInflow", "mediumNetInflow", "largeNetInflow", ...}]}，
        散户净流入按 小单+中单 近似计算。
        """
        # 调试日志：打印原始返回结构
        logger.info(f"[资金流] 原始返回类型: {type(raw).__name__}")
        if isinstance(raw, dict):
            logger.info(f"[资金流] 顶层 keys: {list(raw.keys())}")
            # 尝试找到数据列表
            for key in ("data", "records", "list", "items"):
                val = raw.get(key)
                if isinstance(val, list):
                    logger.info(f"[资金流] 找到数据列表 key='{key}', 长度={len(val)}")
                    if val:
                        logger.info(f"[资金流] 第一条记录 keys: {list(val[0].keys()) if isinstance(val[0], dict) else type(val[0]).__name__}")
                        logger.info(f"[资金流] 第一条记录内容: {json.dumps(val[0], ensure_ascii=False)[:500]}")
                    break
        elif isinstance(raw, list):
            logger.info(f"[资金流] 直接返回列表，长度={len(raw)}")
            if raw:
                logger.info(f"[资金流] 第一条记录 keys: {list(raw[0].keys()) if isinstance(raw[0], dict) else type(raw[0]).__name__}")

        items = raw
        if isinstance(raw, dict):
            for key in ("data", "records", "list", "items"):
                if isinstance(raw.get(key), list):
                    items = raw[key]
                    break
            else:
                return []
        if not isinstance(items, list):
            return []

        flows = []
        for row in items:
            if not isinstance(row, dict):
                continue
            small = self._to_float(self._pick(row, "smallNetInflow", "small_net_inflow"))
            medium = self._to_float(self._pick(row, "mediumNetInflow", "medium_net_inflow"))
            small_pct = self._to_float(self._pick(row, "smallNetInflowPercent", "small_net_inflow_rate"))
            medium_pct = self._to_float(self._pick(row, "mediumNetInflowPercent", "medium_net_inflow_rate"))
            flows.append(CapitalFlow(
                trade_date=self._to_date(self._pick(row, "date", "trade_date", "日期")),
                code=code,
                main_net_inflow=self._to_float(
                    self._pick(row, "mainNetInflow", "main_net_inflow"), default=None),
                main_net_inflow_rate=self._to_float(
                    self._pick(row, "mainNetInflowPercent", "main_net_inflow_rate"), default=None),
                retail_net_inflow=(small + medium) if (small or medium) else None,
                retail_net_inflow_rate=(small_pct + medium_pct) if (small_pct or medium_pct) else None,
                large_order_net_inflow=self._to_float(
                    self._pick(row, "largeNetInflow", "large_order_net_inflow"), default=None),
                large_order_net_inflow_rate=self._to_float(
                    self._pick(row, "largeNetInflowPercent", "large_order_net_inflow_rate"), default=None),
                north_net_inflow=self._to_float(
                    self._pick(row, "northNetInflow", "north_net_inflow"), default=None),
                north_net_inflow_rate=self._to_float(
                    self._pick(row, "northNetInflowPercent", "north_net_inflow_rate"), default=None),
                source=self.name,
            ))
        return flows

    def _to_news(self, raw: Any) -> list[NewsItem]:
        """MCP 新闻返回 → NewsItem 列表。

        china-stock-mcp 返回 [{"keyword", "title", "content", "publish_time"(毫秒), "source", "url"}]。
        """
        items = raw
        if isinstance(raw, dict):
            for key in ("data", "records", "list", "items"):
                if isinstance(raw.get(key), list):
                    items = raw[key]
                    break
            else:
                return []
        if not isinstance(items, list):
            return []

        news = []
        for row in items:
            if not isinstance(row, dict):
                continue
            news.append(NewsItem(
                title=self._pick(row, "title", "标题", default=""),
                date=self._to_datetime(self._pick(row, "publish_time", "pub_time", "date", "时间")),
                summary=self._pick(row, "content", "summary", "摘要", default=""),
                source=self._pick(row, "source", "来源", default=""),
                url=self._pick(row, "url", "link", "链接", default=""),
            ))
        return news

    @staticmethod
    def _to_datetime(value: Any) -> Optional[datetime]:
        """解析时间（支持秒/毫秒时间戳与常见日期格式），解析失败返回 None。"""
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 1e12:
                # china-stock-mcp 的 publish_time 为毫秒时间戳
                timestamp /= 1000.0
            try:
                return datetime.fromtimestamp(timestamp)
            except (ValueError, OSError, OverflowError):
                return None
        text = str(value)[:19]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(text, fmt)
            except (ValueError, TypeError):
                continue
        return None

    # AKShare 财务长表指标名 → FinancialData 字段映射
    _FINANCIAL_LONG_TABLE_MAP: dict[str, str] = {
        "净资产收益率(ROE)": "roe",
        "总资产报酬率(ROA)": "roa",
        "毛利率": "gross_margin",
        "销售净利率": "net_margin",
        "资产负债率": "debt_ratio",
        "基本每股收益": "eps",
        "每股净资产": "bvps",
        "营业总收入增长率": "revenue_growth",
        "归属母公司净利润增长率": "profit_growth",
    }

    @staticmethod
    def _financial_latest_values(raw: dict) -> dict:
        """从 AKShare 财务长表提取最新报告期的各指标值。

        长表形如 {"count": 80, "data": [{"指标": "净资产收益率(ROE)", "20260331": 12.3, ...}]}，
        报告期列名为 8 位数字（YYYYMMDD）。返回 {指标名: 最新报告期值}。
        """
        result: dict = {}
        rows = raw.get("data")
        if not isinstance(rows, list):
            return result
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("指标")
            if not name:
                continue
            periods = [k for k in row.keys() if re.fullmatch(r"\d{8}", str(k))]
            if not periods:
                continue
            result[str(name)] = row[max(periods)]
        return result

    @staticmethod
    def _financial_rows_to_values(rows: list) -> dict:
        """从指标列表（china-stock-mcp json 输出）提取各指标最新非空报告期值。

        列表形如 [{"选项": "常用指标", "指标": "归母净利润", "20260331": 27242512886.45, ...}]，
        报告期列名为 8 位数字（YYYYMMDD），按期倒序取第一个非空值。
        返回 {指标名: 最新非空报告期值}。
        """
        result: dict = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("指标")
            if not name:
                continue
            periods = sorted(
                (k for k in row.keys() if re.fullmatch(r"\d{8}", str(k))),
                reverse=True,
            )
            for period in periods:
                value = row[period]
                if value is not None and value != "":
                    result[str(name)] = value
                    break
        return result

    def _to_financial(self, code: str, raw: Any) -> FinancialData:
        """MCP 财务摘要返回 → FinancialData。

        兼容三种返回格式：
        1. 扁平 dict（如 mcp-eastmoney）：直接含 pe/pb/roe 等字段
        2. AKShare 财务长表（akshare-stock）：{"count": n, "data": [指标 x 报告期]}
        3. 指标列表（china-stock-mcp, output_format=json）：[{"指标": ..., "20260331": ...}]
        """
        if isinstance(raw, list):
            long_values = self._financial_rows_to_values(raw)
        elif isinstance(raw, dict):
            long_values = self._financial_latest_values(raw)
        else:
            raise DataSourceError(f"财务返回格式异常: {type(raw)}")
        if long_values:
            def long_value(field: str) -> Optional[float]:
                for name, target in self._FINANCIAL_LONG_TABLE_MAP.items():
                    if target == field and name in long_values:
                        return self._to_float(long_values[name], default=None)
                return None

            return FinancialData(
                code=code,
                name=self._pick(raw, "name", "股票名称", "名称", default=""),
                roe=long_value("roe"),
                roa=long_value("roa"),
                gross_margin=long_value("gross_margin"),
                net_margin=long_value("net_margin"),
                debt_ratio=long_value("debt_ratio"),
                eps=long_value("eps"),
                bvps=long_value("bvps"),
                revenue_growth=long_value("revenue_growth"),
                profit_growth=long_value("profit_growth"),
                source=self.name,
                fetched_at=datetime.now(),
            )
        if not isinstance(raw, dict):
            raise DataSourceError(f"财务返回格式异常: {type(raw)}")
        return FinancialData(
            code=code,
            name=self._pick(raw, "name", "股票名称", "名称", default=""),
            pe=self._to_float(self._pick(raw, "pe", "pe_ttm", "市盈率"), default=None),
            pe_ttm=self._to_float(self._pick(raw, "pe_ttm", "pe", "市盈率"), default=None),
            pb=self._to_float(self._pick(raw, "pb", "市净率"), default=None),
            roe=self._to_float(self._pick(raw, "roe", "净资产收益率"), default=None),
            roa=self._to_float(self._pick(raw, "roa", "总资产收益率"), default=None),
            gross_margin=self._to_float(self._pick(raw, "gross_margin", "毛利率"), default=None),
            net_margin=self._to_float(self._pick(raw, "net_margin", "净利率"), default=None),
            revenue_growth=self._to_float(
                self._pick(raw, "revenue_growth", "营业收入同比增长", "营收同比"),
                default=None,
            ),
            profit_growth=self._to_float(
                self._pick(raw, "profit_growth", "净利润同比增长", "净利同比"),
                default=None,
            ),
            debt_ratio=self._to_float(self._pick(raw, "debt_ratio", "资产负债率"), default=None),
            eps=self._to_float(self._pick(raw, "eps", "每股收益"), default=None),
            bvps=self._to_float(self._pick(raw, "bvps", "每股净资产"), default=None),
            source=self.name,
            fetched_at=datetime.now(),
        )


def build_mcp_data_source(cfg: dict) -> Optional[MCPDataSource]:
    """从配置构建 MCP 数据源。

    Args:
        cfg: config 中的 mcp 配置段

    Returns:
        MCPDataSource 实例；未启用或无可用 server 时返回 None
    """
    if not cfg.get("enabled", False):
        logger.info("[MCP] mcp 数据源未启用")
        return None

    timeout = float(cfg.get("timeout", 30))
    clients = []
    configs = []
    for server_cfg in cfg.get("servers", []):
        if not server_cfg.get("enabled", True):
            logger.info(f"[MCP] server {server_cfg.get('name', 'mcp')} 已禁用，跳过")
            continue
        tools_cfg = server_cfg.get("tools", {}) or {}
        clients.append(MCPClient(
            name=server_cfg.get("name", "mcp"),
            command=server_cfg.get("command", "python"),
            args=server_cfg.get("args", []),
            timeout=timeout,
        ))
        configs.append({
            "code_param": server_cfg.get("code_param", "code"),
            "code_style": server_cfg.get("code_style", "plain"),
            "kline_mode": tools_cfg.get("kline_mode", "date_range"),
            "output_format": tools_cfg.get("output_format") or server_cfg.get("output_format"),
            "tools": {
                "quote": tools_cfg.get("quote"),
                "kline": tools_cfg.get("kline"),
                "financial": tools_cfg.get("financial"),
                "capital_flow": tools_cfg.get("capital_flow"),
                "news": tools_cfg.get("news"),
            },
        })

    if not clients:
        logger.warning("[MCP] 无可用 MCP server，跳过 MCP 数据源")
        return None

    return MCPDataSource(servers=clients, timeout=timeout, server_configs=configs)
