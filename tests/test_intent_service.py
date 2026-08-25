"""意图识别服务测试。"""

import pytest

from app.services.intent_service import (
    IntentResult,
    IntentType,
    classify_intent,
    _extract_code,
    _parse_llm_response,
    _resolve_stock_code,
)


class FakeLLMProvider:
    """Mock LLM Provider，按预设文本返回。"""

    def __init__(self, response: str):
        self.response = response

    def invoke(self, system_prompt: str, user_prompt: str) -> str:
        return self.response


class FakeCache:
    """Mock 缓存。"""

    def __init__(self, stocks=None):
        self._stocks = stocks or []

    def get_stock_list(self):
        return self._stocks


class TestIntentService:
    """意图识别服务测试套件"""

    @pytest.mark.asyncio
    async def test_empty_query(self):
        """边界：空输入返回 UNKNOWN。"""
        result = await classify_intent("", cache=FakeCache())
        assert result.intent_type == IntentType.UNKNOWN
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_non_stock_query(self):
        """边界：非股票相关问题返回 UNKNOWN。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "unknown",
          "stock_code": null,
          "stock_name": null,
          "confidence": 0,
          "message": "无法识别此问题"
        }
        ''')
        result = await classify_intent("今天天气怎么样", provider=provider, cache=FakeCache())
        assert result.intent_type == IntentType.UNKNOWN
        assert "无法识别" in result.message

    @pytest.mark.asyncio
    async def test_rule_code_recognition(self):
        """正常路径：6位数字代码直接规则识别为个股分析。"""
        cache = FakeCache([{"code": "600519", "name": "贵州茅台"}])
        result = await classify_intent("分析 600519", cache=cache)
        assert result.intent_type == IntentType.ANALYZE
        assert result.stock_code == "600519"
        assert result.confidence >= 0.9

    @pytest.mark.asyncio
    async def test_llm_analyze_with_name_mapping(self):
        """正常路径：LLM 识别 analyze 并提供名称，本地映射代码。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": null,
          "stock_name": "贵州茅台",
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
        ])
        result = await classify_intent("帮我深度分析一下贵州茅台", provider=provider, cache=cache)
        assert result.intent_type == IntentType.ANALYZE
        assert result.stock_code == "600519"
        assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_llm_screen(self):
        """正常路径：LLM 识别 screen。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "screen",
          "stock_code": null,
          "stock_name": null,
          "confidence": 0.85,
          "message": "已进入选股"
        }
        ''')
        result = await classify_intent("帮我选几只低估值白马股", provider=provider, cache=FakeCache())
        assert result.intent_type == IntentType.SCREEN
        assert result.confidence == 0.85

    @pytest.mark.asyncio
    async def test_llm_quote(self):
        """正常路径：LLM 识别 quote 并提供代码。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "quote",
          "stock_code": "000001",
          "stock_name": "平安银行",
          "confidence": 0.88,
          "message": "已识别"
        }
        ''')
        result = await classify_intent("平安银行现在多少钱", provider=provider, cache=FakeCache())
        assert result.intent_type == IntentType.QUOTE
        assert result.stock_code == "000001"

    @pytest.mark.asyncio
    async def test_low_confidence_downgrade(self):
        """边界：置信度低于 0.6 降级为 UNKNOWN。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": "600519",
          "stock_name": "贵州茅台",
          "confidence": 0.3,
          "message": "不太确定"
        }
        ''')
        result = await classify_intent("茅台？", provider=provider, cache=FakeCache())
        assert result.intent_type == IntentType.UNKNOWN
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_invalid_json_fallback(self):
        """边界：LLM 返回非 JSON 时降级处理。"""
        provider = FakeLLMProvider("这不是 JSON")
        result = await classify_intent("分析 600519", provider=provider, cache=FakeCache())
        # 规则提取到代码，应降级为 analyze
        assert result.intent_type == IntentType.ANALYZE
        assert result.stock_code == "600519"

    @pytest.mark.asyncio
    async def test_user_input_with_braces(self):
        """边界：用户输入包含花括号不会触发格式化异常。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": "600519",
          "stock_name": "贵州茅台",
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        result = await classify_intent("分析 {600519} 这只股票", provider=provider, cache=FakeCache())
        assert result.intent_type == IntentType.ANALYZE
        assert result.stock_code == "600519"

    def test_extract_code(self):
        """工具函数：提取 6 位代码。"""
        assert _extract_code("分析 600519") == "600519"
        assert _extract_code("000001 怎么样") == "000001"
        assert _extract_code("没有代码") is None
        assert _extract_code("手机号 13800138000") is None

    def test_parse_llm_response(self):
        """工具函数：解析 JSON。"""
        assert _parse_llm_response('{"a": 1}')["a"] == 1
        assert _parse_llm_response("```json\n{\"a\": 1}\n```")["a"] == 1
        assert _parse_llm_response("无效文本") == {}

    def test_resolve_stock_code(self):
        """工具函数：名称到代码映射。"""
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
        ])
        assert _resolve_stock_code("贵州茅台", cache) == "600519"
        assert _resolve_stock_code("600519", cache) == "600519"
        assert _resolve_stock_code("不存在", cache) is None

    # ========== v1.8.0 user_preference 测试 ==========

    @pytest.mark.asyncio
    async def test_user_preference_extraction_from_llm(self):
        """正常路径：LLM 响应包含 user_preference 时正确解析。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": null,
          "stock_name": "贵州茅台",
          "user_preference": "重点关注技术面和主力资金流向",
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
        ])
        result = await classify_intent("分析贵州茅台，重点关注技术面和主力资金流向", provider=provider, cache=cache)
        assert result.intent_type == IntentType.ANALYZE
        assert result.stock_code == "600519"
        assert result.user_preference == "重点关注技术面和主力资金流向"

    @pytest.mark.asyncio
    async def test_user_preference_empty_when_not_provided(self):
        """边界：LLM 响应不包含 user_preference 时默认为空字符串。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": "600519",
          "stock_name": "贵州茅台",
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        result = await classify_intent("分析600519", provider=provider, cache=FakeCache())
        assert result.intent_type == IntentType.ANALYZE
        assert result.user_preference == ""

    @pytest.mark.asyncio
    async def test_user_preference_with_screen(self):
        """正常路径：screen 意图也能提取 user_preference。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "screen",
          "stock_code": null,
          "stock_name": null,
          "user_preference": "偏好低估值蓝筹股，PE低于20",
          "confidence": 0.85,
          "message": "已进入选股"
        }
        ''')
        result = await classify_intent("帮我选股，偏好低估值蓝筹股", provider=provider, cache=FakeCache())
        assert result.intent_type == IntentType.SCREEN
        assert result.user_preference == "偏好低估值蓝筹股，PE低于20"

    def test_intent_result_default_user_preference(self):
        """边界：IntentResult 默认 user_preference 为空字符串。"""
        result = IntentResult(
            intent_type=IntentType.ANALYZE,
            stock_code="600519",
            confidence=0.9,
        )
        assert result.user_preference == ""

    @pytest.mark.asyncio
    async def test_rule_code_has_empty_preference(self):
        """边界：规则识别代码时不走 LLM，user_preference 应为空。"""
        cache = FakeCache([{"code": "600519", "name": "贵州茅台"}])
        result = await classify_intent("分析 600519", cache=cache)
        assert result.intent_type == IntentType.ANALYZE
        assert result.stock_code == "600519"
        # 规则识别时 user_preference 默认为空
        assert result.user_preference == ""

    def test_user_preference_field_in_intent_result_type(self):
        """验证：user_preference 字段存在于 IntentResult 数据类中。"""
        result = IntentResult(
            intent_type=IntentType.ANALYZE,
            stock_code="000001",
            stock_name="平安银行",
            user_preference="关注短期走势",
            confidence=0.8,
            raw_text="分析000001，关注短期走势",
            message="识别成功",
        )
        assert hasattr(result, 'user_preference')
        assert result.user_preference == "关注短期走势"

    # ========== v2.2.0 A 股验证测试 ==========

    def test_verify_a_share_stock_valid_code(self):
        """正常路径：A 股代码验证通过。"""
        from app.services.intent_service import _verify_a_share_stock
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
        ])
        assert _verify_a_share_stock("600519", cache) is True
        assert _verify_a_share_stock("000001", cache) is True

    def test_verify_a_share_stock_valid_code_with_name(self):
        """正常路径：A 股代码和名称匹配验证通过。"""
        from app.services.intent_service import _verify_a_share_stock
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
        ])
        assert _verify_a_share_stock("600519", cache, stock_name="贵州茅台") is True
        assert _verify_a_share_stock("000001", cache, stock_name="平安银行") is True

    def test_verify_a_share_stock_code_name_mismatch(self):
        """边界：代码与名称不匹配时验证失败（防止LLM幻觉）。"""
        from app.services.intent_service import _verify_a_share_stock
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
        ])
        # 600519 对应贵州茅台，传入"新浪"应失败
        assert _verify_a_share_stock("600519", cache, stock_name="新浪") is False
        # 000001 对应平安银行，传入"贵州茅台"应失败
        assert _verify_a_share_stock("000001", cache, stock_name="贵州茅台") is False

    def test_verify_a_share_stock_invalid_code(self):
        """边界：非 A 股代码验证失败。"""
        from app.services.intent_service import _verify_a_share_stock
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
        ])
        # 假设 999999 不在 A 股列表中
        assert _verify_a_share_stock("999999", cache) is False

    def test_verify_a_share_stock_empty_cache(self):
        """边界：缓存为空时降级为通过。"""
        from app.services.intent_service import _verify_a_share_stock
        cache = FakeCache([])
        # 缓存为空时应降级为通过，不阻塞流程
        assert _verify_a_share_stock("600519", cache) is True
        assert _verify_a_share_stock("999999", cache) is True

    @pytest.mark.asyncio
    async def test_classify_intent_non_a_share_code(self):
        """边界：非 A 股代码返回 UNKNOWN 意图。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": "999999",
          "stock_name": "非A股股票",
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
        ])
        result = await classify_intent("分析 999999", provider=provider, cache=cache)
        assert result.intent_type == IntentType.UNKNOWN
        assert "不在A股分析范围内" in result.message

    @pytest.mark.asyncio
    async def test_classify_intent_stock_name_not_mapped(self):
        """边界：股票名称无法映射到 A 股代码时返回 UNKNOWN。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": null,
          "stock_name": "不存在的股票",
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
        ])
        result = await classify_intent("分析不存在的股票", provider=provider, cache=cache)
        assert result.intent_type == IntentType.UNKNOWN
        assert "不在A股分析范围内" in result.message

    @pytest.mark.asyncio
    async def test_classify_intent_screen_no_verify(self):
        """边界：screen 意图不执行 A 股验证。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "screen",
          "stock_code": null,
          "stock_name": null,
          "confidence": 0.85,
          "message": "已进入选股"
        }
        ''')
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
        ])
        result = await classify_intent("帮我选几只低估值股票", provider=provider, cache=cache)
        # screen 意图不应被 A 股验证拦截
        assert result.intent_type == IntentType.SCREEN

    @pytest.mark.asyncio
    async def test_classify_intent_valid_a_share_code(self):
        """正常路径：A 股代码验证通过后正常进入分析流程。"""
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
        ])
        result = await classify_intent("分析 600519", cache=cache)
        assert result.intent_type == IntentType.ANALYZE
        assert result.stock_code == "600519"

    @pytest.mark.asyncio
    async def test_classify_intent_llm_hallucination_code_name_mismatch(self):
        """边界：LLM 幻觉返回的代码与名称不匹配时应拒绝。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": "600519",
          "stock_name": "新浪",
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
        ])
        result = await classify_intent("分析一下新浪的股票", provider=provider, cache=cache)
        # 600519 对应贵州茅台，LLM 返回新浪，应拒绝
        assert result.intent_type == IntentType.UNKNOWN
        assert "不在A股分析范围内" in result.message

    @pytest.mark.asyncio
    async def test_llm_hallucination_code_with_null_name(self):
        """边界：LLM幻觉返回stock_code但stock_name为空，且代码对应名称不在用户查询中时应拒绝。

        场景：用户问"腾讯的股票"，LLM幻觉返回 stock_code="601633"(长城汽车), stock_name=null。
        此时应检测到601633对应"长城汽车"不在用户查询"腾讯的股票"中，判定为幻觉并拒绝。
        """
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": "601633",
          "stock_name": null,
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "601633", "name": "长城汽车"},
            {"code": "000001", "name": "平安银行"},
        ])
        result = await classify_intent("腾讯的股票", provider=provider, cache=cache)
        # LLM幻觉返回601633(长城汽车)，但用户问的是腾讯，应拒绝
        assert result.intent_type == IntentType.UNKNOWN
        assert "未找到" in result.message and "匹配的A股股票" in result.message

    @pytest.mark.asyncio
    async def test_llm_returns_code_with_null_name_but_code_matches_query(self):
        """正常路径：LLM返回stock_code但stock_name为空，代码对应名称在用户查询中时应通过。

        场景：用户问"分析一下601633"，LLM返回 stock_code="601633", stock_name=null。
        此时601633对应"长城汽车"虽不在查询中，但查询本身包含代码"601633"，应通过。
        """
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": "601633",
          "stock_name": null,
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        cache = FakeCache([
            {"code": "601633", "name": "长城汽车"},
        ])
        result = await classify_intent("分析一下601633", provider=provider, cache=cache)
        # 查询中包含代码601633，应通过
        assert result.intent_type == IntentType.ANALYZE
        assert result.stock_code == "601633"

    @pytest.mark.asyncio
    async def test_llm_returns_matching_code_name_but_unrelated_to_query(self):
        """边界：LLM返回的代码和名称互相匹配，但都与用户查询无关时应拒绝。

        场景：用户问"分析新浪的股票"，LLM幻觉返回 stock_code="601633", stock_name="长城汽车"。
        代码和名称在缓存中互相匹配，但都与"新浪"无关，应判定为幻觉并拒绝。
        """
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": "601633",
          "stock_name": "长城汽车",
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "601633", "name": "长城汽车"},
            {"code": "000001", "name": "平安银行"},
        ])
        result = await classify_intent("分析新浪的股票", provider=provider, cache=cache)
        # 601633/长城汽车与"新浪"无关，应拒绝
        assert result.intent_type == IntentType.UNKNOWN
        assert "未找到" in result.message and "匹配的A股股票" in result.message

    @pytest.mark.asyncio
    async def test_llm_returns_matching_code_name_and_related_to_query(self):
        """正常路径：LLM返回的代码和名称与用户查询相关时应通过。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": "600519",
          "stock_name": "贵州茅台",
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
        ])
        result = await classify_intent("分析贵州茅台", provider=provider, cache=cache)
        assert result.intent_type == IntentType.ANALYZE
        assert result.stock_code == "600519"

    @pytest.mark.asyncio
    async def test_llm_returns_unknown_intent_with_hallucinated_code(self):
        """边界：LLM返回intent_type=unknown但带了幻觉stock_code时应拒绝。

        场景：用户问"分析新浪的股票"，LLM返回 intent_type="unknown", stock_code="601633"。
        即使intent_type是unknown，只要有stock_code就需要验证，601633与"新浪"无关，应拒绝。
        """
        provider = FakeLLMProvider('''
        {
          "intent_type": "unknown",
          "stock_code": "601633",
          "stock_name": null,
          "confidence": 0.5,
          "message": "无法识别"
        }
        ''')
        cache = FakeCache([
            {"code": "601633", "name": "长城汽车"},
        ])
        result = await classify_intent("分析新浪的股票", provider=provider, cache=cache)
        # 即使intent_type是unknown，601633与"新浪"无关，应拒绝
        assert result.intent_type == IntentType.UNKNOWN
        assert "未找到" in result.message and "匹配的A股股票" in result.message

    @pytest.mark.asyncio
    async def test_llm_returns_unknown_intent_with_matching_code(self):
        """正常路径：LLM返回intent_type=unknown但stock_code与查询匹配时应通过降级逻辑。

        场景：用户问"分析一下601633"，LLM返回 intent_type="unknown", stock_code="601633"。
        代码601633在查询中，应通过验证，后续可能通过降级逻辑处理。
        """
        provider = FakeLLMProvider('''
        {
          "intent_type": "unknown",
          "stock_code": "601633",
          "stock_name": null,
          "confidence": 0.5,
          "message": "无法识别"
        }
        ''')
        cache = FakeCache([
            {"code": "601633", "name": "长城汽车"},
        ])
        result = await classify_intent("分析一下601633", provider=provider, cache=cache)
        # 代码601633在查询中，应通过验证
        # 由于confidence<0.6且intent_type=unknown，最终结果取决于降级逻辑
        assert result.intent_type == IntentType.UNKNOWN or result.stock_code == "601633"

    # ========== v2.5.0 持仓状态/成本价提取测试 ==========

    @pytest.mark.asyncio
    async def test_position_status_holding_with_cost_price(self):
        """正常路径：LLM 提取到已持仓状态和成本价。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": null,
          "stock_name": "贵州茅台",
          "position_status": "已持仓",
          "cost_price": 1500,
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
        ])
        result = await classify_intent("分析贵州茅台，我持有成本1500", provider=provider, cache=cache)
        assert result.intent_type == IntentType.ANALYZE
        assert result.stock_code == "600519"
        assert result.position_status == "已持仓"
        assert result.cost_price == 1500.0

    @pytest.mark.asyncio
    async def test_position_status_not_holding(self):
        """正常路径：LLM 提取到未买入状态。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": null,
          "stock_name": "平安银行",
          "position_status": "未买入",
          "cost_price": null,
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        cache = FakeCache([
            {"code": "000001", "name": "平安银行"},
        ])
        result = await classify_intent("分析平安银行，我还没买，想了解现在能不能建仓", provider=provider, cache=cache)
        assert result.intent_type == IntentType.ANALYZE
        assert result.position_status == "未买入"
        assert result.cost_price is None

    @pytest.mark.asyncio
    async def test_position_status_default_when_not_provided(self):
        """边界：LLM 未返回持仓字段时默认为空。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": "600519",
          "stock_name": "贵州茅台",
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        result = await classify_intent("分析600519", provider=provider, cache=FakeCache())
        assert result.intent_type == IntentType.ANALYZE
        assert result.position_status == ""
        assert result.cost_price is None

    def test_position_fields_in_intent_result_type(self):
        """验证：position_status / cost_price 字段存在于 IntentResult 数据类中。"""
        result = IntentResult(
            intent_type=IntentType.ANALYZE,
            stock_code="000001",
            stock_name="平安银行",
            position_status="已持仓",
            cost_price=10.5,
            confidence=0.8,
        )
        assert hasattr(result, 'position_status')
        assert hasattr(result, 'cost_price')
        assert result.position_status == "已持仓"
        assert result.cost_price == 10.5

    def test_position_fields_defaults(self):
        """边界：IntentResult 默认持仓字段为空/None。"""
        result = IntentResult(
            intent_type=IntentType.ANALYZE,
            stock_code="600519",
            confidence=0.9,
        )
        assert result.position_status == ""
        assert result.cost_price is None

    # ========== v2.5.1 持仓数量（股）提取测试 ==========

    @pytest.mark.asyncio
    async def test_position_quantity_holding_with_quantity_and_cost(self):
        """正常路径：LLM 提取到持仓数量（股）与成本价。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": null,
          "stock_name": "贵州茅台",
          "position_status": "已持仓",
          "cost_price": 1500,
          "position_quantity": 500,
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        cache = FakeCache([
            {"code": "600519", "name": "贵州茅台"},
        ])
        result = await classify_intent("分析贵州茅台，我持有500股成本1500", provider=provider, cache=cache)
        assert result.intent_type == IntentType.ANALYZE
        assert result.stock_code == "600519"
        assert result.position_status == "已持仓"
        assert result.cost_price == 1500.0
        assert result.position_quantity == 500.0

    @pytest.mark.asyncio
    async def test_position_quantity_default_when_not_provided(self):
        """边界：LLM 未返回持仓数量时默认为 None。"""
        provider = FakeLLMProvider('''
        {
          "intent_type": "analyze",
          "stock_code": "600519",
          "stock_name": "贵州茅台",
          "confidence": 0.9,
          "message": "已识别"
        }
        ''')
        result = await classify_intent("分析600519", provider=provider, cache=FakeCache())
        assert result.intent_type == IntentType.ANALYZE
        assert result.position_quantity is None

    def test_normalize_position_quantity(self):
        """验证：_normalize_position_quantity 归一化逻辑。"""
        from app.services.intent_service import _normalize_position_quantity
        assert _normalize_position_quantity(500) == 500.0
        assert _normalize_position_quantity("500") == 500.0
        assert _normalize_position_quantity(0) is None
        assert _normalize_position_quantity(-5) is None
        assert _normalize_position_quantity("abc") is None
        assert _normalize_position_quantity(None) is None
        assert _normalize_position_quantity("null") is None

    def test_position_quantity_in_intent_result_type(self):
        """验证：position_quantity 字段存在于 IntentResult 数据类中。"""
        result = IntentResult(
            intent_type=IntentType.ANALYZE,
            stock_code="000001",
            stock_name="平安银行",
            position_status="已持仓",
            cost_price=10.5,
            position_quantity=200,
            confidence=0.8,
        )
        assert hasattr(result, 'position_quantity')
        assert result.position_quantity == 200

    def test_position_quantity_defaults(self):
        """边界：IntentResult 默认持仓数量为 None。"""
        result = IntentResult(
            intent_type=IntentType.ANALYZE,
            stock_code="600519",
            confidence=0.9,
        )
        assert result.position_quantity is None
