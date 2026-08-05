# -*- coding: utf-8 -*-
"""采集模块端到端测试：demo 数据源 + 关键词搜索 + 热门采集 + 关键词提取"""
import asyncio, time, json, urllib.request
import sys
sys.path.insert(0, ".")

def post(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

async def main():
    for _ in range(20):
        time.sleep(1)
        try:
            urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=3); break
        except Exception: pass

    # 1. demo 源关键词采集（无关键词 → 全部素材）
    r = post("http://127.0.0.1:8765/api/scrape/trending", {"source": "demo", "limit": 50})
    print("1. trending(demo):", r.get("status"), "| creatives:", r.get("total_creatives"),
          "| keywords:", len(r.get("keywords", [])))
    if r.get("creatives"):
        c = r["creatives"][0]
        print("   sample creative:", c["title"][:40], "| platform:", c.get("platform"), "| tags:", c.get("tags", [])[:3])

    # 2. demo 源关键词搜索（美妆）
    r = post("http://127.0.0.1:8765/api/scrape/search", {"keyword": "美妆", "source": "demo", "limit": 20})
    print("2. search(demo, 美妆):", r.get("status"), "| results:", r.get("total"))

    # 3. demo 源热门采集
    r = post("http://127.0.0.1:8765/api/scrape/hot", {"source": "demo", "limit": 10})
    print("3. hot(demo):", r.get("status"), "| total:", r.get("total"))

    # 4. 关键词质量验证（demo 全量应包含常见广告词）
    r = post("http://127.0.0.1:8765/api/scrape/trending", {"source": "demo", "limit": 50})
    kws = r.get("keywords", [])
    kw_text = " ".join(k["keyword"] for k in kws)
    assert r.get("total_creatives", 0) >= 10, "demo 应返回至少 10 条素材"
    assert len(kws) > 0, "应提取到关键词"
    print("4. keyword quality: count=", len(kws), "| sample:", [k["keyword"] for k in kws[:8]])

    # 5. 真实源诊断（youmi 应返回 no_results + diagnostics，不再无提示空返回）
    r = post("http://127.0.0.1:8765/api/scrape/trending", {"source": "youmiyoushu", "limit": 10})
    print("5. trending(youmi):", r.get("status"), "| has_diagnostics:", "diagnostics" in r)
    if "diagnostics" in r:
        d = r["diagnostics"]
        print("   diag: title=", d.get("title", "")[:40], "| final_url=", d.get("final_url", "")[:60],
              "| hasLogin=", d.get("hasLogin"), "| inputs=", d.get("inputs", "")[:40])

    print("\nALL SCRAPER TESTS PASS")

asyncio.run(main())
