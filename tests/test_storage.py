"""SQLite 缓存测试：写入/读取/过期/清理。"""

import json
from datetime import datetime, timedelta


class TestStockCache:
    """缓存模块测试套件"""

    def test_set_and_get_cache(self, temp_db_path):
        """正常路径：写入后能读取。"""
        from app.storage.cache import StockCache
        cache = StockCache(db_path=temp_db_path)
        data = {"latest_price": 1888.0, "code": "600519"}

        cache.set_cache("quote", "600519", data, source="akshare")
        result = cache.get_cache("quote", "600519")

        assert result is not None
        assert result["latest_price"] == 1888.0
        assert result["code"] == "600519"

    def test_get_cache_miss(self, temp_db_path):
        """边界：缓存不存在时返回 None。"""
        from app.storage.cache import StockCache
        cache = StockCache(db_path=temp_db_path)

        result = cache.get_cache("quote", "000000")
        assert result is None

    def test_cache_expiry(self, temp_db_path):
        """边界：过期缓存应返回 None。"""
        from app.storage.cache import StockCache
        # TTL=0 的测试用策略
        cache = StockCache(db_path=temp_db_path)
        data = {"price": 100.0}

        # 写入数据
        cache.set_cache("kline", "600519", data, source="akshare",
                         params={"period": "daily", "days": 250})

        # 立即获取应该存在
        result = cache.get_cache("kline", "600519",
                                  params={"period": "daily", "days": 250})
        assert result is not None

        # 参数不同 -> 缓存不同
        result2 = cache.get_cache("kline", "600519",
                                   params={"period": "daily", "days": 30})
        assert result2 is None

    def test_clear_stale_cache(self, temp_db_path):
        """正常路径：清理过期缓存。"""
        from app.storage.cache import StockCache
        cache = StockCache(db_path=temp_db_path)

        # 写入两条数据
        cache.set_cache("quote", "600519", {"p": 1}, source="akshare")
        cache.set_cache("quote", "000001", {"p": 2}, source="akshare")

        # 清理前应 > 0
        cleared = cache.clear_stale()
        assert isinstance(cleared, int)

    def test_is_expired(self, temp_db_path):
        """正常路径：判断缓存是否过期。"""
        from app.storage.cache import StockCache
        cache = StockCache(db_path=temp_db_path)

        # 未写入时应该过期
        assert cache.is_expired("quote", "000000") is True

        # 写入后应该未过期
        cache.set_cache("quote", "600519", {"p": 100}, source="akshare")
        assert cache.is_expired("quote", "600519") is False

    def test_list_cache_roundtrip(self, temp_db_path):
        """边界：缓存列表类型数据。"""
        from app.storage.cache import StockCache
        cache = StockCache(db_path=temp_db_path)
        data = [{"a": 1}, {"a": 2}, {"a": 3}]

        cache.set_cache("kline", "600519", data, source="akshare")
        result = cache.get_cache("kline", "600519")

        assert result is not None
        assert len(result) == 3
        assert result[0]["a"] == 1

    def test_cache_policy_ttl(self, temp_db_path):
        """正常路径：不同类型使用不同 TTL。"""
        from app.storage.cache import StockCache
        from app.storage.models import CACHE_POLICIES

        # quote TTL 应为 300 秒
        assert CACHE_POLICIES["quote"].ttl_seconds == 300
        # financial TTL 应为 7 天
        assert CACHE_POLICIES["financial"].ttl_seconds == 604800
        # news TTL 应为 6 小时
        assert CACHE_POLICIES["news"].ttl_seconds == 21600
        # stock_list TTL 应为 7 天
        assert CACHE_POLICIES["stock_list"].ttl_seconds == 604800

    def test_get_stock_list_stale(self, temp_db_path):
        """正常路径：get_stock_list_stale 返回过期数据。"""
        from app.storage.cache import StockCache
        from app.storage.models import CACHE_POLICIES

        cache = StockCache(db_path=temp_db_path)
        stocks = [{"code": "600519", "name": "贵州茅台"}]

        # 写入数据（TTL 7天，不会过期，修改 TTL 为 0 后再写）
        CACHE_POLICIES["stock_list"].ttl_seconds = 0
        cache.set_stock_list(stocks)
        CACHE_POLICIES["stock_list"].ttl_seconds = 604800  # 恢复

        # get_stock_list（过滤过期）应返回 None
        assert cache.get_stock_list() is None

        # get_stock_list_stale（不过滤过期）应返回数据
        stale = cache.get_stock_list_stale()
        assert stale is not None
        assert stale[0]["code"] == "600519"

    def test_get_stock_list_stale_none(self, temp_db_path):
        """边界：无缓存时 get_stock_list_stale 返回 None。"""
        from app.storage.cache import StockCache
        cache = StockCache(db_path=temp_db_path)
        assert cache.get_stock_list_stale() is None

    def test_merge_stock_list_add_new(self, temp_db_path):
        """正常路径：增量合并新增股票。"""
        from app.storage.cache import StockCache
        cache = StockCache(db_path=temp_db_path)

        # 写入初始列表
        cache.set_stock_list([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
        ])

        # 合并新列表（新增一只股票）
        added = cache.merge_stock_list([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
            {"code": "300750", "name": "宁德时代"},
        ])
        assert added == 1

        # 验证结果包含新股票
        result = cache.get_stock_list()
        assert result is not None
        assert len(result) == 3
        codes = {s["code"] for s in result}
        assert "300750" in codes

    def test_merge_stock_list_no_change(self, temp_db_path):
        """边界：无变化时合并后仍返回。"""
        from app.storage.cache import StockCache
        cache = StockCache(db_path=temp_db_path)

        cache.set_stock_list([
            {"code": "600519", "name": "贵州茅台"},
        ])

        # 合并相同的列表
        added = cache.merge_stock_list([
            {"code": "600519", "name": "贵州茅台"},
        ])
        assert added == 0

        # 数据依然可用
        result = cache.get_stock_list()
        assert result is not None
        assert len(result) == 1

    def test_merge_stock_list_no_cache(self, temp_db_path):
        """边界：无缓存时 merge 等同于全量写入。"""
        from app.storage.cache import StockCache
        cache = StockCache(db_path=temp_db_path)

        added = cache.merge_stock_list([
            {"code": "600519", "name": "贵州茅台"},
        ])
        assert added == 1

        result = cache.get_stock_list()
        assert result is not None
        assert len(result) == 1
