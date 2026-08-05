# AdToEarn — 采集方案评估：传统爬虫 vs LLM + Web Search

> 版本：v1.0 | 日期：2026-08-05 | 状态：待决策
> 背景：有米有数 / AppGrowing 等目标平台存在登录墙 + 反爬（频率限制、验证码、IP 封禁），传统 Playwright 爬虫在无凭据环境下无法稳定抓取。

---

## 1. 可行性：LLM + Web Search 的优势与局限

### 1.1 核心优势（绕过反爬的本质）

| 维度 | 传统爬虫（Playwright） | LLM + Web Search |
|------|----------------------|------------------|
| **反爬接触面** | 直接访问目标站点，直面验证码/IP 封禁/频率限制 | **用户不直接访问目标站点**，由搜索引擎（Bing/Google）的抓取基础设施完成检索，天然规避目标站反爬 |
| **登录墙** | 需账号凭据 + 验证码处理 | 搜索结果中已公开索引的页面可直接获取（登录墙后的内容搜索引擎一般不索引，这是局限） |
| **IP 封禁** | 高频率触发封禁，需代理池 | 请求发往搜索 API（OpenAI/Anthropic），与目标站点无直接关系 |
| **页面渲染** | SPA/JS 渲染需无头浏览器，脆弱 | 搜索服务已处理渲染与解析，返回语义化结果 |
| **维护成本** | 站点改版→选择器全废 | 无选择器依赖，改版不影响 |

### 1.2 局限（必须正视）

| 局限 | 说明 | 影响程度 |
|------|------|---------|
| **时效性** | 依赖搜索引擎索引速度，通常滞后 24h~7d；`gpt-4o-search-preview` 基于 Bing 索引 | 中：热门素材数据对时效敏感 |
| **覆盖范围** | 仅能获取**公开索引**的页面；登录墙后、JS 动态生成未收录的内容拿不到 | 高：这正是目标平台（有米有数）的核心壁垒 |
| **结构化程度** | 返回的是自然语言摘要 + 来源 URL 列表，不是结构化素材卡片 | 中：需二次解析 |
| **内容授权** | 平台"反爬"法律边界 → 搜索摘要属于合理引用，但大量结构化抓取仍需授权 | 中 |
| **仍受反爬影响** | 搜索 API 本身有 rate limit，但远宽松于目标站点 | 低 |

### 1.3 结论（可行性）

**部分可行**：LLM + Web Search 能有效规避目标站点的登录墙与反爬，适合获取**公开的、已索引的**行业趋势、竞品素材描述、文案灵感等"宽泛情报"；但**无法替代**有米有数这类付费数据库的结构化素材数据（其核心数据在登录墙后，搜索引擎不索引）。

---

## 2. 条件限定：时间范围 / 域名 / 结果数量

### 2.1 各提供商能力对照（实测调研结果）

| 能力 | OpenAI (Responses API) | Anthropic Claude | Google Gemini |
|------|------------------------|------------------|---------------|
| **工具激活** | `tools: [{type:"web_search"}]` | `tools: [{type:"web_search_20250305"}]` | `googleSearch` grounding |
| **域名白名单** | ✅ `filters.allowed_domains`（≤20 域，含子域） | ✅ `allowed_domains` 域控制 | ❌ Vertex 不支持 |
| **时间范围** | ❌ 无原生参数 → **提示词限定** | ⚠️ 动态过滤（web_search_20260209+，Agent 化裁剪） | ❌ → 提示词限定 |
| **结果数量** | ⚠️ `search_context_size`（low/medium/high）控上下文量 | ✅ `max_uses` 限搜索次数 | ⚠️ 受上下文限制 |
| **来源返回** | ✅ `include:["web_search_call.action.sources"]` 返回 URL+标题 | ✅ 引文带来源 | ✅ groundingChunks 细粒度引用 |
| **搜索费用** | ~$10/1K 次搜索 + token | ~$10/1K 次搜索 | $14~35/1K 查询（免费层 500-1500/天） |

### 2.2 时间范围限定的具体实现（无原生参数时的标准做法）

统一通过**提示词约束**实现（各平台通用）：

```
请搜索近 7 天内（2026-07-29 至 2026-08-05）发布的广告素材情报，仅关注：
- 电商 / 美妆行业
- 只返回搜索结果，不要生成创意
输出格式：{sources: [{title, url, snippet, date}], summary}
```

高级技巧：
- **显式日期区间**：写死起止日期（`近一周 = 今天-7天`，代码动态计算），比"最近"更精准
- **关键词加时间词**：`2026年 美妆广告 案例`、`fresh ads July 2026`
- **排序暗示**：`最新发布的` / `recently published`
- **Anthropic 动态过滤**（web_search_20260209+）：Claude 可写代码先过滤搜索结果再入上下文，天然支持时间/相关性裁剪

### 2.3 域名限定示例（OpenAI）

```python
response = client.responses.create(
    model="gpt-5",
    tools=[{
        "type": "web_search",
        "filters": {"allowed_domains": ["adspy.com", "appgrowing.cn"]},
    }],
    include=["web_search_call.action.sources"],
    input="近一周美妆行业高点击率广告素材，输出标题/平台/要点",
)
```

---

## 3. 数据质量：准确性、结构化与二次清洗

### 3.1 质量特征

| 特征 | 表现 | 处理方式 |
|------|------|---------|
| **准确性** | 摘要由 LLM 基于多来源生成，含幻觉风险 | 要求 `sources` 引用 + 断言校验 |
| **结构化** | 输出为自然语言，非表格/卡片 | 用**结构化输出约束**（JSON schema / 强制 JSON）二次解析 |
| **来源可溯** | OpenAI `sources` / Anthropic 引文 / Gemini groundingChunks 均给 URL | 保留 URL 用于人工复核 |
| **覆盖偏差** | 搜索引擎索引 ≠ 平台全量素材 | 声明为"情报参考"而非"完整数据" |

### 3.2 推荐清洗管线

```
LLM Web Search 原始输出
   ↓ JSON 强制输出（title/url/snippet/date/summary）
   ↓ 规则清洗（去重 URL、过滤非目标域名、日期范围校验）
   ↓ 结构化入库（对齐现有 creative 数据模型：标题/描述/平台/标签/来源URL）
   ↓ 关键词提取（复用现有 _extract_keywords_from_creatives）
```

---

## 4. 成本与性能

### 4.1 单次"关键词→素材情报"的成本测算

假设：1 次搜索（medium 上下文，约 8K 输入 token）+ 1 次结构化输出（约 1.5K token）

| 项 | 量 | OpenAI GPT-5 计价（估） |
|----|----|---------------------|
| Web Search 工具调用 | 1 次 | ~$0.01 |
| 输入 token（提示词+搜索结果） | ~8K | ~$0.015 |
| 输出 token（结构化结果） | ~1.5K | ~$0.004 |
| **单次合计** | | **≈ $0.03（¥0.22）** |

> 采集 50 个关键词 ≈ $1.5/批；对比：目标平台会员费（有米有数年费数千元）仍有显著成本优势，但精度不可比。

### 4.2 性能特征

| 维度 | 表现 | 应对 |
|------|------|------|
| **响应速度** | 单次搜索 3~10s（含检索+生成） | 异步任务 + 轮询（复用视频任务模式） |
| **并发** | 搜索 API 有 rate limit（OpenAI 默认 ~500 RPM） | 信号量并发控制（如同时 3~5 个请求） |
| **Token 波动** | search_context_size 决定输入量 | 默认 medium，批量采集用 low |

---

## 5. 建议方案

### 5.1 结论：值得切换，但需双轨并行

| 方案 | 适用场景 | 决策 |
|------|---------|------|
| **LLM + Web Search（新增主通道）** | 行业趋势 / 竞品情报 / 文案灵感 / 公开素材搜索 | ✅ **新增**，替代 demo 源作为可验证数据通道 |
| **Playwright 爬虫（保留降级）** | 用户已提供账号凭据 / 目标平台公开页面 | 🔁 保留，作为可选源 |
| **付费 API 接入（远期）** | 需完整素材库数据（有米有数开放 API 等） | 📌 建议后续评估 |

**核心理由**：当前无凭据环境下，LLM Web Search 是唯一能"稳定返回真实、可溯源数据"的通道；成本可控（¥0.2/次），且天然规避反爬。

### 5.2 落地实现要点

1. **LLM API 选择**
   - 首选 **OpenAI Responses API + web_search**（`gpt-4o`/`gpt-5`）：域名过滤、sources 返回、生态成熟
   - 备选 **Anthropic Claude + web_search_20260209**：动态过滤省 token、引文不计费
   - Gemini 需 Google 搜索组件合规展示，暂缓
   - 兼容性：现有 LiteLLM 网关若透传 web_search 工具失败，则**按 provider 直连**（新建 `websearch_provider.py` 适配层，各 provider 独立实现）

2. **搜索工具配置（spec.yaml 新增）**
```yaml
websearch:
  provider: "openai"          # openai | anthropic
  default_tool: "web_search"
  search_context_size: "medium"
  allowed_domains: []          # 空=不限，可配 appgrowing.cn 等
  max_results: 10
  date_range_days: 7           # 提示词注入"近 N 天"
  concurrency: 3               # 并发上限
  cost_guard: {max_queries_per_min: 20, daily_budget_usd: 5}
```

3. **错误处理与降级**
   - 搜索 API 限流（429）→ 指数退避重试（复用 scraper 重试机制）
   - 结果为空 / 解析失败 → 返回 `no_results` + 诊断（复用现有结构）
   - 无 LLM 密钥 → 明确报错引导（与 v3.2 的 not_configured 一致）
   - 时间范围失效（搜索无日期字段）→ 保留 snippet 由 LLM 二次判断

4. **与现有架构融合**
   - 新增 `server/modules/web_search.py`，暴露 `search_creatives(keyword, days, domains)` 异步接口
   - `main.py` 新增 `POST /api/scrape/websearch`；前端数据采集页增加「联网搜索（LLM）」Tab
   - 结果复用现有 creative 数据结构 + 悬浮日志埋点

---

## 6. 验收标准（如切换）

- [ ] 输入关键词 + 时间范围 → 返回 ≥5 条带来源 URL 的真实搜索情报
- [ ] 域名白名单生效（只返回指定域名）
- [ ] 时间范围通过提示词正确限定（抽样人工核验）
- [ ] 429 限流自动重试，无 LLM 密钥时明确报错
- [ ] 单次采集成本 < $0.05，单日预算可配置熔断
- [ ] 悬浮日志可见搜索调用（查询词/耗时/来源数）
