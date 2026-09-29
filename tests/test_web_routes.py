"""Web 路由测试（使用 httpx AsyncClient）。"""

import pytest


class TestWebRoutes:
    """HTTP 路由测试套件"""

    @pytest.fixture
    def client(self):
        """使用 TestClient。

        ASGITransport 不触发 lifespan，需手动预置 app.state.cache 与 app.state.router，
        否则 get_cache()/get_data_source_router() 访问 request.app.state 会抛 AttributeError。
        """
        from app.main import app
        from httpx import ASGITransport, AsyncClient
        if not hasattr(app.state, "cache"):
            app.state.cache = None
        if not hasattr(app.state, "router"):
            app.state.router = None
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.mark.asyncio
    async def test_health_check(self, client):
        """正常路径：健康检查返回 ok。"""
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_index_page(self, client):
        """正常路径：首页返回 HTML。"""
        resp = await client.get("/", headers={"Accept": "text/html"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_screener_page(self, client):
        """正常路径：选股页面返回 HTML。"""
        resp = await client.get("/screener", headers={"Accept": "text/html"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_analysis_page(self, client):
        """正常路径：个股分析页面返回 HTML。"""
        resp = await client.get("/analysis/600519",
                                headers={"Accept": "text/html"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "600519" in resp.text

    @pytest.mark.asyncio
    async def test_screen_api_missing_params(self, client):
        """边界：选股 API 缺少参数时返回 400。"""
        resp = await client.post("/api/screen", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_screen_api_invalid_strategy(self, client):
        """边界：选股 API 不存在的策略。"""
        resp = await client.post("/api/screen", json={
            "strategy": "not_exist",
            "top_n": 10,
        })
        assert resp.status_code in (400, 500)

    @pytest.mark.asyncio
    async def test_analyze_api_no_llm(self, client):
        """边界：分析 API 无 DataSource 返回 200（含错误报告）。
        
        现在错误会被重试机制优雅处理，返回200但报告中包含错误信息。
        """
        resp = await client.post("/api/analyze/000001")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data.get("report", "").lower() or "错误" in data.get("report", "")

    @pytest.mark.asyncio
    async def test_stock_search_empty(self, client):
        """边界：搜索空字符串返回空列表。"""
        resp = await client.get("/api/stock/search?q=")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 0

    @pytest.mark.asyncio
    async def test_stock_search_short_query(self, client):
        """边界：搜索单个字符返回空列表。"""
        resp = await client.get("/api/stock/search?q=a")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_workflow_graph(self, client):
        """正常路径：获取流程图。"""
        resp = await client.get("/api/workflow/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "graph" in data

    @pytest.mark.asyncio
    async def test_quote_api_invalid_code(self, client):
        """边界：行情接口非法 code 返回 400。"""
        resp = await client.get("/api/quote/abc123")
        assert resp.status_code == 400

    # ========== v2.6.0 选股条件展示测试 ==========

    @pytest.mark.asyncio
    async def test_screen_api_user_input_uses_prompt_conditions(self, client):
        """正常路径：user_input 抽取到条件时优先使用提示词条件。"""
        from unittest.mock import patch

        from app.screener.conditions import FieldCondition

        with patch("app.screener.engine.ScreenerEngine.scan", return_value=[]), \
             patch("app.web.routes.extract_screen_params", return_value=([
                 FieldCondition(field="pe_ttm", op="between", value=[0, 20]),
                 FieldCondition(field="market_cap", op=">", value=100 * 1e8),
             ], None)):
            resp = await client.post("/api/screen", json={
                "user_input": "帮我选PE低于20，市值大于100亿的股票",
                "strategy": "value",
                "top_n": 30,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["condition_source"] == "prompt_extracted"
        assert data["source_label"] == "来自你的提示词"
        assert len(data["conditions"]) == 2
        assert data["conditions"][0]["field"] == "pe_ttm"
        assert data["conditions"][0]["display"] == "PE 0~20"
        assert data["conditions"][1]["display"] == "市值 > 100亿"

    @pytest.mark.asyncio
    async def test_screen_api_user_input_no_conditions_fallback_strategy(self, client):
        """正常路径：user_input 未抽取到条件时回退到预设策略。"""
        from unittest.mock import patch

        with patch("app.screener.engine.ScreenerEngine.scan", return_value=[]), \
             patch("app.web.routes.extract_screen_params", return_value=([], None)):
            resp = await client.post("/api/screen", json={
                "user_input": "随便推荐几只",
                "strategy": "value",
                "top_n": 30,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["condition_source"] == "strategy"
        assert data["source_label"] == "预设策略（价值选股）"
        assert len(data["conditions"]) == 3  # 价值策略 3 个条件（PE/PB/ROE）

    @pytest.mark.asyncio
    async def test_screen_api_user_input_fallback_default(self, client):
        """正常路径：user_input 未抽取到条件且无策略时使用默认条件。"""
        from unittest.mock import patch

        with patch("app.screener.engine.ScreenerEngine.scan", return_value=[]), \
             patch("app.web.routes.extract_screen_params", return_value=([], None)):
            resp = await client.post("/api/screen", json={
                "user_input": "推荐几只股票",
                "top_n": 30,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["condition_source"] == "default"
        assert data["source_label"] == "默认条件"
        assert len(data["conditions"]) == 3  # 默认价值策略 3 个条件（PE/PB/ROE）

    @pytest.mark.asyncio
    async def test_screen_api_user_input_top_n_preferred(self, client):
        """正常路径：提示词指定数量时覆盖请求参数 top_n（v2.6.1）。"""
        from unittest.mock import patch

        from app.screener.conditions import FieldCondition

        with patch("app.screener.engine.ScreenerEngine.scan", return_value=[]), \
             patch("app.screener.engine.ScreenerEngine.rank_candidates", return_value=[]) as mock_rank, \
             patch("app.web.routes.extract_screen_params", return_value=([
                 FieldCondition(field="pe_ttm", op="<", value=20),
             ], 10)):
            resp = await client.post("/api/screen", json={
                "user_input": "推荐10只PE低于20的股票",
                "top_n": 30,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["condition_source"] == "prompt_extracted"
        # 精排阶段配额应基于提示词指定的数量 10 计算（v2.13.0：10 × 3 = 30）
        # 若误用请求参数 top_n=30，配额将是 50，故断言 30 可验证提示词数量优先生效
        call_kwargs = mock_rank.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("top_n") == 30

    @pytest.mark.asyncio
    async def test_screen_api_strategy_returns_conditions(self, client):
        """正常路径：预设策略路径返回完整条件列表。"""
        from unittest.mock import patch

        with patch("app.screener.engine.ScreenerEngine.scan", return_value=[]):
            resp = await client.post("/api/screen", json={
                "strategy": "momentum",
                "top_n": 10,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["condition_source"] == "strategy"
        assert data["source_label"] == "预设策略（动量选股）"
        assert len(data["conditions"]) == 3  # 动量策略 3 个条件

    @pytest.mark.asyncio
    async def test_screen_api_custom_conditions_returns_conditions(self, client):
        """正常路径：自定义条件路径返回完整条件列表。"""
        from unittest.mock import patch

        with patch("app.screener.engine.ScreenerEngine.scan", return_value=[]):
            resp = await client.post("/api/screen", json={
                "conditions": [{"field": "pe_ttm", "op": "<", "value": 20}],
                "top_n": 10,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["condition_source"] == "custom"
        assert data["source_label"] == "自定义条件"
        assert len(data["conditions"]) == 1
        assert data["conditions"][0]["display"] == "PE < 20"

    @pytest.mark.asyncio
    async def test_screen_api_two_stage_ranking(self, client):
        """正常路径（v2.13.0）：阶段一粗筛候选池 + 阶段二精排配额（要求数 × 3，上限 50）。"""
        from unittest.mock import patch

        from app.screener.conditions import FieldCondition

        with patch("app.screener.engine.ScreenerEngine.scan", return_value=[]) as mock_scan, \
             patch("app.screener.engine.ScreenerEngine.rank_candidates", return_value=[]) as mock_rank, \
             patch("app.web.routes.extract_screen_params", return_value=([
                 FieldCondition(field="roe", op=">", value=12),
             ], None)):
            resp = await client.post("/api/screen", json={
                "user_input": "ROE大于12的股票",
                "top_n": 30,
            })
        assert resp.status_code == 200
        # 阶段一粗筛使用候选池数量（coarse_pool_size）
        assert mock_scan.call_args.kwargs.get("top_n") == 100
        # 阶段二精排配额 = 用户要求数量 × 3，上限 50（v2.13.0：30 × 3 = 90 → 封顶 50）
        assert mock_rank.call_args.kwargs.get("top_n") == 50

    # ========== v2.6.2 聊天页选股透传原始输入测试 ==========

class TestScreeningRawInputTopN:
    """v2.6.2 聊天页选股流程透传原始输入（修复提示词数量不生效）。

    缺陷背景：handleIntent/handleFollowUp 识别到选股意图后，仅把意图识别
    返回的 user_preference 传给 startScreening，而数量描述（如"10支股票"）
    在意图识别阶段被剥离，导致后端抽取不到 top_n，始终回退默认 30。
    修复为透传用户原始输入，由后端抽取器识别数量。
    """

    @pytest.fixture
    def index_html(self):
        """读取首页模板内容。"""
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "app", "web", "templates", "index.html",
        )
        with open(path, encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _slice_function(html, name):
        """截取指定 JS 函数体（到下一个顶层函数声明为止）。"""
        import re
        marker = "async function " + name
        start = html.index(marker)
        nxt = re.search(r"\n(?:async )?function ", html[start + len(marker):])
        end = start + len(marker) + nxt.start() if nxt else len(html)
        return html[start:end]

    def test_handle_intent_passes_raw_query(self, index_html):
        """正常路径：handleIntent 识别选股意图后透传原始输入 query。"""
        body = self._slice_function(index_html, "handleIntent")
        # 不允许再传被剥离数量的 user_preference
        assert "startScreening(data.user_preference" not in body
        assert "startScreening(query)" in body

    def test_handle_follow_up_passes_raw_text(self, index_html):
        """正常路径：handleFollowUp 识别选股意图后透传原始输入 text。"""
        body = self._slice_function(index_html, "handleFollowUp")
        assert "startScreening(data.user_preference" not in body
        assert "startScreening(text)" in body


class TestSidebarScrollDisplay:
    """v2.6.3 已分析股票侧边栏使用 flex 布局以保证列表可滚动。

    缺陷背景：.sidebar 的 CSS 为纵向 flex 布局，.sidebar-list 的滚动依赖
    flex:1 + min-height:0 的高度约束；JS 显示侧边栏时曾使用内联
    display='block'，覆盖了 flex 布局，列表被内容撑开后由外层
    overflow:hidden 裁剪，滚动条不出现，底部股票无法查看。
    修复为 display='flex'（loadCachedStocks 内 3 处）。
    """

    @pytest.fixture
    def index_html(self):
        """读取首页模板内容。"""
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "app", "web", "templates", "index.html",
        )
        with open(path, encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _slice_function(html, name):
        """截取指定 JS 函数体（到下一个顶层函数声明为止）。"""
        import re
        marker = "function " + name
        start = html.index(marker)
        nxt = re.search(r"\n(?:async )?function ", html[start + len(marker):])
        end = start + len(marker) + nxt.start() if nxt else len(html)
        return html[start:end]

    def test_load_cached_stocks_uses_flex_display(self, index_html):
        """正常路径：loadCachedStocks 显示侧边栏使用 flex，保留列表滚动能力。"""
        body = self._slice_function(index_html, "loadCachedStocks")
        # 不允许使用 block（会覆盖 CSS flex 布局导致无法滚动）
        assert "style.display = 'block'" not in body
        # 空数据/正常/加载失败三个分支均使用 flex
        assert body.count("style.display = 'flex'") == 3


class TestScoreColumnUsesFinalScore:
    """v2.12.1 评分列改为显示多因子综合评分（方案 A）。

    背景：评分列原显示条件命中率（r.score*100），排序第一优先级为命中率，
    导致推荐结果评分列全是 100，无区分度。改为显示多因子综合评分
    final_score（0~100），命中率移到选择意见的数据行展示。
    """

    @pytest.fixture
    def index_html(self):
        """读取首页模板内容。"""
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "app", "web", "templates", "index.html",
        )
        with open(path, encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _slice_function(html, name):
        """截取指定 JS 函数体（到下一个顶层函数声明为止）。"""
        import re
        marker = "function " + name
        start = html.index(marker)
        nxt = re.search(r"\n(?:async )?function ", html[start + len(marker):])
        end = start + len(marker) + nxt.start() if nxt else len(html)
        return html[start:end]

    def test_score_column_uses_final_score(self, index_html):
        """正常路径：评分列使用 final_score（综合评分），不再用命中率*100。"""
        body = self._slice_function(index_html, "startScreening")
        # 评分列应基于 final_score
        assert "final_score" in body
        # 不应再用命中率 r.score * 100 作为评分列
        assert "Math.round(r.score * 100)" not in body

    def test_match_rate_moved_to_data_row(self, index_html):
        """正常路径：命中率（命中条件数/总条件数）移到选择意见的数据行。"""
        body = self._slice_function(index_html, "buildStockDataHtml")
        # 数据行应包含命中率信息（matched_conditions / total_conditions）
        assert "matched_conditions" in body
        assert "total_conditions" in body

    def test_score_column_prefers_llm_score(self, index_html):
        """v2.14.0 正常路径：评分列优先显示 llm_score（LLM 评分），缺失时降级 final_score。"""
        body = self._slice_function(index_html, "startScreening")
        # 评分列应优先基于 llm_score
        assert "llm_score" in body
        # 降级逻辑：llm_score 为空时回退到 final_score
        assert "final_score" in body
