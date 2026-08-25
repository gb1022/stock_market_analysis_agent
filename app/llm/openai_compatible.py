"""OpenAI 兼容协议 LLM Provider 实现。"""

from typing import Iterator

from openai import OpenAI

from app.llm.base import LLMProvider, LLMConfig


class OpenAICompatibleProvider(LLMProvider):
    """统一 OpenAI 兼容协议实现，通过 base_url 切换厂商。

    支持 DeepSeek / Qwen / OpenAI / Moonshot 等所有兼容 OpenAI API 的厂商。
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.timeout,
        )

    def invoke(self, system_prompt: str, user_prompt: str) -> str:
        """调用 LLM，返回完整响应。

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词

        Returns:
            LLM 响应文本
        """
        resp = self._client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        return resp.choices[0].message.content or ""

    def stream_invoke(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """流式调用 LLM，逐块返回。

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词

        Yields:
            LLM 返回的文本块
        """
        resp = self._client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            stream=True,
        )
        for chunk in resp:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
