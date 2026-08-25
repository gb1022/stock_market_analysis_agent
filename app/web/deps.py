"""Web 依赖注入。"""

from fastapi import Request

from app.data_sources.router import DataSourceRouter
from app.storage.cache import StockCache


def get_data_source_router(request: Request) -> DataSourceRouter:
    """获取数据源路由器实例。"""
    return request.app.state.router


def get_cache(request: Request) -> StockCache:
    """获取缓存实例。"""
    return request.app.state.cache
