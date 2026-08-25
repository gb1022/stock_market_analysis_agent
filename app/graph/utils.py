"""Graph 执行日志与 LLM 调用记录工具函数。"""

import json
import re

from app.graph.state import StockAgentState, LogEntry, LLMRecord


def extract_json(text: str) -> str:
    """从 LLM 回复中提取 JSON 字符串。

    处理 LLM 可能返回 ```json...``` 包裹的情况。
    如果提取失败则返回原始文本。
    """
    # 尝试匹配 ```json ... ``` 块
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if m:
        return m.group(1).strip()
    # 尝试匹配 ``` ... ``` 块
    m = re.search(r'```\s*([\s\S]*?)```', text)
    if m:
        return m.group(1).strip()
    # 尝试直接找 {...} 或 [...] 顶层结构
    for start, end in [('{', '}'), ('[', ']')]:
        idx = text.find(start)
        if idx != -1:
            try:
                maybe = text[idx:]
                json.loads(maybe)
                return maybe
            except (json.JSONDecodeError, ValueError):
                continue
    return text.strip()


def add_log(state: StockAgentState, stage: str, message: str,
            log_type: str = "info") -> list[LogEntry]:
    """添加一条执行日志，同时打印到控制台。

    Args:
        state: 当前状态
        stage: 阶段名称
        message: 日志内容
        log_type: 日志类型（info/prompt/llm_response/error）

    Returns:
        新的 logs 列表
    """
    logs = list(state.get("logs", []))
    logs.append({
        "stage": stage,
        "message": message,
        "type": log_type,
    })
    # 同时打印到控制台
    type_tag = {
        "info": "",
        "prompt": "[PROMPT] ",
        "llm_response": "[LLM] ",
        "error": "[ERROR] ",
        "warn": "[WARN] ",
    }.get(log_type, "")

    stage_tag = f"[{stage}] " if stage else ""
    print(f"  {type_tag}{stage_tag}{message}")
    return logs


def add_llm_record(state: StockAgentState, stage: str,
                   system_prompt: str, user_prompt: str,
                   response: str) -> list[LLMRecord]:
    """记录一次 LLM 调用（提示词 + 回复），同时打印到控制台。

    Args:
        state: 当前状态
        stage: 调用阶段
        system_prompt: 系统提示词
        user_prompt: 用户提示词
        response: LLM 完整回复

    Returns:
        新的 llm_records 列表
    """
    records = list(state.get("llm_records", []))

    # 打印分隔线和提示词到控制台
    print(f"\n{'='*60}")
    print(f"  [{stage}] 发送给大模型的完整提示词:")
    print(f"{'='*60}")
    print(f"  [System Prompt]")
    for line in system_prompt.strip().split("\n"):
        print(f"    {line}")
    print(f"\n  [User Prompt]")
    for line in user_prompt.strip().split("\n"):
        print(f"    {line}")

    print(f"\n{'='*60}")
    print(f"  [{stage}] 大模型的完整回复:")
    print(f"{'='*60}")
    for line in response.strip().split("\n"):
        print(f"    {line}")
    print(f"\n")

    records.append({
        "stage": stage,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "response": response,
    })
    return records
