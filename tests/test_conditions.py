"""选股条件构建器测试：链式 API / 白名单 / 边界。"""

import pytest


class TestConditionGroup:
    """ConditionGroup 链式 API 测试套件"""

    def test_add_condition(self):
        """正常路径：添加单条条件。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="between", value=[0, 20]))
        assert len(cg) == 1

    def test_chain_api(self):
        """正常路径：链式添加多条条件。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        cg = (ConditionGroup()
              .add(FieldCondition(field="pe_ttm", op="between", value=[0, 20]))
              .add(FieldCondition(field="pb", op="<", value=3))
              .add(FieldCondition(field="market_cap", op=">", value=5e9)))
        assert len(cg) == 3

    def test_invalid_field(self):
        """边界：不在白名单的字段应抛出 ValueError。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        cg = ConditionGroup()
        with pytest.raises(ValueError, match="不在白名单"):
            cg.add(FieldCondition(field="invalid_field", op=">", value=10))

    def test_invalid_op(self):
        """边界：不合法的操作符应抛出 ValueError（Pydantic Literal 校验）。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        cg = ConditionGroup()
        with pytest.raises(ValueError, match="Input should be"):
            cg.add(FieldCondition(field="pe_ttm", op="!=", value=10))

    def test_between_value_must_be_list(self):
        """边界：between 操作符的值必须是 [min, max] 列表。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        cg = ConditionGroup()
        with pytest.raises(ValueError, match="between"):
            cg.add(FieldCondition(field="pe_ttm", op="between", value=10))

    def test_empty_condition_group(self):
        """边界：空条件组长度为 0。"""
        from app.screener.conditions import ConditionGroup
        cg = ConditionGroup()
        assert len(cg) == 0

    def test_to_query(self):
        """正常路径：条件组转查询字典。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="between", value=[0, 20]))

        query = cg.to_query()
        assert query["market"] == "A"
        assert len(query["conditions"]) == 1
        assert query["conditions"][0]["field"] == "pe_ttm"


class TestFieldConditionRequired:
    """FieldCondition required 字段测试套件（新增于 2026-08-25, v2.7.0）"""

    def test_required_default_true(self):
        """正常路径：required 默认值为 True。"""
        from app.screener.conditions import FieldCondition
        fc = FieldCondition(field="pe_ttm", op="<", value=20)
        assert fc.required is True

    def test_required_false(self):
        """正常路径：软条件 required=False。"""
        from app.screener.conditions import FieldCondition
        fc = FieldCondition(field="turnover_rate", op=">", value=0.3, required=False)
        assert fc.required is False

    def test_group_mixed_required(self):
        """正常路径：硬/软条件混合添加，required 标记正确保留。"""
        from app.screener.conditions import ConditionGroup, FieldCondition
        cg = ConditionGroup()
        cg.add(FieldCondition(field="pe_ttm", op="between", value=[0, 20], required=True))
        cg.add(FieldCondition(field="turnover_rate", op=">", value=0.3, required=False))
        assert len(cg) == 2
        assert cg.conditions[0].required is True
        assert cg.conditions[1].required is False


class TestFieldWhitelistExtension:
    """财务/技术字段白名单扩展测试套件（新增于 2026-08-25, v2.9.0）"""

    def test_roe_in_whitelist(self):
        """正常路径：roe 财务字段可加入条件。"""
        from app.screener.conditions import ConditionGroup, FieldCondition, FIELD_WHITELIST
        assert "roe" in FIELD_WHITELIST
        cg = ConditionGroup()
        cg.add(FieldCondition(field="roe", op=">", value=15))
        assert len(cg) == 1

    def test_rsi24_in_whitelist(self):
        """正常路径：rsi24 技术字段可加入条件。"""
        from app.screener.conditions import ConditionGroup, FieldCondition, FIELD_WHITELIST
        assert "rsi24" in FIELD_WHITELIST
        cg = ConditionGroup()
        cg.add(FieldCondition(field="rsi24", op="<", value=30))
        assert len(cg) == 1

    def test_ma20_in_whitelist(self):
        """正常路径：ma20 技术字段可加入条件。"""
        from app.screener.conditions import ConditionGroup, FieldCondition, FIELD_WHITELIST
        assert "ma20" in FIELD_WHITELIST
        cg = ConditionGroup()
        cg.add(FieldCondition(field="ma20", op=">", value=10))
        assert len(cg) == 1

    def test_financial_fields_in_whitelist(self):
        """正常路径：营收增长与毛利率财务字段可加入条件。"""
        from app.screener.conditions import FIELD_WHITELIST
        assert "revenue_growth" in FIELD_WHITELIST
        assert "gross_margin" in FIELD_WHITELIST
