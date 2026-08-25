"""LLM Provider 抽象基类与配置定义。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator


@dataclass
class LLMConfig:
    """LLM 配置，从 config/default.yaml 加载"""
    provider: str = ""          # 仅用于日志/调试（deepseek/qwen/openai/moonshot/...）
    base_url: str = ""          # API 端点
    api_key: str = ""           # API Key
    model: str = ""             # 模型名
    temperature: float = 0.3
    max_tokens: int = 4096
    timeout: int = 60


class LLMProvider(ABC):
    """LLM Provider 抽象基类"""

    config: LLMConfig

    @abstractmethod
    def invoke(self, system_prompt: str, user_prompt: str) -> str:
        """调用 LLM，返回完整响应文本。

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词

        Returns:
            LLM 返回的文本内容
        """
        pass

    @abstractmethod
    def stream_invoke(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """流式调用 LLM，逐块返回响应文本。

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词

        Yields:
            LLM 返回的文本块
        """
        pass
