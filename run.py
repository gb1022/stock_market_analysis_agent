#!/usr/bin/env python
"""启动入口。

从配置读取 host 和 port，启动 FastAPI 服务器。
"""

import os

import uvicorn

from app.config import ConfigError, load_config


def main() -> None:
    """启动 HTTP 服务器。"""
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"配置加载失败: {e}")
        print("请确保 config/default.yaml 存在且环境变量已设置。")
        return

    server_cfg = cfg.get("server", {})
    host = os.getenv("HOST") or server_cfg.get("host", "127.0.0.1")
    port = int(os.getenv("PORT") or server_cfg.get("port", 8000))

    print(f"股票分析助手正在启动...")
    print(f"地址: http://{host}:{port}")
    print(f"API 文档: http://{host}:{port}/docs")
    print()

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=True,
    )


if __name__ == "__main__":
    main()
