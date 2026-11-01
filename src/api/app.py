"""FastAPI 应用 —— 可演示的 HTTP 接口层。

设计约束（docs/04-api-and-data-contract.md + docs/00-baseline.md §9）：
  * API 层**零业务逻辑**：所有能力来自 ``src.pipeline.get_service()``。
  * 同步实现（MVP）；契约里的 ``TaskResponse`` 留给二期异步。
  * 所有非 2xx 响应只有一种形状：``contracts.ErrorResponse``
    （``{code, message, retryable, detail}``），前端可直接展示 ``message``。
  * ``GET /`` 直接托管 ``web/index.html``，演示时一个进程即可（同源，无 CORS 问题）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from ..contracts import (
    CONTRACT_VERSION,
    CreateProfileRequest,
    CreateProfileResponse,
    CreateReportRequest,
    ErrorResponse,
    HealthResponse,
    ParseOutfitRequest,
    ParseOutfitResponse,
    Report,
    StyleProfile,
)

logger = logging.getLogger("chuangyi.api")

ROOT = Path(__file__).resolve().parent.parent.parent
WEB_INDEX = ROOT / "web" / "index.html"

# 错误码（MVP 简化枚举；完整枚举见 docs/04 §3.2，二期再对齐）
CODE_VALIDATION = "VALIDATION_ERROR"
CODE_NOT_FOUND = "NOT_FOUND"
CODE_INTERNAL = "INTERNAL_ERROR"


# --------------------------------------------------------------------------
# 统一错误响应
# --------------------------------------------------------------------------


def error_response(
    status: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    detail: dict[str, Any] | None = None,
) -> JSONResponse:
    """构造唯一的错误形状（ErrorResponse）。``message`` 是可直接展示的中文。"""
    body = ErrorResponse(code=code, message=message, retryable=retryable, detail=detail)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def _validation_detail(exc: Exception) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            for e in errors():
                loc = e.get("loc") or ()
                fields.append({
                    "path": ".".join(str(p) for p in loc if p != "body") or "body",
                    "reason": str(e.get("msg", "")),
                    "type": str(e.get("type", "")),
                })
        except Exception:  # pragma: no cover - 防御 pydantic 内部变化
            pass
    return {"fields": fields}


def _service(app: FastAPI) -> Any:
    """取业务服务。``create_app(service=...)`` 注入优先（测试用），否则走全局单例。"""
    svc = getattr(app.state, "service", None)
    if svc is None:
        from ..pipeline import get_service

        svc = get_service()
        app.state.service = svc
    return svc


# --------------------------------------------------------------------------
# 应用工厂
# --------------------------------------------------------------------------


def create_app(service: Any | None = None) -> FastAPI:
    """创建 FastAPI 应用。``service`` 为空时在首次请求时懒加载 ``get_service()``。"""
    app = FastAPI(
        title="AI 穿搭顾问 · Demo API",
        description="参考实现：确定性规则引擎 + 可解释报告。契约冻结版本 "
                    f"{CONTRACT_VERSION}。",
        version=CONTRACT_VERSION,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    if service is not None:
        app.state.service = service

    # 演示期允许本地任意端口（前端可能被 file:// 打开或由别的小服务托管）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---------------- 异常处理器 ----------------

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            422, CODE_VALIDATION, "请求参数不合法，请检查字段类型与取值。",
            detail=_validation_detail(exc),
        )

    @app.exception_handler(ValidationError)
    async def _model_validation(request: Request, exc: ValidationError) -> JSONResponse:
        return error_response(
            422, CODE_VALIDATION, "请求参数不合法，请检查字段类型与取值。",
            detail=_validation_detail(exc),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未捕获异常: %s %s", request.method, request.url.path)
        return error_response(
            500, CODE_INTERNAL, "服务内部错误，请稍后重试。",
            retryable=False, detail={"error_type": type(exc).__name__},
        )

    # ---------------- 健康检查 ----------------

    @app.get("/v1/health", response_model=HealthResponse, tags=["system"],
             summary="健康检查（无需认证）")
    def health() -> HealthResponse:
        info = _service(app).health() or {}
        return HealthResponse(
            status="ok",
            contract_version=CONTRACT_VERSION,
            ruleset_version=str(info.get("ruleset_version", "")),
            offline_mode=bool(info.get("offline_mode", True)),
            rule_count=int(info.get("rule_count") or 0),
            providers={str(k): str(v) for k, v in (info.get("providers") or {}).items()},
        )

    # ---------------- 画像 ----------------

    @app.post("/v1/profiles", response_model=CreateProfileResponse, tags=["profile"],
              summary="上传参考穿搭标识 → 建立风格画像")
    def create_profile(req: CreateProfileRequest) -> CreateProfileResponse:
        profile, outfits = _service(app).create_profile(
            req.image_refs, req.note,
            banned_styles=req.banned_styles,
            explore_tags=req.explore_tags,
        )
        warnings: list[str] = []
        for o in outfits:
            warnings.extend(o.parse_warnings or [])
        from ..pipeline import summarize_warnings
        return CreateProfileResponse(
            profile_id=profile.profile_id,
            profile=profile,
            outfits=outfits,
            warnings=summarize_warnings(warnings),
        )

    @app.get("/v1/profiles/{profile_id}", response_model=StyleProfile, tags=["profile"],
             summary="读取风格画像")
    def get_profile(profile_id: str):
        profile = _service(app).get_profile(profile_id)
        if profile is None:
            return error_response(404, CODE_NOT_FOUND, "画像不存在或已被删除。",
                                  detail={"profile_id": profile_id})
        return profile

    # ---------------- 解析 ----------------

    @app.post("/v1/outfits/parse", response_model=ParseOutfitResponse, tags=["outfit"],
              summary="解析一套穿搭（多图视为同一套的不同角度）")
    def parse_outfit(req: ParseOutfitRequest) -> ParseOutfitResponse:
        outfit, warnings = _service(app).parse_outfit(
            req.image_refs, req.occasion, req.season)
        return ParseOutfitResponse(outfit=outfit, warnings=list(warnings))

    # ---------------- 报告 ----------------

    @app.post("/v1/reports", response_model=Report, tags=["report"],
              summary="生成穿搭评审报告（核心端点）")
    def create_report(req: CreateReportRequest):
        try:
            return _service(app).create_report(
                profile_id=req.profile_id,
                outfit_id=req.outfit_id,
                image_refs=req.image_refs,
                occasion=req.occasion,
                season=req.season,
            )
        except KeyError as exc:
            missing = exc.args[0] if exc.args else "resource"
            return error_response(404, CODE_NOT_FOUND, "画像或搭配不存在，无法生成报告。",
                                  detail={"missing": str(missing)})

    @app.get("/v1/reports/{report_id}", response_model=Report, tags=["report"],
             summary="读取评审报告")
    def get_report(report_id: str):
        report = _service(app).get_report(report_id)
        if report is None:
            return error_response(404, CODE_NOT_FOUND, "报告不存在或已被删除。",
                                  detail={"report_id": report_id})
        return report

    # ---------------- 演示前端（同源托管，零外部依赖） ----------------

    @app.get("/", include_in_schema=False)
    def index():
        if not WEB_INDEX.exists():  # pragma: no cover - 部署异常
            return JSONResponse(status_code=404, content={
                "code": CODE_NOT_FOUND, "message": "前端文件 web/index.html 缺失。",
                "retryable": False, "detail": None,
            })
        return FileResponse(WEB_INDEX, media_type="text/html; charset=utf-8")

    return app


app = create_app()
