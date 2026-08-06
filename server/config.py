"""
AdToEarn 配置加载器
SDD 原则：所有配置从 config/spec.yaml 统一加载，代码中禁止硬编码
"""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
SPEC_PATH = BASE_DIR / "config" / "spec.yaml"
ENV_PATH = BASE_DIR / ".env"


class Spec:
    """从 spec.yaml 加载的规范配置"""

    def __init__(self, data: dict):
        self._data = data

    def get(self, *keys: str, default: Any = None) -> Any:
        """按点分路径获取配置值"""
        node: Any = self._data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    @property
    def data(self) -> dict:
        return self._data


def load_spec() -> Spec:
    """加载 spec.yaml"""
    if not SPEC_PATH.exists():
        raise FileNotFoundError(
            f"规范配置文件缺失: {SPEC_PATH}\n"
            "请确保项目包含 config/spec.yaml (SDD 规范文件)"
        )
    with open(SPEC_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Spec(data)


# 加载 .env 环境变量（密钥类配置）
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

# 全局规范实例
spec = load_spec()


def env(key: str, default: str = "") -> str:
    """读取环境变量"""
    return os.getenv(key, default)


class Settings:
    """应用配置门面 - 所有模块通过此对象读取配置"""

    # ------- 服务 -------
    host: str = spec.get("server", "host", default="127.0.0.1")
    port: int = spec.get("server", "port", default=8765)
    app_name: str = spec.get("app", "name", default="AdToEarn WebUI")
    app_version: str = spec.get("app", "version", default="2.0.0")

    # ------- 路径 -------
    base_dir: Path = BASE_DIR
    upload_dir: Path = BASE_DIR / spec.get("paths", "upload_dir", default="uploads")
    cache_dir: Path = BASE_DIR / spec.get("paths", "cache_dir", default="cache")
    web_dir: Path = BASE_DIR / spec.get("paths", "web_dir", default="web")
    templates_dir: Path = BASE_DIR / spec.get("paths", "templates_dir", default="web/templates")
    static_dir: Path = BASE_DIR / spec.get("paths", "static_dir", default="web/static")

    # ------- 上传 -------
    max_upload_size: int = spec.get("upload", "max_file_size_mb", default=100) * 1024 * 1024
    allowed_image_extensions: list = spec.get("upload", "allowed_image_extensions", default=[".jpg", ".png"])
    allowed_video_extensions: list = spec.get("upload", "allowed_video_extensions", default=[".mp4", ".mov"])

    # ------- AI -------
    openai_api_key: str = env(
        spec.get("ai", "api_key_env", default="OPENAI_API_KEY"), ""
    )
    openai_base_url: str = env(
        spec.get("ai", "base_url_env", default="OPENAI_BASE_URL"),
        spec.get("ai", "defaults", "base_url", default="https://api.openai.com/v1"),
    )
    openai_model: str = env(
        spec.get("ai", "text_model_env", default="OPENAI_MODEL"),
        spec.get("ai", "defaults", "text_model", default="gpt-4o"),
    )
    openai_vision_model: str = env(
        spec.get("ai", "vision_model_env", default="OPENAI_VISION_MODEL"),
        spec.get("ai", "defaults", "vision_model", default="gpt-4o"),
    )

    # 生成参数
    gen_temperature: float = spec.get("ai", "generation", "temperature", default=0.8)
    gen_max_tokens: int = spec.get("ai", "generation", "max_tokens", default=3000)
    vision_temperature: float = spec.get("ai", "vision", "temperature", default=0.4)
    vision_max_tokens: int = spec.get("ai", "vision", "max_tokens", default=2500)
    analysis_temperature: float = spec.get("ai", "analysis", "temperature", default=0.4)
    analysis_max_tokens: int = spec.get("ai", "analysis", "max_tokens", default=2000)
    copy_temperature: float = spec.get("ai", "copywriting", "temperature", default=0.9)
    keyword_temperature: float = spec.get("ai", "keyword", "temperature", default=0.5)

    # ------- 爬虫 -------
    scraper_headless: bool = spec.get("scraper", "headless", default=True)
    scraper_timeout: int = spec.get("scraper", "timeout_ms", default=30000)
    scraper_user_agent: str = spec.get(
        "scraper", "user_agent",
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    )
    scraper_viewport: dict = spec.get("scraper", "viewport", default={"width": 1920, "height": 1080})
    scraper_locale: str = spec.get("scraper", "locale", default="zh-CN")

    @property
    def scraper_sources(self) -> dict:
        """爬虫数据源 (来自 spec)"""
        return spec.get("scraper", "sources", default={})

    @property
    def video_providers(self) -> dict:
        """视频生成 API 提供商模板 (来自 spec)"""
        return spec.get("video", "providers", default={})

    @property
    def llm_providers(self) -> dict:
        """LLM 大模型提供商模板 (来自 spec, 经 LiteLLM 调用)"""
        return spec.get("llm", "providers", default={})

    @property
    def llm_default_provider(self) -> str:
        """默认 LLM 提供商 ID"""
        return spec.get("llm", "default_provider", default="openai")

    @property
    def video_poll(self) -> dict:
        """视频任务轮询参数"""
        return {
            "interval": spec.get("video", "poll_interval_sec", default=5),
            "max_attempts": spec.get("video", "poll_max_attempts", default=60),
            "ttl_minutes": spec.get("video", "task_ttl_minutes", default=120),
        }

    # ------- 联网搜索 (LLM Web Search) -------
    @property
    def websearch(self) -> dict:
        """联网搜索配置 (来自 spec)"""
        return spec.get("websearch", default={})

    @property
    def api_config_path(self) -> Path:
        """API 配置持久化文件 (WebUI 运行时写入)"""
        return BASE_DIR / "config" / "api_config.json"

    @property
    def styles(self) -> dict:
        """风格模板 (来自 spec)"""
        return spec.get("styles", default={})

    # ------- 广告账户审计 -------
    @property
    def audit_data_file(self) -> Path:
        """审计数据持久化文件"""
        return BASE_DIR / spec.get("audit", "data_file", default="cache/audit_data.json")

    @property
    def audit_field_map_file(self) -> Path:
        """字段映射配置持久化文件（用户映射 + 学习的同义词）"""
        return BASE_DIR / spec.get("audit", "field_map_file", default="config/audit_field_map.json")

    @property
    def audit_field_map(self) -> dict:
        """字段映射引擎配置 (来自 spec)"""
        return spec.get("audit", "field_map", default={})

    @property
    def audit_tag_groups(self) -> list:
        """预设标签组 (来自 spec)"""
        return spec.get("audit", "tag_groups", default=[])

    @property
    def audit_tag_lib_file(self) -> Path:
        """用户自定义标签库持久化文件"""
        return BASE_DIR / spec.get("audit", "tag_lib_file", default="config/audit_tag_lib.json")

    @property
    def audit_rule_state_file(self) -> Path:
        """信号规则启用状态持久化文件"""
        return BASE_DIR / spec.get("audit", "rule_state_file", default="config/audit_rule_state.json")

    @property
    def audit_signal_rules(self) -> dict:
        """信号规则定义 (来自 spec)"""
        return spec.get("audit", "signal_rules", default={})

    @property
    def audit_sample(self) -> dict:
        """示例数据生成参数 (来自 spec)"""
        return spec.get("audit", "sample", default={})

    @property
    def audit_anomaly(self) -> dict:
        """异常检测阈值 (来自 spec)"""
        return spec.get("audit", "anomaly", default={})

    @property
    def audit_health(self) -> dict:
        """健康评分配置 (来自 spec)"""
        return spec.get("audit", "health_score", default={})

    # ------- 反向解析 -------
    video_frames: int = spec.get("reverse_parser", "video_frames", default=4)
    analysis_prompt_path: Path = BASE_DIR / spec.get(
        "reverse_parser", "analysis_prompt_file", default="config/prompts/analysis.txt"
    )

    # ------- 生成 -------
    default_gen_count: int = spec.get("generator", "default_count", default=3)
    max_gen_count: int = spec.get("generator", "max_count", default=10)

    # ------- 功能开关 -------
    mock_enabled: bool = spec.get("debug", "mock_enabled", default=False)

    @property
    def ai_configured(self) -> bool:
        return bool(self.openai_api_key)


settings = Settings()

# 确保目录存在
settings.upload_dir.mkdir(parents=True, exist_ok=True)
settings.cache_dir.mkdir(parents=True, exist_ok=True)
