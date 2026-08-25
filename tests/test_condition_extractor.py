"""选股条件抽取测试（v2.6.0）。

覆盖：LLM 抽取数值条件 / 区间 / 定性词映射 / 单位换算 / 空与降级边界 / display 格式化。
"""

import pytest

from app.screener.conditions import FieldCondition
from app.screener.condition_extractor import (
    extract_conditions,
    extract_screen_params,
    parse_conditions_json,
    _parse_llm_response,
    _to_field_condition,
    _normalize_value,
    format_condition,
    describe_conditions,
    MAX_TOP_N,
)


class FakeLLMProvider:
    """Mock LLM Provider，按预设文本返回。"""

    def __init__(self, response: str = "", error: bool = False):
        self.response = response
        self.error = error
        self.last_system_prompt = ""
        self.last_user_prompt = ""

    def invoke(self, system_prompt: str, user_prompt: str) -> str:
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt
        if self.error:
            raise RuntimeError("LLM 调用失败")
        return self.response


class TestExtractConditions:
    """extract_conditions 主函数测试套件"""

    def test_extract_numeric_conditions(self):
        """正常路径：抽取数值条件，市值单位自动换算（亿→元）。"""
        provider = FakeLLMProvider('''
        {"conditions": [
            {"field": "pe_ttm", "op": "<", "value": 20},
            {"field": "market_cap", "op": ">", "value": 100}
        ]}
        ''')
        conditions = extract_conditions("帮我选PE低于20，市值大于100亿的股票", provider=provider)
        assert len(conditions) == 2
        assert conditions[0].field == "pe_ttm"
        assert conditions[0].op == "<"
        assert conditions[0].value == 20.0
        assert conditions[1].field == "market_cap"
        assert conditions[1].op == ">"
        assert conditions[1].value == 100 * 1e8  # 亿 → 元

    def test_extract_between_condition(self):
        """正常路径：区间条件（PE 在 10 到 20 之间）→ between。"""
        provider = FakeLLMProvider('''
        {"conditions": [
            {"field": "pe_ttm", "op": "between", "value": [10, 20]}
        ]}
        ''')
        conditions = extract_conditions("PE在10到20之间的股票", provider=provider)
        assert len(conditions) == 1
        assert conditions[0].field == "pe_ttm"
        assert conditions[0].op == "between"
        assert conditions[0].value == [10.0, 20.0]

    def test_extract_qualitative_mapping(self):
        """正常路径：定性词由 LLM 映射为数值条件（低估值 → PE/PB 区间）。"""
        provider = FakeLLMProvider('''
        {"conditions": [
            {"field": "pe_ttm", "op": "between", "value": [0, 20]},
            {"field": "pb", "op": "between", "value": [0, 3]}
        ]}
        ''')
        conditions = extract_conditions("推荐几只低估值白马股", provider=provider)
        assert len(conditions) == 2
        assert conditions[0].field == "pe_ttm"
        assert conditions[1].field == "pb"

    def test_extract_turnover_rate_condition(self):
        """正常路径：换手率条件（大于 5%）。"""
        provider = FakeLLMProvider('''
        {"conditions": [
            {"field": "turnover_rate", "op": ">", "value": 5}
        ]}
        ''')
        conditions = extract_conditions("换手率大于5%的活跃股", provider=provider)
        assert len(conditions) == 1
        assert conditions[0].field == "turnover_rate"
        assert conditions[0].value == 5.0

    def test_extract_empty_when_no_conditions(self):
        """边界：LLM 未抽取到任何条件时返回空列表。"""
        provider = FakeLLMProvider('{"conditions": []}')
        conditions = extract_conditions("随便看看", provider=provider)
        assert conditions == []

    def test_extract_empty_on_invalid_field(self):
        """边界：字段不在白名单时忽略（如 roe 不支持）。"""
        provider = FakeLLMProvider('''
        {"conditions": [
            {"field": "roe", "op": ">", "value": 15}
        ]}
        ''')
        conditions = extract_conditions("ROE大于15的股票", provider=provider)
        assert conditions == []

    def test_extract_llm_failure(self):
        """边界：LLM 调用失败时返回空列表（降级不报错）。"""
        provider = FakeLLMProvider(error=True)
        conditions = extract_conditions("帮我选股", provider=provider)
        assert conditions == []

    def test_extract_invalid_json(self):
        """边界：LLM 返回非 JSON 时返回空列表。"""
        provider = FakeLLMProvider("这不是 JSON")
        conditions = extract_conditions("帮我选股", provider=provider)
        assert conditions == []

    def test_extract_prompt_contains_user_input(self):
        """正常路径：调用 LLM 时提示词包含用户输入。"""
        provider = FakeLLMProvider('{"conditions": []}')
        extract_conditions("帮我选低估值股票", provider=provider)
        assert "帮我选低估值股票" in provider.last_user_prompt
        # 提示词应包含字段白名单约束
        assert "pe_ttm" in provider.last_system_prompt


class TestParseConditionsJson:
    """parse_conditions_json 测试套件"""

    def test_parse_valid_json(self):
        """正常路径：解析合法 JSON 条件列表。"""
        conditions = parse_conditions_json('''
        {"conditions": [{"field": "pb", "op": "<", "value": 3}]}
        ''')
        assert len(conditions) == 1
        assert conditions[0].field == "pb"

    def test_parse_markdown_wrapped(self):
        """正常路径：解析 markdown 代码块包裹的 JSON。"""
        conditions = parse_conditions_json('''
        ```json
        {"conditions": [{"field": "pe_ttm", "op": ">", "value": 10}]}
        ```
        ''')
        assert len(conditions) == 1
        assert conditions[0].value == 10.0

    def test_parse_empty_response(self):
        """边界：空响应返回空列表。"""
        assert parse_conditions_json("") == []

    def test_parse_no_conditions_key(self):
        """边界：响应缺少 conditions 键返回空列表。"""
        assert parse_conditions_json('{"other": 1}') == []

    def test_parse_null_conditions(self):
        """边界：conditions 为 null 返回空列表。"""
        assert parse_conditions_json('{"conditions": null}') == []


class TestToFieldCondition:
    """_to_field_condition 单元测试"""

    def test_valid_item(self):
        """正常路径：合法字段/操作符/值 → FieldCondition。"""
        cond = _to_field_condition({"field": "pb", "op": "<", "value": 3})
        assert isinstance(cond, FieldCondition)
        assert cond.field == "pb"
        assert cond.op == "<"
        assert cond.value == 3.0

    def test_invalid_field(self):
        """边界：字段不在白名单返回 None。"""
        assert _to_field_condition({"field": "roe", "op": ">", "value": 15}) is None

    def test_invalid_op(self):
        """边界：操作符不合法返回 None。"""
        assert _to_field_condition({"field": "pe_ttm", "op": "!=", "value": 10}) is None

    def test_invalid_value(self):
        """边界：值无法解析返回 None。"""
        assert _to_field_condition({"field": "pe_ttm", "op": ">", "value": "abc"}) is None

    def test_between_wrong_length(self):
        """边界：between 值长度不为 2 返回 None。"""
        assert _to_field_condition({"field": "pe_ttm", "op": "between", "value": [1, 2, 3]}) is None


class TestNormalizeValue:
    """_normalize_value 单位换算测试"""

    def test_market_cap_convert_to_yuan(self):
        """正常路径：市值从亿换算为元。"""
        assert _normalize_value("market_cap", 100) == 100 * 1e8

    def test_amount_convert_to_yuan(self):
        """正常路径：成交额从万元换算为元。"""
        assert _normalize_value("amount", 50000) == 50000 * 1e4

    def test_volume_keep_hands(self):
        """正常路径：成交量保持手。"""
        assert _normalize_value("volume", 100000) == 100000.0

    def test_plain_field(self):
        """正常路径：无单位字段原样返回。"""
        assert _normalize_value("pe_ttm", 20) == 20.0
        assert _normalize_value("turnover_rate", 5) == 5.0

    def test_between_list_conversion(self):
        """正常路径：between 列表整体换算。"""
        assert _normalize_value("market_cap", [50, 200]) == [50 * 1e8, 200 * 1e8]

    def test_invalid_value(self):
        """边界：无法解析的值返回 None。"""
        assert _normalize_value("pe_ttm", "abc") is None
        assert _normalize_value("market_cap", "abc") is None

    def test_invalid_list_element(self):
        """边界：between 列表含非法元素返回 None。"""
        assert _normalize_value("market_cap", ["abc", 200]) is None


class TestFormatCondition:
    """format_condition / describe_conditions 展示格式化测试"""

    def test_format_between(self):
        """正常路径：between 显示为 a~b。"""
        cond = FieldCondition(field="pe_ttm", op="between", value=[0, 20])
        assert format_condition(cond) == "PE 0~20"

    def test_format_compare(self):
        """正常路径：比较操作符显示。"""
        cond = FieldCondition(field="pb", op="<", value=3)
        assert format_condition(cond) == "PB < 3"

    def test_format_market_cap_in_yi(self):
        """正常路径：市值按亿显示。"""
        cond = FieldCondition(field="market_cap", op=">", value=5e9)
        assert format_condition(cond) == "市值 > 50亿"

    def test_format_percent(self):
        """正常路径：百分比字段带 % 单位。"""
        cond = FieldCondition(field="turnover_rate", op=">", value=0.3)
        assert format_condition(cond) == "换手率 > 0.3%"

    def test_format_volume_hands(self):
        """正常路径：成交量带手单位。"""
        cond = FieldCondition(field="volume", op=">", value=100000)
        assert format_condition(cond) == "成交量 > 100000手"

    def test_describe_conditions(self):
        """正常路径：describe_conditions 输出含 display 字段。"""
        conds = [
            FieldCondition(field="pe_ttm", op="between", value=[0, 20]),
            FieldCondition(field="market_cap", op=">", value=5e9),
        ]
        described = describe_conditions(conds)
        assert len(described) == 2
        assert described[0]["field"] == "pe_ttm"
        assert described[0]["display"] == "PE 0~20"
        assert described[1]["display"] == "市值 > 50亿"

    def test_describe_empty(self):
        """边界：空条件列表返回空列表。"""
        assert describe_conditions([]) == []


class TestExtractScreenParams:
    """extract_screen_params 推荐数量（top_n）提取测试（v2.6.1）"""

    def test_extract_conditions_and_top_n(self):
        """正常路径：一次抽取同时拿到条件与推荐数量。"""
        provider = FakeLLMProvider('''
        {"conditions": [{"field": "pe_ttm", "op": "<", "value": 20}], "top_n": 10}
        ''')
        conditions, top_n = extract_screen_params("推荐10只PE低于20的股票", provider=provider)
        assert len(conditions) == 1
        assert conditions[0].field == "pe_ttm"
        assert top_n == 10

    def test_no_top_n_when_not_specified(self):
        """边界：提示词未指定数量时 top_n 为 None。"""
        provider = FakeLLMProvider('{"conditions": []}')
        conditions, top_n = extract_screen_params("随便看看", provider=provider)
        assert conditions == []
        assert top_n is None

    def test_invalid_top_n(self):
        """边界：top_n 无法解析时返回 None。"""
        provider = FakeLLMProvider('{"conditions": [], "top_n": "abc"}')
        _, top_n = extract_screen_params("推荐几只股票", provider=provider)
        assert top_n is None

    def test_top_n_above_limit_capped(self):
        """边界：top_n 超过上限时截断为 MAX_TOP_N。"""
        provider = FakeLLMProvider('{"conditions": [], "top_n": 500}')
        _, top_n = extract_screen_params("推荐500只股票", provider=provider)
        assert top_n == MAX_TOP_N

    def test_top_n_non_positive(self):
        """边界：top_n 为非正整数时返回 None。"""
        provider = FakeLLMProvider('{"conditions": [], "top_n": 0}')
        _, top_n = extract_screen_params("推荐股票", provider=provider)
        assert top_n is None

    def test_top_n_as_float_string(self):
        """正常路径：top_n 为数字字符串（"10"）时正确解析。"""
        provider = FakeLLMProvider('{"conditions": [], "top_n": "10"}')
        _, top_n = extract_screen_params("推荐10只股票", provider=provider)
        assert top_n == 10

    def test_top_n_fraction_rounded(self):
        """边界：top_n 为小数时向下取整。"""
        provider = FakeLLMProvider('{"conditions": [], "top_n": 10.8}')
        _, top_n = extract_screen_params("推荐十只左右的股票", provider=provider)
        assert top_n == 10
