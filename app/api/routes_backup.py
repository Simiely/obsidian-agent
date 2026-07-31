"""/api/backup：快照列表 / 创建 / 状态 / 单文件历史与恢复 / 整库恢复 / 删除。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import AppServices, get_services, require_auth
from app.core.backup import BackupError

router = APIRouter(
    prefix="/api/backup", tags=["backup"], dependencies=[Depends(require_auth)]
)

RESTORE_CONFIRM_CODE = "RESTORE"


class RestoreFileBody(BaseModel):
    path: str
    snapshotId: str


class RestoreAllBody(BaseModel):
    snapshotId: str
    confirmCode: str


@router.get("/list")
def list_backups(services: AppServices = Depends(get_services)) -> dict:
    return {
        "snapshots": services.backup.list_snapshots(),
        "retention": services.backup.retention,
        "runner": services.backup_runner.status(),
    }


@router.post("/now", status_code=202)
def backup_now(services: AppServices = Depends(get_services)) -> dict:
    try:
        services.backup_runner.run_backup("manual")
    except BackupError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "taskId": "backup-manual"}


@router.get("/status")
def backup_status(services: AppServices = Depends(get_services)) -> dict:
    return services.backup_runner.status()


@router.get("/history")
def history(path: str, services: AppServices = Depends(get_services)) -> dict:
    """单文件版本历史：来自快照 + 写前备份（pre-write）。"""
    versions: list[dict] = []
    for s in services.backup.list_snapshots():
        snap_file = services.backup.snapshot_root / s["id"] / "tree" / path
        if snap_file.is_file():
            versions.append(
                {"snapshotId": s["id"], "at": s["createdAt"], "source": "snapshot"}
            )
    pre_write = services.backup.backup_root / "pre-write" / f"{path}.bak"
    if pre_write.is_file():
        versions.append(
            {
                "snapshotId": "pre-write",
                "at": pre_write.stat().st_mtime,
                "source": "pre-write",
            }
        )
    return {"path": path, "versions": versions}


@router.post("/restore-file")
def restore_file(body: RestoreFileBody, services: AppServices = Depends(get_services)) -> dict:
    try:
        target = services.backup.restore_file(body.path, body.snapshotId)
    except BackupError as e:
        raise HTTPException(404, str(e)) from e
    services.index.update_paths({body.path})
    return {"path": body.path, "restoredFrom": body.snapshotId, "target": target}


@router.post("/restore", status_code=202)
def restore_all(body: RestoreAllBody, services: AppServices = Depends(get_services)) -> dict:
    if body.confirmCode != RESTORE_CONFIRM_CODE:
        raise HTTPException(400, "确认码错误：需输入 RESTORE")
    try:
        services.backup_runner.run_restore(body.snapshotId, after=services.index.full_rebuild)
    except BackupError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "taskId": "restore", "snapshotId": body.snapshotId}


@router.delete("/{snap_id}")
def delete_snapshot(snap_id: str, services: AppServices = Depends(get_services)) -> dict:
    try:
        services.backup.delete_snapshot(snap_id)
    except BackupError as e:
        raise HTTPException(404, str(e)) from e
    return {"ok": True, "deleted": snap_id}
