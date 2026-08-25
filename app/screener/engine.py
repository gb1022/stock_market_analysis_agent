"""选股引擎执行器。

对全市场股票并发采集数据 → 用 ConditionGroup 过滤 → TopN。
"""

import logging
from datetime import datetime
from typing import Optional

from app.screener.conditions import ConditionGroup
from app.models.screener import ScreenResult
from app.storage.cache import StockCache

logger = logging.getLogger("stock_agent")

# AKShare spot 列名 → 引擎内部字段名映射
_SPOT_FIELD_MAP = {
    "市盈率-动态": "pe_ttm",
    "市净率": "pb",
    "换手率": "turnover_rate",
    "总市值": "market_cap",
    "涨跌幅": "change_percent",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
}

# 字段展示名称和单位
_FIELD_DISPLAY: dict[str, tuple[str, str, str]] = {
    "pe_ttm": ("PE", "", "低估"),
    "pb": ("PB", "", "低估值"),
    "turnover_rate": ("换手率", "%", "活跃"),
    "market_cap": ("市值", "亿", "大盘"),
    "change_percent": ("涨跌幅", "%", "上涨"),
    "volume": ("成交量", "手", "放量"),
    "amount": ("成交额", "元", "高成交"),
    "amplitude": ("振幅", "%", "波动大"),
}

# 市值显示阈值
_MARKET_CAP_DIVISOR = 1e8  # 转为亿


class ScreenerEngine:
    """选股引擎。

    使用 AKShare / 东方财富 / 腾讯财经进行全市场行情数据筛选。
    三级降级：AKShare → Eastmoney clist → Tencent batch
    """

    # 类级别缓存，避免同一次服务上下文中重复拉取
    _tencent_data_cache = None
    _tencent_cache_time = None
    _TENCENT_CACHE_TTL = 60  # 秒

    def __init__(self, max_workers: int = 10) -> None:
        self._max_workers = max_workers
        self._data_cache = None  # 实例级缓存，单次 scan() 内部不重复拉取

    def scan(self, conditions: ConditionGroup,
             top_n: int = 30) -> list[ScreenResult]:
        """执行全市场扫描。

        先从 AKShare 获取全市场实时行情，失败则降级到东方财富 HTTP API。
        按条件过滤后返回 TopN。

        Args:
            conditions: 筛选条件组
            top_n: 返回的股票数量上限

        Returns:
            符合条件的股票列表（按匹配度排序）
        """
        stock_data = self._fetch_via_akshare()
        if not stock_data:
            logger.info("[选股引擎] AKShare 拉取失败，降级到东方财富 API")
            stock_data = self._fetch_via_eastmoney()
        if not stock_data:
            logger.info("[选股引擎] 东方财富拉取失败，降级到腾讯财经批量 API")
            stock_data = self._fetch_via_tencent()

        if not stock_data:
            logger.error("[选股引擎] 所有数据源均失败，无法执行选股")
            return []

        # 执行筛选
        results = self.scan_with_data(conditions, stock_data, top_n)
        logger.info(f"[选股引擎] 筛选完成，{len(results)}/{len(stock_data)} 只股票符合条件")
        return results

    def _fetch_via_akshare(self) -> list[dict]:
        """通过 AKShare 拉取全市场行情数据。"""
        try:
            import akshare as ak
            import time
            t0 = time.time()
            logger.info("[选股引擎-AKShare] 拉取全市场行情...")
            df = ak.stock_zh_a_spot_em()
            elapsed = time.time() - t0
            logger.info(f"[选股引擎-AKShare] 完成，{len(df)} 只，{elapsed:.1f}s")
            return self._df_to_stock_data(df)
        except ImportError:
            logger.warning("[选股引擎-AKShare] akshare 未安装")
            return []
        except Exception as e:
            logger.warning(f"[选股引擎-AKShare] 拉取失败: {e}")
            return []

    # 东方财富端点优先级（push2delay 是 CDN 端点，反爬较弱）
    _EASTMONEY_ENDPOINTS = [
        "https://push2delay.eastmoney.com/api/qt/clist/get",
        "https://push2.eastmoney.com/api/qt/clist/get",
    ]

    def _fetch_via_eastmoney(self) -> list[dict]:
        """通过东方财富 HTTP API 拉取全市场行情。

        降级策略（2 层）：
          1. requests + push2delay/push2 端点 + verify=False
          2. curl_cffi 浏览器 TLS 指纹模拟
        每页最多 100 条，自动分页获取全部 A 股。
        """
        import time
        t0 = time.time()

        all_stocks = self._try_eastmoney_requests()
        if all_stocks:
            elapsed = time.time() - t0
            logger.info(f"[选股引擎-Eastmoney] 完成，{len(all_stocks)} 只，{elapsed:.1f}s (requests)")
            return all_stocks

        all_stocks = self._try_eastmoney_curl_cffi()
        if all_stocks:
            elapsed = time.time() - t0
            logger.info(f"[选股引擎-Eastmoney] 完成，{len(all_stocks)} 只，{elapsed:.1f}s (curl_cffi)")
            return all_stocks

        return []

    def _try_eastmoney_requests(self) -> list[dict]:
        """通过 requests + 多端点尝试拉取东方财富行情数据。"""
        import time
        from urllib.parse import urlencode

        import requests as req
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        fields = "f2,f3,f5,f6,f7,f8,f9,f12,f14,f20,f21,f23"
        base_params = {
            "pz": "100",
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": fields,
        }

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://quote.eastmoney.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        }

        for endpoint in self._EASTMONEY_ENDPOINTS:
            try:
                label = endpoint.split("//")[1].split("/")[0]
                logger.info(f"[选股引擎-Eastmoney] 尝试 {label} (requests)...")

                session = req.Session()
                session.verify = False
                session.headers.update(headers)

                all_stocks = []
                page = 1
                total_pages = None

                while True:
                    params = {**base_params, "pn": str(page)}
                    url = f"{endpoint}?{urlencode(params)}"

                    resp = session.get(url, timeout=15)
                    resp.raise_for_status()
                    data = resp.json()
                    page_data = data.get("data") or {}

                    if total_pages is None:
                        total = page_data.get("total", 0)
                        total_pages = (total + 99) // 100  # 向上取整
                        logger.info(f"[选股引擎-Eastmoney] 共 {total} 只股票，{total_pages} 页")

                    diffs = page_data.get("diff") or []
                    for d in diffs:
                        all_stocks.append({
                            "code": str(d.get("f12", "")).zfill(6),
                            "name": str(d.get("f14", "")),
                            "pe_ttm": self._safe_float(d.get("f9")),
                            "pb": self._safe_float(d.get("f23")),
                            "turnover_rate": self._safe_float(d.get("f8")),
                            "market_cap": self._safe_float(d.get("f20")),
                            "change_percent": self._safe_float(d.get("f3")),
                            "volume": int(self._safe_float(d.get("f5")) or 0),
                            "amount": self._safe_float(d.get("f6")),
                            "amplitude": self._safe_float(d.get("f7")),
                        })

                    if page >= total_pages or len(diffs) < 100:
                        break
                    page += 1

                return all_stocks

            except Exception as e:
                logger.warning(f"[选股引擎-Eastmoney] {label} 失败: {e}")
                continue

        return []

    def _try_eastmoney_curl_cffi(self) -> list[dict]:
        """通过 curl_cffi 浏览器 TLS 指纹模拟拉取东方财富行情。

        curl_cffi 可模拟 Chrome 的 TLS 握手特征，绕过东财的反爬检测。
        """
        import time
        from urllib.parse import urlencode

        try:
            from curl_cffi import requests as curl_req
        except ImportError:
            logger.info("[选股引擎-Eastmoney] curl_cffi 未安装，跳过")
            return []

        fields = "f2,f3,f5,f6,f7,f8,f9,f12,f14,f20,f21,f23"
        base_params = {
            "pz": "100",
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": fields,
        }

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://quote.eastmoney.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

        for endpoint in self._EASTMONEY_ENDPOINTS:
            try:
                label = endpoint.split("//")[1].split("/")[0]
                logger.info(f"[选股引擎-Eastmoney] 尝试 {label} (curl_cffi)...")

                all_stocks = []
                page = 1
                total_pages = None

                while True:
                    params = {**base_params, "pn": str(page)}
                    url = f"{endpoint}?{urlencode(params)}"

                    resp = curl_req.get(
                        url,
                        headers=headers,
                        impersonate="chrome131",
                        timeout=15,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    page_data = data.get("data") or {}

                    if total_pages is None:
                        total = page_data.get("total", 0)
                        total_pages = (total + 99) // 100
                        logger.info(f"[选股引擎-Eastmoney] curl_cffi 共 {total} 只股票，{total_pages} 页")

                    diffs = page_data.get("diff") or []
                    for d in diffs:
                        all_stocks.append({
                            "code": str(d.get("f12", "")).zfill(6),
                            "name": str(d.get("f14", "")),
                            "pe_ttm": self._safe_float(d.get("f9")),
                            "pb": self._safe_float(d.get("f23")),
                            "turnover_rate": self._safe_float(d.get("f8")),
                            "market_cap": self._safe_float(d.get("f20")),
                            "change_percent": self._safe_float(d.get("f3")),
                            "volume": int(self._safe_float(d.get("f5")) or 0),
                            "amount": self._safe_float(d.get("f6")),
                            "amplitude": self._safe_float(d.get("f7")),
                        })

                    if page >= total_pages or len(diffs) < 100:
                        break
                    page += 1

                return all_stocks

            except Exception as e:
                logger.warning(f"[选股引擎-Eastmoney] {label} (curl_cffi) 失败: {e}")
                continue

        return []

    def _fetch_via_tencent(self) -> list[dict]:
        """通过腾讯财经批量 API 拉取全市场行情（三级兜底）。

        先用 AKShare 获取股票代码列表（轻量），再分批通过腾讯 HTTP API 获取行情。
        腾讯 API 使用纯 HTTP，不受 SSL 污染影响。
        带 60 秒类级别缓存，避免短时间内重复拉取。
        """
        # 检查类级别缓存
        import time
        now = time.time()
        if (ScreenerEngine._tencent_data_cache is not None and
                ScreenerEngine._tencent_cache_time is not None and
                (now - ScreenerEngine._tencent_cache_time) < ScreenerEngine._TENCENT_CACHE_TTL):
            logger.info("[选股引擎-Tencent] 命中缓存，跳过拉取")
            return ScreenerEngine._tencent_data_cache

        if self._data_cache is not None:
            return self._data_cache

        try:
            import requests
            import akshare as ak

            t0 = time.time()

            # 获取股票代码列表：先试 AKShare，失败则读本地 SQLite 缓存
            all_codes = self._load_stock_codes_via_akshare(ak, logger)
            if not all_codes:
                logger.info("[选股引擎-Tencent] AKShare 获取代码列表失败，尝试本地缓存...")
                all_codes = self._load_stock_codes_from_cache()
            if not all_codes:
                logger.error("[选股引擎-Tencent] 无可用股票代码列表，无法拉取行情")
                return []

            logger.info(f"[选股引擎-Tencent] 代码列表获取完成，{len(all_codes)} 只")

            # 分批请求（每批约 600 只，控制 URL 长度）
            BATCH_SIZE = 600
            all_stocks = []
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.qq.com/",
            })

            total_batches = (len(all_codes) + BATCH_SIZE - 1) // BATCH_SIZE
            for batch_i in range(total_batches):
                start = batch_i * BATCH_SIZE
                end = min(start + BATCH_SIZE, len(all_codes))
                batch = all_codes[start:end]

                tcodes = ",".join(item[2] for item in batch)
                url = f"http://qt.gtimg.cn/q={tcodes}"

                try:
                    resp = session.get(url, timeout=20)
                    resp.encoding = "gbk"
                    text = resp.text
                except Exception as e:
                    logger.warning(f"[选股引擎-Tencent] 批次 {batch_i+1}/{total_batches} 请求失败: {e}")
                    continue

                # 解析响应：每行格式 v_sh600519="1~name~code~price~..."
                # 腾讯响应中 fields[2] 是原始 6 位代码（无前缀）
                code_to_info = {item[0]: item for item in batch}
                for line in text.split("\n"):
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    idx_start = line.find('"')
                    if idx_start == -1:
                        continue
                    idx_end = line.rfind('"')
                    if idx_end <= idx_start:
                        continue
                    data_str = line[idx_start + 1:idx_end]
                    fields = data_str.split("~")
                    if len(fields) < 47:
                        continue

                    # 响应中 fields[2] 是原始 6 位代码
                    raw_code = fields[2] if len(fields) > 2 else ""
                    orig = code_to_info.get(raw_code)
                    if not orig:
                        continue

                    all_stocks.append({
                        "code": orig[0],
                        "name": fields[1] or orig[1],
                        "pe_ttm": self._safe_float(fields[39]),
                        "pb": self._safe_float(fields[46]),
                        "turnover_rate": self._safe_float(fields[38]),
                        # 腾讯返回亿元，转为元（乘 1e8）与 AKShare/Eastmoney 保持一致
                        "market_cap": (self._safe_float(fields[45]) or 0) * 1e8,
                        "change_percent": self._safe_float(fields[32]),
                        "volume": int(self._safe_float(fields[6]) or 0),
                        # 腾讯返回万元，转为元（乘 10000）与 AKShare 保持一致
                        "amount": (self._safe_float(fields[37]) or 0) * 10000,
                        "amplitude": self._safe_float(fields[43]),
                    })

                logger.info(f"[选股引擎-Tencent] 批次 {batch_i+1}/{total_batches} 完成")

            elapsed = time.time() - t0
            logger.info(f"[选股引擎-Tencent] 完成，{len(all_stocks)} 只，{elapsed:.1f}s")
            # 更新缓存
            ScreenerEngine._tencent_data_cache = all_stocks
            ScreenerEngine._tencent_cache_time = time.time()
            self._data_cache = all_stocks
            return all_stocks

        except ImportError as e:
            logger.warning(f"[选股引擎-Tencent] 依赖缺失: {e}")
            return []
        except Exception as e:
            logger.error(f"[选股引擎-Tencent] 拉取失败: {e}")
            return []

    def _load_stock_codes_via_akshare(self, ak, logger) -> list[tuple]:
        """通过 AKShare 获取股票代码列表，成功时写入本地缓存。

        Args:
            ak: akshare 模块
            logger: 日志记录器

        Returns:
            [(code, name, tencent_code), ...] 列表，失败时返回空列表
        """
        try:
            import io, sys
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()  # 抑制 akshare 内部的 tqdm 进度条
            try:
                df_codes = ak.stock_info_a_code_name()
            finally:
                sys.stdout = old_stdout

            all_codes = []
            for _, row in df_codes.iterrows():
                code = str(row["code"]).zfill(6)
                name = str(row.get("name", ""))
                tencent_code = ("sh" if code.startswith("6") else "sz") + code
                all_codes.append((code, name, tencent_code))

            # 成功获取后写入本地缓存
            if all_codes:
                try:
                    cache = StockCache()
                    stocks = [{"code": c, "name": n} for c, n, _ in all_codes]
                    cache.set_stock_list(stocks, source="akshare")
                except Exception:
                    pass  # 缓存写入失败不阻塞主流程

            return all_codes
        except Exception as e:
            logger.warning(f"[选股引擎-Tencent] AKShare 获取代码列表失败: {e}")
            return []

    def _load_stock_codes_from_cache(self) -> list[tuple]:
        """从本地 SQLite 缓存加载股票代码列表。

        Returns:
            [(code, name, tencent_code), ...] 列表，缓存不存在或为空时返回空列表
        """
        try:
            cache = StockCache()
            stocks = cache.get_stock_list_stale()
            if not stocks:
                return []
            all_codes = []
            for s in stocks:
                code = str(s.get("code", "")).zfill(6)
                name = str(s.get("name", ""))
                if not code:
                    continue
                tencent_code = ("sh" if code.startswith("6") else "sz") + code
                all_codes.append((code, name, tencent_code))
            return all_codes
        except Exception as e:
            logger.warning(f"[选股引擎-Tencent] 本地缓存读取失败: {e}")
            return []

    def _df_to_stock_data(self, df) -> list[dict]:
        """将 AKShare DataFrame 转换为引擎内部格式。"""
        stock_data = []
        for _, row in df.iterrows():
            item = {
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
            }
            for col_name, field_name in _SPOT_FIELD_MAP.items():
                val = row.get(col_name)
                item[field_name] = self._safe_float(val)
            stock_data.append(item)
        return stock_data

    def scan_with_data(self, conditions: ConditionGroup,
                       stock_data: list[dict],
                       top_n: int = 30) -> list[ScreenResult]:
        """使用已有数据执行扫描（用于测试和 Mock）。

        Args:
            conditions: 筛选条件组
            stock_data: 股票数据列表
            top_n: 返回的股票数量上限

        Returns:
            符合条件的股票列表
        """
        results: list[ScreenResult] = []

        for data in stock_data:
            matched = 0
            total = len(conditions.conditions)
            reason_parts: list[str] = []

            for cond in conditions.conditions:
                field = cond.field
                op = cond.op
                value = cond.value
                actual = data.get(field)

                if actual is None:
                    continue

                is_match = False
                if op == ">":
                    is_match = actual > value
                elif op == "<":
                    is_match = actual < value
                elif op == ">=":
                    is_match = actual >= value
                elif op == "<=":
                    is_match = actual <= value
                elif op == "==":
                    is_match = actual == value
                elif op == "between" and isinstance(value, list):
                    is_match = value[0] <= actual <= value[1]

                if is_match:
                    matched += 1
                    # 生成字段说明
                    display_info = _FIELD_DISPLAY.get(field)
                    if display_info:
                        display_name, unit, tag = display_info
                        # 市值特殊处理：转为亿
                        if field == "market_cap":
                            display_val = f"{actual / _MARKET_CAP_DIVISOR:.0f}"
                        elif unit:
                            display_val = f"{actual:.1f}"
                        else:
                            display_val = f"{actual:.1f}"
                        reason_parts.append(f"{display_name} {display_val}{unit}({tag})")
                    else:
                        reason_parts.append(f"{field}={actual}")

            if total > 0 and matched == total:
                score = matched / total
                reason_text = " + ".join(reason_parts) if reason_parts else f"命中 {matched}/{total} 个条件"
                results.append(ScreenResult(
                    code=data.get("code", ""),
                    name=data.get("name", ""),
                    score=score,
                    matched_conditions=matched,
                    total_conditions=total,
                    reason=reason_text,
                    analyzed_at=datetime.now(),
                ))

        # 按匹配度排序
        results.sort(key=lambda r: (-r.score, -r.matched_conditions))
        return results[:top_n]

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        """安全转换为 float。"""
        if value is None:
            return None
        try:
            import math
            v = float(value)
            return None if math.isnan(v) else v
        except (ValueError, TypeError):
            return None
