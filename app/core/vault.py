"""Vault 访问核心：目录树 / 读写（编码容错）/ 忽略规则 / 路径安全 / watchdog 监听。

设计约束（见 docs/02-架构设计.md §2）：
- 不 import 任何 Web 框架
- 写操作全部原子写（临时文件 + os.replace），防止半截文件
- 路径一律走 resolve_safe_path()（坑 #7 路径穿越防护）
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("obsidian-agent.vault")

MAX_DECODE_ATTEMPTS = 3  # utf-8-sig / utf-8 / gb18030

# 与 docs/04-配置参考.md IGNORE_DIRS 默认值保持一致
DEFAULT_IGNORE_DIRS = [".obsidian", ".trash", ".git", "node_modules", ".stfolder"]


class VaultError(Exception):
    """Vault 领域错误基类。"""


class PathNotAllowed(VaultError):
    """路径非法（越界 / 绝对路径 / 非 md）。"""


class FileTooLarge(VaultError):
    """文件超过 MAX_FILE_BYTES。"""


@dataclass
class FileMeta:
    size: int
    mtime_ns: int
    mtime: str
    encoding: str
    newline: str


@dataclass
class FileContent:
    text: str
    meta: FileMeta


def _decode(data: bytes) -> tuple[str, str]:
    """解码顺序：utf-8-sig（自动去 BOM）→ utf-8 → gb18030（坑 #6 编码容错）。"""
    last_err: UnicodeDecodeError | None = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError as e:  # pragma: no cover - 异常分支
            last_err = e
    raise VaultError(f"无法解码文件内容（尝试 utf-8/gb18030 均失败）: {last_err}")  # pragma: no cover


def _detect_newline(text: str) -> str:
    """检测首个换行风格（坑 #6：写入保留原文件换行）。"""
    crlf = text.find("\r\n")
    lf = text.find("\n")
    if crlf == -1 or (lf != -1 and lf < crlf):
        return "\n"
    return "\r\n"


def _normalize_newline(text: str, target: str) -> str:
    if target == "\n":
        return text.replace("\r\n", "\n")
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


class Vault:
    """一个 Obsidian 库（vault）的文件系统门面。"""

    def __init__(
        self,
        root: Path,
        ignore_dirs: list[str] | None = None,
        ignore_files: list[str] | None = None,
        max_file_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.ignore_dirs = set(ignore_dirs if ignore_dirs is not None else DEFAULT_IGNORE_DIRS)
        self.ignore_files = set(ignore_files or [])
        self.max_file_bytes = max_file_bytes
        if not self.root.is_dir():
            raise VaultError(f"vault 路径不存在或不是目录: {self.root}")

    # ---------- 路径安全（坑 #7） ----------

    def resolve_safe_path(
        self,
        rel: str | Path,
        *,
        must_exist: bool = False,
        md_only: bool = False,
    ) -> Path:
        """将 vault 内相对路径规范化为绝对路径，禁止越界 / 绝对路径 / 盘符 / 非 md。"""
        rel_str = rel.as_posix() if isinstance(rel, Path) else str(rel).replace("\\", "/")
        p = Path(rel_str)
        if p.is_absolute() or (len(rel_str) >= 2 and rel_str[1] == ":"):
            raise PathNotAllowed(f"非法路径：不允许绝对路径/盘符: {rel_str!r}")
        full = (self.root / p).resolve()
        try:
            full.relative_to(self.root)
        except ValueError as e:
            raise PathNotAllowed(f"非法路径：越出 vault 范围: {rel_str!r}") from e
        if md_only and full.suffix.lower() != ".md":
            raise PathNotAllowed(f"仅允许 .md 文件: {rel_str!r}")
        if must_exist and not full.is_file():
            raise FileNotFoundError(f"文件不存在: {rel_str!r}")
        return full

    # ---------- 忽略规则 ----------

    def is_ignored(self, rel: str | Path) -> bool:
        """点开头目录/文件、配置的 ignore_dirs、ignore_files 一律忽略。"""
        rel_str = rel.as_posix() if isinstance(rel, Path) else str(rel).replace("\\", "/")
        for part in Path(rel_str).parts:
            if part in self.ignore_dirs or part in self.ignore_files:
                return True
            if part.startswith("."):
                return True
        return False

    def walk_all(self) -> list[tuple[str, Path]]:
        """遍历 vault 内全部非忽略文件（含附件），返回 (相对路径, 绝对路径)。"""
        out: list[tuple[str, Path]] = []
        for abs_path in self.root.rglob("*"):
            if not abs_path.is_file():
                continue
            rel = abs_path.relative_to(self.root).as_posix()
            if self.is_ignored(rel):
                continue
            out.append((rel, abs_path))
        return out

    def walk_md(self) -> list[tuple[str, Path]]:
        return [(rel, p) for rel, p in self.walk_all() if p.suffix.lower() == ".md"]

    # ---------- 元信息 / 树 ----------

    def meta(self, rel: str | Path) -> FileMeta:
        full = self.resolve_safe_path(rel, must_exist=True)
        st = full.stat()
        data = full.read_bytes()
        text, encoding = _decode(data)
        newline = _detect_newline(text)
        return FileMeta(
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            mtime=st.st_mtime,
            encoding=encoding,
            newline=newline,
        )

    def tree(self, rel: str | Path = "") -> list[dict]:
        """目录树（相对路径、按名称排序）。tags 由 M2 markdown 解析后由 API 层补充。"""
        base = self.resolve_safe_path(rel, md_only=False) if str(rel) else self.root
        if not base.is_dir():
            raise FileNotFoundError(f"目录不存在: {rel!r}")
        nodes: list[dict] = []
        for entry in sorted(base.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            rel_child = entry.relative_to(self.root).as_posix()
            if entry.is_dir():
                if self.is_ignored(rel_child):
                    continue
                nodes.append({"name": entry.name, "path": rel_child, "type": "dir", "children": []})
            elif entry.is_file():
                if self.is_ignored(rel_child):
                    continue
                st = entry.stat()
                nodes.append(
                    {
                        "name": entry.name,
                        "path": rel_child,
                        "type": "file",
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                    }
                )
        return nodes

    # ---------- 读写 ----------

    def read(self, rel: str | Path) -> FileContent:
        full = self.resolve_safe_path(rel, must_exist=True)
        st = full.stat()
        text, encoding = _decode(full.read_bytes())
        newline = _detect_newline(text)
        meta = FileMeta(
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            mtime=st.st_mtime,
            encoding=encoding,
            newline=newline,
        )
        return FileContent(text=text, meta=meta)

    def write(self, rel: str | Path, content: str, *, newline: str | None = None) -> Path:
        """原子写：内容恒为 UTF-8 无 BOM；换行默认保留原文件风格（坑 #6）。"""
        full = self.resolve_safe_path(rel, must_exist=True, md_only=True)
        if full.stat().st_size > self.max_file_bytes:
            raise FileTooLarge(f"文件超过 {self.max_file_bytes} 字节，禁止直接重写（可用编辑器人工处理）: {full}")
        target_newline = newline or self._existing_newline(full) or "\n"
        data = _normalize_newline(content, target_newline).encode("utf-8")
        self._atomic_write(full, data)
        return full

    def create(self, rel: str | Path, content: str = "") -> Path:
        full = self.resolve_safe_path(rel, md_only=True)
        if full.exists():
            raise FileExistsError(f"文件已存在: {rel!r}")
        self._atomic_write(full, content.encode("utf-8"))
        return full

    def delete(self, rel: str | Path) -> None:
        full = self.resolve_safe_path(rel, must_exist=True)
        if self.is_ignored(full.relative_to(self.root).as_posix()):
            raise PathNotAllowed(f"禁止删除忽略目录内文件: {rel!r}")
        full.unlink()

    def _existing_newline(self, full: Path) -> str | None:
        try:
            text, _ = _decode(full.read_bytes())
            return _detect_newline(text)
        except VaultError:
            return None

    def _atomic_write(self, full: Path, data: bytes) -> None:
        full.parent.mkdir(parents=True, exist_ok=True)
        tmp = full.with_name(f"{full.name}.tmp-{uuid.uuid4().hex[:8]}")
        tmp.write_bytes(data)
        os.replace(tmp, full)


# ---------- watchdog 监听（debounce，坑 #4） ----------

class VaultWatcher:
    """文件变更监听：事件去重 + debounce 合并，回调 on_change(set[相对路径])。"""

    def __init__(
        self,
        vault: Vault,
        debounce_seconds: float = 2.0,
        on_change: callable | None = None,  # type: ignore[name-defined]
    ) -> None:
        from watchdog.observers import Observer  # 延迟导入，非核心路径不依赖 watchdog

        self.vault = vault
        self.debounce_seconds = debounce_seconds
        self.on_change = on_change or (lambda paths: None)
        self._observer = Observer()
        self._timer: threading.Timer | None = None
        self._pending: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        from watchdog.events import FileSystemEventHandler  # 延迟导入

        class _Handler(FileSystemEventHandler):
            def __init__(self, watcher: VaultWatcher) -> None:
                self._watcher = watcher

            def on_any_event(self, event) -> None:  # type: ignore[no-untyped-def]
                src = getattr(event, "src_path", None)
                if not src:
                    return
                try:
                    rel = Path(src).resolve().relative_to(self._watcher.vault.root).as_posix()
                except ValueError:
                    return
                if self._watcher.vault.is_ignored(rel):
                    return
                self._watcher._schedule(rel)

        self._observer.schedule(_Handler(self), str(self.vault.root), recursive=True)
        self._observer.daemon = True
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=3)
        if self._timer:
            self._timer.cancel()

    def _schedule(self, rel: str) -> None:
        with self._lock:
            self._pending.add(rel)
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_seconds, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            paths = set(self._pending)
            self._pending.clear()
            self._timer = None
        if paths:
            try:
                self.on_change(paths)
            except Exception:  # pragma: no cover - 回调异常不应拖垮监听线程
                logger.exception("on_change 回调异常")


def copy_file(src: Path, dst: Path) -> None:
    """复制文件（供备份/恢复使用），自动建父目录。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
