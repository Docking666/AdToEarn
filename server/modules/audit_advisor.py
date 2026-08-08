"""
Phase11: 基于 CSV 的决策支持建议引擎（audit_advisor）

业界共识：统计层出依据 → 规则层出建议 → 人决策。
本模块生成「建议卡」：可解释、带置信度/样本量/有效期/数据缺口，
不做黑箱判断。数据基于用户上传 CSV（无平台 API、无连续性）：
  - 截面建议（单快照即可）：素材显著性 / 账户 ROAS 排名 / 占比异常
  - 序列建议（≥2 快照解锁）：环比 / 突变 / 素材衰减 / 边际递减
  - 策略建议（确定性规则）：预算/出价调整卡（人肉同步平台）

素材显著性用 Beta-Binomial 贝叶斯检验（math.lgamma 手写，规避 scipy 依赖）。
"""
import math
import random
from datetime import datetime, timedelta
from typing import Optional

from ..config import settings
from .app_logger import log_collector, EVENT_CONFIG

# 建议类型
TYPE_SECTION = "截面"
TYPE_SEQUENCE = "序列"
TYPE_STRATEGY = "策略"

# 置信度
CONF_HIGH = "高"
CONF_MED = "中"
CONF_LOW = "低"


def _beta_binomial_superior(a_succ: int, a_fail: int, b_succ: int, b_fail: int,
                            n_samples: int = 40000, seed: int = 42) -> dict:
    """Beta-Binomial 近似：P(素材A 转化率 > 素材B)
    用 Beta(α,β) 后验（α=succ+1, β=fail+1）蒙特卡洛采样近似，
    返回 {p_a_gt_b, ci_lo, ci_hi, diff_expected}
    """
    rnd = random.Random(seed)
    total = a_succ + a_fail + b_succ + b_fail
    if total < 20:  # 样本太少，蒙特卡洛不稳定
        return {"p_a_gt_b": 0.5, "ci_lo": 0.0, "ci_hi": 0.0, "diff_expected": 0.0, "reliable": False}
    a_alpha = a_succ + 1
    a_beta = a_fail + 1
    b_alpha = b_succ + 1
    b_beta = b_fail + 1

    def _beta_sample(alpha: float, beta: float):
        # 用 Gamma 近似采样（math.lgamma 无直接采样，用正态近似简化）
        # 更精确做法：Gamma(α,1)/(Gamma(α,1)+Gamma(β,1))，这里用均值±波动近似
        mean = alpha / (alpha + beta)
        var = (alpha * beta) / ((alpha + beta) ** 2 * (alpha + beta + 1))
        return max(0.0, min(1.0, rnd.gauss(mean, math.sqrt(var))))

    wins = 0
    diffs = []
    for _ in range(n_samples):
        pa = _beta_sample(a_alpha, a_beta)
        pb = _beta_sample(b_alpha, b_beta)
        if pa > pb:
            wins += 1
        diffs.append(pa - pb)
    diffs.sort()
    return {
        "p_a_gt_b": wins / n_samples,
        "ci_lo": diffs[int(n_samples * 0.05)],
        "ci_hi": diffs[int(n_samples * 0.95)],
        "diff_expected": sum(diffs) / n_samples,
        "reliable": True,
    }


class AuditAdvisor:
    """决策建议引擎（基于当前数据 + 历史快照）"""

    def __init__(self):
        self._cfg = None

    def cfg(self) -> dict:
        if self._cfg is None:
            self._cfg = (settings.audit_advisor or {})
        return self._cfg

    # ==================== 主入口 ====================
    def generate_suggestions(self, account: Optional[str] = None, days: Optional[int] = None) -> dict:
        """生成建议卡列表
        :return {"suggestions": [...], "snapshot_count": N, "meta": {...}}
        """
        from .audit import audit_service

        suggestions = []
        records = audit_service._filter(account=account, days=days)
        snapshots = audit_service.list_snapshots()
        snap_count = len(snapshots)
        now = datetime.now()

        if not records:
            return {"suggestions": [], "snapshot_count": snap_count,
                    "meta": {"data_gap": "暂无审计数据，请先导入 CSV 或生成示例数据"}}

        # ---- 截面建议（单快照即可） ----
        if records:
            suggestions += self._section_creative(records)
            suggestions += self._section_account_roas(records)
            suggestions += self._section_share(records)

        # ---- 序列建议（≥2 快照解锁） ----
        if snap_count >= 1:
            prev = audit_service._load_snapshot_records(snapshots[0]["id"]) if snapshots else []
            if prev:
                suggestions += self._sequence_compare(records, prev, snapshots[0])
        if snap_count >= 2:
            suggestions += self._strategy_budget(records, snapshots)

        # 有效期与上限
        max_s = int(self.cfg().get("max_suggestions", 8))
        valid_days = int(self.cfg().get("valid_until_days", 14))
        for s in suggestions:
            s["id"] = f"s{abs(hash((s['type'], s['title'], s['action'])) ) % 99999:05d}"
            s["valid_until"] = (now + timedelta(days=valid_days)).date().isoformat()
            s["snapshot_count"] = snap_count

        log_collector.info(EVENT_CONFIG, f"决策建议生成: {len(suggestions)} 条（快照 {snap_count} 份）", {
            "suggestions": len(suggestions), "snapshots": snap_count,
        })
        return {
            "suggestions": suggestions[:max_s],
            "snapshot_count": snap_count,
            "meta": {
                "has_creative": any(r.get("mapped", {}).get("creative") for r in records),
                "data_gap": "" if records else "暂无审计数据",
            },
        }

    # ==================== 截面：素材显著性 ====================
    def _section_creative(self, records: list) -> list:
        """素材 CTR/CVR 显著性：按 creative 聚合，Beta-Binomial 检验，显著才建议"""
        cfg = self.cfg()
        min_sample = int(cfg.get("min_sample_creative", 200))
        creative_groups = {}
        for r in records:
            m = r.get("mapped", {})
            c = m.get("creative")
            if not c:
                continue
            g = creative_groups.setdefault(c, {"impressions": 0, "clicks": 0, "conversions": 0, "spend": 0.0, "conversion_value": 0.0})
            g["impressions"] += int(m.get("impressions", 0) or 0)
            g["clicks"] += int(m.get("clicks", 0) or 0)
            g["conversions"] += int(m.get("conversions", 0) or 0)
            g["spend"] += float(m.get("spend", 0) or 0)
            g["conversion_value"] += float(m.get("conversion_value", 0) or 0)
        if len(creative_groups) < 2:
            return []
        items = sorted(creative_groups.items(), key=lambda kv: kv[1]["impressions"], reverse=True)
        out = []
        # 取曝光最高的两组对比 CVR（转化是决策核心）
        a_name, a = items[0]
        b_name, b = items[1]
        if a["impressions"] < min_sample or b["impressions"] < min_sample:
            out.append({
                "type": TYPE_SECTION, "category": "creative", "title": "素材显著性检验（样本不足）",
                "action": "继续积累曝光数据后重试",
                "evidence": f"{a_name} {a['impressions']} 曝光 / {b_name} {b['impressions']} 曝光（需 ≥{min_sample}）",
                "confidence": CONF_LOW,
                "data_gap": f"素材曝光样本不足 {min_sample}，暂不输出显著性结论",
            })
            return out
        test = _beta_binomial_superior(a["conversions"], a["impressions"] - a["conversions"],
                                       b["conversions"], b["impressions"] - b["conversions"])
        a_cvr = a["conversions"] / a["impressions"] * 100 if a["impressions"] else 0
        b_cvr = b["conversions"] / b["impressions"] * 100 if b["impressions"] else 0
        if test["p_a_gt_b"] >= 0.85:
            conf = CONF_HIGH if test["p_a_gt_b"] >= 0.95 else CONF_MED
            out.append({
                "type": TYPE_SECTION, "category": "creative", "title": f"素材「{a_name}」转化率显著更高",
                "action": f"建议优先加量「{a_name}」，将「{b_name}」降为观察位",
                "evidence": (f"{a_name} CVR {a_cvr:.2f}% vs {b_name} CVR {b_cvr:.2f}%"
                             f"（P(前者更优)={test['p_a_gt_b']:.0%}，期望差异 {test['diff_expected']*100:+.2f}pp）"),
                "confidence": conf,
                "data_gap": "",
            })
        elif test["p_a_gt_b"] <= 0.15:
            out.append({
                "type": TYPE_SECTION, "category": "creative", "title": f"素材「{b_name}」转化率显著更高",
                "action": f"建议优先加量「{b_name}」，将「{a_name}」降为观察位",
                "evidence": (f"{b_name} CVR {b_cvr:.2f}% vs {a_name} CVR {a_cvr:.2f}%"
                             f"（P(后者更优)={1-test['p_a_gt_b']:.0%}）"),
                "confidence": CONF_MED,
                "data_gap": "",
            })
        else:
            out.append({
                "type": TYPE_SECTION, "category": "creative", "title": "素材 A/B 转化差异不显著",
                "action": "暂不建议调整素材权重，保持双素材并行观察",
                "evidence": (f"{a_name} CVR {a_cvr:.2f}% vs {b_name} CVR {b_cvr:.2f}%"
                             f"（P(前者更优)={test['p_a_gt_b']:.0%}，差异未达显著线）"),
                "confidence": CONF_HIGH,
                "data_gap": "",
            })
        return out

    # ==================== 截面：账户 ROAS 排名 ====================
    def _section_account_roas(self, records: list) -> list:
        cfg = self.cfg()
        roas_th = float(cfg.get("roas_threshold", 1.0))
        by_acct = {}
        for r in records:
            m = r.get("mapped", {})
            acct = m.get("account", "未分组账户")
            g = by_acct.setdefault(acct, {"spend": 0.0, "conversion_value": 0.0})
            g["spend"] += float(m.get("spend", 0) or 0)
            g["conversion_value"] += float(m.get("conversion_value", 0) or 0)
        if len(by_acct) < 1:
            return []
        ranked = sorted(by_acct.items(), key=lambda kv: (kv[1]["conversion_value"] / kv[1]["spend"]) if kv[1]["spend"] > 0 else 0, reverse=True)
        out = []
        for acct, g in ranked:
            if g["spend"] <= 0:
                continue
            roas = g["conversion_value"] / g["spend"]
            if roas < roas_th:
                out.append({
                    "type": TYPE_SECTION, "category": "account", "title": f"账户「{acct}」ROAS {roas:.2f} 低于阈值",
                    "action": f"建议核查该账户预算分配（当前花费 ¥{g['spend']:.0f}），可先降预算观察",
                    "evidence": f"ROAS {roas:.2f} < 阈值 {roas_th}，转化价值 ¥{g['conversion_value']:.0f}",
                    "confidence": CONF_MED,
                    "data_gap": "",
                })
                if len(out) >= 2:
                    break
        return out

    # ==================== 截面：占比异常 ====================
    def _section_share(self, records: list) -> list:
        cfg = self.cfg()
        share_th = float(cfg.get("spend_share_threshold", 40))
        total = sum(float(r.get("mapped", {}).get("spend", 0) or 0) for r in records)
        if total <= 0:
            return []
        by_acct = {}
        for r in records:
            m = r.get("mapped", {})
            acct = m.get("account", "未分组账户")
            by_acct[acct] = by_acct.get(acct, 0.0) + float(m.get("spend", 0) or 0)
        out = []
        for acct, spend in sorted(by_acct.items(), key=lambda kv: kv[1], reverse=True)[:3]:
            share = spend / total * 100
            if share >= share_th:
                roas = 0.0
                cv = sum(float(r.get("mapped", {}).get("conversion_value", 0) or 0)
                         for r in records if r.get("mapped", {}).get("account") == acct)
                roas = cv / spend if spend else 0
                if roas < float(cfg.get("roas_threshold", 1.0)):
                    out.append({
                        "type": TYPE_SECTION, "category": "share", "title": f"账户「{acct}」花费占比 {share:.0f}% 且 ROAS 偏低",
                        "action": "建议将部分预算向高 ROAS 账户平移，降低单账户集中风险",
                        "evidence": f"占比 {share:.0f}%（阈值 {share_th:.0f}%），ROAS {roas:.2f}",
                        "confidence": CONF_MED,
                        "data_gap": "",
                    })
        return out

    # ==================== 序列：环比 / 突变 / 衰减 ====================
    def _sequence_compare(self, records: list, prev_records: list, prev_meta: dict) -> list:
        cfg = self.cfg()
        out = []
        cur = self._agg(records)
        pre = self._agg(prev_records)
        if pre["spend"] <= 0:
            return out

        # 环比 spend / roas
        spend_chg = (cur["spend"] - pre["spend"]) / pre["spend"] * 100
        cur_roas = cur["conversion_value"] / cur["spend"] if cur["spend"] else 0
        pre_roas = pre["conversion_value"] / pre["spend"] if pre["spend"] else 0
        roas_chg = (cur_roas - pre_roas) / pre_roas * 100 if pre_roas else 0
        if abs(spend_chg) >= 20:
            direction = "上升" if spend_chg > 0 else "下降"
            out.append({
                "type": TYPE_SEQUENCE, "category": "delta", "title": f"花费较上期快照 {direction} {abs(spend_chg):.0f}%",
                "action": "建议核查预算调整/投放节奏是否与预期一致",
                "evidence": f"本期 ¥{cur['spend']:.0f} vs 上期 ¥{pre['spend']:.0f}（{prev_meta.get('imported_at', '')[:10]}）",
                "confidence": CONF_MED,
                "data_gap": "",
            })
        if roas_chg <= -20:
            out.append({
                "type": TYPE_SEQUENCE, "category": "delta", "title": f"ROAS 较上期快照下降 {abs(roas_chg):.0f}%",
                "action": "建议关注转化端（素材疲劳/人群饱和/落地页），暂缓加量",
                "evidence": f"本期 ROAS {cur_roas:.2f} vs 上期 {pre_roas:.2f}",
                "confidence": CONF_MED,
                "data_gap": "",
            })
        # 素材衰减：同素材跨快照 CTR 下降
        ctr_drop = float(cfg.get("ctr_drop_pct", 20))
        cur_ctr = {c: g["clicks"] / g["impressions"] if g["impressions"] else 0
                   for c, g in self._creative_agg(records).items()}
        pre_ctr = {c: g["clicks"] / g["impressions"] if g["impressions"] else 0
                   for c, g in self._creative_agg(prev_records).items()}
        for c in pre_ctr:
            if c in cur_ctr and pre_ctr[c] > 0 and cur_ctr[c] > 0:
                drop = (pre_ctr[c] - cur_ctr[c]) / pre_ctr[c] * 100
                if drop >= ctr_drop:
                    out.append({
                        "type": TYPE_SEQUENCE, "category": "creative", "title": f"素材「{c}」CTR 下降 {drop:.0f}%",
                        "action": "建议准备替换素材或更换展示位（素材疲劳迹象）",
                        "evidence": f"上期 CTR {pre_ctr[c]*100:.2f}% → 本期 {cur_ctr[c]*100:.2f}%",
                        "confidence": CONF_LOW,
                        "data_gap": "",
                    })
        # 边际递减预判：spend 增 + roas 降
        if spend_chg > 10 and roas_chg < -10:
            out.append({
                "type": TYPE_SEQUENCE, "category": "margin", "title": "边际递减信号：花费增而 ROAS 降",
                "action": "建议本轮见好就收，暂停追加预算，观察下期快照确认",
                "evidence": f"spend {spend_chg:+.0f}% / ROAS {roas_chg:+.0f}%",
                "confidence": CONF_MED,
                "data_gap": "",
            })
        return out

    # ==================== 策略：预算/出价建议卡 ====================
    def _strategy_budget(self, records: list, snapshots: list) -> list:
        cfg = self.cfg()
        roas_th = float(cfg.get("roas_threshold", 1.0))
        consec = int(cfg.get("consecutive_snapshots", 2))
        cut = int(cfg.get("budget_cut_pct", 30))
        out = []
        # 历史快照的账户 ROAS（含当前，按时间倒序）
        roas_history = [self._acct_roas(records)]
        for s in snapshots[:consec + 1]:
            roas_history.append(self._acct_roas(self._snap_records(s)))
        cur_roas_by_acct = roas_history[0]
        for acct, roas in cur_roas_by_acct.items():
            if roas >= roas_th:
                continue
            streak = 1
            for h in roas_history[1:]:
                if h.get(acct, roas) < roas_th:
                    streak += 1
                else:
                    break
            if streak >= consec:
                out.append({
                    "type": TYPE_STRATEGY, "category": "budget",
                    "title": f"账户「{acct}」连续 {streak} 期 ROAS<{roas_th}",
                    "action": (f"建议本周预算下调 {cut}%（人肉同步平台），并考虑出价从 OCPM 切换为 CPA 目标；"
                               f"若 2 期仍无改善则暂停"),
                    "evidence": f"近 {streak} 期快照 ROAS 均低于 {roas_th}（当前 {roas:.2f}）",
                    "confidence": CONF_HIGH if streak >= 3 else CONF_MED,
                    "data_gap": "",
                })
                if len(out) >= 2:
                    break
        return out

    # ==================== 工具方法 ====================
    @staticmethod
    def _agg(records: list) -> dict:
        spend = sum(float(r.get("mapped", {}).get("spend", 0) or 0) for r in records)
        cv = sum(float(r.get("mapped", {}).get("conversion_value", 0) or 0) for r in records)
        return {"spend": spend, "conversion_value": cv}

    @staticmethod
    def _creative_agg(records: list) -> dict:
        out = {}
        for r in records:
            m = r.get("mapped", {})
            c = m.get("creative")
            if not c:
                continue
            g = out.setdefault(c, {"impressions": 0, "clicks": 0})
            g["impressions"] += int(m.get("impressions", 0) or 0)
            g["clicks"] += int(m.get("clicks", 0) or 0)
        return out

    @staticmethod
    def _acct_roas(records: list) -> dict:
        by_acct = {}
        for r in records:
            m = r.get("mapped", {})
            acct = m.get("account", "未分组账户")
            g = by_acct.setdefault(acct, {"spend": 0.0, "cv": 0.0})
            g["spend"] += float(m.get("spend", 0) or 0)
            g["cv"] += float(m.get("conversion_value", 0) or 0)
        return {a: (g["cv"] / g["spend"] if g["spend"] else 0) for a, g in by_acct.items()}

    @staticmethod
    def _snap_records(snap: dict) -> list:
        return snap.get("records", [])


# 全局单例
audit_advisor = AuditAdvisor()
