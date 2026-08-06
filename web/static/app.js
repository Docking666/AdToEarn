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
      { id: "audit", label: "账户审计", icon: "M3 3v18h18M7 15l3-4 3 3 5-7" },
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

    // ===== 广告账户审计 =====
    const auditCsvInput = ref(null);
    const auditMeta = reactive({ record_count: 0, accounts: [], date_min: null, date_max: null, has_sample: false, raw_fields: [] });
    const auditSummary = ref(null);
    const auditTrend = ref([]);
    const auditAccounts = ref([]);
    const auditAnomalies = ref([]);
    const auditSignals = ref([]);          // 数据信号（统一 schema）
    const auditRules = ref({});            // 信号规则 + 启用状态
    const showRulePanel = ref(false);      // 规则配置面板开关
    // Phase4: 多维透视
    const auditPivotDims = ref([]);        // 选择的维度（标签组 ID）
    const auditPivotMetric = ref("spend"); // 排序指标
    const auditPivotResult = ref(null);    // 透视结果
    const auditPivotLoading = ref(false);
    const auditLoading = reactive({ sample: false, clear: false, import: false, detect: false });
    const auditFilter = reactive({ account: "", days: 0 });
    const auditChartType = ref("volume");
    const auditTrendChart = ref(null);
    const auditAccountChart = ref(null);
    let auditTrendChartInst = null;
    let auditAccountChartInst = null;
    // Phase1: 拖拽上传 + 字段映射
    const auditDragOver = ref(false);
    const auditUploadFile = ref(null);
    const auditFieldDetect = ref(null);        // 探测结果 {columns, row_count, matches, samples}
    const auditFieldMapEdit = reactive({});    // 用户编辑中的映射 {col: standard_field}
    const auditStandardFields = ref({});       // 标准字段定义
    const auditPendingContent = ref("");       // 待导入的文件内容
    const auditPendingFormat = ref("csv");
    // Phase2: 原始数据表格 + 批量打标
    const auditRecords = ref([]);               // 原始记录列表（含 raw+tags）
    const auditRecordTotal = ref(0);
    const auditRecordOffset = ref(0);
    const auditRecordLimit = 20;
    const auditRecordsLoading = ref(false);
    const auditSelectedRows = ref([]);          // 选中的行索引
    const auditDisplayFields = ref([]);         // 表格展示的 raw 字段列表
    const auditTagLibrary = ref({ groups: [] }); // 标签库
    const showTagPanel = ref(false);
    const auditPendingTags = reactive({});      // 待打标的标签 {group_id: [tags]}
    const auditTagMode = ref("add");

    const auditMetricCards = computed(() => {
      const m = auditSummary.value?.metrics;
      if (!m) return [];
      return [
        { key: "impressions", label: "曝光量", value: fmtNum(m.impressions), sub: `${fmtNum(m.impressions / Math.max(1, m.day_count))}/日`, tone: "" },
        { key: "clicks", label: "点击量", value: fmtNum(m.clicks), sub: `CTR ${m.ctr}%`, tone: "" },
        { key: "conversions", label: "转化量", value: fmtNum(m.conversions), sub: `CVR ${m.cvr}%`, tone: "" },
        { key: "spend", label: "总花费", value: "¥" + fmtMoney(m.spend), sub: `CPC ¥${fmtMoney(m.cpc)} · CPM ¥${fmtMoney(m.cpm)}`, tone: "" },
        { key: "cpa", label: "获客成本 CPA", value: "¥" + fmtMoney(m.cpa), sub: m.cpa > 0 ? "越低越好" : "无转化", tone: m.cpa > 0 && m.cpa > 50 ? "warn" : "good" },
        { key: "roas", label: "投产比 ROAS", value: m.roas, sub: m.roas > 0 ? "越高越好" : "无价值数据", tone: m.roas >= 1 ? "good" : "warn" },
        { key: "conv_value", label: "转化价值", value: "¥" + fmtMoney(m.conversion_value), sub: `${m.account_count} 个账户 · ${m.day_count} 天`, tone: "" },
        { key: "records", label: "数据记录", value: fmtNum(m.record_count), sub: `账户: ${auditMeta.accounts.join(" / ") || "-"}`, tone: "" },
      ];
    });

    function fmtNum(n) {
      if (n === null || n === undefined) return "0";
      return Number(n).toLocaleString("zh-CN", { maximumFractionDigits: 0 });
    }
    function fmtMoney(n) {
      if (n === null || n === undefined) return "0.00";
      return Number(n).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    const severityLabel = (s) => auditSeverityLabels[s] || s;
    const severityBadge = (s) => ({ critical: "failed", high: "failed", medium: "mock", low: "" }[s] || "");
    const auditSeverityLabels = { critical: "严重", high: "高危", medium: "中等", low: "提示" };

    async function loadAuditMeta() {
      try {
        const d = await (await fetch("/api/audit/meta")).json();
        auditMeta.record_count = d.record_count || 0;
        auditMeta.accounts = d.accounts || [];
        auditMeta.date_min = d.date_min;
        auditMeta.date_max = d.date_max;
        auditMeta.has_sample = !!d.has_sample;
      } catch (e) {}
    }

    async function loadAuditAll() {
      const params = new URLSearchParams();
      if (auditFilter.account) params.set("account", auditFilter.account);
      if (auditFilter.days) params.set("days", auditFilter.days);
      const qs = params.toString() ? "?" + params : "";
      try {
        const [summary, trend, accounts, signals, rules] = await Promise.all([
          fetch(`/api/audit/summary${qs}`).then(r => r.json()),
          fetch(`/api/audit/trend${qs}`).then(r => r.json()),
          fetch(`/api/audit/accounts${qs}`).then(r => r.json()),
          fetch(`/api/audit/signals${qs}`).then(r => r.json()),
          fetch(`/api/audit/rules`).then(r => r.json()),
        ]);
        auditSummary.value = summary;
        auditTrend.value = trend.trend || [];
        auditAccounts.value = accounts.accounts || [];
        auditSignals.value = signals.signals || [];
        auditRules.value = rules.rules || {};
        renderAuditCharts();
      } catch (e) { console.error(e); }
    }

    // ===== 信号规则开关（Phase3） =====
    const signalCategoryLabels = {
      surge: "🔥 突增", decay: "📉 衰减", inefficiency: "⚠️ 低效",
      data_quality: "🛠 数据质量", shift: "🔄 权重变化", hint: "💡 提示",
    };
    const signalCategoryLabel = (c) => signalCategoryLabels[c] || c;
    const ruleCategoryLabel = (c) => ({ surge: "突增", decay: "衰减", inefficiency: "低效", data_quality: "数据质量", shift: "权重变化", hint: "提示" }[c] || c);

    async function toggleAuditRule(rid, e) {
      const enabled = e.target.checked;
      try {
        await fetch("/api/audit/rules", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rule_id: rid, enabled }),
        });
        if (auditRules.value[rid]) auditRules.value[rid].enabled = enabled;
        await loadAuditAll();  // 重新计算信号
      } catch (err) { console.error(err); }
    }

    async function resetAuditRules() {
      try {
        await fetch("/api/audit/rules/reset", { method: "POST" });
        await loadAuditAll();
      } catch (err) { console.error(err); }
    }

    async function setRuleMethod(rid, method) {
      try {
        await fetch("/api/audit/rules", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rule_id: rid, enabled: auditRules.value[rid]?.enabled ?? true, method }),
        });
        if (auditRules.value[rid]) auditRules.value[rid].method = method;
        await loadAuditAll();  // 用新方法重算信号
      } catch (err) { console.error(err); }
    }

    // ===== Phase4: 多维透视 =====
    async function loadAuditPivot() {
      if (!auditPivotDims.value.length) {
        alert("请至少选择一个透视维度");
        return;
      }
      auditPivotLoading.value = true;
      auditPivotResult.value = null;
      try {
        const params = new URLSearchParams();
        if (auditFilter.account) params.set("account", auditFilter.account);
        if (auditFilter.days) params.set("days", auditFilter.days);
        const res = await fetch(`/api/audit/pivot${params.toString() ? "?" + params : ""}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dimensions: auditPivotDims.value, metric: auditPivotMetric.value }),
        });
        const d = await res.json();
        if (res.ok) {
          auditPivotResult.value = d;
        } else {
          alert("透视失败：" + (d.detail || JSON.stringify(d)));
        }
      } catch (err) { alert("透视失败：" + err.message); }
      finally { auditPivotLoading.value = false; }
    }

    function switchAuditChart(type) {
      auditChartType.value = type;
      renderAuditCharts();
    }

    function renderAuditCharts() {
      if (!window.echarts) return;
      // 趋势图
      if (auditTrendChart.value) {
        if (!auditTrendChartInst) auditTrendChartInst = echarts.init(auditTrendChart.value);
        const dates = auditTrend.value.map(t => t.date);
        const type = auditChartType.value;
        const series = type === "volume" ? [
          { name: "曝光量", type: "bar", data: auditTrend.value.map(t => t.impressions), itemStyle: { color: "#6366f1" } },
          { name: "点击量", type: "line", smooth: true, data: auditTrend.value.map(t => t.clicks), itemStyle: { color: "#34d399" } },
          { name: "转化量", type: "line", smooth: true, data: auditTrend.value.map(t => t.conversions), itemStyle: { color: "#fbbf24" } },
        ] : type === "cost" ? [
          { name: "花费", type: "bar", data: auditTrend.value.map(t => t.spend), itemStyle: { color: "#f87171" } },
          { name: "转化价值", type: "line", smooth: true, data: auditTrend.value.map(t => t.conversion_value), itemStyle: { color: "#34d399" } },
        ] : [
          { name: "CTR(%)", type: "line", smooth: true, data: auditTrend.value.map(t => t.ctr), itemStyle: { color: "#60a5fa" } },
          { name: "CPA(¥)", type: "line", smooth: true, data: auditTrend.value.map(t => t.cpa), itemStyle: { color: "#f87171" } },
          { name: "ROAS", type: "line", smooth: true, data: auditTrend.value.map(t => t.roas), itemStyle: { color: "#34d399" } },
        ];
        auditTrendChartInst.setOption({
          backgroundColor: "transparent",
          tooltip: { trigger: "axis", backgroundColor: "#1e2230", borderColor: "rgba(255,255,255,.14)", textStyle: { color: "#e6e9f2" } },
          legend: { textStyle: { color: "#9aa3b8" }, top: 0 },
          grid: { left: 48, right: 16, top: 36, bottom: 24 },
          xAxis: { type: "category", data: dates, axisLine: { lineStyle: { color: "rgba(255,255,255,.14)" } }, axisLabel: { color: "#9aa3b8" } },
          yAxis: { type: "value", splitLine: { lineStyle: { color: "rgba(255,255,255,.06)" } }, axisLabel: { color: "#9aa3b8" } },
          series,
        }, true);
      }
      // 账户对比图
      if (auditAccountChart.value) {
        if (!auditAccountChartInst) auditAccountChartInst = echarts.init(auditAccountChart.value);
        const accounts = auditAccounts.value;
        auditAccountChartInst.setOption({
          backgroundColor: "transparent",
          tooltip: { trigger: "axis", backgroundColor: "#1e2230", borderColor: "rgba(255,255,255,.14)", textStyle: { color: "#e6e9f2" } },
          legend: { textStyle: { color: "#9aa3b8" }, top: 0 },
          grid: { left: 48, right: 16, top: 36, bottom: 24 },
          xAxis: { type: "category", data: accounts.map(a => a.account), axisLine: { lineStyle: { color: "rgba(255,255,255,.14)" } }, axisLabel: { color: "#9aa3b8", interval: 0, rotate: accounts.length > 4 ? 20 : 0 } },
          yAxis: { type: "value", splitLine: { lineStyle: { color: "rgba(255,255,255,.06)" } }, axisLabel: { color: "#9aa3b8" } },
          series: [
            { name: "花费(¥)", type: "bar", data: accounts.map(a => a.spend), itemStyle: { color: "#6366f1" }, barMaxWidth: 28 },
            { name: "转化价值(¥)", type: "bar", data: accounts.map(a => a.conversion_value), itemStyle: { color: "#34d399" }, barMaxWidth: 28 },
          ],
        }, true);
      }
    }

    function onAuditFileSelect(e) {
      const file = e.target.files[0];
      if (file) handleAuditFile(file);
      e.target.value = "";
    }

    function onAuditDrop(e) {
      auditDragOver.value = false;
      const file = e.dataTransfer.files[0];
      if (file) handleAuditFile(file);
    }

    async function handleAuditFile(file) {
      auditUploadFile.value = file;
      const fmt = file.name.toLowerCase().endsWith(".json") ? "json" : "csv";
      auditLoading.detect = true;
      auditFieldDetect.value = null;
      const reader = new FileReader();
      reader.onload = async () => {
        const content = String(reader.result || "");
        auditPendingContent.value = content;
        auditPendingFormat.value = fmt;
        try {
          // 1. 探测字段
          const res = await fetch("/api/audit/fields/detect", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content, format: fmt }),
          });
          const d = await res.json();
          if (res.ok && d.columns) {
            auditFieldDetect.value = d;
            // 初始化编辑态：用自动匹配结果填充
            for (const col of d.columns) {
              auditFieldMapEdit[col] = d.matches[col]?.standard_field || "";
            }
            // 加载标准字段定义（首次）
            if (!Object.keys(auditStandardFields.value).length) {
              await loadAuditStandardFields();
            }
          } else {
            alert("字段探测失败：" + (d.detail || JSON.stringify(d)));
          }
        } catch (err) { alert("文件读取失败：" + err.message); }
        finally { auditLoading.detect = false; }
      };
      reader.readAsText(file);
    }

    async function loadAuditStandardFields() {
      try {
        const d = await (await fetch("/api/audit/fields/standard")).json();
        auditStandardFields.value = d.standard_fields || {};
      } catch (e) {}
    }

    const auditMapLayerLabel = (layer) => ({
      exact: "自动", fuzzy: "模糊", user: "已配置", none: "未映射"
    }[layer] || "");

    async function onAuditFieldMapChange(col) {
      // 用户调整映射 → 实时保存到后端
      const std = auditFieldMapEdit[col] || "";
      try {
        await fetch("/api/audit/field-map", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ column_name: col, standard_field: std || null }),
        });
        // 更新本地 matches 状态
        if (auditFieldDetect.value) {
          auditFieldDetect.value.matches[col] = {
            standard_field: std || null,
            layer: std ? "user" : "none",
            confidence: std ? 1.0 : 0,
          };
        }
      } catch (e) {}
    }

    async function confirmAuditImport() {
      // 确认导入：发送文件内容 + 用户映射
      auditLoading.import = true;
      try {
        const res = await fetch("/api/audit/import/file", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: auditPendingContent.value, format: auditPendingFormat.value }),
        });
        const d = await res.json();
        if (d.ok) {
          alert(`导入成功：${d.imported} 条记录` + (d.errors?.length ? `（${d.errors.length} 行错误）` : ""));
          auditFieldDetect.value = null;
          auditUploadFile.value = null;
          auditPendingContent.value = "";
          await refreshAudit();
        } else {
          alert("导入失败：" + JSON.stringify(d.errors || d.detail || d));
        }
      } catch (err) { alert("导入失败：" + err.message); }
      finally { auditLoading.import = false; }
    }

    // ===== Phase2: 原始数据表格 + 批量打标 =====
    async function loadAuditRecords() {
      auditRecordsLoading.value = true;
      try {
        const params = new URLSearchParams();
        if (auditFilter.account) params.set("account", auditFilter.account);
        if (auditFilter.days) params.set("days", auditFilter.days);
        params.set("limit", auditRecordLimit);
        params.set("offset", auditRecordOffset.value);
        const d = await (await fetch(`/api/audit/records/tagged?${params}`)).json();
        auditRecords.value = d.records || [];
        auditRecordTotal.value = d.total || 0;
        // 更新展示字段（取 raw 字段，最多 8 列避免过宽）
        if (auditRecords.value.length) {
          auditDisplayFields.value = Object.keys(auditRecords.value[0].raw || {}).slice(0, 8);
        }
      } catch (e) { console.error(e); }
      finally { auditRecordsLoading.value = false; }
    }

    async function loadAuditTagLibrary() {
      try {
        const d = await (await fetch("/api/audit/tags/library")).json();
        auditTagLibrary.value = d;
      } catch (e) {}
    }

    const auditTagGroupName = (gid) => {
      const g = auditTagLibrary.value.groups?.find(x => x.id === gid);
      return g ? g.name : gid;
    };

    function toggleSelectAll(e) {
      if (e.target.checked) {
        auditSelectedRows.value = auditRecords.value.map(r => r.index);
      } else {
        auditSelectedRows.value = [];
      }
    }

    function togglePendingTag(gid, tag) {
      if (!auditPendingTags[gid]) auditPendingTags[gid] = [];
      const idx = auditPendingTags[gid].indexOf(tag);
      if (idx >= 0) auditPendingTags[gid].splice(idx, 1);
      else auditPendingTags[gid].push(tag);
    }

    async function applyBatchTag() {
      if (!auditSelectedRows.value.length) return;
      // 对每个有 pending tags 的组执行打标
      const groups = Object.keys(auditPendingTags).filter(gid => auditPendingTags[gid].length);
      if (!groups.length && auditTagMode.value !== "clear") {
        alert("请至少选择一个标签");
        return;
      }
      for (const gid of groups) {
        try {
          await fetch("/api/audit/tag/batch", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              row_indices: auditSelectedRows.value,
              group_id: gid,
              tags: auditPendingTags[gid],
              mode: auditTagMode.value,
            }),
          });
        } catch (e) { console.error(e); }
      }
      // 如果是 clear 模式且没选标签，需要传 group_id（从 pendingTags 取第一个或让用户选）
      auditPendingTags.value = {};
      showTagPanel.value = false;
      auditSelectedRows.value = [];
      await loadAuditRecords();
      alert("打标完成");
    }

    async function generateAuditSample() {
      auditLoading.sample = true;
      try {
        const d = await (await fetch("/api/audit/sample", { method: "POST" })).json();
        if (d.ok) { alert(`已生成 ${d.imported} 条示例数据（标注「含示例数据」）`); await refreshAudit(); }
      } catch (err) { alert("生成失败：" + err.message); }
      finally { auditLoading.sample = false; }
    }

    async function clearAuditData() {
      if (!confirm("确定清空全部审计数据？此操作不可恢复。")) return;
      auditLoading.clear = true;
      try {
        await fetch("/api/audit/data", { method: "DELETE" });
        auditSummary.value = null;
        auditTrend.value = [];
        auditAccounts.value = [];
        auditAnomalies.value = [];
        auditSignals.value = [];
        await loadAuditMeta();
      } catch (e) {}
      finally { auditLoading.clear = false; }
    }

    // Phase5: 导出 Excel
    async function exportAuditExcel() {
      if (!auditMeta.record_count) return;
      try {
        const params = new URLSearchParams();
        if (auditFilter.account) params.set("account", auditFilter.account);
        if (auditFilter.days) params.set("days", auditFilter.days);
        const res = await fetch(`/api/audit/export${params.toString() ? "?" + params : ""}`);
        if (!res.ok) { alert("导出失败：" + res.status); return; }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `adtoearn_audit_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "")}.xlsx`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (err) { alert("导出失败：" + err.message); }
    }

    async function refreshAudit() {
      await loadAuditMeta();
      await loadAuditTagLibrary();
      if (auditMeta.record_count) {
        await loadAuditAll();
        auditRecordOffset.value = 0;
        await loadAuditRecords();
      }
    }

    // 页面切换 / 窗口尺寸变化时渲染图表
    watch(currentPage, (p) => {
      if (p === "audit") {
        refreshAudit();
        setTimeout(renderAuditCharts, 100);
      }
    });
    window.addEventListener("resize", () => {
      auditTrendChartInst?.resize();
      auditAccountChartInst?.resize();
    });

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
      // 广告账户审计
      auditCsvInput, auditMeta, auditSummary, auditTrend, auditAccounts, auditAnomalies,
      auditSignals, auditRules, showRulePanel,
      auditLoading, auditFilter, auditChartType, auditTrendChart, auditAccountChart,
      auditMetricCards, severityLabel, severityBadge, fmtNum, fmtMoney,
      loadAuditAll, switchAuditChart, onAuditFileSelect, generateAuditSample, clearAuditData,
      exportAuditExcel,
      // Phase3: 信号规则开关
      signalCategoryLabel, ruleCategoryLabel, toggleAuditRule, resetAuditRules, setRuleMethod,
      // Phase4: 多维透视
      auditPivotDims, auditPivotMetric, auditPivotResult, auditPivotLoading, loadAuditPivot,
      // Phase1: 拖拽 + 字段映射
      auditDragOver, auditUploadFile, auditFieldDetect, auditFieldMapEdit, auditStandardFields,
      onAuditDrop, handleAuditFile, auditMapLayerLabel, onAuditFieldMapChange, confirmAuditImport,
      // Phase2: 原始数据表格 + 批量打标
      auditRecords, auditRecordTotal, auditRecordOffset, auditRecordsLoading, auditSelectedRows,
      auditDisplayFields, auditTagLibrary, showTagPanel, auditPendingTags, auditTagMode,
      loadAuditRecords, loadAuditTagLibrary, auditTagGroupName, toggleSelectAll,
      togglePendingTag, applyBatchTag,
      // 悬浮日志
      logPanel, logBody, filteredLogs, logFabRef,
      toggleLogPanel, toggleLogFilter, clearLogs,
      logTime, logEventLabel, logDetail,
    };
  },
}).mount("#app");
