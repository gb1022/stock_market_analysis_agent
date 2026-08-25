"""验证 akshare-stock-mcp 与 mcp-eastmoney 两个 MCP Server 的数据拉取能力。

用法：在项目根目录执行 `python verify_mcp.py [股票代码]`
分别直连两个 MCP Server，各自调用 quote（实时行情）与 kline（历史K线），
输出每项的 成功/失败 结果，用于排查 MCP 数据源问题。
"""

import logging
import sys
from datetime import date, timedelta

# 确保能导入 app 包
sys.path.insert(0, ".")

from app.data_sources.base import DataSourceError
from app.data_sources.mcp_source import MCPClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("stock_agent")

# 与 config/default.yaml 中 mcp.servers 配置保持一致
SERVER_SPECS = [
    {
        "name": "akshare-stock",
        "command": "python",
        "args": ["-u", "-X", "utf8", "-m", "akshare_stock_mcp.server"],
        "quote_tool": "get_stock_realtime",
        "kline_tool": "get_stock_history",
    },
    {
        "name": "mcp-eastmoney",
        "command": "mcp-eastmoney",
        "args": [],
        "quote_tool": "get_stock_quote",
        "kline_tool": "get_kline",
    },
    {
        "name": "stock-api",
        "command": "C:\\Program Files\\nodejs\\node.exe",
        # stock-api mcp 子命令：node 绝对路径直接启动，规避 npx/cmd 在 CreateProcess 下的兼容问题
        "args": [
            "C:\\Users\\Admin\\AppData\\Roaming\\npm\\node_modules\\stock-api\\dist\\cli.js",
            "mcp",
        ],
        "quote_tool": "get_stock",
        "kline_tool": "get_klines",
    },
]


def to_stock_api_code(code: str) -> str:
    """转换为 stock-api 代码格式（SH600519 / SZ000651）。"""
    code = code.strip().lower().replace("sh", "").replace("sz", "")
    prefix = "SH" if code.startswith("6") or code.startswith("9") else "SZ"
    return f"{prefix}{code}"


def build_quote_args(name: str, code: str) -> dict:
    """按 server 的 code_param 构造行情入参。"""
    if name == "akshare-stock":
        return {"symbol": code}
    if name == "stock-api":
        return {"code": to_stock_api_code(code)}
    return {"code": code}


def build_kline_args(name: str, code: str) -> dict:
    """按 server 的 kline 模式构造 K 线入参（akshare 按日期范围、eastmoney 按数量）。"""
    if name == "akshare-stock":
        start = (date.today() - timedelta(days=250)).strftime("%Y%m%d")
        end = date.today().strftime("%Y%m%d")
        return {
            "symbol": code,
            "period": "daily",
            "start_date": start,
            "end_date": end,
            "adjust": "qfq",
        }
    if name == "stock-api":
        return {
            "code": to_stock_api_code(code),
            "period": "day",
            "count": 30,
            "adjust": "qfq",
        }
    return {"code": code, "period": "daily", "count": 30}


def verify_server(spec: dict, code: str) -> dict:
    """启动单个 MCP Server 并验证 quote / kline。"""
    client = MCPClient(
        name=spec["name"],
        command=spec["command"],
        args=spec["args"],
        timeout=60.0,
    )
    results = {"name": spec["name"], "quote": None, "kline": None}
    try:
        logger.info("===== 启动 %s =====", spec["name"])
        client.start()
    except Exception as e:
        logger.error("[%s] 启动失败: %s", spec["name"], e)
        return results

    try:
        logger.info("[%s] 调用 quote 工具 %s(%s)...", spec["name"], spec["quote_tool"], build_quote_args(spec["name"], code))
        raw = client.call_tool(spec["quote_tool"], build_quote_args(spec["name"], code))
        if _is_error(raw):
            raise DataSourceError(f"{spec['name']} 返回错误: {raw['error']}")
        results["quote"] = raw
        logger.info("[%s] quote 成功: %s", spec["name"], _brief(raw))
    except Exception as e:
        logger.error("[%s] quote 失败: %s", spec["name"], e)

    try:
        args = build_kline_args(spec["name"], code)
        logger.info("[%s] 调用 kline 工具 %s(%s)...", spec["name"], spec["kline_tool"], args)
        raw = client.call_tool(spec["kline_tool"], args)
        if _is_error(raw):
            raise DataSourceError(f"{spec['name']} 返回错误: {raw['error']}")
        results["kline"] = raw
        logger.info("[%s] kline 成功，共 %s 条", spec["name"], _count(raw))
    except Exception as e:
        logger.error("[%s] kline 失败: %s", spec["name"], e)

    try:
        client.stop()
    except Exception as e:
        logger.warning("[%s] 关闭失败: %s", spec["name"], e)
    return results


def _is_error(raw) -> bool:
    """akshare-stock 失败时返回 {'error': ..., 'data': []}，含 error 字段视为失败。"""
    return isinstance(raw, dict) and bool(raw.get("error"))


def _brief(raw) -> str:
    """提取 quote 结果的关键字段，便于观察。"""
    d = raw
    if isinstance(d, dict):
        # stock-api 返回结构：{"input":..., "response": {"stock": {...}}}
        resp = d.get("response")
        if isinstance(resp, dict) and isinstance(resp.get("stock"), dict):
            d = resp["stock"]
    if isinstance(d, dict):
        keys = ("name", "名称", "code", "symbol", "price", "now", "最新价", "f14", "f2")
        picked = {k: d.get(k) for k in keys if k in d}
        return picked if picked else str(d)[:200]
    return str(raw)[:200]


def _count(raw) -> int:
    """统计 K 线记录条数。"""
    if isinstance(raw, dict):
        # stock-api 返回结构：{"input":..., "response": {"count": n, "klines": [...]}}
        resp = raw.get("response")
        if isinstance(resp, dict) and isinstance(resp.get("klines"), list):
            return len(resp["klines"])
        data = raw.get("data") or raw.get("klines") or raw.get("list") or []
        return len(data) if isinstance(data, list) else 0
    return 0


def main() -> None:
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    print(f"\n===== MCP 验证开始，股票代码: {code} =====\n")
    summary = []
    for spec in SERVER_SPECS:
        results = verify_server(spec, code)
        summary.append(results)
        print("")

    print("===== 汇总 =====")
    for r in summary:
        quote_ok = "成功" if r["quote"] is not None else "失败"
        kline_ok = "成功" if r["kline"] is not None else "失败"
        print(f"- {r['name']}: quote {quote_ok}, kline {kline_ok} (K线条数: {_count(r['kline'])})")


if __name__ == "__main__":
    main()
