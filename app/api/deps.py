"""API 层公共依赖：应用服务单例 / 认证 / 禁写校验。

AppServices 数据类已移至 app/services.py（S1：解除 core→api 反向依赖），
本模块只保留 API 专属依赖（get_services / require_auth / ensure_writable）。
"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from app.agent.safety import SafetyError, check_write_rules
from app.services import AppServices


def get_services(request: Request) -> AppServices:
    services: AppServices = request.app.state.services
    return services


async def require_auth(request: Request, x_auth_token: str | None = Header(default=None)) -> None:
    """AUTH_TOKEN 非空时启用简单口令认证（04-配置参考 §1.4）。"""
    settings = request.app.state.services.settings
    if settings.auth_token and x_auth_token != settings.auth_token:
        raise HTTPException(status_code=401, detail="未授权：X-Auth-Token 缺失或错误")


def ensure_writable(services: AppServices, rel: str) -> None:
    """写操作前检查：路径不在禁写目录、不以点开头（03 API 参考 §4.3 白名单语义）。

    复用 agent.safety.check_write_rules（共享白名单唯一实现），SafetyError 转 403。
    注意：`..` 越界路径不在此拦截——由 vault.resolve_safe_path 报 422（语义区分）。
    """
    try:
        check_write_rules(services.settings, rel)
    except SafetyError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
