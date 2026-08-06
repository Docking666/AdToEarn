"""
字段映射引擎 (Field Mapper)
三层 fallback（业界主流"建议+确认"模式，0 LLM 成本）：
  1) 同义词字典精确匹配（预置中英文 + 用户扩展自动学习）
  2) rapidfuzz 模糊匹配（Levenshtein + Jaro-Winkler 混合评分）
  3) 人工兜底（前端高亮未映射字段，用户选择后自动写入字典学习）

设计参考：
  - Supermetrics：自动字段映射 + 用户可编辑预映射
  - Funnel Data Hub：no-code 工作台手动调映射，定义一次复用
  - @bernierllc/csv-mapper：同义词字典 + Jaro-Winkler/Levenshtein 模糊匹配
  - Jepto Unified Fields：跨平台字段归一表

数据流：
  CSV 表头 → match(表头) → 返回 {标准字段, 置信度, 来源(layer), 备选}
  用户纠正 → learn(原始列名, 标准字段) → 写入 user_synonyms 持久化
"""
import json
import threading
from pathlib import Path
from typing import Optional

from ..config import settings

# 置信度阈值
CONFIDENCE_EXACT = 0.95       # 同义词字典精确命中
CONFIDENCE_FUZZY_MIN = 0.85   # 模糊匹配下限（低于此值不自动匹配）

# 匹配来源层
LAYER_EXACT = "exact"          # 同义词字典精确
LAYER_FUZZY = "fuzzy"          # rapidfuzz 模糊
LAYER_USER = "user"            # 用户扩展字典（学习过的）
LAYER_NONE = "none"            # 未匹配


class FieldMapper:
    """字段映射引擎：CSV 列名 → 标准字段"""

    def __init__(self):
        self._lock = threading.Lock()
        self._map_file: Path = settings.audit_field_map_file
        # 用户扩展字典 + 持久化映射，加载一次缓存
        self._user_state = self._load_user_state()

    # ==================== 持久化 ====================
    def _load_user_state(self) -> dict:
        """加载用户扩展字典 + 显式映射配置"""
        if self._map_file.exists():
            try:
                return json.loads(self._map_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"user_synonyms": {}, "explicit_map": {}}

    def _save_user_state(self):
        """持久化用户扩展字典 + 显式映射"""
        self._map_file.parent.mkdir(parents=True, exist_ok=True)
        self._map_file.write_text(
            json.dumps(self._user_state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ==================== 同义词字典构建 ====================
    def _build_synonym_index(self) -> dict:
        """合并 spec 预置同义词 + spec 中的 user_synonyms 占位 + 运行时学习的用户扩展
        返回 {小写别名: 标准字段} 字典
        """
        cfg = settings.audit_field_map
        synonyms = cfg.get("synonyms", {})
        # spec 中的 user_synonyms（初始为空，向后兼容）
        spec_user = cfg.get("user_synonyms", {})
        # 运行时学习的用户扩展（持久化在 audit_field_map.json）
        runtime_user = self._user_state.get("user_synonyms", {})

        index = {}
        # 预置同义词
        for std_field, alias_list in synonyms.items():
            for alias in alias_list:
                index[alias.strip().lower()] = std_field
            index[std_field.lower()] = std_field
        # spec 内 user_synonyms
        for std_field, alias_list in spec_user.items():
            for alias in alias_list:
                index[alias.strip().lower()] = std_field
        # 运行时学习的用户扩展（最高优先级，可覆盖预置）
        for std_field, alias_list in runtime_user.items():
            for alias in alias_list:
                index[alias.strip().lower()] = std_field
        return index

    # ==================== 核心匹配 ====================
    def match(self, column_name: str) -> dict:
        """匹配单个列名 → 返回 {standard_field, confidence, layer, candidates}
        layer: exact / fuzzy / user / none
        """
        if not column_name or not column_name.strip():
            return {"standard_field": None, "confidence": 0, "layer": LAYER_NONE, "candidates": []}

        col = column_name.strip()
        col_lower = col.lower()

        # 显式映射（用户在 UI 上手动指定的，最高优先级）
        explicit = self._user_state.get("explicit_map", {})
        if col in explicit:
            std = explicit[col]
            return {
                "standard_field": std,
                "confidence": 1.0,
                "layer": LAYER_USER,
                "candidates": self._candidates(col_lower),
            }

        # 第 1 层：同义词字典精确匹配
        index = self._build_synonym_index()
        if col_lower in index:
            return {
                "standard_field": index[col_lower],
                "confidence": CONFIDENCE_EXACT,
                "layer": LAYER_EXACT,
                "candidates": [],
            }

        # 第 2 层：rapidfuzz 模糊匹配
        try:
            from rapidfuzz import fuzz, process
        except ImportError:
            # 兜底：用 difflib（标准库）
            import difflib
            best_score = 0
            best_field = None
            for alias, std in index.items():
                score = difflib.SequenceMatcher(None, col_lower, alias).ratio()
                if score > best_score:
                    best_score = score
                    best_field = std
            if best_score >= CONFIDENCE_FUZZY_MIN:
                return {
                    "standard_field": best_field,
                    "confidence": round(best_score, 3),
                    "layer": LAYER_FUZZY,
                    "candidates": [],
                }
        else:
            # rapidfuzz.process.extractOne 一次比对所有别名，性能优
            choices = list(index.keys())
            if choices:
                result = process.extractOne(col_lower, choices, scorer=fuzz.WRatio)
                if result and result[1] >= CONFIDENCE_FUZZY_MIN * 100:
                    matched_alias = result[0]
                    score = result[1] / 100
                    return {
                        "standard_field": index[matched_alias],
                        "confidence": round(score, 3),
                        "layer": LAYER_FUZZY,
                        "candidates": [],
                    }

        # 第 3 层：人工兜底
        return {
            "standard_field": None,
            "confidence": 0,
            "layer": LAYER_NONE,
            "candidates": self._candidates(col_lower)[:5],  # 给前端展示备选
        }

    def match_batch(self, columns: list) -> dict:
        """批量匹配列名 → 返回 {列名: match_result}"""
        return {col: self.match(col) for col in columns}

    def _candidates(self, col_lower: str) -> list:
        """返回 top-5 候选标准字段（供前端下拉）"""
        cfg = settings.audit_field_map
        std_fields = list(cfg.get("standard_fields", {}).keys())
        return std_fields

    # ==================== 学习（用户纠正） ====================
    def set_explicit(self, column_name: str, standard_field: Optional[str]):
        """用户在 UI 上指定/纠正映射 → 写入 explicit_map 持久化
        standard_field 为 None 或空字符串时，从 explicit_map 中删除该列（取消映射）
        注意：此方法只写 explicit_map，不学习到 user_synonyms（避免临时映射污染字典）；
        要让某列名永久自动命中，请用 learn_synonym()
        """
        with self._lock:
            explicit = self._user_state.setdefault("explicit_map", {})
            if standard_field and standard_field.strip():
                explicit[column_name] = standard_field.strip()
            else:
                explicit.pop(column_name, None)
            self._save_user_state()

    def learn_synonym(self, alias: str, standard_field: str):
        """显式学习一个同义词到 user_synonyms（下次同名列自动命中，无需再选）
        适用场景：用户在 UI 上点了「记住此映射」按钮
        """
        with self._lock:
            user_syn = self._user_state.setdefault("user_synonyms", {})
            user_syn.setdefault(standard_field, [])
            if alias not in user_syn[standard_field]:
                user_syn[standard_field].append(alias)
            self._save_user_state()

    # ==================== 配置查询 ====================
    def get_standard_fields(self) -> dict:
        """返回标准字段定义（label/unit/required/warn）"""
        return settings.audit_field_map.get("standard_fields", {})

    def get_user_state(self) -> dict:
        """返回用户扩展状态（用于前端展示已学习的映射）"""
        return self._user_state

    def get_synonyms(self) -> dict:
        """返回合并后的完整同义词字典（预置 + 用户扩展）"""
        cfg = settings.audit_field_map
        synonyms = {k: list(v) for k, v in cfg.get("synonyms", {}).items()}
        # 合并运行时学习的
        for std, alias_list in self._user_state.get("user_synonyms", {}).items():
            synonyms.setdefault(std, [])
            for alias in alias_list:
                if alias not in synonyms[std]:
                    synonyms[std].append(alias)
        return synonyms


# 全局单例
field_mapper = FieldMapper()
