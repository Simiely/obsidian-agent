"""API 层公共依赖：应用服务单例 / 认证 / 禁写校验。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import Header, HTTPException, Request

from app.agent.service import AgentService
from app.config import Settings
from app.core.backup import BackupEngine, BackupRunner, BackupScheduler
from app.core.indexer.service import IndexService
from app.core.search import SearchService
from app.core.vault import Vault


@dataclass
class AppServices:
    settings: Settings
    vault: Vault
    index: IndexService
    search: SearchService
    backup: BackupEngine
    backup_runner: BackupRunner
    backup_scheduler: BackupScheduler
    agent: AgentService


def get_services(request: Request) -> AppServices:
    return request.app.state.services


async def require_auth(
    request: Request, x_auth_token: str | None = Header(default=None)
) -> None:
    """AUTH_TOKEN 非空时启用简单口令认证（04-配置参考 §1.4）。"""
    settings = request.app.state.services.settings
    if settings.auth_token and x_auth_token != settings.auth_token:
        raise HTTPException(status_code=401, detail="未授权：X-Auth-Token 缺失或错误")


def ensure_writable(services: AppServices, rel: str) -> None:
    """写操作前检查：路径不在禁写目录、不以点开头（03 API 参考 §4.3 白名单语义）。

    注意：`..` 越界路径不在此拦截——由 vault.resolve_safe_path 报 422（语义区分）。
    """
    parts = Path(rel).parts
    if ".." in parts:
        return
    for d in services.settings.disallowed_write_dirs_list:
        if d in parts:
            raise HTTPException(status_code=403, detail=f"禁止写入目录: {d}")
    if any(p.startswith(".") for p in parts):
        raise HTTPException(status_code=403, detail="禁止写入隐藏目录/文件")
