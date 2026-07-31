"""/api/vault：目录树 / 文件读写 / 元信息。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import AppServices, ensure_writable, get_services, require_auth
from app.core.vault import FileTooLarge, PathNotAllowed

router = APIRouter(prefix="/api/vault", tags=["vault"], dependencies=[Depends(require_auth)])


class FileWrite(BaseModel):
    path: str
    content: str


class FileCreate(BaseModel):
    path: str
    content: str = ""


@router.get("/tree")
def tree(
    path: str = "",
    services: AppServices = Depends(get_services),
) -> list[dict[str, Any]]:
    try:
        return services.vault.tree(path)
    except (FileNotFoundError, PathNotAllowed) as e:
        raise HTTPException(
            status_code=404 if isinstance(e, FileNotFoundError) else 422, detail=str(e)
        ) from e


@router.get("/file")
def read_file(
    path: str = Query(...),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        content = services.vault.read(path)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except PathNotAllowed as e:
        raise HTTPException(422, str(e)) from e
    meta = content.meta
    return {
        "path": path,
        "content": content.text,
        "meta": {
            "size": meta.size,
            "mtime": meta.mtime,
            "encoding": meta.encoding,
            "newline": meta.newline,
        },
    }


@router.put("/file")
def write_file(
    body: FileWrite,
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    ensure_writable(services, body.path)
    try:
        services.vault.write(body.path, body.content)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except (PathNotAllowed, FileTooLarge) as e:
        raise HTTPException(422 if isinstance(e, PathNotAllowed) else 403, str(e)) from e
    services.index.update_paths({body.path})
    return {"path": body.path, "ok": True}


@router.post("/file", status_code=201)
def create_file(
    body: FileCreate,
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    ensure_writable(services, body.path)
    try:
        services.vault.create(body.path, body.content)
    except FileExistsError as e:
        raise HTTPException(409, str(e)) from e
    except PathNotAllowed as e:
        raise HTTPException(422, str(e)) from e
    services.index.update_paths({body.path})
    return {"path": body.path, "ok": True}


@router.delete("/file")
def delete_file(
    path: str = Query(...),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    ensure_writable(services, path)
    try:
        services.vault.delete(path)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except PathNotAllowed as e:
        raise HTTPException(422, str(e)) from e
    services.index.update_paths({path})
    return {"path": path, "ok": True}


@router.get("/meta")
def meta(
    path: str = Query(...),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        m = services.vault.meta(path)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except PathNotAllowed as e:
        raise HTTPException(422, str(e)) from e
    return {
        "path": path,
        "size": m.size,
        "mtime": m.mtime,
        "encoding": m.encoding,
        "newline": m.newline,
    }
