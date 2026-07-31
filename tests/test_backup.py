"""M1：备份引擎测试（快照 / 硬链接增量 / 保留策略 / 恢复 / 降级 / cron）。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core.backup import BackupEngine, BackupScheduler, CronSpec, RetentionSpec
from app.core.vault import Vault


@pytest.fixture
def env(tmp_path: Path) -> tuple[Vault, BackupEngine]:
    root = tmp_path / "vault"
    (root / "sub").mkdir(parents=True)
    # write_bytes 避免 Windows 换行转换（\n → \r\n）
    (root / "a.md").write_bytes(("# A\n" * 50).encode("utf-8"))
    (root / "b.md").write_bytes(("# B\n" * 50).encode("utf-8"))
    (root / "sub" / "c.md").write_bytes(("# C\n" * 50).encode("utf-8"))
    vault = Vault(root=root)
    engine = BackupEngine(vault=vault, backup_root=tmp_path / "backups")
    return vault, engine


# ---------- 快照 ----------

def test_create_snapshot(env: tuple[Vault, BackupEngine]) -> None:
    _, engine = env
    snap = engine.create_snapshot(reason="manual")
    assert snap["files"] == 3
    assert snap["reason"] == "manual"
    assert snap["verify"] == "ok"
    assert engine.snapshot_files(snap["id"]) == ["a.md", "b.md", "sub/c.md"]


def test_snapshot_restores_layout(env: tuple[Vault, BackupEngine]) -> None:
    _, engine = env
    snap = engine.create_snapshot()
    tree = engine.snapshot_root / snap["id"] / "tree"
    assert (tree / "a.md").read_text(encoding="utf-8") == "# A\n" * 50


def test_incremental_hardlink(env: tuple[Vault, BackupEngine]) -> None:
    """未变文件应硬链接复用上一快照（st_ino 相同），仅变更文件重新复制。"""
    vault, engine = env
    snap1 = engine.create_snapshot()
    vault.write("a.md", "# A 修改\n" * 50)
    snap2 = engine.create_snapshot()
    tree1 = engine.snapshot_root / snap1["id"] / "tree"
    tree2 = engine.snapshot_root / snap2["id"] / "tree"
    # b.md 未变 → 硬链接（同一 inode）
    assert (tree1 / "b.md").stat().st_ino == (tree2 / "b.md").stat().st_ino
    # a.md 变更 → 新内容
    assert (tree2 / "a.md").read_text(encoding="utf-8") == "# A 修改\n" * 50


def test_hardlink_fallback_to_copy(env: tuple[Vault, BackupEngine], monkeypatch: pytest.MonkeyPatch) -> None:
    """坑 #13：os.link 失败应降级为复制而非报错。"""
    import os as _os

    _, engine = env
    engine.create_snapshot()
    engine.create_snapshot()  # 第二次会尝试硬链接
    calls = {"failed": 0}

    def fake_link(src: Path, dst: Path) -> None:
        calls["failed"] += 1
        raise OSError("not supported")

    monkeypatch.setattr(_os, "link", fake_link)
    snap = engine.create_snapshot()
    assert snap["files"] == 3
    assert calls["failed"] > 0  # 确实尝试过链接并降级


# ---------- 保留策略 ----------

def test_retention_parse() -> None:
    spec = RetentionSpec.parse("7d,4w,3m")
    assert (spec.days, spec.weeks, spec.months) == (7, 4, 3)
    with pytest.raises(Exception):
        RetentionSpec.parse("7x")


def test_cleanup_retention(env: tuple[Vault, BackupEngine]) -> None:
    _, engine = env
    engine.auto_cleanup = False  # 关闭创建时自动清理，手动控制
    engine.retention = RetentionSpec(days=2, weeks=0, months=0)  # 只保留最近 2 天各 1 份
    ids = []
    for i in range(4):
        snap = engine.create_snapshot(reason="test")
        ids.append(snap["id"])
        # 伪造时间戳使快照跨天
        created = datetime.now() - timedelta(days=3 - i)
        mf = engine.snapshot_root / snap["id"] / "manifest.json"
        data = json.loads(mf.read_text(encoding="utf-8"))
        data["createdAt"] = created.isoformat(timespec="seconds")
        mf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    removed = engine.cleanup_retention()
    assert len(removed) == 2  # 4 天窗口保留 2 份
    remaining = {s["id"] for s in engine.list_snapshots()}
    assert len(remaining) == 2


# ---------- 恢复 ----------

def test_backup_for_write(env: tuple[Vault, BackupEngine]) -> None:
    vault, engine = env
    path = engine.backup_for_write("a.md")
    assert Path(path).is_file()
    engine.vault.write("a.md", "changed")
    assert engine.backup_for_write("a.md") == path  # 已有备份不重复写


def test_restore_file(env: tuple[Vault, BackupEngine]) -> None:
    vault, engine = env
    snap = engine.create_snapshot()
    vault.write("a.md", "# 被改坏\n")
    engine.restore_file("a.md", snap["id"])
    assert vault.read("a.md").text == "# A\n" * 50
    # 恢复前当前版本应备份到 pre-restore
    pre = engine.backup_root / "pre-restore" / "a.md.bak"
    assert pre.read_text(encoding="utf-8") == "# 被改坏\n"


def test_restore_all(env: tuple[Vault, BackupEngine]) -> None:
    vault, engine = env
    snap = engine.create_snapshot()
    vault.write("a.md", "# 改坏\n")
    vault.create("垃圾.md", "x")
    result = engine.restore_all(snap["id"])
    assert result["restored"] == 3
    assert vault.read("a.md").text == "# A\n" * 50
    assert not (vault.root / "垃圾.md").exists()
    assert result["preRestoreSnap"]  # 恢复前强制快照（坑 #14）


def test_verify_detects_corruption(env: tuple[Vault, BackupEngine]) -> None:
    _, engine = env
    snap = engine.create_snapshot()
    (engine.snapshot_root / snap["id"] / "tree" / "b.md").write_text("corrupted", encoding="utf-8")
    ok, msg = engine.verify_snapshot(snap["id"])
    assert not ok
    assert "checksum" in msg


# ---------- cron ----------

def test_cron_parse_daily() -> None:
    spec = CronSpec.parse("0 2 * * *")
    now = datetime(2026, 7, 31, 22, 30)
    assert spec.next_run(now) == datetime(2026, 8, 1, 2, 0)


def test_cron_parse_step_and_list() -> None:
    spec = CronSpec.parse("*/15 9,18 * * 1-5")
    assert spec.minute == {0, 15, 30, 45}
    assert spec.hour == {9, 18}
    assert spec.weekday == {1, 2, 3, 4, 5}  # cron 惯例：0=周日 → 1-5 = 周一到周五
    now = datetime(2026, 7, 31, 22, 30)  # 周五
    assert spec.next_run(now) == datetime(2026, 8, 3, 9, 0)  # 下一个工作日 9:00


def test_cron_next_run_same_hour() -> None:
    spec = CronSpec.parse("0 2 * * *")
    now = datetime(2026, 7, 31, 2, 0)
    assert spec.next_run(now) == datetime(2026, 8, 1, 2, 0)  # 不含当前时刻


def test_scheduler_disabled_when_empty(env: tuple[Vault, BackupEngine]) -> None:
    _, engine = env
    sched = BackupScheduler(engine, "")
    sched.start()
    assert sched._thread is None  # 空表达式不启动线程
