"""/api/search：全文检索。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import AppServices, get_services, require_auth

router = APIRouter(
    prefix="/api/search", tags=["search"], dependencies=[Depends(require_auth)]
)


@router.get("")
def search(
    q: str = Query(...),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    services: AppServices = Depends(get_services),
) -> dict:
    return services.search.search(q, page=page, page_size=pageSize)
