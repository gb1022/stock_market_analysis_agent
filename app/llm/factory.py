"""LLM Provider 工厂函数。"""

from app.config import load_config
from app.llm.base import LLMConfig
from app.llm.openai_compatible import OpenAICompatibleProvider


def create_llm_provider() -> OpenAICompatibleProvider:
    """从配置文件创建 LLM Provider。

    通过 config/default.yaml 中的配置创建 OpenAI 兼容协议的 Provider。
    不硬编码任何默认模型，全部由配置驱动。

    Returns:
        配置好的 OpenAICompatibleProvider 实例

    Raises:
        ConfigError: 配置缺失或环境变量未设置时由 load_config() 抛出
    """
    cfg = load_config()
    llm_cfg = cfg["llm"]

    config = LLMConfig(
        provider=llm_cfg.get("provider", ""),
        base_url=llm_cfg.get("base_url", ""),
        api_key=llm_cfg.get("api_key", ""),
        model=llm_cfg.get("model", ""),
        temperature=float(llm_cfg.get("temperature", 0.3)),
        max_tokens=int(llm_cfg.get("max_tokens", 4096)),
        timeout=int(llm_cfg.get("timeout", 60)),
    )
    return OpenAICompatibleProvider(config)
