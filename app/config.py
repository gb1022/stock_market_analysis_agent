"""配置加载模块。

从 config/default.yaml 加载配置，支持 ${VAR} 环境变量引用解析。
启动时会自动加载项目根目录下的 .env 文件。
用户只需配置 .env 一个文件。
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    """配置相关错误"""
    pass


def _resolve_env_var(value: str) -> str:
    """解析配置值中的 ${VAR} 和 ${VAR:-default} 环境变量引用。

    支持两种语法：
      ${VAR}          — VAR 必须设置，否则抛 ConfigError
      ${VAR:-default} — VAR 未设置或为空时用 default
      ${VAR:=default} — 同 :-，同时将 default 赋值给 VAR

    Args:
        value: 可能包含 ${VAR} 或 ${VAR:-default} 的字符串

    Returns:
        解析后的字符串（环境变量的值替换占位符）

    Raises:
        ConfigError: 必要环境变量未设置时抛出
    """
    # 先匹配带默认值的 ${VAR:-default} 和 ${VAR:=default}
    pattern = re.compile(r'\$\{(\w+)(:[=-])([^}]*)\}')

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        modifier = match.group(2)  # ':-' 或 ':='
        default_val = match.group(3)
        env_val = os.getenv(var_name)
        if env_val is None or env_val == "":
            # 如果是 := 语法，回写默认值到环境变量
            if modifier == ':=':
                os.environ[var_name] = default_val
            return default_val
        return env_val

    # 解析带默认值的
    result = pattern.sub(_replace, value)

    # 再解析不带默认值的 ${VAR}
    pattern2 = re.compile(r'\$\{(\w+)\}')

    def _replace2(match: re.Match) -> str:
        var_name = match.group(1)
        env_val = os.getenv(var_name)
        if env_val is None:
            raise ConfigError(
                f"环境变量 {var_name} 未设置，"
                f"请检查 .env 文件"
            )
        return env_val

    return pattern2.sub(_replace2, result)


def _resolve_env_vars_in_config(config: Any) -> Any:
    """递归解析配置中的所有 ${VAR} 引用。

    Args:
        config: 配置字典或字符串

    Returns:
        解析后的配置
    """
    if isinstance(config, str):
        # 只在字符串包含 ${...} 模式时尝试解析
        if '${' in config:
            return _resolve_env_var(config)
        return config
    elif isinstance(config, dict):
        return {k: _resolve_env_vars_in_config(v) for k, v in config.items()}
    elif isinstance(config, list):
        return [_resolve_env_vars_in_config(item) for item in config]
    return config


def _find_config_path() -> Path:
    """查找 config/default.yaml 文件路径。

    查找顺序：
    1. 当前工作目录下的 config/default.yaml
    2. 脚本所在目录的上级目录中的 config/default.yaml

    Returns:
        配置文件的 Path 对象

    Raises:
        ConfigError: 找不到配置文件时抛出
    """
    candidates = [
        Path.cwd() / "config" / "default.yaml",
        Path(__file__).parent.parent / "config" / "default.yaml",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise ConfigError(
        f"找不到配置文件 config/default.yaml，"
        f"已尝试路径: {[str(p) for p in candidates]}"
    )


def load_config() -> dict:
    """加载并解析配置文件。

    自动加载项目根目录下的 .env 文件到环境变量。
    从 config/default.yaml 加载配置，自动解析 ${VAR} 引用。
    用户只需编辑 .env 一个文件即可配置 API Key 和 URL。

    Returns:
        解析后的配置字典

    Raises:
        ConfigError: 配置缺失或环境变量未设置时抛出
    """
    # 自动加载 .env 文件（仅需这一个地方配置 API Key 等敏感信息）
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        # 尝试脚本所在目录的上级目录
        alt_env = Path(__file__).parent.parent / ".env"
        if alt_env.exists():
            load_dotenv(dotenv_path=alt_env)

    config_path = _find_config_path()
    with open(config_path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    if raw_config is None:
        raise ConfigError("配置文件为空")

    config = _resolve_env_vars_in_config(raw_config)

    # 校验必要字段
    llm = config.get("llm", {})
    if not llm.get("provider"):
        raise ConfigError("配置缺少 llm.provider")
    if not llm.get("base_url"):
        raise ConfigError("配置缺少 llm.base_url")
    if not llm.get("api_key"):
        raise ConfigError("配置缺少 llm.api_key")

    return config
