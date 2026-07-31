"""Obsidian Agent —— 应用入口（M3：API 层完整路由 + 启动生命周期）。

create_app(settings) 工厂模式：测试可注入自定义配置；uvicorn 入口使用模块级 app。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent.service import AgentService
from app.api import routes_agent, routes_backup, routes_index, routes_search, routes_vault
from app.api.deps import AppServices
from app.config import Settings, get_settings
from app.core.backup import BackupEngine, BackupError, BackupRunner, BackupScheduler
from app.core.indexer.fts5 import Fts5Index
from app.core.indexer.service import IndexService
from app.core.search import SearchService
from app.core.vault import FileTooLarge, PathNotAllowed, Vault, VaultError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("obsidian-agent")

APP_VERSION = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    """应用工厂：构建领域服务 → 挂载路由 → 生命周期（索引/watcher/定时备份）。"""
    settings = settings or get_settings()
    settings.validate_paths()  # 坑 #11：备份目录不得位于 vault 内，失败即拒绝启动

    vault = Vault(
        root=settings.vault_path,
        ignore_dirs=settings.ignore_dirs_list,
        ignore_files=settings.ignore_files_list,
        max_file_bytes=settings.max_file_bytes,
    )
    backend = Fts5Index(
        db_path=settings.data_dir / "index.db",
        userdict=settings.jieba_userdict or None,
    )
    index = IndexService(vault=vault, backend=backend)
    search = SearchService(index)
    backup = BackupEngine(
        vault=vault,
        backup_root=settings.resolved_backup_dir,
        retention=settings.backup_retention,
        max_bytes=settings.backup_max_bytes,
        verify=settings.backup_verify,
        enabled=settings.backup_enabled,
    )
    services = AppServices(
        settings=settings,
        vault=vault,
        index=index,
        search=search,
        backup=backup,
        backup_runner=BackupRunner(backup),
        backup_scheduler=BackupScheduler(backup, settings.backup_schedule),
        agent=AgentService(vault=vault, index=index, backup=backup, settings=settings),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # 启动：全量索引（后台线程，不阻塞启动）→ watcher → 定时备份
        if settings.watch_enabled:
            index.start_watcher(settings.watch_debounce_seconds)
        services.backup_scheduler.start()

        def _bootstrap() -> None:
            try:
                index.full_rebuild()
                logger.info("初始索引完成 total=%s", backend.total_docs())
            except Exception:  # pragma: no cover
                logger.exception("初始索引失败")

        boot_thread = threading.Thread(target=_bootstrap, daemon=True, name="initial-index")
        boot_thread.start()
        yield
        index.stop_watcher()
        services.backup_scheduler.stop()
        # 优雅关闭：等待初始索引线程结束再关连接，避免并发 close sqlite（access violation）
        boot_thread.join(timeout=30)
        if boot_thread.is_alive():  # pragma: no cover - 大库超时场景
            logger.warning("初始索引仍在运行，跳过索引库连接关闭（进程退出时回收）")
        else:
            backend.close()

    app = FastAPI(
        title="Obsidian Agent",
        description=(
            "Dockerized Obsidian AI workspace: browse, edit, "
            "full-text search and AI agent over your vault"
        ),
        version=APP_VERSION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.services = services

    app.include_router(routes_vault.router)
    app.include_router(routes_search.router)
    app.include_router(routes_index.router)
    app.include_router(routes_backup.router)
    app.include_router(routes_agent.router)

    @app.get("/api/health", tags=["system"])
    def health() -> dict[str, Any]:
        st = index.status()
        return {
            "status": "ok",
            "version": APP_VERSION,
            "vault": str(settings.vault_path),
            "indexBackend": st["backend"],
            "indexState": st["state"],
        }

    @app.get("/api/version", tags=["system"])
    def version() -> dict[str, Any]:
        return {"version": APP_VERSION}

    _register_exception_handlers(app)

    # 静态前端：M4 起优先挂载 Vue3 构建产物 dist，缺失时回退 M0 静态占位页
    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    static_dir = frontend_dir / "dist"
    if not static_dir.is_dir():
        static_dir = frontend_dir / "static"
        logger.info("未找到 frontend/dist（Vue 构建产物），回退使用 frontend/static")
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """核心领域异常 → HTTP 状态码映射（docs/05 §1 错误约定）。"""

    @app.exception_handler(PathNotAllowed)
    async def _path_not_allowed(_: Request, exc: PathNotAllowed) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(FileTooLarge)
    async def _file_too_large(_: Request, exc: FileTooLarge) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(FileNotFoundError)
    async def _not_found(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(FileExistsError)
    async def _conflict(_: Request, exc: FileExistsError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(BackupError)
    async def _backup_error(_: Request, exc: BackupError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(VaultError)
    async def _vault_error(_: Request, exc: VaultError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})


# uvicorn 入口：配置错误（如 vault 路径不存在）时不阻塞 import，由占位 app 暴露错误
def _placeholder_app(error: str) -> FastAPI:
    """配置错误的占位应用：/api/health 返回 500，其余接口不可用。"""

    app = FastAPI(title="Obsidian Agent (配置错误)", version=APP_VERSION)

    @app.get("/api/health")
    def health() -> JSONResponse:
        return JSONResponse(status_code=500, content={"status": "error", "detail": error})

    return app


try:
    app = create_app()
except Exception as exc:  # pragma: no cover - 仅配置错误路径
    logger.error("应用初始化失败（检查 VAULT_PATH 等配置）: %s", exc)
    app = _placeholder_app(str(exc))
