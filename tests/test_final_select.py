# -*- coding: utf-8 -*-
"""阶段三 LLM 精选推荐测试（v2.13.0 / v2.14.0）。

覆盖：
- 阶段二输出配额计算（用户要求 × 3，上限 50）
- 阶段三正常路径（Mock LLM 返回合法 JSON，精选出目标数量并回填意见）
- LLM 调用失败时兜底取前 target_count 只，opinion 为空
- LLM 返回的 code 不在候选池中时忽略该条
- 候选池数量不足 target_count 时返回全部候选
- 提示词包含防虚构规则与用户要求
- 意见超过 100 字时截断
- v2.14.0：LLM 评分（llm_score）回填、按评分降序排序、缺失评分默认 0
"""

import json
from unittest.mock import MagicMock


def _make_candidates(n: int):
    """构造 n 只测试用候选股票。"""
    from app.models.screener import ScreenResult
    results = []
    for i in range(n):
        code = f"{i:06d}"
        results.append(ScreenResult(
            code=code,
            name=f"测试股票{i}",
            score=1.0,
            matched_conditions=3,
            total_conditions=3,
            reason=f"命中全部条件 {i}",
            change_percent=1.0 + i * 0.1,
            amount=5e9,
            pe_ttm=10 + i,
            pb=1.2,
            turnover_rate=2.5,
            market_cap=3000e8,
            volume=100000,
            amplitude=3.2,
            final_score=90.0 - i,
        ))
    return results


class TestRankPoolSize:
    """阶段二输出配额计算测试"""

    def test_rank_pool_size_10(self):
        """正常路径：用户要求 10 只 → 配额 30。"""
        from app.screener.final_select import compute_rank_pool_size
        assert compute_rank_pool_size(10) == 30

    def test_rank_pool_size_15(self):
        """正常路径：用户要求 15 只 → 配额 45。"""
        from app.screener.final_select import compute_rank_pool_size
        assert compute_rank_pool_size(15) == 45

    def test_rank_pool_size_20_capped(self):
        """边界：用户要求 20 只 → 3 倍为 60，封顶 50。"""
        from app.screener.final_select import compute_rank_pool_size
        assert compute_rank_pool_size(20) == 50

    def test_rank_pool_size_30_capped(self):
        """边界：用户要求 30 只（默认）→ 封顶 50。"""
        from app.screener.final_select import compute_rank_pool_size
        assert compute_rank_pool_size(30) == 50


class TestLLMFinalSelect:
    """阶段三 LLM 精选测试"""

    def test_final_select_normal(self):
        """正常路径：Mock LLM 返回带评分的 JSON，精选出目标数量、回填评分与意见并按评分降序。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(5)
        # LLM 返回顺序为 000003 先、000001 后，但 000001 评分更高，应排前面（v2.14.0 按评分降序）
        llm_response = json.dumps([
            {"code": "000003", "score": 90, "opinion": "动量强劲，资金关注度高。"},
            {"code": "000001", "score": 95, "opinion": "估值偏低，基本面稳健。"},
        ], ensure_ascii=False)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = llm_response

        results = llm_final_select(candidates, mock_provider, target_count=2,
                                   user_input="帮我选低估值的股票")

        assert len(results) == 2
        assert results[0].code == "000001"
        assert results[0].llm_score == 95
        assert results[0].opinion == "估值偏低，基本面稳健。"
        assert results[1].code == "000003"
        assert results[1].llm_score == 90
        assert results[1].opinion == "动量强劲，资金关注度高。"

    def test_final_select_score_desc(self):
        """正常路径：LLM 返回乱序评分时，结果按 llm_score 降序排列。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(4)
        llm_response = json.dumps([
            {"code": "000002", "score": 70, "opinion": "意见2"},
            {"code": "000000", "score": 99, "opinion": "意见0"},
            {"code": "000003", "score": 55, "opinion": "意见3"},
        ], ensure_ascii=False)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = llm_response

        results = llm_final_select(candidates, mock_provider, target_count=3)

        assert [r.code for r in results] == ["000000", "000002", "000003"]
        assert [r.llm_score for r in results] == [99, 70, 55]

    def test_final_select_score_tie_keeps_final_score_order(self):
        """边界：LLM 评分相同时，按原规则 final_score 降序保持稳定。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(3)
        # 000001 与 000000 评分同为 80，000000 的 final_score(90) > 000001 的(89)，应排前面
        llm_response = json.dumps([
            {"code": "000001", "score": 80, "opinion": "意见B"},
            {"code": "000000", "score": 80, "opinion": "意见A"},
        ], ensure_ascii=False)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = llm_response

        results = llm_final_select(candidates, mock_provider, target_count=2)

        assert results[0].code == "000000"
        assert results[1].code == "000001"

    def test_final_select_missing_score_default_zero(self):
        """边界：LLM 返回的股票缺少 score 字段时默认 0 分，排在有评分的股票之后。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(3)
        llm_response = json.dumps([
            {"code": "000001", "score": 80, "opinion": "有评分"},
            {"code": "000002", "opinion": "无评分"},
        ], ensure_ascii=False)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = llm_response

        results = llm_final_select(candidates, mock_provider, target_count=2)

        assert results[0].code == "000001"
        assert results[0].llm_score == 80
        assert results[1].code == "000002"
        assert results[1].llm_score == 0

    def test_final_select_llm_failure_llm_score_none(self):
        """边界：LLM 调用失败兜底时，llm_score 保持 None（未评分）。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(3)
        mock_provider = MagicMock()
        mock_provider.invoke.side_effect = Exception("LLM 服务不可用")

        results = llm_final_select(candidates, mock_provider, target_count=2)

        assert results[0].llm_score is None
        assert results[1].llm_score is None

    def test_final_select_prompt_contains_score(self):
        """正常路径：提示词明确要求 LLM 输出 score 评分字段。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(2)
        mock_provider = MagicMock()
        mock_provider.invoke.return_value = "[]"

        llm_final_select(candidates, mock_provider, target_count=1)

        call_args = mock_provider.invoke.call_args
        user_prompt = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("user_prompt", "")
        assert '"score"' in user_prompt

    def test_screen_result_llm_score_default_none(self):
        """正常路径：ScreenResult 新增 llm_score 字段默认值为 None。"""
        from app.models.screener import ScreenResult

        r = ScreenResult(code="000001", name="测试")
        assert r.llm_score is None

    def test_final_select_llm_failure_fallback(self):
        """边界：LLM 调用抛异常时，兜底取前 target_count 只，opinion 为空。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(5)
        mock_provider = MagicMock()
        mock_provider.invoke.side_effect = Exception("LLM 服务不可用")

        results = llm_final_select(candidates, mock_provider, target_count=2)

        # 兜底：按原排序取前 2 只，不抛异常
        assert len(results) == 2
        assert results[0].code == "000000"
        assert results[1].code == "000001"
        assert results[0].opinion == ""
        assert results[1].opinion == ""

    def test_final_select_invalid_json_fallback(self):
        """边界：LLM 返回非法 JSON 时，兜底取前 target_count 只。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(5)
        mock_provider = MagicMock()
        mock_provider.invoke.return_value = "这不是JSON内容"

        results = llm_final_select(candidates, mock_provider, target_count=2)

        assert len(results) == 2
        assert results[0].code == "000000"
        assert results[0].opinion == ""

    def test_final_select_unknown_code_ignored(self):
        """边界：LLM 返回的 code 不在候选池中，忽略该条，不足部分按原排序补齐。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(3)
        # 999999 不在候选池应被忽略；000001 给高分使其排最前（v2.14.0 按评分排序）
        llm_response = json.dumps([
            {"code": "999999", "score": 99, "opinion": "不存在的股票"},
            {"code": "000001", "score": 100, "opinion": "有效意见"},
        ], ensure_ascii=False)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = llm_response

        results = llm_final_select(candidates, mock_provider, target_count=3)

        # 999999 被忽略；000001 保留意见；不足目标数量时按原排序补齐（补齐全为 0 分，按 final_score 排）
        assert len(results) == 3
        assert results[0].code == "000001"
        assert results[0].opinion == "有效意见"
        assert results[1].code == "000000"
        assert results[1].opinion == ""
        assert results[2].code == "000002"

    def test_final_select_candidates_less_than_target(self):
        """边界：候选池数量不足 target_count 时，返回全部候选。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(2)
        llm_response = json.dumps([
            {"code": "000000", "opinion": "意见一"},
            {"code": "000001", "opinion": "意见二"},
        ], ensure_ascii=False)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = llm_response

        results = llm_final_select(candidates, mock_provider, target_count=10)

        assert len(results) == 2

    def test_final_select_empty_candidates(self):
        """边界：空候选池直接返回空列表，不调用 LLM。"""
        from app.screener.final_select import llm_final_select

        mock_provider = MagicMock()
        results = llm_final_select([], mock_provider, target_count=5)

        assert results == []
        mock_provider.invoke.assert_not_called()

    def test_final_select_prompt_anti_fabrication(self):
        """正常路径：提示词包含防虚构规则。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(2)
        mock_provider = MagicMock()
        mock_provider.invoke.return_value = "[]"

        llm_final_select(candidates, mock_provider, target_count=1)

        call_args = mock_provider.invoke.call_args
        system_prompt = call_args[0][0] if call_args[0] else call_args[1].get("system_prompt", "")
        user_prompt = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("user_prompt", "")
        combined = system_prompt + user_prompt
        assert "不得编造" in combined or "仅基于" in combined or "虚构" in combined

    def test_final_select_prompt_contains_user_input(self):
        """正常路径：提示词中包含用户的选股要求。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(2)
        mock_provider = MagicMock()
        mock_provider.invoke.return_value = "[]"

        llm_final_select(candidates, mock_provider, target_count=1,
                         user_input="帮我选低估值的股票")

        call_args = mock_provider.invoke.call_args
        user_prompt = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("user_prompt", "")
        assert "帮我选低估值的股票" in user_prompt

    def test_final_select_prompt_contains_stock_data(self):
        """正常路径：提示词中包含候选股票的关键数据。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(2)
        mock_provider = MagicMock()
        mock_provider.invoke.return_value = "[]"

        llm_final_select(candidates, mock_provider, target_count=1)

        call_args = mock_provider.invoke.call_args
        user_prompt = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("user_prompt", "")
        assert "000000" in user_prompt
        assert "测试股票0" in user_prompt
        assert "000001" in user_prompt

    def test_final_select_prompt_contains_target_count(self):
        """正常路径：提示词中明确要求选出的股票数量。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(5)
        mock_provider = MagicMock()
        mock_provider.invoke.return_value = "[]"

        llm_final_select(candidates, mock_provider, target_count=3)

        call_args = mock_provider.invoke.call_args
        user_prompt = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("user_prompt", "")
        assert "3" in user_prompt

    def test_final_select_opinion_truncated(self):
        """边界：LLM 返回超过 100 字的意见时截断到 100 字。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(2)
        long_opinion = "这是一段非常长的意见" * 20  # 超过 100 字
        llm_response = json.dumps([
            {"code": "000000", "opinion": long_opinion},
        ], ensure_ascii=False)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = llm_response

        results = llm_final_select(candidates, mock_provider, target_count=1)

        assert len(results) == 1
        assert len(results[0].opinion) <= 100

    def test_final_select_markdown_wrapped_json(self):
        """边界：LLM 返回 markdown 代码块包裹的 JSON 也能正确解析。"""
        from app.screener.final_select import llm_final_select

        candidates = _make_candidates(2)
        llm_response = '```json\n[{"code": "000000", "opinion": "测试意见"}]\n```'

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = llm_response

        results = llm_final_select(candidates, mock_provider, target_count=1)

        assert len(results) == 1
        assert results[0].opinion == "测试意见"
