"""Prompt 防虚构规则验证测试。"""

import re


class TestPromptAntiFabrication:
    """验证所有 Prompt 模板都包含防虚构规则"""

    def find_anti_fabrication_line(self, prompt_text: str) -> str:
        """找防虚构规则在 prompt 中的行。"""
        lines = prompt_text.split("\n")
        for i, line in enumerate(lines):
            if "数据真实性铁律" in line or "ANTI_FABRICATION" in line.upper():
                return f"Line {i}: {line.strip()}"
        return ""

    @property
    def all_prompts(self):
        """获取 ALL_PROMPTS 映射。"""
        from app.graph.prompts import ALL_PROMPTS
        return ALL_PROMPTS

    def test_all_prompts_have_anti_fabrication_placeholder(self):
        """正常路径：每个 Prompt 模板都包含 {anti_fabrication_rule} 占位符。"""
        missing = []
        for name, prompt in self.all_prompts.items():
            if "{anti_fabrication_rule}" not in prompt:
                missing.append(name)

        assert not missing, (
            f"以下 Prompt 缺少 {{anti_fabrication_rule}} 占位符: {missing}"
        )

    def test_all_prompts_have_stock_code_placeholder(self):
        """正常路径：每个 Prompt 都有 {stock_code} 占位符。"""
        missing = []
        for name, prompt in self.all_prompts.items():
            if "{stock_code}" not in prompt:
                missing.append(name)
        assert not missing, (
            f"以下 Prompt 缺少 {{stock_code}} 占位符: {missing}"
        )

    def test_all_prompts_non_empty(self):
        """边界：每个 Prompt 模板非空。"""
        for name, prompt in self.all_prompts.items():
            assert len(prompt.strip()) > 100, f"Prompt '{name}' 内容不足"

    def test_anti_fabrication_rule_content(self):
        """验证防虚构规则常量的内容完整性。"""
        from app.graph.prompts import ANTI_FABRICATION_RULE
        text = ANTI_FABRICATION_RULE.strip()
        assert len(text) > 50
        assert "数据真实性铁律" in text
        assert "编造" in text
        assert "数据来源" in text
        assert "风险提示" in text
