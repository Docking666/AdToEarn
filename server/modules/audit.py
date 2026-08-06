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
        """投放数据总览：关键指标 + 健康评分 + 账户数"""
        records = self._filter(account=account, days=days)
        metrics = self._calc(records)
        anomalies = self.detect_anomalies(records)
        health = self._health_score(anomalies)
        accounts = len({r.get("mapped", {}).get("account") for r in records})
        metrics.update({
            "account_count": accounts,
            "day_count": len({r.get("mapped", {}).get("date") for r in records}),
            "record_count": len(records),
        })
        return {
            "metrics": metrics,
            "health": health,
            "anomaly_count": len(anomalies),
            "anomaly_by_severity": {
                sev: len([a for a in anomalies if a["severity"] == sev])
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

    # ==================== 异常检测 ====================
    def detect_anomalies(self, records: Optional[list] = None) -> list:
        """异常/风险检测（参照 Claude-ads 的 check 思路，输出分级发现项）
        维度：全局样本检查 + 按账户的时间序列检查（花费突增/CTR骤降/转化中断）
              + 账户对比检查（CPA 过高 / ROAS 过低）
        """
        records = self._filter() if records is None else records
        if not records:
            return [{
                "severity": SEV_LOW,
                "type": "no_data",
                "title": "暂无投放数据",
                "description": "上传 CSV/JSON 数据或生成示例数据后开始审计",
                "suggestion": "前往「数据导入」上传投放数据",
            }]
        anomalies = []
        anomaly_cfg = settings.audit_anomaly
        total = self._calc(records)
        dates_all = sorted({r.get("mapped", {}).get("date", "") for r in records})

        # ---- 全局样本检查 ----
        if total["impressions"] < int(anomaly_cfg.get("min_impressions", 10000)):
            anomalies.append(self._mk(
                SEV_LOW, "low_impressions", "曝光量不足",
                f"总曝光 {total['impressions']:,}，样本可能不足以支撑可靠结论",
                "建议补充更长周期或更多账户的数据",
            ))
        if len(dates_all) < int(anomaly_cfg.get("min_sample_days", 14)):
            anomalies.append(self._mk(
                SEV_LOW, "small_sample", "数据样本不足",
                f"仅有 {len(dates_all)} 天数据，趋势分析参考价值有限",
                "建议补充至少 14 天的投放数据",
            ))

        # ---- 按账户时间序列检查 ----
        surge_ratio = float(anomaly_cfg.get("spend_surge_ratio", 3.0))
        ctr_drop_ratio = float(anomaly_cfg.get("ctr_drop_ratio", 0.5))
        no_conv_days = int(anomaly_cfg.get("no_conversion_days", 3))

        for acc, acc_recs in self._group_by_account(records).items():
            dates = sorted({r.get("mapped", {}).get("date", "") for r in acc_recs})
            # 账户内每日聚合（跨账户隔离，避免被全局平均稀释）
            day_map = {d: self._calc([r for r in acc_recs if r.get("mapped", {}).get("date") == d]) for d in dates}
            prev_window = []  # 前 7 日指标列表

            for i, d in enumerate(dates):
                cur = day_map[d]
                if i >= 7:
                    prev = self._calc(prev_window)  # prev 为前 7 日累计，需换算日均值
                    prev_daily_spend = prev["spend"] / 7.0
                    prev_ctr = prev["ctr"]
                    # 花费突增（当日 vs 前 7 日日均）
                    if prev_daily_spend > 0 and cur["spend"] >= prev_daily_spend * surge_ratio:
                        anomalies.append(self._mk(
                            SEV_HIGH if cur["spend"] >= prev_daily_spend * surge_ratio * 1.5 else SEV_MEDIUM,
                            "spend_surge", f"花费突增 {cur['spend'] / prev_daily_spend:.1f} 倍",
                            f"「{acc}」{d} 花费 ¥{cur['spend']:,.2f}，为前 7 日均值（¥{prev_daily_spend:,.2f}/日）的 {cur['spend'] / prev_daily_spend:.1f} 倍",
                            "检查是否误配预算/出价、流量质量下降或异常点击",
                            date=d, account=acc,
                        ))
                    # CTR 骤降（需有足够曝光才可信）
                    if (prev_ctr > 0 and cur["ctr"] > 0
                            and cur["impressions"] >= int(anomaly_cfg.get("min_impressions", 10000))
                            and cur["ctr"] < prev_ctr * ctr_drop_ratio):
                        anomalies.append(self._mk(
                            SEV_MEDIUM, "ctr_drop", "点击率骤降",
                            f"「{acc}」{d} 点击率 {cur['ctr']}%，低于前 7 日均值（{prev_ctr}%）的 {int(ctr_drop_ratio * 100)}%",
                            "检查素材疲劳、受众定向变化或展示位置质量",
                            date=d, account=acc,
                        ))
                    prev_window.pop(0)
                prev_window.append(cur)

            # 连续花费无转化
            streak = 0
            for d in dates:
                dm = day_map[d]
                if dm["spend"] > 0 and dm["conversions"] == 0:
                    streak += 1
                    if streak == no_conv_days:
                        anomalies.append(self._mk(
                            SEV_HIGH, "no_conversion", "连续花费无转化",
                            f"账户「{acc}」连续 {streak} 天有花费但转化量为 0",
                            "暂停该账户/系列并排查落地页、受众与转化追踪",
                            date=d, account=acc,
                        ))
                else:
                    streak = 0

        # ---- 账户对比检查：CPA 过高 / ROAS 过低 ----
        cpa_surge = float(anomaly_cfg.get("cpa_surge_ratio", 2.0))
        roas_warn = float(anomaly_cfg.get("roas_warn_below", 1.0))
        total_cpa = total["cpa"]
        for acc, acc_recs in self._group_by_account(records).items():
            m = self._calc(acc_recs)
            if total_cpa > 0 and m["cpa"] > total_cpa * cpa_surge and m["conversions"] > 0:
                anomalies.append(self._mk(
                    SEV_MEDIUM, "cpa_high", "获客成本过高",
                    f"账户「{acc}」CPA ¥{m['cpa']:,.2f}，为整体均值（¥{total_cpa:,.2f}）的 {m['cpa'] / total_cpa:.1f} 倍",
                    "拆分优化该账户的关键词/素材/出价策略",
                    account=acc,
                ))
            if m["spend"] > 0 and m["roas"] < roas_warn:
                anomalies.append(self._mk(
                    SEV_HIGH if m["spend"] > 1000 else SEV_MEDIUM,
                    "roas_low", "投产比低于警戒线",
                    f"账户「{acc}」ROAS {m['roas']} < {roas_warn}，花费 ¥{m['spend']:,.2f} 未产生足够转化价值",
                    "评估是否暂停该账户或大幅调整预算分配",
                    account=acc,
                ))

        # 排序：critical > high > medium > low
        order = {SEV_CRITICAL: 0, SEV_HIGH: 1, SEV_MEDIUM: 2, SEV_LOW: 3}
        anomalies.sort(key=lambda a: (order.get(a["severity"], 9), a["title"]))
        return anomalies

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
