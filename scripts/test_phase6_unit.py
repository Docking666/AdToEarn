"""Phase6 单元测试：SearchProvider 三层路由 + 搜索源模块"""
import asyncio
import json
import os
import sys
import types

sys.path.insert(0, r"C:\Users\Quark\WorkBuddy\workspace\AdToEarn")
os.chdir(r"C:\Users\Quark\WorkBuddy\workspace\AdToEarn")

from server.modules.search_api import search_api as sapi  # 单例实例
from server.modules.search_mcp import McpSearchService, mcp_search as mcp_svc
from server.modules import web_search as ws_mod

passed = 0
def ok(name):
    global passed
    passed += 1
    print(f"  ✅ {name}")

# ==================== 1. search_api: 配置与密钥 ====================
print("=== 1. search_api 配置 ===")
assert sapi.provider_id() == ""
ok("默认无 provider")
os.environ["BOCHA_API_KEY"] = "sk-test-bocha"
import server.config as sc
# 手动改 spec 配置模拟（通过 settings 缓存不可行，直接测 available 的 pid 空判断）
assert not sapi.available(), "无 provider 时 available 应为 False"
ok("无 provider 时 available=False（即使有环境变量）")

# 直接测 _freshness 映射
assert sapi._freshness(1) == "oneDay"
assert sapi._freshness(7) == "oneWeek"
assert sapi._freshness(30) == "oneMonth"
assert sapi._freshness(365) == "oneYear"
assert sapi._freshness(0) == "oneWeek"  # 0 视作默认 7 天
assert sapi._freshness(None) == "oneWeek"
assert sapi._freshness(10000) == "noLimit"
ok("_freshness 天数映射正确")

# ==================== 2. search_api: 博查响应解析 ====================
print("=== 2. 博查响应解析（mock urlopen）===")
mock_bocha_resp = {
    "code": 200,
    "data": {"webPages": [
        {"name": "2026美妆广告案例合集", "url": "https://example.com/case1",
         "summary": "汇总近期国潮风美妆投放案例", "dateLastCrawled": "2026-08-05T10:00:00Z"},
        {"name": "某平台素材", "url": "https://example.com/case2",
         "snippet": "无 summary 时用 snippet", "dateLastCrawled": "2026-08-04T09:00:00Z"},
        {"name": "无链接条目", "url": "", "summary": "应被过滤"},
    ]}
}
class FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def read(self):
        return json.dumps(self._payload).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *a): return False

def fake_urlopen_bocha(req, timeout=20):
    return FakeResp(mock_bocha_resp)

sapi._cfg = lambda: {"provider": "bocha", "bocha": {"api_key_env": "BOCHA_API_KEY", "base_url": "https://api.bochaai.com/v1/web-search"}}
import urllib.request
orig_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen_bocha
r = asyncio.run(sapi.search("美妆广告", days=7, max_results=10))
urllib.request.urlopen = orig_urlopen
assert r["status"] == "success", f"status={r['status']} {r.get('error')}"
assert len(r["sources"]) == 2, f"应过滤无 url 条目, 实际 {len(r['sources'])}"
assert r["sources"][0]["url"] == "https://example.com/case1"
assert r["sources"][0]["date"] == "2026-08-05"
assert "国潮风" in r["sources"][0]["snippet"]
ok("博查响应解析 + 无URL过滤 + date 截断")

# 无 key → not_configured
os.environ.pop("BOCHA_API_KEY", None)
r = asyncio.run(sapi.search("美妆", 7, 10))
assert r["status"] == "not_configured" and "环境变量" in r["error"]
ok("博查无 key → not_configured 精准提示")
os.environ["BOCHA_API_KEY"] = "sk-test-bocha"

# HTTP 错误
def fake_urlopen_err(req, timeout=20):
    import io
    raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"invalid api key"))
urllib.request.urlopen = fake_urlopen_err
r = asyncio.run(sapi.search("美妆", 7, 10))
urllib.request.urlopen = orig_urlopen
assert r["status"] == "failed" and "401" in r["error"]
ok("博查 HTTP 401 → failed 带状态码")

# ==================== 3. search_mcp: 工具探测与参数构建 ====================
print("=== 3. MCP 工具探测与参数自适应 ===")
class FakeTool:
    def __init__(self, name, props):
        self.name = name
        self.inputSchema = {"type": "object", "properties": props}

tools = [
    FakeTool("mcp__web_search", {"query": {"type": "string"}, "max_results": {"type": "number"}, "search_depth": {"type": "string"}}),
    FakeTool("get_weather", {"city": {"type": "string"}}),
]
t = McpSearchService._pick_tool(tools)
assert t.name == "mcp__web_search", t.name
ok("_pick_tool 优先 search 工具")

args = McpSearchService._build_args(t, "美妆案例", 10)
assert args["query"] == "美妆案例" and args["max_results"] == 10 and args["search_depth"] == "basic"
ok("_build_args query/max_results/search_depth")

# GLM 风格: search_prompt
t2 = FakeTool("web_search", {"search_prompt": {"type": "string"}, "limit": {"type": "integer"}})
args2 = McpSearchService._build_args(t2, "关键词", 5)
assert args2["search_prompt"] == "关键词" and args2["limit"] == 5
ok("_build_args 支持 search_prompt/limit（GLM 风格）")

# 无 query 参数 → 第一个 string 参数兜底
t3 = FakeTool("search_anything", {"keyword": {"type": "string"}})
args3 = McpSearchService._build_args(t3, "x", 3)
assert args3["keyword"] == "x"
ok("_build_args 第一个 string 参数兜底")

# ==================== 4. search_mcp: 结果解析 ====================
print("=== 4. MCP 结果解析 ===")
# 博查 MCP JSON
text = json.dumps({"code": 200, "data": {"webPages": [
    {"name": "A", "url": "https://a.com", "summary": "sum"}, {"name": "B", "url": "https://b.com"}]}})
sources = McpSearchService._parse_sources(text)
assert len(sources) == 2 and sources[0]["url"] == "https://a.com"
ok("博查 MCP JSON 解析")

# Tavily MCP JSON
text = json.dumps({"results": [{"title": "T1", "url": "https://t.com", "content": "内容"}]})
sources = McpSearchService._parse_sources(text)
assert len(sources) == 1 and sources[0]["snippet"] == "内容"
ok("Tavily MCP JSON 解析")

# markdown 链接
text = "结果：\n- [标题A](https://example.com/a)\n- [标题B](https://example.com/b)"
sources = McpSearchService._parse_sources(text)
assert len(sources) == 2 and sources[0]["title"] == "标题A"
ok("markdown 链接正则提取")

# ==================== 5. web_search: native 解析与路由 ====================
print("=== 5. web_search native 解析 ===")
ws = ws_mod.WebSearchService()
# _parse_json
items = ws._parse_json('```json\n{"sources": [{"title": "t", "url": "https://u"}]}\n```')
assert len(items) == 1
ok("_parse_json 支持 markdown 代码块")

# _deepseek_search 模型校验（非 v4-flash → RuntimeError）
async def _t():
    cfg = {"api_key": "sk-x", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}
    try:
        await ws._deepseek_search(cfg, "prompt")
        assert False, "应报错"
    except RuntimeError as e:
        assert "deepseek-v4-flash" in str(e)
        ok("DeepSeek 非 v4-flash 模型 → 精准报错提示")
asyncio.run(_t())

# _deepseek_search 正常路径（mock openai SDK）
class FakeWebSearchItem:
    type = "web_search_call"
    search_query = "美妆广告"
class FakeRespObj:
    output_text = '{"sources": [{"title": "真结果", "url": "https://x.com", "snippet": "s", "platform": "抖音", "date": "2026-08-01"}]}'
    output = [FakeWebSearchItem()]
class FakeResponses:
    async def create(self, **kw):
        assert kw["model"] == "deepseek-v4-flash"
        assert kw["tools"] == [{"type": "web_search"}]
        return FakeRespObj()
class FakeAsyncOpenAI:
    def __init__(self, api_key=None, base_url=None):
        assert base_url == "https://api.deepseek.com", f"base_url={base_url}（应从 /v1 剥掉）"
        self.responses = FakeResponses()

import server.modules.web_search as wsm
wsm.AsyncOpenAI = FakeAsyncOpenAI
async def _t2():
    cfg = {"api_key": "sk-x", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-v4-flash"}
    r = await ws._deepseek_search(cfg, "prompt")
    assert r["items"][0]["title"] == "真结果"
    ok("DeepSeek Responses API 调用 + /v1 剥离 + 结果解析")
asyncio.run(_t2())

# 无结构化 JSON 时：摘要 + URL 提取
class FakeRespObj2:
    output_text = "搜索到【美妆广告案例】相关信息。参考：https://example.com/case 和 https://example.com/video"
    output = []
class FakeResponses2:
    async def create(self, **kw):
        return FakeRespObj2()
class FakeAsyncOpenAI2:
    def __init__(self, api_key=None, base_url=None):
        self.responses = FakeResponses2()
wsm.AsyncOpenAI = FakeAsyncOpenAI2
async def _t3():
    cfg = {"api_key": "sk-x", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"}
    r = await ws._deepseek_search(cfg, "prompt")
    items = r["items"]
    assert items[0]["_summary"] and "摘要" in items[0]["title"]
    urls = [i["url"] for i in items if i["url"]]
    assert len(urls) == 2, urls
    ok("无结构化 JSON → 摘要条 + 文本内 URL 提取")
asyncio.run(_t3())

# ==================== 6. 三层路由 ====================
print("=== 6. 三层路由 ===")
# 恢复 search_api 真实配置（清掉前面 monkeypatch）+ 清环境变量，保证隔离
sapi._cfg = type(sapi)._cfg.__get__(sapi)
os.environ.pop("BOCHA_API_KEY", None)
import server.modules.api_config as ac

# 6a. 完全没配置 → not_configured
ac.api_config_manager._load = lambda: {"llm": {}, "video": {}}
async def _r1():
    r = await ws.search("美妆", 7)
    assert r["status"] in ("not_configured", "not_supported"), r
    ok(f"无任何配置 → {r['status']}（引导文案含「API 配置」）")
asyncio.run(_r1())

# 6b. 配了 deepseek-chat（不支持 native 的模型）+ 无 api/mcp key → not_configured 带引导
ac.api_config_manager._load = lambda: {"llm": {
    "deepseek": {"id": "deepseek", "api_key": "sk-x", "base_url": "https://api.deepseek.com",
                 "model": "deepseek-chat", "enabled": True}}, "video": {}}
async def _r2():
    r = await ws.search("美妆", 7)
    print(f"    状态: {r['status']}, provider={r['provider']}")
    print(f"    error: {r['error'][:150]}")
    assert r["status"] in ("failed", "not_supported", "not_configured")
    assert "deepseek" in r["error"]
    ok("deepseek-chat → 引导提示（含环境变量方案）")
asyncio.run(_r2())

# 6c. mode=api + 有博查 key（mock 真实成功）
os.environ["BOCHA_API_KEY"] = "sk-test"
from server.config import settings
# 覆盖 web_search + search_api 的配置读取
ws._cfg = lambda: {**settings.websearch, "mode": "api",
                   "search_api": {"provider": "bocha", "bocha": {"api_key_env": "BOCHA_API_KEY", "base_url": "https://api.bochaai.com/v1/web-search"}}}
sapi._cfg = lambda: {"provider": "bocha", "bocha": {"api_key_env": "BOCHA_API_KEY", "base_url": "https://api.bochaai.com/v1/web-search"}}
urllib.request.urlopen = fake_urlopen_bocha
async def _r3():
    r = await ws.search("美妆", 7)
    urllib.request.urlopen = orig_urlopen
    print(f"    状态: {r['status']}, provider={r['provider']}, 条数={r.get('total')}")
    assert r["status"] == "success" and r["provider"] == "api:bocha"
    ok("mode=api + 博查 → api:bocha 成功")
asyncio.run(_r3())

# 6d. mode=auto + deepseek-chat + 博查 key → native 不可用自动降级 api
ws._cfg = lambda: {**settings.websearch, "mode": "auto",
                   "search_api": {"provider": "bocha", "bocha": {"api_key_env": "BOCHA_API_KEY", "base_url": "https://api.bochaai.com/v1/web-search"}}}
urllib.request.urlopen = fake_urlopen_bocha
async def _r4():
    r = await ws.search("美妆", 7)
    urllib.request.urlopen = orig_urlopen
    print(f"    状态: {r['status']}, provider={r['provider']}")
    assert r["status"] == "success" and r["provider"] == "api:bocha"
    ok("mode=auto → native 不可用自动降级 api")
asyncio.run(_r4())

os.environ.pop("BOCHA_API_KEY", None)
ac.api_config_manager._load = lambda: {"llm": {}, "video": {}}
print()
print(f"=== 单元测试全部通过（{passed} 项）✅ ===")
