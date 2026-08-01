"""/api/search：全文检索。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_services, require_auth
from app.schemas import SearchOut
from app.services import AppServices

router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Depends(require_auth)])


@router.get("", response_model=SearchOut)
def search(
    q: str = Query(...),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    services: AppServices = Depends(get_services),
) -> Any:
    return services.search.search(q, page=page, page_size=pageSize)
