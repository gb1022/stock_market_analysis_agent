"""AKShare 数据源单元测试（重点：股东户数方案 A/B 双通道）。

覆盖场景：
- 方案 B：单股直查东财 RPT_HOLDERNUMLATEST（SECURITY_CODE filter）
- 方案 A：全市场最新股东户数（symbol='最新'）缓存过滤
- 批量判定：60 秒窗口内 >= 3 只不同股票自动切换方案 A
- _analyze_shareholder_count 集成：increase / unknown 分支
"""

from datetime import date

import pandas as pd

from app.data_sources.akshare_source import AKShareSource
from app.models.stock import ExtendedAnalysis


class FakeResponse:
    """模拟 requests.Response（仅提供 json）。"""

    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _fake_single_payload(prev_count=None) -> dict:
    """模拟 RPT_HOLDERNUMLATEST 单股返回（600519 最新一期）。"""
    row = {
        "SECURITY_CODE": "600519",
        "END_DATE": "2026-06-30 00:00:00",
        "HOLDER_NUM": 296404,
        "HOLD_NOTICE_DATE": "2026-08-15 00:00:00",
    }
    if prev_count is not None:
        row["PRE_HOLDER_NUM"] = prev_count
    return {"result": {"pages": 1, "data": [row]}}


def _fake_latest_all_df() -> pd.DataFrame:
    """模拟 ak.stock_zh_a_gdhs(symbol='最新') 全市场最新股东户数。"""
    return pd.DataFrame([
        {
            "代码": "600519", "名称": "贵州茅台",
            "股东户数-本次": 296404, "股东户数-上次": 243159,
            "股东户数-增减比例": 21.9,
            "股东户数统计截止日-本次": date(2026, 6, 30),
            "股东户数统计截止日-上次": date(2025, 12, 31),
        },
        {
            "代码": "603013", "名称": "某股",
            "股东户数-本次": 1000, "股东户数-上次": 900,
            "股东户数-增减比例": 11.1,
            "股东户数统计截止日-本次": date(2026, 6, 30),
            "股东户数统计截止日-上次": date(2025, 12, 31),
        },
    ])


# ---------- 方案 B：单股直查 ----------


def test_single_query_two_periods(monkeypatch):
    """正常路径：方案 B 单股直查，用 PRE_HOLDER_NUM 得到两期对比。"""
    monkeypatch.setattr(
        "app.data_sources.akshare_source.requests.get",
        lambda *a, **k: FakeResponse(_fake_single_payload(prev_count=243159)),
    )
    source = AKShareSource()
    result = source._query_shareholder_two_periods("600519")

    assert result == (296404, 243159, "2026-06-30 00:00:00", None)


def test_single_query_without_prev(monkeypatch):
    """边界：方案 B 无 PRE_HOLDER_NUM 时 prev 为 None（单期模式）。"""
    monkeypatch.setattr(
        "app.data_sources.akshare_source.requests.get",
        lambda *a, **k: FakeResponse(_fake_single_payload(prev_count=None)),
    )
    source = AKShareSource()
    result = source._query_shareholder_two_periods("600519")

    assert result[0] == 296404
    assert result[1] is None


def test_single_query_empty(monkeypatch):
    """边界：方案 B 无返回数据时为 None。"""
    monkeypatch.setattr(
        "app.data_sources.akshare_source.requests.get",
        lambda *a, **k: FakeResponse({"result": {"pages": 0, "data": []}}),
    )
    source = AKShareSource()
    assert source._query_shareholder_two_periods("600519") is None


# ---------- 方案 A：全市场最新缓存过滤 ----------


def test_latest_all_filter_by_code(monkeypatch):
    """正常路径：方案 A 全量缓存按代码过滤。"""
    monkeypatch.setattr(
        "app.data_sources.akshare_source.ak.stock_zh_a_gdhs",
        lambda symbol: _fake_latest_all_df(),
    )
    source = AKShareSource()
    result = source._shareholder_from_latest_all("600519")

    assert result == (296404, 243159, date(2026, 6, 30), date(2025, 12, 31))


def test_latest_all_not_found(monkeypatch):
    """边界：方案 A 全量中无该代码时返回 None。"""
    monkeypatch.setattr(
        "app.data_sources.akshare_source.ak.stock_zh_a_gdhs",
        lambda symbol: _fake_latest_all_df(),
    )
    source = AKShareSource()
    assert source._shareholder_from_latest_all("000001") is None


def test_latest_all_cached(monkeypatch):
    """正常路径：方案 A 缓存生效，第二次不重复拉取。"""
    calls = {"n": 0}

    def fake_gdhs(symbol):
        calls["n"] += 1
        return _fake_latest_all_df()

    monkeypatch.setattr(
        "app.data_sources.akshare_source.ak.stock_zh_a_gdhs", fake_gdhs
    )
    source = AKShareSource()
    source._get_gdhs_latest_all()
    source._get_gdhs_latest_all()

    assert calls["n"] == 1


# ---------- 批量判定 ----------


def test_batch_mode_detected_after_three_distinct(monkeypatch):
    """边界：60 秒窗口内 3 只不同股票判定为批量模式。"""
    monkeypatch.setattr(
        "app.data_sources.akshare_source.requests.get",
        lambda *a, **k: FakeResponse(_fake_single_payload(prev_count=100)),
    )
    source = AKShareSource()
    assert source._use_batch_shareholder_mode("600519") is False
    assert source._use_batch_shareholder_mode("600000") is False
    assert source._use_batch_shareholder_mode("600001") is True


def test_single_repeated_stock_not_batch(monkeypatch):
    """边界：同一只股票重复请求不判定为批量。"""
    source = AKShareSource()
    for _ in range(5):
        assert source._use_batch_shareholder_mode("600519") is False


# ---------- _analyze_shareholder_count 集成 ----------


def test_analyze_shareholder_count_increase(monkeypatch):
    """正常路径：方案 B 数据填充 ExtendedAnalysis（户数增加 → increase）。"""
    monkeypatch.setattr(
        "app.data_sources.akshare_source.requests.get",
        lambda *a, **k: FakeResponse(_fake_single_payload(prev_count=243159)),
    )
    source = AKShareSource()
    result = ExtendedAnalysis(source="akshare", fetched_at=None)
    source._analyze_shareholder_count(result, "600519")

    assert result.shareholder_count_latest == 296404
    assert result.shareholder_count_previous == 243159
    assert result.shareholder_count_change == "increase"
    assert result.shareholder_change_rate == 21.9
    assert "截止2026-06-30" in result.shareholder_analysis_summary


def test_analyze_shareholder_count_batch_path(monkeypatch):
    """正常路径：批量模式下走方案 A 全量缓存过滤。"""
    monkeypatch.setattr(
        "app.data_sources.akshare_source.ak.stock_zh_a_gdhs",
        lambda symbol: _fake_latest_all_df(),
    )
    source = AKShareSource()
    # 连续 3 只不同股票触发批量模式
    source._use_batch_shareholder_mode("600519")
    source._use_batch_shareholder_mode("600000")
    source._use_batch_shareholder_mode("600001")

    result = ExtendedAnalysis(source="akshare", fetched_at=None)
    source._analyze_shareholder_count(result, "603013")

    assert result.shareholder_count_latest == 1000
    assert result.shareholder_count_previous == 900
    assert result.shareholder_count_change == "increase"


def test_analyze_shareholder_count_unknown(monkeypatch):
    """边界：方案 B 无数据时置 unknown。"""
    monkeypatch.setattr(
        "app.data_sources.akshare_source.requests.get",
        lambda *a, **k: FakeResponse({"result": {"pages": 0, "data": []}}),
    )
    source = AKShareSource()
    result = ExtendedAnalysis(source="akshare", fetched_at=None)
    source._analyze_shareholder_count(result, "600519")

    assert result.shareholder_count_change == "unknown"
    assert result.shareholder_analysis_summary == "暂无股东户数数据"
