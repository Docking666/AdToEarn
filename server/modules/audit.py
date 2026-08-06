"""
广告账户审计模块 (Audit)
参照 Claude-ads 的设计思路，对广告投放效果进行可视化数据分析：
  - 投放数据总览（健康评分 + 关键指标）
  - 时间维度趋势分析
  - 账户维度数据对比
  - 异常 / 风险提示（分级：critical / high / medium / low）

数据模型（双轨制，Phase1 升级）：
  每条记录 = { raw: {原始所有字段}, mapped: {映射后标准字段}, tags: {} }
  raw   : 用户上传 CSV 的原始字段，全部保留，不做任何强制映射
  mapped: 经字段映射引擎处理后的 7 个标准字段（account/date/impressions/clicks/
          conversions/spend/conversion_value），用于指标计算与异常检测
  tags  : 行级标签（Phase2 批量打标用），结构 {group_name: [tag_values]}

派生指标：CTR / CVR / CPC / CPM / CPA / ROAS

数据来源：
  1) WebUI 上传 CSV（推荐列：account,date,impressions,clicks,conversions,spend,conversion_value）
  2) WebUI 上传 JSON（数组形式，字段同上）
  3) 生成示例数据（sample=true，演示/测试用）
持久化：JSON 文件（settings.audit_data_file），与 api_config.json 同风格
"""

import csv
import io
import json
import random
import statistics
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from ..config import settings
from .app_logger import log_collector, EVENT_SYSTEM, EVENT_CONFIG
from .field_mapper import field_mapper

# 严重度级别
SEV_CRITICAL = "critical"
SEV_HIGH = "high"
SEV_MEDIUM = "medium"
SEV_LOW = "low"

SEV_LABELS = {
    SEV_CRITICAL: "严重",
    SEV_HIGH: "高危",
    SEV_MEDIUM: "中等",
    SEV_LOW: "提示",
}

# 指标元信息（供前端展示：名称/单位/精度/方向）
METRIC_META = {
    "impressions": {"label": "曝光量", "unit": "", "precision": 0, "format": "num"},
    "clicks": {"label": "点击量", "unit": "", "precision": 0, "format": "num"},
    "conversions": {"label": "转化量", "unit": "", "precision": 0, "format": "num"},
    "spend": {"label": "花费", "unit": "¥", "precision": 2, "format": "money"},
    "conversion_value": {"label": "转化价值", "unit": "¥", "precision": 2, "format": "money"},
    "ctr": {"label": "点击率 CTR", "unit": "%", "precision": 2, "format": "percent"},
    "cvr": {"label": "转化率 CVR", "unit": "%", "precision": 2, "format": "percent"},
    "cpc": {"label": "单次点击成本 CPC", "unit": "¥", "precision": 2, "format": "money"},
    "cpm": {"label": "千次曝光成本 CPM", "unit": "¥", "precision": 2, "format": "money"},
    "cpa": {"label": "获客成本 CPA", "unit": "¥", "precision": 2, "format": "money"},
    "roas": {"label": "投产比 ROAS", "unit": "", "precision": 2, "format": "ratio"},
}


class AuditService:
    """广告账户审计服务（数据管理 + 指标计算 + 异常检测）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._data_file: Path = settings.audit_data_file

    # ==================== 数据读写 ====================
    def _load(self) -> list:
        """读取全部投放记录"""
        if self._data_file.exists():
            try:
                data = json.loads(self._data_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _save(self, records: list):
        """持久化投放记录"""
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
        self._data_file.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ==================== 数据管理 ====================
    def clear(self) -> dict:
        """清空全部审计数据"""
        with self._lock:
            self._save([])
        log_collector.info(EVENT_CONFIG, "审计数据已清空")
        return {"ok": True, "count": 0}

    def get_meta(self) -> dict:
        """数据元信息：记录数 / 账户列表 / 时间范围 / 是否含示例数据 / 原始字段列表"""
        records = self._load()
        accounts = sorted({r.get("mapped", {}).get("account", "") for r in records if r.get("mapped", {}).get("account")})
        dates = sorted(r.get("mapped", {}).get("date", "") for r in records if r.get("mapped", {}).get("date"))
        has_sample = any(r.get("sample") for r in records)
        # 收集所有 raw 字段（供前端表格列展示）
        raw_fields = []
        seen = set()
        for r in records:
            for k in r.get("raw", {}).keys():
                if k not in seen:
                    seen.add(k)
                    raw_fields.append(k)
        return {
            "record_count": len(records),
            "accounts": accounts,
            "date_min": dates[0] if dates else None,
            "date_max": dates[-1] if dates else None,
            "has_sample": has_sample,
            "raw_fields": raw_fields,
            "metric_meta": METRIC_META,
            "severity_labels": SEV_LABELS,
        }

    def import_records(self, records: list, source: str = "upload") -> dict:
        """批量导入记录（覆盖式：导入即替换，保持与 api_config 保存语义一致）
        Phase1 升级：自动调用 field_mapper 将原始列名映射到标准字段
        """
        normalized = []
        errors = []
        # 第 1 行用于推断列名 → 字段映射（同批数据假设列名一致）
        column_map = {}
        if records and isinstance(records[0], dict):
            for col in records[0].keys():
                m = field_mapper.match(col)
                if m["standard_field"]:
                    column_map[col] = m["standard_field"]
        for i, raw in enumerate(records):
            try:
                rec = self._normalize(raw, column_map)
                if rec:
                    normalized.append(rec)
            except ValueError as e:
                errors.append({"row": i + 1, "error": str(e)})
        if not normalized:
            return {"ok": False, "imported": 0, "errors": errors or [{"row": 1, "error": "未解析到有效数据，请检查列名与格式"}]}
        with self._lock:
            self._save(normalized)
        log_collector.info(EVENT_CONFIG, f"审计数据导入成功: {len(normalized)} 条", {
            "source": source, "errors": len(errors),
            "mapped_fields": list(column_map.keys()),
        })
        return {"ok": True, "imported": len(normalized), "errors": errors, "column_map": column_map}

    def _normalize(self, raw: dict, column_map: Optional[dict] = None) -> Optional[dict]:
        """单条记录标准化与校验
        Phase1 双轨制：保留 raw 原始所有字段 + 构建 mapped 标准字段
        column_map: 预先匹配好的 {原始列名: 标准字段} 映射；为 None 时按列名逐个匹配
        """
        if not isinstance(raw, dict):
            raise ValueError("记录必须是对象")

        # raw: 原始所有字段保留
        raw_clean = {k: v for k, v in raw.items() if v is not None and v != ""}

        # 构建 mapped: 标准字段
        mapped = {}
        for raw_col, value in raw_clean.items():
            if column_map and raw_col in column_map:
                std_field = column_map[raw_col]
            elif column_map is None:
                m = field_mapper.match(raw_col)
                std_field = m["standard_field"]
            else:
                std_field = None
            if std_field and std_field not in mapped:
                mapped[std_field] = value

        # 必需字段校验（account/date）
        if not mapped.get("account"):
            mapped["account"] = "未分组账户"
        if not mapped.get("date"):
            raise ValueError("缺少日期字段（需映射到 date 标准字段）")
        try:
            d = self._parse_date(str(mapped["date"]))
            mapped["date"] = d.isoformat()
        except ValueError:
            raise ValueError(f"日期格式无效: {mapped['date']}（应为 YYYY-MM-DD）")
        mapped["account"] = str(mapped["account"]).strip() or "未分组账户"

        # 数值字段：清洗 + 类型转换
        for num_field in ("impressions", "clicks", "conversions", "spend", "conversion_value"):
            if num_field in mapped:
                try:
                    v = str(mapped[num_field]).replace(",", "").replace("¥", "").replace("元", "").replace("$", "").strip()
                    mapped[num_field] = float(v) if num_field in ("spend", "conversion_value") else int(float(v))
                except (TypeError, ValueError):
                    raise ValueError(f"数值字段无效: {num_field}={mapped[num_field]}")
            else:
                mapped[num_field] = 0 if num_field != "conversion_value" else 0.0
        mapped["spend"] = round(float(mapped["spend"]), 2)
        mapped["conversion_value"] = round(float(mapped["conversion_value"]), 2)

        # 全 0 记录视为无效行
        if (mapped["impressions"] <= 0 and mapped["clicks"] <= 0
                and mapped["conversions"] <= 0 and mapped["spend"] <= 0
                and mapped["conversion_value"] <= 0):
            raise ValueError("该行无有效数值（全为 0）")

        # 双轨记录
        rec = {"raw": raw_clean, "mapped": mapped, "tags": {}}
        if raw.get("sample"):
            rec["sample"] = True
        return rec

    @staticmethod
    def _parse_date(val: str) -> date:
        """解析日期，支持 YYYY-MM-DD / YYYY/MM/DD / 时间戳（秒/毫秒）"""
        val = val.strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        # 时间戳
        try:
            ts = float(val)
            if ts > 1e12:
                ts /= 1000
            return datetime.fromtimestamp(ts).date()
        except ValueError:
            pass
        raise ValueError(f"无法解析日期: {val}")

    # ==================== CSV / JSON 解析 ====================
    def parse_csv(self, content: bytes) -> list:
        """解析 CSV 内容为记录列表"""
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("CSV 缺少表头")
        return [dict(row) for row in reader]

    def parse_json(self, content: bytes) -> list:
        """解析 JSON 内容（数组）为记录列表"""
        text = content.decode("utf-8-sig", errors="replace")
        data = json.loads(text)
        if isinstance(data, dict):
            # 兼容 {"records": [...]}
            data = data.get("records", [])
        if not isinstance(data, list):
            raise ValueError("JSON 应为记录数组")
        return data

    # ==================== 指标计算 ====================
    @staticmethod
    def _calc(records: list) -> dict:
        """聚合计算关键指标（对给定记录集合，读 mapped 域）"""
        def get(r, k, default=0):
            v = r.get("mapped", {}).get(k, default)
            try:
                return float(v) if k in ("spend", "conversion_value") else int(v)
            except (TypeError, ValueError):
                return default
        impressions = sum(get(r, "impressions") for r in records)
        clicks = sum(get(r, "clicks") for r in records)
        conversions = sum(get(r, "conversions") for r in records)
        spend = sum(get(r, "spend", 0.0) for r in records)
        conv_value = sum(get(r, "conversion_value", 0.0) for r in records)
        return {
            "impressions": impressions,
            "clicks": clicks,
            "conversions": conversions,
            "spend": round(spend, 2),
            "conversion_value": round(conv_value, 2),
            "ctr": round(clicks / impressions * 100, 2) if impressions else 0.0,
            "cvr": round(conversions / clicks * 100, 2) if clicks else 0.0,
            "cpc": round(spend / clicks, 2) if clicks else 0.0,
            "cpm": round(spend / impressions * 1000, 2) if impressions else 0.0,
            "cpa": round(spend / conversions, 2) if conversions else 0.0,
            "roas": round(conv_value / spend, 2) if spend else 0.0,
        }

    def summary(self, account: Optional[str] = None, days: Optional[int] = None) -> dict:
        """投放数据总览：关键指标 + 健康评分 + 信号统计"""
        records = self._filter(account=account, days=days)
        metrics = self._calc(records)
        signals = self.detect_signals(records)
        health = self._health_score(signals)
        accounts = len({r.get("mapped", {}).get("account") for r in records})
        metrics.update({
            "account_count": accounts,
            "day_count": len({r.get("mapped", {}).get("date") for r in records}),
            "record_count": len(records),
        })
        return {
            "metrics": metrics,
            "health": health,
            "anomaly_count": len(signals),
            "anomaly_by_severity": {
                sev: len([a for a in signals if a["severity"] == sev])
                for sev in (SEV_CRITICAL, SEV_HIGH, SEV_MEDIUM, SEV_LOW)
            },
        }

    def trend(self, account: Optional[str] = None, days: Optional[int] = None) -> list:
        """时间维度趋势：按日聚合，返回每日指标序列"""
        records = self._filter(account=account, days=days)
        by_date = defaultdict(list)
        for r in records:
            d = r.get("mapped", {}).get("date")
            if d:
                by_date[d].append(r)
        trend = []
        for d in sorted(by_date.keys()):
            daily = self._calc(by_date[d])
            daily["date"] = d
            trend.append(daily)
        return trend

    def by_account(self, days: Optional[int] = None) -> list:
        """账户维度对比：按账户聚合全部指标"""
        records = self._filter(days=days)
        by_acc = defaultdict(list)
        for r in records:
            acc = r.get("mapped", {}).get("account")
            if acc:
                by_acc[acc].append(r)
        result = []
        for acc, recs in sorted(by_acc.items()):
            m = self._calc(recs)
            m["account"] = acc
            m["day_count"] = len({r.get("mapped", {}).get("date") for r in recs})
            m["record_count"] = len(recs)
            result.append(m)
        return result

    # ==================== 信号规则引擎（Phase3） ====================

    @property
    def _rule_state_file(self) -> Path:
        return settings.audit_rule_state_file

    def _load_rule_state(self) -> dict:
        """加载用户规则启用状态（持久化），与 spec 默认合并"""
        saved = {}
        if self._rule_state_file.exists():
            try:
                saved = json.loads(self._rule_state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                saved = {}
        rules = settings.audit_signal_rules
        state = {}
        for rid, rcfg in rules.items():
            default = bool(rcfg.get("default_enabled", True))
            state[rid] = saved.get(rid, default)
        return state

    def _save_rule_state(self, state: dict):
        self._rule_state_file.parent.mkdir(parents=True, exist_ok=True)
        self._rule_state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get_rule_states(self) -> dict:
        """获取全部信号规则定义 + 启用状态"""
        rules = settings.audit_signal_rules
        state = self._load_rule_state()
        result = {}
        for rid, rcfg in rules.items():
            result[rid] = {
                "id": rid,
                "name": rcfg.get("name", rid),
                "description": rcfg.get("description", ""),
                "category": rcfg.get("category", "hint"),
                "enabled": state.get(rid, True),
                "default_enabled": bool(rcfg.get("default_enabled", True)),
            }
        return result

    def set_rule_state(self, rule_id: str, enabled: bool) -> dict:
        """设置单个规则启用状态"""
        rules = settings.audit_signal_rules
        if rule_id not in rules:
            raise ValueError(f"未知规则: {rule_id}")
        state = self._load_rule_state()
        state[rule_id] = bool(enabled)
        self._save_rule_state(state)
        return {"ok": True, "rule_id": rule_id, "enabled": bool(enabled)}

    def reset_rule_states(self) -> dict:
        """重置全部规则为默认启用"""
        self._save_rule_state({})
        return {"ok": True}

    def _is_rule_enabled(self, rule_id: str) -> bool:
        state = self._load_rule_state()
        return state.get(rule_id, True)

    # ---- 统计工具（MAD 抗离群） ----
    @staticmethod
    def _mad_zscore(values: list, x: float) -> float:
        """计算 x 相对 values 的稳健 z-score（中位数 + MAD）
        z = (x - median) / (1.4826 * MAD)
        """
        if not values:
            return 0.0
        median = statistics.median(values)
        if len(values) >= 2:
            deviations = [abs(v - median) for v in values]
            mad = statistics.median(deviations)
        else:
            mad = 0.0
        if mad == 0:
            # MAD 为 0（值都相等）时，用均值 + 小 epsilon 兜底
            mean = sum(values) / len(values)
            if mean == 0:
                return 0.0
            scale = max(mean * 0.05, 1e-9)
        else:
            scale = 1.4826 * mad
        return (x - median) / scale

    def _mk_signal(self, severity, category, title, description, suggestion="",
                   account=None, date=None, impact_amount=0.0, impact_desc="",
                   metrics=None) -> dict:
        """构造统一 schema 的信号"""
        return {
            "severity": severity,
            "category": category,
            "title": title,
            "description": description,
            "suggestion": suggestion,
            "account": account,
            "date": date,
            "impact": {"amount": round(impact_amount, 2), "desc": impact_desc},
            "metrics": metrics or {},
        }

    def detect_signals(self, records: Optional[list] = None) -> list:
        """信号引擎主入口：合并 6 类规则信号（参照 Claude-ads + marqops 业界实践）
        统一 schema：severity / category / impact / metrics
        规则启用状态可配置（默认全部启用，健康评分默认启用）
        """
        records = self._filter() if records is None else records
        if not records:
            return [self._mk_signal(
                SEV_LOW, "hint", "暂无投放数据",
                "上传 CSV/JSON 数据或生成示例数据后开始审计",
                suggestion="前往「数据导入」上传投放数据",
                impact_desc="无数据", metrics={"record_count": 0},
            )]
        signals = []
        rules = settings.audit_signal_rules
        state = self._load_rule_state()

        # 全局聚合
        total = self._calc(records)
        dates_all = sorted({r.get("mapped", {}).get("date", "") for r in records})

        # ---- 提示类：样本不足 / 曝光不足 ----
        if state.get("small_sample", True):
            cfg = rules.get("small_sample", {})
            min_days = int(cfg.get("min_sample_days", 14))
            if len(dates_all) < min_days:
                signals.append(self._mk_signal(
                    SEV_LOW, "hint", "数据样本不足",
                    f"仅有 {len(dates_all)} 天数据，趋势分析参考价值有限",
                    "建议补充至少 14 天的投放数据",
                    impact_desc="统计可靠性不足",
                    metrics={"days": len(dates_all), "min_days": min_days},
                ))
        if state.get("low_impressions", True):
            cfg = rules.get("low_impressions", {})
            min_imp = int(cfg.get("min_impressions", 10000))
            if total["impressions"] < min_imp:
                signals.append(self._mk_signal(
                    SEV_LOW, "hint", "曝光量不足",
                    f"总曝光 {total['impressions']:,}，样本可能不足以支撑可靠结论",
                    "建议补充更长周期或更多账户的数据",
                    impact_desc="样本量偏小",
                    metrics={"impressions": total["impressions"], "min": min_imp},
                ))

        # ---- 按账户时间序列（花费突增 / CTR 骤降 / 连续无转化） ----
        for acc, acc_recs in self._group_by_account(records).items():
            dates = sorted({r.get("mapped", {}).get("date", "") for r in acc_recs})
            day_map = {d: self._calc([r for r in acc_recs if r.get("mapped", {}).get("date") == d]) for d in dates}

            # 花费突增（MAD + 同周几季节性基线）
            if state.get("spend_surge", True):
                signals += self._detect_spend_surge(acc, dates, day_map, rules.get("spend_surge", {}))

            # CTR 骤降（MAD + 同周几 + 曝光下限）
            if state.get("ctr_drop", True):
                signals += self._detect_ctr_drop(acc, dates, day_map, rules.get("ctr_drop", {}))

            # 连续花费无转化（数据质量分流：追踪中断 vs 业务下滑）
            if state.get("no_conversion", True):
                signals += self._detect_no_conversion(acc, dates, day_map, rules.get("no_conversion", {}))

        # ---- 账户对比（ROAS 过低 / CPA 过高） ----
        if state.get("roas_low", True):
            signals += self._detect_roas_low(records, total, rules.get("roas_low", {}))
        if state.get("cpa_high", True):
            signals += self._detect_cpa_high(records, total, rules.get("cpa_high", {}))

        # ---- 排序：severity + impact 金额降序 ----
        sev_order = {SEV_CRITICAL: 0, SEV_HIGH: 1, SEV_MEDIUM: 2, SEV_LOW: 3}
        signals.sort(key=lambda s: (sev_order.get(s["severity"], 9), -s["impact"]["amount"]))
        return signals

    # ---- 各规则检测实现 ----
    def _baseline_values(self, dates: list, day_map: dict, field: str,
                         weekday_window: int = 0) -> list:
        """构建基线值列表：weekday_window>0 时取同周几历史，否则取连续窗口历史"""
        # 返回与 dates 等长的「截至当日的历史基线窗口」较复杂；简化：
        # 调用方传过来的是全序列，这里直接返回全量历史值（不含最后一天）
        values = [day_map[d][field] for d in dates]
        return values

    def _detect_spend_surge(self, acc, dates, day_map, cfg) -> list:
        """花费突增：窗口累计法（业界实践）
        最近 N 天（duration_days）累计花费 vs 历史日均中位数 × N：
          - 累计超额 = 窗口累计 - 历史日均 × 窗口
          - 触发：超额比例 ≥ min_change_pct 且 超额金额 ≥ min_impact
        非重叠窗口去重：同一账户检测到一次后跳过 window 天，避免滑动窗口重复报警。
        """
        signals = []
        min_pct = float(cfg.get("min_change_pct", 50.0))
        min_impact = float(cfg.get("min_impact_amount", 2000.0))
        window = max(int(cfg.get("duration_days", 2)), 1)
        last_trigger_idx = -window  # 上次触发位置（非重叠窗口）
        for i in range(len(dates)):
            if i < window:
                continue
            # 跳过上次触发附近的窗口（非重叠去重）
            if i - last_trigger_idx < window:
                continue
            # 最近 window 天窗口（含当日）
            cur_total = sum(day_map[x]["spend"] for x in dates[i - window + 1:i + 1])
            # 窗口之前的历史日均（中位数，抗离群）
            hist = [day_map[x]["spend"] for x in dates[:i - window + 1]]
            if not hist:
                continue
            median_daily = statistics.median(hist)
            if median_daily <= 0:
                continue
            expected = median_daily * window
            excess = cur_total - expected
            pct_change = excess / expected * 100
            if pct_change < min_pct or excess < min_impact:
                continue
            ratio = cur_total / expected
            severity = SEV_HIGH if ratio >= 2.0 else SEV_MEDIUM
            signals.append(self._mk_signal(
                severity, "surge", f"花费突增（{ratio:.1f} 倍）",
                f"「{acc}」最近 {window} 天（{dates[i - window + 1]}~{dates[i]}）花费 ¥{cur_total:,.2f}，"
                f"高于历史日均 ¥{median_daily:,.2f} × {window} 天预期的 {pct_change:.0f}%",
                "检查是否误配预算/出价、流量质量下降或异常点击",
                account=acc, date=dates[i], impact_amount=excess,
                impact_desc="预估超额花费",
                metrics={"ratio": round(ratio, 2), "magnitude": f"{pct_change:.0f}%",
                         "window_days": window, "excess": round(excess, 2)},
            ))
            last_trigger_idx = i
        return signals

    def _detect_ctr_drop(self, acc, dates, day_map, cfg) -> list:
        """CTR 骤降：窗口累计法（业界实践）
        最近 N 天窗口 CTR（累计点击/累计曝光）vs 历史窗口 CTR 中位数：
          触发条件：下降比例 ≥ min_change_pct 且 曝光足够 且 预估损失 ≥ min_impact
        非重叠窗口去重。
        """
        signals = []
        min_pct = float(cfg.get("min_change_pct", 40.0))
        min_imp = float(cfg.get("min_impressions", 10000))
        min_impact = float(cfg.get("min_impact_amount", 1000.0))
        window = max(int(cfg.get("duration_days", 2)), 1)
        last_trigger_idx = -window
        for i in range(len(dates)):
            if i < window:
                continue
            if i - last_trigger_idx < window:
                continue
            # 窗口累计点击/曝光 → 窗口 CTR
            win_dates = dates[i - window + 1:i + 1]
            win_clicks = sum(day_map[x]["clicks"] for x in win_dates)
            win_imp = sum(day_map[x]["impressions"] for x in win_dates)
            win_spend = sum(day_map[x]["spend"] for x in win_dates)
            if win_imp < min_imp:
                continue
            win_ctr = win_clicks / win_imp * 100 if win_imp > 0 else 0
            if win_ctr <= 0:
                continue
            # 历史窗口 CTR（每个前 window 天窗口计算一次，取中位数）
            hist_windows = []
            for j in range(window, i - window + 1):
                h_dates = dates[j - window + 1:j + 1]
                h_clicks = sum(day_map[x]["clicks"] for x in h_dates)
                h_imp = sum(day_map[x]["impressions"] for x in h_dates)
                if h_imp >= min_imp:
                    hist_windows.append(h_clicks / h_imp * 100)
            if not hist_windows:
                continue
            median_ctr = statistics.median(hist_windows)
            if median_ctr <= 0:
                continue
            pct_drop = (median_ctr - win_ctr) / median_ctr * 100
            if pct_drop < min_pct:
                continue
            # 预估损失 = 曝光不变时损失的点击 × 平均 CPC（曝光加权，避免花费随点击下降而低估）
            # 损失点击 = win_imp × (median_ctr - win_ctr) / 100
            lost_clicks = win_imp * (median_ctr - win_ctr) / 100
            avg_cpc = win_spend / win_clicks if win_clicks > 0 else 0
            impact = lost_clicks * avg_cpc
            if impact < min_impact:
                continue
            severity = SEV_HIGH if pct_drop >= min_pct * 2 else SEV_MEDIUM
            signals.append(self._mk_signal(
                severity, "decay", f"点击率骤降（{pct_drop:.0f}%）",
                f"「{acc}」最近 {window} 天（{win_dates[0]}~{win_dates[-1]}）窗口 CTR {win_ctr:.2f}%，"
                f"低于历史窗口 CTR 中位 {median_ctr:.2f}% 达 {pct_drop:.0f}%",
                "检查素材疲劳、受众定向变化或展示位置质量",
                account=acc, date=win_dates[-1], impact_amount=impact,
                impact_desc="预估因 CTR 下降损失",
                metrics={"magnitude": f"{pct_drop:.0f}%", "win_ctr": round(win_ctr, 2),
                         "median_ctr": round(median_ctr, 2), "window_days": window},
            ))
            last_trigger_idx = i
        return signals

    def _detect_no_conversion(self, acc, dates, day_map, cfg) -> list:
        """连续花费无转化：数据质量分流（追踪中断 vs 业务下滑）"""
        signals = []
        no_conv_days = int(cfg.get("no_conversion_days", 3))
        min_impact = float(cfg.get("min_impact_amount", 500.0))
        imp_ratio = float(cfg.get("tracking_break_imp_ratio", 0.7))
        streak = 0
        for d in dates:
            dm = day_map[d]
            if dm["spend"] > 0 and dm["conversions"] == 0:
                streak += 1
                if streak == no_conv_days:
                    impact = dm["spend"]
                    # 分流：检查曝光是否相对历史正常（正常 → 追踪中断；下降 → 业务下滑）
                    imp_now = dm["impressions"]
                    idx = dates.index(d)
                    hist_imp = [day_map[x]["impressions"] for x in dates[:idx]] if idx > 0 else []
                    if hist_imp:
                        median_imp = statistics.median(hist_imp)
                        is_tracking_break = (median_imp > 0 and imp_now >= median_imp * imp_ratio)
                    else:
                        is_tracking_break = True  # 无历史则归为数据质量
                    if impact < min_impact and not is_tracking_break:
                        streak = 0
                        continue
                    if is_tracking_break:
                        signals.append(self._mk_signal(
                            SEV_HIGH, "data_quality", "连续花费无转化（疑似追踪中断）",
                            f"「{acc}」连续 {streak} 天有花费但转化量为 0，曝光 {imp_now:,} 相对历史正常，可能为转化追踪中断而非业务下滑",
                            "检查 Pixel/转化 API/落地页埋点是否正常",
                            account=acc, date=d, impact_amount=impact,
                            impact_desc="连续无转化期间的累计花费",
                            metrics={"duration_days": streak, "impressions": imp_now,
                                     "tracking_break": True},
                        ))
                    else:
                        signals.append(self._mk_signal(
                            SEV_HIGH, "decay", "连续花费无转化（业务下滑）",
                            f"「{acc}」连续 {streak} 天有花费但转化量为 0，曝光同步下降",
                            "暂停该账户/系列并排查受众、素材与落地页",
                            account=acc, date=d, impact_amount=impact,
                            impact_desc="连续无转化期间的累计花费",
                            metrics={"duration_days": streak, "impressions": imp_now,
                                     "tracking_break": False},
                        ))
            else:
                streak = 0
        return signals

    def _detect_roas_low(self, records, total, cfg) -> list:
        """ROAS 过低：账户维度，持续低于警戒线"""
        signals = []
        roas_warn = float(cfg.get("roas_warn_below", 1.0))
        min_impact = float(cfg.get("min_impact_amount", 1000.0))
        for acc, acc_recs in self._group_by_account(records).items():
            m = self._calc(acc_recs)
            if m["spend"] > 0 and m["roas"] < roas_warn:
                impact = m["spend"] * (roas_warn - m["roas"])  # 与警戒线差距的预估浪费
                if impact < min_impact:
                    continue
                signals.append(self._mk_signal(
                    SEV_HIGH if m["spend"] > 5000 else SEV_MEDIUM,
                    "inefficiency", "投产比低于警戒线",
                    f"「{acc}」ROAS {m['roas']} < {roas_warn}，花费 ¥{m['spend']:,.2f} 未产生足够转化价值",
                    "评估是否暂停该账户或大幅调整预算分配",
                    account=acc, impact_amount=impact,
                    impact_desc="相对警戒线的预估投入损失",
                    metrics={"roas": m["roas"], "spend": m["spend"], "warn_below": roas_warn},
                ))
        return signals

    def _detect_cpa_high(self, records, total, cfg) -> list:
        """CPA 过高：账户 CPA 显著高于整体均值"""
        signals = []
        ratio = float(cfg.get("cpa_surge_ratio", 2.0))
        min_impact = float(cfg.get("min_impact_amount", 0.0))
        total_cpa = total["cpa"]
        if total_cpa <= 0:
            return signals
        for acc, acc_recs in self._group_by_account(records).items():
            m = self._calc(acc_recs)
            if m["cpa"] > total_cpa * ratio and m["conversions"] > 0:
                impact = (m["cpa"] - total_cpa) * m["conversions"]  # 超额成本 × 转化数
                if impact < min_impact:
                    continue
                signals.append(self._mk_signal(
                    SEV_MEDIUM, "inefficiency", "获客成本过高",
                    f"「{acc}」CPA ¥{m['cpa']:,.2f}，为整体均值（¥{total_cpa:,.2f}）的 {m['cpa'] / total_cpa:.1f} 倍",
                    "拆分优化该账户的关键词/素材/出价策略",
                    account=acc, impact_amount=impact,
                    impact_desc="相对整体均值的超额获客成本",
                    metrics={"cpa": m["cpa"], "avg_cpa": total_cpa, "ratio": round(m["cpa"] / total_cpa, 1)},
                ))
        return signals

    # ---- 兼容旧 API ----
    def detect_anomalies(self, records: Optional[list] = None) -> list:
        """兼容旧接口：调用新信号引擎，映射旧字段名（type/suggestion）"""
        signals = self.detect_signals(records)
        result = []
        for s in signals:
            result.append({
                "severity": s["severity"],
                "type": s["category"],
                "title": s["title"],
                "description": s["description"],
                "suggestion": s["suggestion"],
                "date": s["date"],
                "account": s["account"],
            })
        return result

    def _health_score(self, anomalies: list) -> dict:
        """健康评分（参照 Claude-ads：基础 100，按严重度扣分，A/B/C/D 分级）"""
        cfg = settings.audit_health
        score = 100
        for a in anomalies:
            score += int(cfg.get(a["severity"], 0))
        score = max(0, min(100, score))
        grades = cfg.get("grades", ["A", "B", "C", "D"])
        thresholds = cfg.get("grade_thresholds", [90, 75, 60, 0])
        grade = grades[0]
        for g, t in zip(grades, thresholds):
            if score >= t:
                grade = g
                break
        return {"score": score, "grade": grade}

    # ==================== 工具 ====================
    @staticmethod
    def _mk(severity, type_, title, description, suggestion, date=None, account=None) -> dict:
        """构造异常发现项"""
        return {
            "severity": severity,
            "type": type_,
            "title": title,
            "description": description,
            "suggestion": suggestion,
            "date": date,
            "account": account,
        }

    @staticmethod
    def _group_by_account(records: list) -> dict:
        by_acc = defaultdict(list)
        for r in records:
            acc = r.get("mapped", {}).get("account")
            if acc:
                by_acc[acc].append(r)
        return dict(by_acc)

    def _filter(self, account: Optional[str] = None, days: Optional[int] = None) -> list:
        """按账户与最近 N 天过滤记录（基于 mapped 域）"""
        records = self._load()
        if account:
            records = [r for r in records if r.get("mapped", {}).get("account") == account]
        if days:
            cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
            records = [r for r in records if r.get("mapped", {}).get("date", "") >= cutoff]
        return records

    # ==================== 示例数据生成 ====================
    def generate_sample(self) -> dict:
        """生成示例投放数据（sample=true 标记，用于演示/测试）
        注入可控异常模式，便于直观验证异常检测：
          - 「信息流-素材测试」某日花费突增 3 倍+
          - 「海外-Meta」末段 CTR 骤降
          - 「搜索-精准」末段连续无转化
        """
        cfg = settings.audit_sample
        accounts = cfg.get("accounts", ["主账户-品牌", "搜索-精准", "信息流-素材测试", "海外-Meta"])
        days = int(cfg.get("days", 30))
        base_imp = float(cfg.get("base_impressions", 80000))
        base_spend = float(cfg.get("base_spend", 800))
        min_conv = int(cfg.get("min_conversions", 4))

        rnd = random.Random(20260805)  # 固定种子，保证可复现
        records = []
        end = date.today()
        for acc_idx, acc in enumerate(accounts):
            acc_imp = base_imp * (0.5 + 0.5 * acc_idx)  # 不同账户规模
            acc_spend = base_spend * (0.6 + 0.4 * acc_idx)
            for i in range(days):
                d = end - timedelta(days=days - 1 - i)
                # 周末/工作日波动 + 随机噪声
                weekday_factor = 1.15 if d.weekday() >= 5 else 1.0
                impressions = int(acc_imp * weekday_factor * rnd.uniform(0.75, 1.25))
                ctr = rnd.uniform(0.8, 2.2) / 100  # 0.8% ~ 2.2%
                clicks = int(impressions * ctr)
                cpc = rnd.uniform(1.2, 3.5)
                spend = round(clicks * cpc, 2)
                # 转化率 2% ~ 8%，保底
                cvr = rnd.uniform(0.02, 0.08)
                conversions = max(min_conv, int(clicks * cvr))
                conversion_value = round(conversions * rnd.uniform(30, 90), 2)

                # ---- 异常注入 ----
                # 1) 信息流-素材测试：第 18-20 天前后花费突增（预算误配，转化未同步）
                if acc == "信息流-素材测试" and 18 <= i <= 20:
                    spend = round(spend * 5.0, 2)  # 确定性 5 倍，确保越过 3 倍阈值
                    conversion_value = round(conversion_value * 0.4, 2)  # 价值没跟上
                # 2) 海外-Meta：最后 4 天 CTR 骤降（素材疲劳）
                if acc == "海外-Meta" and i >= days - 4:
                    clicks = max(2, int(clicks * 0.4))
                    spend = round(clicks * cpc, 2)
                    conversions = max(0, int(conversions * 0.3))
                # 3) 搜索-精准：最后 3 天花费保留但转化归零（转化追踪/落地页问题）
                if acc == "搜索-精准" and i >= days - 3:
                    conversions = 0
                    conversion_value = 0

                records.append({
                    "raw": {
                        "account": acc,
                        "date": d.isoformat(),
                        "impressions": impressions,
                        "clicks": clicks,
                        "conversions": conversions,
                        "spend": spend,
                        "conversion_value": conversion_value,
                    },
                    "mapped": {
                        "account": acc,
                        "date": d.isoformat(),
                        "impressions": impressions,
                        "clicks": clicks,
                        "conversions": conversions,
                        "spend": spend,
                        "conversion_value": conversion_value,
                    },
                    "tags": {},
                    "sample": True,
                })
        with self._lock:
            self._save(records)
        log_collector.info(EVENT_SYSTEM, f"已生成审计示例数据: {len(records)} 条 / {len(accounts)} 账户 / {days} 天")
        return {"ok": True, "imported": len(records), "sample": True}

    # ==================== 标签库管理（Phase2） ====================
    @property
    def _tag_lib_file(self) -> Path:
        return settings.audit_tag_lib_file

    def _load_tag_lib(self) -> dict:
        """加载用户自定义标签库（覆盖/扩展 spec 预设）"""
        if self._tag_lib_file.exists():
            try:
                return json.loads(self._tag_lib_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        # 初始化：用 spec 预设标签组
        lib = {"groups": settings.audit_tag_groups}
        return lib

    def _save_tag_lib(self, lib: dict):
        self._tag_lib_file.parent.mkdir(parents=True, exist_ok=True)
        self._tag_lib_file.write_text(json.dumps(lib, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_tag_library(self) -> dict:
        """获取标签库（预设 + 用户自定义合并）"""
        return self._load_tag_lib()

    def add_tag_to_group(self, group_id: str, tag: str) -> dict:
        """向标签组添加一个标签值"""
        lib = self._load_tag_lib()
        for g in lib.get("groups", []):
            if g["id"] == group_id:
                if tag not in g.get("tags", []):
                    g.setdefault("tags", []).append(tag)
                    self._save_tag_lib(lib)
                return {"ok": True, "group": g}
        return {"ok": False, "error": f"标签组 {group_id} 不存在"}

    def add_tag_group(self, group_id: str, name: str, description: str = "") -> dict:
        """新增一个标签组"""
        lib = self._load_tag_lib()
        for g in lib.get("groups", []):
            if g["id"] == group_id:
                return {"ok": False, "error": f"标签组 {group_id} 已存在"}
        lib.setdefault("groups", []).append({
            "id": group_id, "name": name, "description": description, "tags": []
        })
        self._save_tag_lib(lib)
        return {"ok": True, "group": lib["groups"][-1]}

    # ==================== 行级打标（Phase2） ====================
    def batch_tag(self, row_indices: list, group_id: str, tags: list, mode: str = "add") -> dict:
        """批量打标：对指定行追加/替换/移除标签
        row_indices: 行索引列表（0-based，对应 _load() 返回的列表索引）
        group_id: 标签组 ID
        tags: 标签值列表
        mode: add(追加) / replace(替换) / remove(移除指定标签) / clear(清空该组)
        """
        with self._lock:
            records = self._load()
            tagged_count = 0
            for idx in row_indices:
                if 0 <= idx < len(records):
                    rec = records[idx]
                    rec.setdefault("tags", {})
                    if mode == "clear":
                        rec["tags"].pop(group_id, None)
                    elif mode == "remove":
                        existing = rec["tags"].get(group_id, [])
                        rec["tags"][group_id] = [t for t in existing if t not in tags]
                        if not rec["tags"][group_id]:
                            rec["tags"].pop(group_id)
                    elif mode == "replace":
                        rec["tags"][group_id] = list(tags)
                    else:  # add
                        existing = rec["tags"].get(group_id, [])
                        for t in tags:
                            if t not in existing:
                                existing.append(t)
                        rec["tags"][group_id] = existing
                    tagged_count += 1
            self._save(records)
        log_collector.info(EVENT_CONFIG, f"批量打标: {tagged_count} 行 / 组={group_id} / 模式={mode}", {
            "row_count": tagged_count, "group": group_id, "tags": tags, "mode": mode,
        })
        return {"ok": True, "tagged": tagged_count, "group_id": group_id, "mode": mode}

    def get_records_with_tags(self, account: Optional[str] = None, days: Optional[int] = None,
                              limit: int = 500, offset: int = 0) -> dict:
        """获取原始记录（含 raw + tags），分页"""
        records = self._filter(account=account, days=days)
        total = len(records)
        # 分页
        page = records[offset:offset + limit]
        # 返回 raw + tags + 索引（供前端多选用）
        result = []
        for i, r in enumerate(page):
            result.append({
                "index": offset + i,
                "raw": r.get("raw", {}),
                "mapped": r.get("mapped", {}),
                "tags": r.get("tags", {}),
                "sample": r.get("sample", False),
            })
        return {"records": result, "total": total, "limit": limit, "offset": offset}


# 全局单例
audit_service = AuditService()
