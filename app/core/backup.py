"""备份子系统：整库快照（硬链接增量）/ 定时调度 / 保留策略 / 校验 / 恢复。

设计（docs/09 §10）：
- 快照 = 普通目录树（可人工直接读取），未变文件硬链接复用上一快照（零空间占用）
- os.link 失败自动降级为复制（坑 #13）
- 备份根目录必须位于 vault 之外（坑 #11，由 config.validate_paths 保证）
- 恢复前强制先建"恢复前快照"（坑 #14）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from app.core.vault import Vault, copy_file

logger = logging.getLogger("obsidian-agent.backup")

SNAPSHOT_MANIFEST = "manifest.json"
PRE_WRITE_DIR = "pre-write"
PRE_RESTORE_DIR = "pre-restore"
SNAPSHOT_DIR = "snapshots"


class BackupError(Exception):
    """备份/恢复错误基类。"""


@dataclass
class RetentionSpec:
    days: int = 7
    weeks: int = 4
    months: int = 3

    @classmethod
    def parse(cls, expr: str) -> RetentionSpec:
        """解析 "7d,4w,3m" 形式。"""
        spec = cls()
        for part in expr.split(","):
            part = part.strip().lower()
            if not part:
                continue
            unit = part[-1]
            try:
                n = int(part[:-1])
            except ValueError as e:
                raise BackupError(f"无效保留策略: {expr!r}") from e
            if unit == "d":
                spec.days = n
            elif unit == "w":
                spec.weeks = n
            elif unit == "m":
                spec.months = n
            else:
                raise BackupError(f"无效保留策略单位: {expr!r}")
        return spec


@dataclass
class CronSpec:
    """极简 cron：支持 `*`、数字、`*/n`、`a,b,c`；仅分钟/小时/日/月/周 5 段。"""

    minute: set[int] | None = None  # None = 任意
    hour: set[int] | None = None
    day: set[int] | None = None
    month: set[int] | None = None
    weekday: set[int] | None = None  # 0=周日

    @classmethod
    def parse(cls, expr: str) -> CronSpec:
        parts = expr.split()
        if len(parts) != 5:
            raise BackupError(f"无效 cron 表达式（需 5 段）: {expr!r}")
        return cls(
            minute=_parse_field(parts[0], 0, 59),
            hour=_parse_field(parts[1], 0, 23),
            day=_parse_field(parts[2], 1, 31),
            month=_parse_field(parts[3], 1, 12),
            weekday=_parse_field(parts[4], 0, 6),
        )

    def next_run(self, after: datetime) -> datetime:
        """计算 after 之后的下一次触发时间（按分钟步进扫描，最多 7 天跨周末）。"""
        now = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(7 * 24 * 60):
            if self._match(now):
                return now
            now += timedelta(minutes=1)
        raise BackupError(f"无法在 7 天内找到 cron 触发点: {self}")  # pragma: no cover

    def _match(self, dt: datetime) -> bool:
        if self.minute is not None and dt.minute not in self.minute:
            return False
        if self.hour is not None and dt.hour not in self.hour:
            return False
        if self.month is not None and dt.month not in self.month:
            return False
        # day 与 weekday 的 OR 语义简化：day 任意时只看 weekday，否则两者都要匹配
        # cron 惯例 weekday 0=周日，Python weekday() 0=周一 → 转换 (wd+1)%7
        if self.day is None and self.weekday is None:
            return True
        day_ok = self.day is None or dt.day in self.day
        wd_ok = self.weekday is None or ((dt.weekday() + 1) % 7) in self.weekday
        if self.day is None:
            return wd_ok
        if self.weekday is None:
            return day_ok
        return day_ok and wd_ok


def _parse_field(field: str, lo: int, hi: int) -> set[int] | None:
    field = field.strip()
    if field == "*":
        return None
    values: set[int] = set()
    for part in field.split(","):
        if "/" in part:
            base, step_s = part.split("/")
            step = int(step_s)
            start = lo if base == "*" else int(base)
            values.update(range(start, hi + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            values.update(range(int(a), int(b) + 1))
        else:
            values.add(int(part))
    return values


class BackupEngine:
    """快照引擎。backup_root 布局：

    <backup_root>/
      snapshots/<snap-id>/manifest.json + tree/<relpath>
      pre-write/<relpath>.bak
      pre-restore/<relpath>.bak
    """

    def __init__(
        self,
        vault: Vault,
        backup_root: Path,
        retention: RetentionSpec | str = "7d,4w,3m",
        max_bytes: int = 10 * 1024 * 1024,
        verify: bool = True,
        enabled: bool = True,
        auto_cleanup: bool = True,
    ) -> None:
        self.vault = vault
        self.backup_root = backup_root.expanduser().resolve()
        self.retention = (
            retention if isinstance(retention, RetentionSpec) else RetentionSpec.parse(retention)
        )
        self.max_bytes = max_bytes
        self.verify_enabled = verify
        self.enabled = enabled
        self.auto_cleanup = auto_cleanup
        self.snapshot_root = self.backup_root / SNAPSHOT_DIR
        self.snapshot_root.mkdir(parents=True, exist_ok=True)

    # ---------- 快照 ----------

    def create_snapshot(self, reason: str = "manual") -> dict[str, Any]:
        if not self.enabled:
            raise BackupError("备份已禁用（BACKUP_ENABLED=false）")
        # 微秒精度：同一秒内多次快照 createdAt 仍可排序（否则增量判断退化）
        created_at = datetime.now().isoformat(timespec="microseconds")
        # id 含秒级时间戳 + 唯一后缀：同一秒多次快照不冲突（否则增量判断失效）
        snap_id = "snap-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        dest = self.snapshot_root / snap_id / "tree"
        prev_tree = self._latest_snapshot_tree_before(created_at)

        files = 0
        total_bytes = 0
        skipped: list[str] = []
        checksums: dict[str, str] = {}
        for rel, src in self.vault.walk_all():
            if src.stat().st_size > self.max_bytes:
                skipped.append(rel)
                continue
            dst = dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if prev_tree is not None:
                prev_src = prev_tree / rel
                if prev_src.is_file() and self._same_file(prev_src, src):
                    if self._hardlink_or_copy(prev_src, dst):
                        files += 1
                        total_bytes += src.stat().st_size
                        continue
            shutil.copy2(src, dst)
            files += 1
            total_bytes += src.stat().st_size

        # 抽样 checksum 基准（verify 用）：最多 100 个文件
        sample = sorted(dest.rglob("*"))[:100]
        for f in sample:
            if f.is_file():
                rel_md = f.relative_to(dest).as_posix()
                checksums[rel_md] = _md5(f)

        manifest = {
            "id": snap_id,
            "createdAt": created_at,
            "reason": reason,
            "files": files,
            "bytes": total_bytes,
            "skipped": skipped,
            "checksums": checksums,
            "verify": "pending",
        }
        self._write_manifest(snap_id, manifest)
        if self.verify_enabled:
            ok, msg = self.verify_snapshot(snap_id)
            manifest["verify"] = "ok" if ok else f"fail: {msg}"
            self._write_manifest(snap_id, manifest)
        if self.auto_cleanup:
            self.cleanup_retention()
        logger.info(
            "快照完成 %s files=%s bytes=%s skipped=%s", snap_id, files, total_bytes, len(skipped)
        )
        return self.get_snapshot(snap_id)

    def get_snapshot(self, snap_id: str) -> dict[str, Any]:
        mf = self.snapshot_root / snap_id / SNAPSHOT_MANIFEST
        if not mf.is_file():
            raise BackupError(f"快照不存在: {snap_id}")
        return cast(dict[str, Any], json.loads(mf.read_text(encoding="utf-8")))

    def list_snapshots(self) -> list[dict[str, Any]]:
        """按 createdAt 降序（id 含随机后缀，不能按 id 排序）。"""
        snaps = []
        for mf in self.snapshot_root.glob(f"*/{SNAPSHOT_MANIFEST}"):
            snaps.append(json.loads(mf.read_text(encoding="utf-8")))
        snaps.sort(key=lambda s: s["createdAt"], reverse=True)
        return snaps

    def snapshot_files(self, snap_id: str) -> list[str]:
        tree = self.snapshot_root / snap_id / "tree"
        if not tree.is_dir():
            raise BackupError(f"快照目录不存在: {snap_id}")
        return sorted(p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file())

    def verify_snapshot(self, snap_id: str) -> tuple[bool, str]:
        """抽样校验：文件数一致 + manifest 记录的抽样 checksum 比对。"""
        mf = self.snapshot_root / snap_id / SNAPSHOT_MANIFEST
        if not mf.is_file():
            return False, "manifest 缺失"
        data = json.loads(mf.read_text(encoding="utf-8"))
        files = self.snapshot_files(snap_id)
        if len(files) != data["files"]:
            return False, f"文件数不一致 manifest={data['files']} 实际={len(files)}"
        tree = self.snapshot_root / snap_id / "tree"
        for rel, expected in (data.get("checksums") or {}).items():
            if _md5(tree / rel) != expected:
                return False, f"checksum 不一致: {rel}"
        return True, "ok"

    # ---------- 保留策略 ----------

    def cleanup_retention(self) -> list[str]:
        """按 日/周/月 桶保留最新快照，其余删除。返回被删除的 snap_id 列表。"""
        snaps = self.list_snapshots()
        removed: list[str] = []
        seen_days: set[tuple[int, int, int]] = set()
        seen_weeks: set[tuple[int, int]] = set()
        seen_months: set[tuple[int, int]] = set()
        for s in snaps:  # 已按时间降序
            snap_id = s["id"]
            created = datetime.fromisoformat(s["createdAt"])
            d = created.date()
            day_key = (d.year, d.month, d.day)
            week_key = d.isocalendar()[:2]
            month_key = (d.year, d.month)
            keep = False
            if len(seen_days) < self.retention.days and day_key not in seen_days:
                seen_days.add(day_key)
                keep = True
            elif len(seen_weeks) < self.retention.weeks and week_key not in seen_weeks:
                seen_weeks.add(week_key)
                keep = True
            elif len(seen_months) < self.retention.months and month_key not in seen_months:
                seen_months.add(month_key)
                keep = True
            if not keep:
                shutil.rmtree(self.snapshot_root / snap_id, ignore_errors=True)
                removed.append(snap_id)
        if removed:
            logger.info("保留策略清理 %s", removed)
        return removed

    # ---------- 恢复 ----------

    def delete_snapshot(self, snap_id: str) -> None:
        """删除指定快照（API DELETE /api/backup/{id}）。"""
        d = self.snapshot_root / snap_id
        if not d.is_dir():
            raise BackupError(f"快照不存在: {snap_id}")
        shutil.rmtree(d, ignore_errors=True)

    def backup_for_write(self, rel: str) -> str:
        """写前单文件备份（M5 safety 调用）→ pre-write/<rel>.bak。"""
        src = self.vault.resolve_safe_path(rel, must_exist=True)
        dst = self.backup_root / PRE_WRITE_DIR / (rel + ".bak")
        if not dst.exists():
            copy_file(src, dst)
        return dst.as_posix()

    def restore_file(self, rel: str, snap_id: str) -> str:
        """从快照恢复单文件；恢复前把当前版本备份到 pre-restore（坑 #14 可回退）。"""
        src = self.snapshot_root / snap_id / "tree" / rel
        if not src.is_file():
            raise BackupError(f"快照 {snap_id} 中不存在文件: {rel}")
        target = self.vault.resolve_safe_path(rel, md_only=True)
        if target.is_file():
            copy_file(target, self.backup_root / PRE_RESTORE_DIR / (rel + ".bak"))
        copy_file(src, target)
        return target.as_posix()

    def restore_all(self, snap_id: str) -> dict[str, Any]:
        """整库恢复：① 强制先建恢复前快照 ② 清空 vault ③ 从快照复制回。"""
        if not self.enabled:
            raise BackupError("备份已禁用")
        pre = self.create_snapshot(reason="pre-restore")
        tree = self.snapshot_root / snap_id / "tree"
        if not tree.is_dir():
            raise BackupError(f"快照目录不存在: {snap_id}")
        for _rel, src in self.vault.walk_all():
            src.unlink()
        restored = 0
        for rel in self.snapshot_files(snap_id):
            if self.vault.is_ignored(rel):
                continue
            copy_file(tree / rel, self.vault.root / rel)
            restored += 1
        logger.info(
            "整库恢复完成 snap=%s restored=%s preRestoreSnap=%s", snap_id, restored, pre["id"]
        )
        return {"snapId": snap_id, "restored": restored, "preRestoreSnap": pre["id"]}

    # ---------- 内部 ----------

    def _latest_snapshot_tree_before(self, created_at: str) -> Path | None:
        """返回 createdAt 早于指定时间的最新快照 tree（用于硬链接增量）。"""
        for s in self.list_snapshots():
            if s["createdAt"] < created_at:
                return self.snapshot_root / str(s["id"]) / "tree"
        return None

    def _same_file(self, a: Path, b: Path) -> bool:
        sa, sb = a.stat(), b.stat()
        return sa.st_size == sb.st_size and sa.st_mtime_ns == sb.st_mtime_ns

    def _hardlink_or_copy(self, src: Path, dst: Path) -> bool:
        """硬链接复用，失败降级复制（坑 #13）。"""
        try:
            os.link(src, dst)
            return True
        except OSError:
            shutil.copy2(src, dst)
            return False

    def _write_manifest(self, snap_id: str, manifest: dict[str, Any]) -> None:
        (self.snapshot_root / snap_id / SNAPSHOT_MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class BackupRunner:
    """后台备份/恢复任务包装（API 层使用），单线程槽位，可查状态。"""

    def __init__(self, engine: BackupEngine) -> None:
        self.engine = engine
        self._thread: threading.Thread | None = None
        self.last: dict[str, Any] | None = None
        self.error: str | None = None
        self._kind: str | None = None

    def run_backup(self, reason: str = "manual") -> None:
        if self._busy():
            raise BackupError("已有备份/恢复任务进行中")
        self._start("backup", reason)

    def run_restore(self, snap_id: str, after: Callable[[], Any] | None = None) -> None:
        if self._busy():
            raise BackupError("已有备份/恢复任务进行中")
        self._start("restore", snap_id, after)

    def _start(self, kind: str, arg: str, after: Callable[[], Any] | None = None) -> None:
        self._kind = kind
        self.error = None
        self._thread = threading.Thread(
            target=self._work, args=(kind, arg, after), daemon=True, name=f"backup-{kind}"
        )
        self._thread.start()

    def _work(self, kind: str, arg: str, after: Callable[[], Any] | None) -> None:
        try:
            if kind == "backup":
                self.last = self.engine.create_snapshot(reason=arg)
            else:
                self.last = self.engine.restore_all(arg)
                if after:
                    after()  # 整库恢复后重建索引
        except Exception as e:  # pragma: no cover - 异常统一记录
            self.error = str(e)
            logger.exception("后台 %s 任务失败", kind)

    def _busy(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict[str, Any]:
        return {
            "running": self._busy(),
            "kind": self._kind,
            "lastAt": (self.last or {}).get("createdAt"),
            "lastReason": (self.last or {}).get("reason"),
            "error": self.error,
            "snapshots": len(self.engine.list_snapshots()),
        }


class BackupScheduler:
    """定时快照线程：每 30s 检查 cron 是否到点。"""

    def __init__(self, engine: BackupEngine, cron_expr: str) -> None:
        self.engine = engine
        self.spec = CronSpec.parse(cron_expr) if cron_expr.strip() else None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_fired: datetime | None = None

    def start(self) -> None:
        if self.spec is None:
            logger.info("备份调度未启用（BACKUP_SCHEDULE 为空）")
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="backup-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                now = datetime.now()
                assert self.spec is not None
                if self._last_fired is None or now >= self.spec.next_run(self._last_fired):
                    if self._last_fired is not None:
                        self.engine.create_snapshot(reason="scheduled")
                    self._last_fired = now
            except Exception:  # pragma: no cover
                logger.exception("定时快照失败")
            self._stop.wait(30)


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
