"""本地 SQLite 缓存读写接口。"""

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

from app.storage.database import create_connection, init_db
from app.storage.models import CACHE_POLICIES, CachePolicy


class StockCache:
    """本地 SQLite 缓存，线程安全。

    提供缓存数据的写入、读取、过期判断、清理功能。
    """

    def __init__(self, db_path: str = "data/stock_cache.db") -> None:
        self._conn = create_connection(db_path)
        init_db(self._conn)

    def _get_policy(self, data_type: str) -> CachePolicy:
        """获取数据类型的缓存策略。"""
        return CACHE_POLICIES.get(data_type, CachePolicy(ttl_seconds=3600))

    def _get_expires_at(self, data_type: str) -> str:
        """计算过期时间 ISO 字符串。"""
        policy = self._get_policy(data_type)
        expires = datetime.now() + timedelta(seconds=policy.ttl_seconds)
        return expires.isoformat()

    def get_cache(self, data_type: str, stock_code: str,
                  params: Optional[dict] = None) -> Optional[Any]:
        """查找有效缓存。

        Args:
            data_type: 数据类型（quote/kline/financial/capital_flow/news）
            stock_code: 股票代码
            params: 额外参数（用于生成 cache_key）

        Returns:
            缓存的数据（已反序列化），如果未命中或已过期返回 None
        """
        cache_key = self._build_key(data_type, stock_code, params)
        now = datetime.now().isoformat()

        cursor = self._conn.execute(
            "SELECT data_json, source, fetched_at FROM stock_cache "
            "WHERE cache_key = ? AND expires_at > ?",
            (cache_key, now),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        data_json, source, fetched_at = row
        data = json.loads(data_json)
        # 返回时附加来源和采集时间，方便调用方使用
        if isinstance(data, dict):
            data["_cache_source"] = source
            data["_cache_fetched_at"] = fetched_at
        return data

    def set_cache(self, data_type: str, stock_code: str,
                  data: Any, source: str,
                  params: Optional[dict] = None) -> None:
        """写入缓存。自动计算 expires_at。

        Args:
            data_type: 数据类型
            stock_code: 股票代码
            data: 要缓存的数据（必须是可 JSON 序列化的）
            source: 数据来源
            params: 额外参数（用于生成 cache_key）
        """
        cache_key = self._build_key(data_type, stock_code, params)
        data_json = json.dumps(data, ensure_ascii=False, default=str)
        fetched_at = datetime.now().isoformat()
        expires_at = self._get_expires_at(data_type)

        self._conn.execute(
            "INSERT OR REPLACE INTO stock_cache "
            "(cache_key, data_type, stock_code, data_json, source, fetched_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cache_key, data_type, stock_code, data_json, source, fetched_at, expires_at),
        )
        self._conn.commit()

    def is_expired(self, data_type: str, stock_code: str,
                   params: Optional[dict] = None) -> bool:
        """判断缓存是否过期。

        Args:
            data_type: 数据类型
            stock_code: 股票代码
            params: 额外参数

        Returns:
            True=已过期或不存在, False=有效
        """
        cached = self.get_cache(data_type, stock_code, params)
        return cached is None

    def clear_stale(self) -> int:
        """清理已过期缓存。

        Returns:
            清理的条数
        """
        now = datetime.now().isoformat()
        cursor = self._conn.execute(
            "DELETE FROM stock_cache WHERE expires_at < ?",
            (now,),
        )
        self._conn.commit()
        return cursor.rowcount

    def clear_stock_cache(self, stock_code: str) -> int:
        """清除指定股票代码的所有缓存数据。

        Args:
            stock_code: 股票代码

        Returns:
            删除的条数
        """
        cursor = self._conn.execute(
            "DELETE FROM stock_cache WHERE stock_code = ?",
            (stock_code,),
        )
        self._conn.commit()
        return cursor.rowcount

    def _build_key(self, data_type: str, stock_code: str,
                   params: Optional[dict] = None) -> str:
        """构建缓存键。"""
        if params:
            # 参数排序确保键的唯一性
            param_str = json.dumps(params, sort_keys=True)
            return f"{data_type}:{stock_code}:{param_str}"
        return f"{data_type}:{stock_code}"

    # ---- 全量股票列表缓存（搜索补全用） ----

    def get_stock_list(self) -> Optional[list[dict]]:
        """获取缓存的全部 A 股列表。

        Returns:
            缓存的股票列表（[{code, name}, ...]），未命中或已过期返回 None
        """
        cache_key = "stock_list:all"
        now = datetime.now().isoformat()

        cursor = self._conn.execute(
            "SELECT data_json FROM stock_cache "
            "WHERE cache_key = ? AND expires_at > ?",
            (cache_key, now),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def get_stock_list_stale(self) -> Optional[list[dict]]:
        """获取缓存的股票列表（不过滤过期时间）。

        用于启动预热时优先使用旧缓存，不阻塞启动。
        即使 TTL 过期也返回数据，调用方自行决定是否更新。

        Returns:
            缓存的股票列表（[{code, name}, ...]），完全不存在时返回 None
        """
        cache_key = "stock_list:all"
        cursor = self._conn.execute(
            "SELECT data_json, expires_at FROM stock_cache "
            "WHERE cache_key = ?",
            (cache_key,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def merge_stock_list(self, new_stocks: list[dict], source: str = "akshare") -> int:
        """增量合并股票列表：用新拉取的列表与缓存合并，新增的股票加入，已有的保留。

        Args:
            new_stocks: 新拉取的股票列表，每项包含 code 和 name
            source: 数据来源

        Returns:
            本次新增的股票数量
        """
        # 读取缓存中的旧列表
        cached = self.get_stock_list_stale()
        if cached is None:
            # 没有缓存，直接全量写入
            self.set_stock_list(new_stocks, source=source)
            return len(new_stocks)

        # 建立旧 code 索引
        old_codes = {s["code"] for s in cached}
        added = 0
        for s in new_stocks:
            if s["code"] not in old_codes:
                cached.append(s)
                added += 1

        if added > 0:
            # 有新增，写回缓存并刷新过期时间
            self.set_stock_list(cached, source=source)
        else:
            # 无新增，只刷新过期时间（延长 TTL）
            cache_key = "stock_list:all"
            data_json = json.dumps(cached, ensure_ascii=False)
            fetched_at = datetime.now().isoformat()
            expires_at = self._get_expires_at("stock_list")
            self._conn.execute(
                "UPDATE stock_cache SET data_json=?, fetched_at=?, expires_at=? WHERE cache_key=?",
                (data_json, fetched_at, expires_at, cache_key),
            )
            self._conn.commit()

        return added

    def set_stock_list(self, stocks: list[dict], source: str = "akshare") -> None:
        """缓存全量股票列表。

        Args:
            stocks: 股票列表，每项包含 code 和 name
            source: 数据来源
        """
        cache_key = "stock_list:all"
        data_json = json.dumps(stocks, ensure_ascii=False)
        fetched_at = datetime.now().isoformat()
        expires_at = self._get_expires_at("stock_list")

        self._conn.execute(
            "INSERT OR REPLACE INTO stock_cache "
            "(cache_key, data_type, stock_code, data_json, source, fetched_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cache_key, "stock_list", "*", data_json, source, fetched_at, expires_at),
        )
        self._conn.commit()

    def update_policy(self, data_type: str, ttl_seconds: int) -> None:
        """运行时更新某种数据类型的缓存 TTL。

        Args:
            data_type: 数据类型（如 stock_list）
            ttl_seconds: 新的 TTL（秒）
        """
        from app.storage.models import CACHE_POLICIES
        if data_type in CACHE_POLICIES:
            CACHE_POLICIES[data_type].ttl_seconds = ttl_seconds

    # ---- 分析结果摘要缓存（永久存储） ----

    def set_analysis_summary(self, stock_code: str, summary: dict, max_count: int = 50) -> None:
        """保存分析结果摘要（永久存储，覆盖旧数据）。

        Args:
            stock_code: 股票代码
            summary: 摘要数据（投资评分、风险等级、资金流向等）
            max_count: 缓存数量上限，超过时删除最旧的记录
        """
        # 先保存/更新当前股票的摘要
        cache_key = f"analysis_summary:{stock_code}"
        data_json = json.dumps(summary, ensure_ascii=False, default=str)
        fetched_at = datetime.now().isoformat()
        expires_at = self._get_expires_at("analysis_summary")

        self._conn.execute(
            "INSERT OR REPLACE INTO stock_cache "
            "(cache_key, data_type, stock_code, data_json, source, fetched_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cache_key, "analysis_summary", stock_code, data_json, "analysis", fetched_at, expires_at),
        )
        self._conn.commit()

        # 检查是否超过上限，超过则删除最旧的
        self._enforce_analysis_summary_limit(max_count)

    def _enforce_analysis_summary_limit(self, max_count: int) -> None:
        """确保分析摘要缓存不超过上限，超过时删除最旧的记录。

        Args:
            max_count: 缓存数量上限
        """
        cursor = self._conn.execute(
            "SELECT cache_key FROM stock_cache "
            "WHERE data_type = 'analysis_summary' "
            "ORDER BY fetched_at ASC"
        )
        rows = cursor.fetchall()

        if len(rows) > max_count:
            # 删除最旧的记录
            to_delete = len(rows) - max_count
            keys_to_delete = [row[0] for row in rows[:to_delete]]
            placeholders = ",".join(["?" for _ in keys_to_delete])
            self._conn.execute(
                f"DELETE FROM stock_cache WHERE cache_key IN ({placeholders})",
                keys_to_delete,
            )
            self._conn.commit()

    def get_analysis_summary(self, stock_code: str) -> Optional[dict]:
        """获取指定股票的分析结果摘要。

        Args:
            stock_code: 股票代码

        Returns:
            摘要数据字典，不存在返回 None
        """
        cache_key = f"analysis_summary:{stock_code}"
        now = datetime.now().isoformat()

        cursor = self._conn.execute(
            "SELECT data_json, fetched_at FROM stock_cache "
            "WHERE cache_key = ? AND expires_at > ?",
            (cache_key, now),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        data_json, fetched_at = row
        data = json.loads(data_json)
        data["_cache_fetched_at"] = fetched_at
        return data

    def get_all_analysis_summaries(self, limit: int = 20) -> list[dict]:
        """获取所有分析结果摘要（按采集时间倒序）。

        Args:
            limit: 返回数量上限

        Returns:
            摘要数据列表
        """
        now = datetime.now().isoformat()
        cursor = self._conn.execute(
            "SELECT stock_code, data_json, fetched_at FROM stock_cache "
            "WHERE data_type = 'analysis_summary' AND expires_at > ? "
            "ORDER BY fetched_at DESC LIMIT ?",
            (now, limit),
        )
        rows = cursor.fetchall()

        results = []
        for stock_code, data_json, fetched_at in rows:
            try:
                data = json.loads(data_json)
                data["code"] = stock_code
                data["fetched_at"] = fetched_at
                results.append(data)
            except Exception:
                continue

        return results
