"""SQLite FTS5 + jieba 预分词实现（默认索引后端，零外部依赖）。

中文策略（docs/09 §2.3）：
- 入库前用 jieba 分词，token 以空格连接，供 FTS5 unicode61 切词
- 检索 query 走同一分词管线；含 FTS5 特殊字符（- + 等）的词加引号防注入
- body_original 列存原文（UNINDEXED），供 search 层做原文高亮/定位
- 断点续传：meta 表记录 last_full_at / total
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import jieba

from app.core.indexer.base import IndexDoc, IndexRow

logger = logging.getLogger("obsidian-agent.indexer.fts5")

_TABLE = "docs_fts"
_META = "meta"

# 该字符集外的 token 视为含特殊字符，查询时加引号
_SAFE_TOKEN_RE = re.compile(r"^[\w\u4e00-\u9fff]+$", re.UNICODE)


def _meaningful(word: str) -> bool:
    """是否含有效字符（字母/数字/中文）——过滤纯标点 token（如 '-'）。"""
    return any(c.isalnum() or "\u4e00" <= c <= "\u9fff" for c in word)


def tokenize(text: str) -> str:
    """jieba 分词 → 空格连接的预分词串（过滤纯标点）。"""
    return " ".join(w for w in jieba.lcut(text, cut_all=False) if _meaningful(w))


def tokenize_query(text: str) -> str:
    """查询分词：安全词不加引号（保持 AND 语义），特殊字符词加引号防语法注入。"""
    parts: list[str] = []
    for word in jieba.lcut(text, cut_all=False):
        word = word.strip()
        if not word or not _meaningful(word):
            continue
        if _SAFE_TOKEN_RE.match(word):
            parts.append(word)
        else:
            parts.append('"' + word.replace('"', '""') + '"')
    return " ".join(parts)


class Fts5Index:
    name = "fts5"

    def __init__(self, db_path: Path, userdict: str | None = None) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)  # data_dir 可能未创建
        if userdict and Path(userdict).is_file():
            jieba.load_userdict(str(Path(userdict).expanduser()))
            logger.info("已加载 jieba 自定义词典: %s", userdict)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # 单连接跨线程共享（check_same_thread=False 仅关闭检查，不代表线程安全）：
        # 后台索引线程 / API 请求线程 / 状态轮询并发访问同一连接会导致
        # SQLite 内部状态错乱 → Linux 上 segfault（GitHub Actions 实测崩溃）。
        # 用 RLock 串行化全部连接操作。
        self._lock = threading.RLock()
        self._init_schema()

    # ---------- schema ----------

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                f"""CREATE VIRTUAL TABLE IF NOT EXISTS {_TABLE} USING fts5(
                    path UNINDEXED,
                    title,
                    body,
                    tags,
                    body_original UNINDEXED,
                    mtime_ns UNINDEXED,
                    tokenize='unicode61'
                )"""
            )
            self._conn.execute(f"CREATE TABLE IF NOT EXISTS {_META} (k TEXT PRIMARY KEY, v TEXT)")

    # ---------- 写入 ----------

    def build(self, docs: Iterable[IndexDoc], batch_size: int = 200) -> int:
        """全量重建（先清空再分批写入）。返回索引文档数。"""
        with self._lock:
            with self._conn:
                self._conn.execute(f"DELETE FROM {_TABLE}")
            count = 0
            batch: list[tuple[Any, ...]] = []
            for doc in docs:
                batch.append(self._row_tuple(doc))
                count += 1
                if len(batch) >= batch_size:
                    self._insert_batch(batch)
                    batch.clear()
            if batch:
                self._insert_batch(batch)
            self._set_meta("total", str(count))
            self._set_meta("last_full_at", datetime.now().isoformat(timespec="seconds"))
            logger.info("索引重建完成 total=%s", count)
            return count

    def upsert(self, doc: IndexDoc) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(f"DELETE FROM {_TABLE} WHERE path = ?", (doc.path,))
                self._conn.execute(
                    f"INSERT INTO {_TABLE} (path, title, body, tags, body_original, mtime_ns) "
                    f"VALUES (?, ?, ?, ?, ?, ?)",
                    self._row_tuple(doc),
                )

    def remove(self, path: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(f"DELETE FROM {_TABLE} WHERE path = ?", (path,))

    def _insert_batch(self, rows: list[tuple[Any, ...]]) -> None:
        with self._conn:
            self._conn.executemany(
                f"INSERT INTO {_TABLE} (path, title, body, tags, body_original, mtime_ns) "
                f"VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

    def _row_tuple(self, doc: IndexDoc) -> tuple[Any, ...]:
        return (doc.path, doc.title, doc.body, doc.tags, doc.body_original, doc.mtime_ns)

    # ---------- 查询 ----------

    def search_rows(self, query: str, limit: int, offset: int) -> list[IndexRow]:
        q = tokenize_query(query)
        if not q:
            return []
        sql = (
            f"SELECT path, title, body_original, tags, mtime_ns FROM {_TABLE} "
            f"WHERE {_TABLE} MATCH ? ORDER BY rank LIMIT ? OFFSET ?"
        )
        with self._lock:
            rows = self._conn.execute(sql, (q, limit, offset)).fetchall()
        return [IndexRow(**dict(r)) for r in rows]

    def count(self, query: str) -> int:
        q = tokenize_query(query)
        if not q:
            return 0
        with self._lock:
            row = self._conn.execute(
                f"SELECT count(*) AS n FROM {_TABLE} WHERE {_TABLE} MATCH ?", (q,)
            ).fetchone()
        return int(row["n"])

    def total_docs(self) -> int:
        # FTS5 空表时 count(*) 返回 NULL（已知怪癖），必须容错
        with self._lock:
            row = self._conn.execute(f"SELECT count(*) AS n FROM {_TABLE}").fetchone()
        return int(row["n"] or 0) if row else 0

    def last_full_at(self) -> str | None:
        return self._get_meta("last_full_at")

    def _set_meta(self, k: str, v: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    f"INSERT INTO {_META} (k, v) VALUES (?, ?) "
                    f"ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                    (k, v),
                )

    def _get_meta(self, k: str) -> str | None:
        with self._lock:
            row = self._conn.execute(f"SELECT v FROM {_META} WHERE k = ?", (k,)).fetchone()
        return str(row["v"]) if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
