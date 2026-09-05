"""FastAPI 应用装配：安全中间件、API 路由、服务端页面、健康检查与生命周期初始化。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import audit
from .config import settings
from .db import connect, init_db, now_iso
from .embeddings import embedding_service
from .index import vector_index
from .pages import router as pages_router
from .routers import admin as admin_router
from .routers import auth as auth_router
from .routers import query as query_router
from .security import hash_password

logger = logging.getLogger("rag")

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"


def _bootstrap() -> None:
    """启动期一次性初始化（幂等，容器重启安全）。"""
    init_db()
    db = connect()
    try:
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))
        db.execute(
            "UPDATE documents SET status='failed', error=? WHERE status='parsing'",
            ("服务重启导致上次入库中断，请对该文档执行“重新索引”",),
        )
        n_users = db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        if n_users == 0:
            if not settings.root_password:
                raise RuntimeError(
                    "数据库为空且未设置 RAG_ROOT_PASSWORD：请先在 .env 中配置初始 root 密码后重启"
                )
            now = now_iso()
            db.execute(
                "INSERT INTO users (username, password_hash, role, is_active, created_at, updated_at)"
                " VALUES ('root', ?, 'root', 1, ?, ?)",
                (hash_password(settings.root_password), now, now),
            )
            audit.log_audit(
                db,
                action="system_init",
                username="root",
                detail="首次启动创建初始 root 账号",
            )
            logger.info("已用 RAG_ROOT_PASSWORD 创建初始 root 账号（建议尽快在界面修改密码）")
        if not settings.secret_key:
            logger.warning(
                "未设置 RAG_SECRET_KEY：会话令牌哈希使用内置开发密钥，接入正式环境前必须配置"
            )
        vector_index.reload(db)
    finally:
        db.close()


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _bootstrap()
        embedding_service.start()  # 后台线程加载/下载嵌入模型，不阻塞启动
        print(
            "\n"
            "──────────────────────────────────────────────────────\n"
            f"  局域网 RAG 试点 v{settings.version}\n"
            f"  访问地址: http://{settings.host}:{settings.port}（容器内由 8088 映射）\n"
            f"  对外地址: {settings.public_origin or '(未配置 RAG_PUBLIC_ORIGIN)'}\n"
            f"  嵌入模型: {settings.embed_model} [{settings.embed_backend}]\n"
            f"  模型接口: {settings.deepseek_base_url} ({settings.deepseek_model})\n"
            "──────────────────────────────────────────────────────\n"
        )
        yield

    app = FastAPI(
        title="局域网 RAG 试点",
        description="FastAPI + SQLite + BGE 本地检索 + DeepSeek API（仅检索片段外发）",
        version=settings.version,
        lifespan=lifespan,
    )

    # ---- 安全中间件：同源写请求校验 + 基础安全响应头 ----
    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        method = request.method.upper()
        if method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
            probe = request.headers.get("origin") or request.headers.get("referer")
            if probe:
                try:
                    probe_host = urlparse(probe).netloc
                except ValueError:
                    probe_host = ""
                host = (request.headers.get("host") or "").lower()
                if probe_host and probe_host.lower() != host:
                    return JSONResponse(
                        status_code=403, content={"detail": "跨站请求被拒绝（同源校验失败）"}
                    )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # ---- API ----
    app.include_router(auth_router.router, prefix="/api")
    app.include_router(query_router.router, prefix="/api")
    app.include_router(admin_router.router, prefix="/api")

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "version": settings.version,
            "model_ready": embedding_service.state == "ready",
            "model_state": embedding_service.state,
            "model_message": embedding_service.message or None,
        }

    @app.get("/api/ready")
    def ready():
        payload = {
            "status": "ready" if embedding_service.state == "ready" else "not_ready",
            "model_ready": embedding_service.state == "ready",
            "model_state": embedding_service.state,
            "model_message": embedding_service.message or None,
        }
        if not payload["model_ready"]:
            return JSONResponse(status_code=503, content=payload)
        return payload

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        response = FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
        return response

    # ---- 服务端页面 + 静态资源 ----
    if TEMPLATES_DIR.is_dir():
        app.include_router(pages_router)
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


app = create_app()
