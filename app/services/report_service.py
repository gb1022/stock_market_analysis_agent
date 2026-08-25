"""报告渲染服务。"""

from datetime import datetime
from typing import Optional


def build_report(state: dict) -> str:
    """从 LangGraph 终态构建分析报告。

    优先返回 LLM 生成的 final_report，若无则构建简单摘要。

    Args:
        state: Agent 终态字典

    Returns:
        Markdown 格式的报告文本
    """
    # 优先返回 LLM 生成的完整报告
    if state.get("final_report"):
        return state["final_report"]

    # 降级：构建简单摘要
    error = state.get("error")
    if error:
        return f"分析过程中出现错误:\n\n{error}\n\n请稍后重试。"

    # 极简报告
    code = state.get("stock_code", "未知")
    market = state.get("market", "A")
    phase = state.get("current_phase", "unknown")

    lines = [
        f"# {code} ({market}) 分析报告",
        f"",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"状态: {phase}",
        f"",
    ]

    perception = state.get("perception")
    if perception:
        lines.append("## 关键观察")
        for obs in perception.get("key_observations", []):
            lines.append(f"- {obs}")

    report_text = "\n".join(lines)
    if "不构成投资建议" not in report_text:
        report_text += "\n\n---\n**免责声明**：以上内容仅基于历史数据的技术分析，不构成投资建议。"
    return report_text
