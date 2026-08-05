"""
AdToEarn WebUI - FastAPI 主应用 (SDD 重构版)
所有路径、参数均通过 settings (来自 spec.yaml) 获取
"""

import asyncio
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import settings
from .modules.scraper import scraper
from .modules.reverse_parser import reverse_parser
from .modules.generator import generator
from .modules.api_config import api_config_manager, DOMAIN_LLM, DOMAIN_VIDEO
from .modules.app_logger import log_collector, LEVEL_INFO, EVENT_SYSTEM
from .modules.web_search import web_search
from .modules.audit import audit_service, SEV_CRITICAL, SEV_HIGH, SEV_MEDIUM, SEV_LOW

# 创建 FastAPI 应用
app = FastAPI(
    title=settings.app_name,
    description=settings.app_description if hasattr(settings, "app_description") else "广告素材采集 · 反向解析 · 风格迁移生成",
    version=settings.app_version,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")


# ==================== 请求模型 ====================

class ScrapeRequest(BaseModel):
    source: str = "youmiyoushu"
    industry: str = ""
    platform: str = ""
    keyword: str = ""
    category: str = "all"
    days: int = 7
    limit: int = 30


class GenerateRequest(BaseModel):
    source_analysis: dict
    target_style: str = "modern_minimal"
    product_info: str = ""
    platform: str = ""
    count: int = 3


class PromptVariationRequest(BaseModel):
    base_prompt: str
    style_id: str = "modern_minimal"
    count: int = 5


class ApiConfigSaveRequest(BaseModel):
    # LLM 域字段
    litellm_prefix: str = ""
    base_url: str = ""
    vision_model: str = ""
    supports_vision: bool = True
    # 视频域字段
    endpoint: str = ""
    model: str = ""
    duration: int = 0
    resolution: str = ""
    aspect_ratio: str = ""
    # 通用
    api_key: str = ""
    enabled: bool = True


class ApiTestRequest(BaseModel):
    api_key: str = ""
    endpoint: str = ""
    model: str = ""
    litellm_prefix: str = ""
    base_url: str = ""


class VideoGenerateRequest(BaseModel):
    provider: str = "seedance"
    prompt: str
    duration: int = 0
    resolution: str = ""
    aspect_ratio: str = ""


class WebSearchRequest(BaseModel):
    keyword: str
    days: int = 0
    domains: list = []
    max_results: int = 0


class AuditImportRequest(BaseModel):
    """审计数据导入请求（JSON 记录数组）"""
    records: list = []


class AuditImportFileRequest(BaseModel):
    """审计数据导入请求（文本内容 + 格式）"""
    content: str = ""
    format: str = "csv"  # csv | json


# ==================== 页面路由 ====================

@app.get("/", response_class=HTMLResponse)
async def index():
    """主页面"""
    index_file = settings.templates_dir / "index.html"
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "adtoearn-webui",
        "version": settings.app_version,
        "timestamp": datetime.now().isoformat(),
        "mock_enabled": settings.mock_enabled,
        "config_status": {
            "llm": "configured" if api_config_manager.get_active_llm_config() else "not_configured",
            "video": {
                pid: ("configured" if (api_config_manager.get_config(DOMAIN_VIDEO, pid) or {}).get("api_key") else "not_configured")
                for pid in settings.video_providers
            },
        },
        "scraper_available": True,
    }


# ==================== 运行日志 API (B1) ====================

@app.get("/api/logs")
async def get_logs(limit: int = 200, min_level: str = LEVEL_INFO):
    """获取最近运行日志（按级别过滤）"""
    return {"logs": log_collector.get_recent(limit=limit, min_level=min_level)}


@app.get("/api/logs/stream")
async def logs_stream():
    """SSE 实时日志流"""
    from fastapi.responses import StreamingResponse

    queue = log_collector.subscribe()

    async def event_gen():
        try:
            # 先推最近日志
            for entry in log_collector.get_recent(limit=100):
                yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
            # 持续推送新日志
            while True:
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            log_collector.unsubscribe(queue)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ==================== 数据采集 API ====================

@app.get("/api/sources")
async def get_sources():
    """获取可用数据源 (来自 spec)"""
    return {
        "sources": [
            {
                "id": k,
                "name": v["name"],
                "url": v["url"],
                "strategy": v.get("strategy", "url"),
            }
            for k, v in settings.scraper_sources.items()
        ]
    }


@app.post("/api/scrape/trending")
async def scrape_trending(req: ScrapeRequest):
    """采集热门关键词"""
    return await scraper.scrape_trending_keywords(
        source=req.source,
        industry=req.industry,
        platform=req.platform,
        limit=req.limit,
    )


@app.post("/api/scrape/hot")
async def scrape_hot(req: ScrapeRequest):
    """采集热门素材"""
    return await scraper.scrape_hot_creatives(
        source=req.source,
        category=req.category,
        days=req.days,
        limit=req.limit,
    )


@app.post("/api/scrape/search")
async def scrape_search(req: ScrapeRequest):
    """按关键词搜索素材（Playwright 爬虫）"""
    if not req.keyword:
        raise HTTPException(status_code=400, detail="keyword 不能为空")
    return await scraper.search_creatives(
        keyword=req.keyword,
        source=req.source,
        platform=req.platform,
        limit=req.limit,
    )


@app.post("/api/scrape/websearch")
async def scrape_websearch(req: WebSearchRequest):
    """联网搜索（LLM Web Search）— 主通道，规避目标站反爬"""
    if not req.keyword.strip():
        raise HTTPException(status_code=400, detail="keyword 不能为空")
    return await web_search.search(
        keyword=req.keyword.strip(),
        days=req.days or None,
        domains=req.domains or None,
        max_results=req.max_results or None,
    )


# ==================== 素材反向解析 API ====================

@app.post("/api/analyze/upload")
async def upload_and_analyze(file: UploadFile = File(...)):
    """上传文件并分析"""
    ext = Path(file.filename).suffix.lower()
    allowed = settings.allowed_image_extensions + settings.allowed_video_extensions
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {ext}")

    # 校验文件大小
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    if file_size > settings.max_upload_size:
        raise HTTPException(status_code=413, detail=f"文件过大，最大支持 {settings.max_upload_size // (1024*1024)}MB")

    file_id = str(uuid.uuid4())
    save_path = Path(settings.upload_dir) / f"{file_id}{ext}"
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    result = await reverse_parser.analyze(str(save_path))
    result["original_filename"] = file.filename
    result["file_id"] = file_id
    return result


@app.post("/api/analyze/file")
async def analyze_file(file_path: str):
    """分析本地文件"""
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return await reverse_parser.analyze(file_path)


# ==================== 素材生成 API ====================

@app.get("/api/styles")
async def get_styles():
    """获取可用风格列表 (来自 spec)"""
    return {"styles": generator.get_available_styles()}


@app.post("/api/generate")
async def generate_creative(req: GenerateRequest):
    """基于反向解析结果生成新素材"""
    return await generator.generate_creative(
        source_analysis=req.source_analysis,
        target_style=req.target_style,
        product_info=req.product_info,
        platform=req.platform,
        count=req.count,
    )


@app.post("/api/generate/variations")
async def generate_variations(req: PromptVariationRequest):
    """生成 Prompt 变体"""
    return await generator.generate_image_prompt_variations(
        base_prompt=req.base_prompt,
        style_id=req.style_id,
        count=req.count,
    )


# ==================== API 配置管理 API ====================

@app.get("/api/apiconfig/providers")
async def api_providers(domain: str = DOMAIN_LLM):
    """获取指定域的提供商模板列表（llm/video，含自定义）"""
    if domain not in (DOMAIN_LLM, DOMAIN_VIDEO):
        raise HTTPException(status_code=400, detail="domain 必须为 llm 或 video")
    return {"domain": domain, "providers": api_config_manager.list_providers(domain)}


@app.get("/api/apiconfig")
async def get_api_configs(domain: Optional[str] = None):
    """获取已保存的 API 配置（密钥脱敏）；domain 为空返回全量"""
    return api_config_manager.get_configs(domain)


@app.post("/api/apiconfig/{domain}/{provider_id}")
async def save_api_config(domain: str, provider_id: str, req: ApiConfigSaveRequest):
    """保存/更新提供商配置（双域）"""
    if domain not in (DOMAIN_LLM, DOMAIN_VIDEO):
        raise HTTPException(status_code=400, detail="domain 必须为 llm 或 video")
    try:
        cfg = api_config_manager.save_config(domain, provider_id, req.model_dump())
        return {"ok": True, "config": cfg}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/apiconfig/{domain}/{provider_id}")
async def delete_api_config(domain: str, provider_id: str):
    """删除提供商配置（双域）"""
    if domain not in (DOMAIN_LLM, DOMAIN_VIDEO):
        raise HTTPException(status_code=400, detail="domain 必须为 llm 或 video")
    return {"ok": api_config_manager.delete_config(domain, provider_id)}


@app.post("/api/apiconfig/{domain}/{provider_id}/test")
async def test_api_connection(domain: str, provider_id: str, req: ApiTestRequest):
    """测试 API 连接（双域）"""
    if domain not in (DOMAIN_LLM, DOMAIN_VIDEO):
        raise HTTPException(status_code=400, detail="domain 必须为 llm 或 video")
    payload = req.model_dump()
    result = await api_config_manager.test_connection(domain, provider_id, payload)
    return result


# ==================== 素材生成 API（创意方案 + 视频，统一入口） ====================

@app.post("/api/video/generate")
async def generate_video(req: VideoGenerateRequest):
    """根据素材描述生成视频（视频即素材，统一由 generator 产出）"""
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="素材描述不能为空")
    params = {
        "duration": req.duration or 0,
        "resolution": req.resolution,
        "aspect_ratio": req.aspect_ratio,
    }
    return await generator.generate_video(req.provider, req.prompt.strip(), params)


@app.get("/api/video/task/{task_id}")
async def get_video_task(task_id: str):
    """查询视频生成任务状态"""
    task = generator.get_video_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return task


@app.get("/api/video/tasks")
async def list_video_tasks(limit: int = 20):
    """获取最近视频生成任务"""
    return {"tasks": generator.list_video_tasks(limit)}


# ==================== 工作流 API ====================

@app.post("/api/workflow/complete")
async def complete_workflow(
    file: UploadFile = File(...),
    target_style: str = "modern_minimal",
    product_info: str = "",
    platform: str = "",
    count: int = 3,
):
    """
    一键完成完整工作流：上传 → 反向解析 → 风格迁移生成
    """
    ext = Path(file.filename).suffix.lower()
    allowed = settings.allowed_image_extensions + settings.allowed_video_extensions
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {ext}")

    file_id = str(uuid.uuid4())
    save_path = Path(settings.upload_dir) / f"{file_id}{ext}"
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    analysis_result = await reverse_parser.analyze(str(save_path))
    analysis_result["original_filename"] = file.filename

    generation_result = await generator.generate_creative(
        source_analysis=analysis_result,
        target_style=target_style,
        product_info=product_info,
        platform=platform,
        count=count,
    )

    return {
        "workflow": "complete",
        "steps": {
            "upload": {"status": "success", "filename": file.filename, "file_id": file_id},
            "analysis": {
                "status": analysis_result.get("status", "unknown"),
                "keywords": analysis_result.get("analysis", {}).get("关键词", []),
                "style_tags": analysis_result.get("analysis", {}).get("风格标签", []),
            },
            "generation": {
                "status": generation_result.get("status", "unknown"),
                "count": generation_result.get("count", 0),
                "target_style": generation_result.get("target_style", ""),
            },
        },
        "analysis_result": analysis_result,
        "generation_result": generation_result,
        "completed_at": datetime.now().isoformat(),
    }


# ==================== 广告账户审计 API ====================

@app.get("/api/audit/meta")
async def audit_meta():
    """审计数据元信息（记录数/账户列表/时间范围/指标定义）"""
    return audit_service.get_meta()


@app.get("/api/audit/summary")
async def audit_summary(account: Optional[str] = None, days: Optional[int] = None):
    """投放数据总览：关键指标 + 健康评分 + 异常统计"""
    return audit_service.summary(account=account, days=days)


@app.get("/api/audit/trend")
async def audit_trend(account: Optional[str] = None, days: Optional[int] = None):
    """时间维度趋势：按日聚合"""
    return {"trend": audit_service.trend(account=account, days=days)}


@app.get("/api/audit/accounts")
async def audit_accounts(days: Optional[int] = None):
    """账户维度数据对比"""
    return {"accounts": audit_service.by_account(days=days)}


@app.get("/api/audit/anomalies")
async def audit_anomalies(account: Optional[str] = None):
    """异常 / 风险发现项（分级）"""
    records = audit_service._filter(account=account) if account else None
    return {"anomalies": audit_service.detect_anomalies(records)}


@app.get("/api/audit/records")
async def audit_records(account: Optional[str] = None, days: Optional[int] = None, limit: int = 500):
    """原始投放记录（分页查看）"""
    records = audit_service._filter(account=account, days=days)
    return {"records": records[-limit:], "total": len(records)}


@app.post("/api/audit/import")
async def audit_import(req: AuditImportRequest):
    """导入投放记录（JSON 数组）"""
    result = audit_service.import_records(req.records, source="json")
    return result


@app.post("/api/audit/import/file")
async def audit_import_file(req: AuditImportFileRequest):
    """导入投放数据（CSV/JSON 文本内容）"""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="内容不能为空")
    try:
        if req.format == "json":
            records = audit_service.parse_json(req.content.encode("utf-8"))
        else:
            records = audit_service.parse_csv(req.content.encode("utf-8"))
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"解析失败: {e}")
    result = audit_service.import_records(records, source=req.format)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["导入失败"]))
    return result


@app.post("/api/audit/sample")
async def audit_sample():
    """生成示例投放数据（标注 sample=true，演示/测试用）"""
    return audit_service.generate_sample()


@app.delete("/api/audit/data")
async def audit_clear():
    """清空审计数据"""
    return audit_service.clear()


# ==================== 启动事件 ====================

@app.on_event("startup")
async def startup_event():
    print("\n  " + "=" * 42)
    print(f"  {settings.app_name} v{settings.app_version}")
    print(f"  AI 配置: {'已配置' if settings.ai_configured else '未配置 (使用模拟模式)'}")
    print(f"  数据源: {', '.join(settings.scraper_sources.keys())}")
    print(f"  风格: {len(settings.styles)} 种")
    print("  " + "=" * 42 + "\n")
    log_collector.info(EVENT_SYSTEM, f"服务启动 {settings.app_name} v{settings.app_version}", {
        "mock_enabled": settings.mock_enabled,
        "llm_configured": bool(api_config_manager.get_active_llm_config()),
    })
