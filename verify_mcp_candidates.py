"""验证候选 MCP Server 拉取 财务/资金流/新闻 数据的能力。

用法：在项目根目录执行 `python verify_mcp_candidates.py [股票代码]`
依次启动候选 MCP（china-stock-mcp / ashare-mcp / ahshare-mcp / stock-sdk-mcp），
对每个 server 按维度（财务 / 资金流 / 新闻）调用对应工具，
输出每项的 成功/失败 结果与数据摘要，用于选型决策。
"""

import json
import logging
import sys

# 确保能导入 app 包
sys.path.insert(0, ".")

from app.data_sources.mcp_source import MCPClient

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("stock_agent")

NODE = "C:\\Program Files\\nodejs\\node.exe"
AH_SHARE_DIST = "C:\\Users\\Admin\\AppData\\Roaming\\npm\\node_modules\\ahshare-mcp\\dist\\main.js"
STOCK_SDK_DIST = "C:\\Users\\Admin\\AppData\\Roaming\\npm\\node_modules\\stock-sdk-mcp\\dist\\index.js"

# 候选 MCP 及按维度（财务/资金流/新闻）的冒烟调用配置
# dim 取值: financial / capital_flow / news；args 中的 {code} 在运行时替换为股票代码
CANDIDATES = [
    {
        "name": "china-stock-mcp",
        "command": "python",
        "args": ["-u", "-X", "utf8", "-m", "china_stock_mcp"],
        "checks": [
            {"dim": "财务", "tool": "get_financial_metrics", "args": {"symbol": "{code}"}},
            {"dim": "资金流", "tool": "get_fund_flow", "args": {"symbol": "{code}"}},
            {"dim": "新闻", "tool": "get_news_data", "args": {"symbol": "{code}"}},
        ],
    },
    {
        "name": "ashare-mcp",
        "command": "python",
        "args": ["-u", "-X", "utf8", "-m", "a_share_mcp.mcp_server"],
        # 财务工具基于 Baostock：code 需带市场前缀（sh.600519 / sz.000001），year 为字符串
        "checks": [
            {"dim": "财务", "tool": "get_profit_data",
             "args": {"code": "sh.{code}", "year": "2025", "quarter": 4}},
        ],
    },
    {
        "name": "ahshare-mcp",
        "command": NODE,
        "args": [AH_SHARE_DIST],
        "checks": [
            {"dim": "财务", "tool": "get_financial_metrics", "args": {"symbol": "{code}", "recent_n": 2}},
            {"dim": "新闻", "tool": "get_news_data", "args": {"symbol": "{code}", "recent_n": 5}},
        ],
    },
    {
        "name": "stock-sdk-mcp",
        "command": NODE,
        "args": [STOCK_SDK_DIST],
        "checks": [
            {"dim": "资金流", "tool": "get_stock_fund_flow_history", "args": {"symbol": "{code}", "period": "daily"}},
        ],
    },
]


def _fill_args(args: dict, code: str) -> dict:
    """将模板参数中的 {code} 替换为实际股票代码。"""
    return {k: (v.replace("{code}", code) if isinstance(v, str) else v) for k, v in args.items()}


def _is_success(raw) -> bool:
    """判定返回是否有实际数据（非空列表/非空 dict/非空文本）。"""
    if raw is None:
        return False
    if isinstance(raw, list):
        return len(raw) > 0
    if isinstance(raw, dict):
        return bool(raw) and any(bool(v) for v in raw.values() if isinstance(v, (list, str, int, float)))
    return bool(str(raw).strip())


def _summarize(raw) -> str:
    """提取返回结果的关键信息，便于人工判断。"""
    if isinstance(raw, list):
        if not raw:
            return "空列表"
        return f"{len(raw)} 条，首条: {json.dumps(raw[0], ensure_ascii=False)[:140]}"
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, list):
                if not value:
                    return f"字段[{key}] 为空列表"
                return f"字段[{key}] {len(value)} 条，首条: {json.dumps(value[0], ensure_ascii=False)[:120]}"
        sample = json.dumps(raw, ensure_ascii=False)[:160]
        return f"字段: {list(raw.keys())[:8]}，样本: {sample}"
    if isinstance(raw, str):
        return f"文本({len(raw)}字): {raw[:120]}"
    return str(raw)[:160]


def verify_candidate(spec: dict, code: str) -> list[dict]:
    """启动单个候选 MCP，按配置依次冒烟调用各维度工具。"""
    client = MCPClient(
        name=spec["name"],
        command=spec["command"],
        args=spec["args"],
        timeout=60.0,
    )
    results = []
    try:
        logger.info("===== 启动 %s =====", spec["name"])
        client.start()
    except Exception as e:
        logger.error("[%s] 启动失败: %s", spec["name"], e)
        return [{"dim": c["dim"], "tool": c["tool"], "ok": False, "detail": f"server 启动失败: {e}"}
                for c in spec["checks"]]

    for check in spec["checks"]:
        args = _fill_args(check["args"], code)
        try:
            logger.info("[%s] 调用 %s(%s)...", spec["name"], check["tool"], args)
            raw = client.call_tool(check["tool"], args)
            ok = _is_success(raw)
            results.append({
                "dim": check["dim"],
                "tool": check["tool"],
                "ok": ok,
                "detail": _summarize(raw),
            })
            logger.info("[%s] %s %s: %s", spec["name"], check["dim"], "成功" if ok else "无数据", results[-1]["detail"])
        except Exception as e:
            results.append({"dim": check["dim"], "tool": check["tool"], "ok": False, "detail": str(e)[:200]})
            logger.error("[%s] %s(%s) 失败: %s", spec["name"], check["tool"], args, e)

    try:
        client.stop()
    except Exception as e:
        logger.warning("[%s] 关闭失败: %s", spec["name"], e)
    return results


def main() -> None:
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    print(f"\n===== 候选 MCP 验证开始，股票代码: {code} =====\n")

    all_results = []
    for spec in CANDIDATES:
        results = verify_candidate(spec, code)
        all_results.append({"name": spec["name"], "results": results})
        print("")

    print("===== 明细 =====")
    for entry in all_results:
        print(f"\n[{entry['name']}]")
        for r in entry["results"]:
            mark = "成功" if r["ok"] else "失败"
            print(f"  [{r['dim']}] {r['tool']}: {mark} - {r['detail']}")

    print("\n===== 汇总表 =====")
    dims = ["财务", "资金流", "新闻"]
    header = "| MCP | " + " | ".join(dims) + " |"
    print(header)
    print("|---|" + "---|" * len(dims))
    for entry in all_results:
        by_dim = {r["dim"]: ("成功" if r["ok"] else "失败") for r in entry["results"]}
        row = f"| {entry['name']} | " + " | ".join(by_dim.get(d, "不提供") for d in dims) + " |"
        print(row)


if __name__ == "__main__":
    main()
