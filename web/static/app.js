/**
 * AdToEarn WebUI v3 - Vue 3 应用逻辑
 * 双域 API 配置（LLM + 视频）+ 统一素材生成（创意方案/视频）
 */

const { createApp, ref, reactive, computed, watch, onMounted } = Vue;

createApp({
  setup() {
    // ===== 导航 =====
    const navItems = [
      { id: "dashboard", label: "工作台", icon: "M3 12l9-9 9 9M5 10v10h5v-6h4v6h5V10" },
      { id: "scraper", label: "数据采集", icon: "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM8 10h8" },
      { id: "parser", label: "素材解析", icon: "M15 10l4.55-2.2a1 1 0 011.45.9v6.6a1 1 0 01-1.45.9L15 14M5 4h10a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z" },
      { id: "generator", label: "素材生成", icon: "M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6l2.1 2.1m0-12.8l-2.1 2.1M7.7 16.3l-2.1 2.1M12 8a4 4 0 100 8 4 4 0 000-8z" },
      { id: "workflow", label: "一键工作流", icon: "M4 4v5h5M20 20v-5h-5M4 12a8 8 0 0114-5M20 12a8 8 0 01-14 5" },
      { id: "apiconfig", label: "API 配置", icon: "M12 15a3 3 0 100-6 3 3 0 000 6zm7-3a7 7 0 11-14 0 7 7 0 0114 0z" },
    ];
    const currentPage = ref("dashboard");
    const switchPage = (id) => { currentPage.value = id; };

    // ===== 健康状态 =====
    const health = reactive({ ai_configured: false });
    fetch("/health").then(r => r.json()).then(d => { health.ai_configured = d.ai_configured; }).catch(() => {});

    // ===== 工作台统计 =====
    const stats = reactive({ keywords: 0, analyzed: 0, generated: 0 });

    // ===== 数据源 & 风格 =====
    const sources = ref({});
    const styles = ref([]);
    fetch("/api/sources").then(r => r.json()).then(d => { sources.value = d.sources; }).catch(() => {});
    fetch("/api/styles").then(r => r.json()).then(d => { styles.value = d.styles; }).catch(() => {});

    // ===== 提供商模板 =====
    const llmProviders = ref([]);
    const videoProviders = ref([]);
    const savedConfigs = ref({ llm: {}, video: {} });
    loadProviders();
    loadApiConfigs();

    async function loadProviders() {
      try {
        const [llm, vid] = await Promise.all([
          fetch("/api/apiconfig/providers?domain=llm").then(r => r.json()),
          fetch("/api/apiconfig/providers?domain=video").then(r => r.json()),
        ]);
        llmProviders.value = llm.providers || [];
        videoProviders.value = vid.providers || [];
      } catch (e) {}
    }

    async function loadApiConfigs() {
      try {
        savedConfigs.value = await (await fetch("/api/apiconfig")).json();
      } catch (e) { savedConfigs.value = { llm: {}, video: {} }; }
    }

    const configuredLlmCount = computed(() => Object.keys(savedConfigs.value.llm || {}).length);
    const configuredVideoCount = computed(() => Object.keys(savedConfigs.value.video || {}).length);
    const configuredProviderCount = computed(() => configuredLlmCount.value + configuredVideoCount.value);

    // 当前 LLM 是否支持视觉（用于解析页引导）
    const healthLLMVision = computed(() => {
      if (!configuredLlmCount.value) return false;
      const active = savedConfigs.value.llm || {};
      // 任一启用配置支持视觉即认为可用（保守判断：优先检查 supports_vision 为 true 的）
      return Object.values(active).some(c => c.supports_vision);
    });

    // ===== API 配置页（双域） =====
    const configDomain = ref("llm");
    const configForm = reactive({
      provider: "", api_key: "", endpoint: "", model: "", vision_model: "",
      base_url: "", litellm_prefix: "", supports_vision: true, multimodal_models: [],
    });
    const editingProvider = ref(null);
    const testing = ref(false);
    const saving = ref(false);
    const testResult = ref(null);

    const activeProviders = computed(() => configDomain.value === "llm" ? llmProviders.value : videoProviders.value);
    const activeConfigs = computed(() => savedConfigs.value[configDomain.value] || {});
    const activeProviderMeta = computed(() => activeProviders.value.find(p => p.id === configForm.provider) || null);
    const editingProviderName = computed(() => activeProviders.value.find(x => x.id === editingProvider.value)?.name || "");
    const savedKeyHint = computed(() => {
      const cfg = activeConfigs.value[configForm.provider];
      return cfg?.api_key_masked ? `已保存密钥 ${cfg.api_key_masked}（留空保持不变）` : "";
    });

    function onProviderSelect() {
      const meta = activeProviderMeta.value;
      if (meta) {
        if (configDomain.value === "llm") {
          // 切换提供商时自动应用官方配置（URL 来自 spec.default_base_url）
          configForm.base_url = meta.default_base_url || "";
          configForm.litellm_prefix = meta.litellm_prefix || "openai";
          configForm.model = meta.default_model || "";
          configForm.vision_model = meta.vision_default_model || "";
          configForm.supports_vision = !!meta.supports_vision;
          configForm.multimodal_models = meta.multimodal_models || [];
        } else {
          configForm.endpoint = meta.default_endpoint || "";
          configForm.model = meta.default_model || "";
        }
      }
      testResult.value = null;
    }

    function editConfig(pid) {
      const cfg = activeConfigs.value[pid];
      if (!cfg) return;
      editingProvider.value = pid;
      configForm.provider = pid;
      configForm.api_key = "";
      if (configDomain.value === "llm") {
        configForm.base_url = cfg.base_url || "";
        configForm.litellm_prefix = cfg.litellm_prefix || "";
        configForm.model = cfg.model || "";
        configForm.vision_model = cfg.vision_model || "";
        configForm.supports_vision = !!cfg.supports_vision;
        configForm.endpoint = "";
        // 补充多模态模型列表（用于 UI 提示）
        const meta = activeProviders.value.find(p => p.id === pid);
        configForm.multimodal_models = meta?.multimodal_models || [];
      } else {
        configForm.endpoint = cfg.endpoint || "";
        configForm.model = cfg.model || "";
        configForm.base_url = "";
      }
      testResult.value = null;
    }

    async function testConnection() {
      testing.value = true;
      testResult.value = null;
      try {
        const res = await fetch(`/api/apiconfig/${configDomain.value}/${configForm.provider}/test`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...configForm }),
        });
        testResult.value = await res.json();
      } catch (e) { testResult.value = { ok: false, error: "网络错误" }; }
      finally { testing.value = false; }
    }

    async function saveConfig() {
      if (!configForm.provider) return;
      saving.value = true;
      try {
        const res = await fetch(`/api/apiconfig/${configDomain.value}/${configForm.provider}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...configForm }),
        });
        const d = await res.json();
        if (d.ok) {
          await loadApiConfigs();
          configForm.api_key = "";
          editingProvider.value = null;
          testResult.value = null;
        }
      } catch (e) { console.error(e); }
      finally { saving.value = false; }
    }

    async function deleteConfig(pid) {
      const name = activeConfigs.value[pid]?.name || pid;
      if (!confirm(`确定删除「${name}」的配置？`)) return;
      await fetch(`/api/apiconfig/${configDomain.value}/${pid}`, { method: "DELETE" });
      await loadApiConfigs();
      if (editingProvider.value === pid) { editingProvider.value = null; configForm.provider = ""; }
    }

    function switchConfigDomain(domain) {
      configDomain.value = domain;
      editingProvider.value = null;
      configForm.provider = "";
      configForm.api_key = "";
      testResult.value = null;
    }

    // ===== 素材生成（创意方案 + 视频统一） =====
    const outputType = ref("creative"); // creative | video
    const genForm = reactive({ product_info: "", platform: "", count: 5 });
    const selectedStyle = ref(null);
    const generating = ref(false);
    const generation = ref(null);

    // 视频参数
    const videoForm = reactive({ provider: "seedance", prompt: "", duration: 5, resolution: "720p", aspect_ratio: "16:9" });
    const videoGenerating = ref(false);
    const videoTask = ref(null);
    const recentVideoTasks = ref([]);
    let videoPollTimer = null;

    const videoProviderMeta = computed(() => videoProviders.value.find(p => p.id === videoForm.provider) || null);

    function onVideoProviderSelect() {
      const meta = videoProviderMeta.value;
      if (!meta) return;
      videoForm.duration = meta.duration_default || 5;
      videoForm.resolution = meta.resolution_default || "720p";
      videoForm.aspect_ratio = meta.aspect_ratio_default || "16:9";
    }

    const selectStyleByName = (name) => {
      const match = styles.value.find(s => s.name === name);
      if (match) { selectedStyle.value = match.id; switchPage("generator"); }
    };
    const goGenerate = () => switchPage("generator");

    async function generateCreatives() {
      if (!analysis.value || !selectedStyle.value) return;
      generating.value = true;
      generation.value = null;
      try {
        const res = await fetch("/api/generate", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source_analysis: analysis.value, target_style: selectedStyle.value, product_info: genForm.product_info, platform: genForm.platform, count: genForm.count }),
        });
        generation.value = await res.json();
        stats.generated = generation.value.creatives?.length || 0;
      } catch (e) { console.error(e); }
      finally { generating.value = false; }
    }

    // ---- 视频生成 ----
    async function generateVideo() {
      if (!videoForm.prompt.trim()) return;
      videoGenerating.value = true;
      videoTask.value = null;
      try {
        const res = await fetch("/api/video/generate", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider: videoForm.provider, prompt: videoForm.prompt, duration: videoForm.duration, resolution: videoForm.resolution, aspect_ratio: videoForm.aspect_ratio }),
        });
        const d = await res.json();
        if (d.ok) { videoTask.value = { task_id: d.task_id, progress: 0, status: "queued", mode: d.mode || "api", provider: d.provider || videoForm.provider }; startVideoPolling(d.task_id); }
        else { videoTask.value = { status: "failed", error: d.error }; videoGenerating.value = false; }
      } catch (e) { videoTask.value = { status: "failed", error: "网络错误" }; videoGenerating.value = false; }
    }

    function startVideoPolling(taskId) {
      stopVideoPolling();
      const fetchTask = async () => {
        try {
          const d = await (await fetch(`/api/video/task/${taskId}`)).json();
          videoTask.value = d;
          if (d.status === "succeeded" || d.status === "failed") {
            stopVideoPolling();
            videoGenerating.value = false;
            loadRecentTasks();
          }
        } catch (e) {
          stopVideoPolling();
          videoGenerating.value = false;
          videoTask.value = { status: "failed", error: "任务查询失败" };
        }
      };
      fetchTask();
      videoPollTimer = setInterval(fetchTask, 2000);
    }

    function stopVideoPolling() {
      if (videoPollTimer) { clearInterval(videoPollTimer); videoPollTimer = null; }
    }

    async function loadRecentTasks() {
      try {
        const d = await (await fetch("/api/video/tasks?limit=10")).json();
        recentVideoTasks.value = d.tasks || [];
      } catch (e) {}
    }

    async function viewTask(taskId) {
      try {
        const d = await (await fetch(`/api/video/task/${taskId}`)).json();
        videoTask.value = d;
        videoGenerating.value = false;
      } catch (e) {}
    }
    loadRecentTasks();

    // ===== 采集 =====
    const scrapeMode = ref("web");  // web | playwright
    const scrapeForm = reactive({ source: "youmiyoushu", industry: "", keyword: "", days: 7, domains: "" });
    const scraping = ref(false);
    const searching = ref(false);
    const scrapeLoading = ref(false);
    const scrapeResult = ref(null);
    const platforms = ["抖音", "快手", "小红书", "百度", "腾讯", "Google", "Meta", "TikTok"];

    const scrapeResultMeta = computed(() => {
      const r = scrapeResult.value;
      if (!r) return "";
      if (r.sources) return `关键词: ${r.keyword} | 近 ${r.days || "?"} 天 | 提供商: ${r.provider || "-"} | 共 ${r.sources.length} 条结果`;
      return `来源：${r.source} | 共 ${r.total_creatives || r.keywords?.length || 0} 个素材，提取 ${r.keywords?.length || 0} 个关键词`;
    });

    const statusText = (s) => ({ success: "成功", failed: "失败", mock: "模拟", partial: "部分", no_results: "无结果" }[s] || s);
    const trendIcon = (t) => ({ up: "▲", down: "▼", stable: "•" }[t] || "");

    async function scrapeWebSearch() {
      if (!scrapeForm.keyword) return;
      searching.value = true;
      scrapeLoading.value = true;
      scrapeResult.value = null;
      try {
        const domains = scrapeForm.domains
          ? scrapeForm.domains.split(",").map(d => d.trim()).filter(Boolean)
          : [];
        const res = await fetch("/api/scrape/websearch", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ keyword: scrapeForm.keyword, days: scrapeForm.days, domains }),
        });
        scrapeResult.value = await res.json();
        stats.keywords = scrapeResult.value.sources?.length || 0;
      } catch (e) { scrapeResult.value = { status: "failed", error: "网络错误", sources: [] }; }
      finally { searching.value = false; scrapeLoading.value = false; }
    }

    async function scrapeTrending() {
      scraping.value = true;
      scrapeLoading.value = true;
      scrapeResult.value = null;
      try {
        const res = await fetch("/api/scrape/trending", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: scrapeForm.source, industry: scrapeForm.industry, limit: 50 }),
        });
        scrapeResult.value = await res.json();
        stats.keywords = scrapeResult.value.keywords?.length || 0;
      } catch (e) { scrapeResult.value = { status: "failed", source: "error", keywords: [] }; }
      finally { scraping.value = false; scrapeLoading.value = false; }
    }

    async function scrapeSearch() {
      if (!scrapeForm.keyword) return;
      searching.value = true;
      scrapeLoading.value = true;
      try {
        const res = await fetch("/api/scrape/search", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: scrapeForm.source, keyword: scrapeForm.keyword, limit: 20 }),
        });
        const data = await res.json();
        scrapeResult.value = { source: data.source, status: data.status, keywords: data.results?.map(r => ({ keyword: r.title, frequency: "", trend: "stable" })) || [], total_creatives: data.total };
      } catch (e) { scrapeResult.value = { status: "failed", source: "error", keywords: [] }; }
      finally { searching.value = false; scrapeLoading.value = false; }
    }

    const useKeyword = (kw) => { scrapeForm.keyword = kw; };

    // ===== 素材解析 =====
    const fileInput = ref(null);
    const dragOver = ref(false);
    const analyzing = ref(false);
    const analysis = ref(null);

    function handleDrop(e) { dragOver.value = false; const f = e.dataTransfer.files[0]; if (f) analyzeFile(f); }
    function handleFileSelect(e) { const f = e.target.files[0]; if (f) analyzeFile(f); e.target.value = ""; }

    async function analyzeFile(file) {
      analyzing.value = true;
      analysis.value = null;
      const fd = new FormData();
      fd.append("file", file);
      try {
        const res = await fetch("/api/analyze/upload", { method: "POST", body: fd });
        analysis.value = await res.json();
        stats.analyzed = 1;
      } catch (e) { console.error(e); }
      finally { analyzing.value = false; }
    }

    const kvText = (obj) => JSON.stringify(obj, null, 2).replace(/[{}"]/g, "").replace(/,/g, "\n");

    // ===== 一键工作流 =====
    const wfInput = ref(null);
    const wfDragOver = ref(false);
    const wfFile = ref(null);
    const wfRunning = ref(false);
    const wfResult = ref(null);
    const wfStep = ref(0);
    const wfSteps = [
      { label: "上传文件", icon: "①" },
      { label: "AI 反向解析", icon: "②" },
      { label: "风格迁移生成", icon: "③" },
    ];
    const wfStepState = (i) => i < wfStep.value ? "done" : i === wfStep.value ? "active" : "";

    function handleWfDrop(e) { wfDragOver.value = false; wfFile.value = e.dataTransfer.files[0]; }

    async function runWorkflow() {
      if (!wfFile.value || !selectedStyle.value) return;
      wfRunning.value = true;
      wfResult.value = null;
      wfStep.value = 0;
      const fd = new FormData();
      fd.append("file", wfFile.value);
      const params = new URLSearchParams({ target_style: selectedStyle.value, product_info: genForm.product_info, count: "3" });
      await sleep(400); wfStep.value = 1;
      try {
        const res = await fetch(`/api/workflow/complete?${params}`, { method: "POST", body: fd });
        wfResult.value = await res.json();
        wfStep.value = 2;
        stats.analyzed = 1;
        stats.generated = wfResult.value.generation_result?.creatives?.length || 0;
        await sleep(300); wfStep.value = 3;
      } catch (e) { console.error(e); }
      finally { wfRunning.value = false; }
    }

    const sleep = (ms) => new Promise(r => setTimeout(r, ms));

    // ===== 悬浮运行日志 (B2) =====
    const logPanel = reactive({
      expanded: false,
      minLevel: "info",   // info | warn | error
      entries: [],
      errorCount: 0,
      pos: { x: window.innerWidth - 330, y: window.innerHeight - 300 },
      dragging: false,
      dragOffset: { x: 0, y: 0 },
    });
    const logBody = ref(null);
    let logEventSource = null;
    let logAutoScroll = true;

    const eventLabels = {
      llm: "LLM", video: "视频", scraper: "采集",
      config: "配置", parser: "解析", system: "系统",
    };
    const logEventLabel = (e) => eventLabels[e] || e;
    const logTime = (ts) => { try { const d = new Date(ts); return d.toLocaleTimeString("zh-CN", { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0"); } catch { return ""; } };
    const logDetail = (d) => Object.entries(d).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join("  ");

    const filteredLogs = computed(() => {
      const rank = { info: 0, warn: 1, error: 2 };
      const min = rank[logPanel.minLevel] || 0;
      return logPanel.entries.filter(e => (rank[e.level] || 0) >= min);
    });

    // 悬浮窗位置（用 onMounted + ref 直接控制 style，绕开 :style 编译）
    const logFabRef = ref(null);
    function updateFabPos() {
      if (logFabRef.value) {
        logFabRef.value.style.left = logPanel.pos.x + 'px';
        logFabRef.value.style.top = logPanel.pos.y + 'px';
      }
    }
    onMounted(() => {
      // 通过 querySelector 兜底（Vue 3 全局 CDN 对该 ref 绑定不可靠）
      const el = logFabRef.value || document.querySelector('.log-fab');
      if (el) {
        el.style.left = logPanel.pos.x + 'px';
        el.style.top = logPanel.pos.y + 'px';
      }
      const dragHandler = (e) => {
        if (!logPanel.expanded) return;
        if (e.target.closest('button')) return;
        logPanel.dragging = true;
        logPanel.dragOffset = { x: e.clientX - logPanel.pos.x, y: e.clientY - logPanel.pos.y };
      };
      if (el) el.addEventListener('mousedown', dragHandler);
      window.addEventListener('mousemove', (e) => {
        if (logPanel.dragging) {
          logPanel.pos.x = Math.max(0, Math.min(window.innerWidth - 40, e.clientX - logPanel.dragOffset.x));
          logPanel.pos.y = Math.max(0, Math.min(window.innerHeight - 40, e.clientY - logPanel.dragOffset.y));
          if (el) { el.style.left = logPanel.pos.x + 'px'; el.style.top = logPanel.pos.y + 'px'; }
        }
      });
      window.addEventListener('mouseup', () => { logPanel.dragging = false; });
    });

    function toggleLogPanel() {
      logPanel.expanded = !logPanel.expanded;
      if (logPanel.expanded) { loadRecentLogs(); connectLogStream(); }
    }

    function toggleLogFilter(level) {
      logPanel.minLevel = logPanel.minLevel === level ? "info" : level;
    }

    function clearLogs() {
      logPanel.entries = [];
      logPanel.errorCount = 0;
      fetch("/api/logs", { method: "DELETE" }).catch(() => {});
    }

    async function loadRecentLogs() {
      try {
        const d = await (await fetch("/api/logs?limit=200")).json();
        logPanel.entries = (d.logs || []).slice(-200);
        updateErrorCount();
        scrollLogToBottom();
      } catch (e) {}
    }

    function connectLogStream() {
      if (logEventSource) { logEventSource.close(); }
      logEventSource = new EventSource("/api/logs/stream");
      logEventSource.onmessage = (ev) => {
        try {
          const entry = JSON.parse(ev.data);
          logPanel.entries.push(entry);
          if (logPanel.entries.length > 500) logPanel.entries.splice(0, logPanel.entries.length - 500);
          if (entry.level === "error") logPanel.errorCount++;
          scrollLogToBottom();
        } catch (e) {}
      };
      logEventSource.onerror = () => { /* 自动重连 */ };
    }

    function updateErrorCount() {
      logPanel.errorCount = logPanel.entries.filter(e => e.level === "error").length;
    }

    function scrollLogToBottom() {
      if (logBody.value && logAutoScroll) {
        requestAnimationFrame(() => { logBody.value.scrollTop = logBody.value.scrollHeight; });
      }
    }

    // 拖拽事件已在 onMounted 中绑定

    // 初次连接流（后台静默接收）
    connectLogStream();

    // ===== 步骤卡片 =====
    const steps = [
      { title: "数据采集", desc: "爬取热门素材关键词与趋势", target: "scraper" },
      { title: "素材反向解析", desc: "AI 提取关键词与生成 Prompt", target: "parser" },
      { title: "素材生成", desc: "创意方案 / 视频素材统一产出", target: "generator" },
    ];

    return {
      navItems, currentPage, switchPage,
      health, stats, steps,
      sources, styles, platforms,
      scrapeForm, scraping, searching, scrapeLoading, scrapeResult, scrapeMode, scrapeResultMeta,
      statusText, trendIcon, scrapeTrending, scrapeSearch, scrapeWebSearch, useKeyword,
      fileInput, dragOver, analyzing, analysis, handleDrop, handleFileSelect, kvText,
      // 素材生成（双产出）
      outputType, genForm, selectedStyle, generating, generation,
      generateCreatives, selectStyleByName, goGenerate,
      videoForm, videoGenerating, videoTask, recentVideoTasks, videoProviderMeta,
      onVideoProviderSelect, generateVideo, viewTask,
      // API 配置（双域）
      llmProviders, videoProviders, savedConfigs, configuredLlmCount, configuredVideoCount, configuredProviderCount, healthLLMVision,
      configDomain, configForm, editingProvider, editingProviderName, savedKeyHint,
      activeProviders, activeConfigs, activeProviderMeta, testing, saving, testResult,
      switchConfigDomain, onProviderSelect, editConfig, testConnection, saveConfig, deleteConfig,
      wfInput, wfDragOver, wfFile, wfRunning, wfResult, wfStep, wfSteps, wfStepState, handleWfDrop, runWorkflow,
      // 悬浮日志
      logPanel, logBody, filteredLogs, logFabRef,
      toggleLogPanel, toggleLogFilter, clearLogs,
      logTime, logEventLabel, logDetail,
    };
  },
}).mount("#app");
