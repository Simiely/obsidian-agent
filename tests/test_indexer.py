"""M2：FTS5 + jieba 索引测试（中文命中 / 增量 / 删除 / 特殊字符）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.indexer.fts5 import Fts5Index, tokenize_query
from app.core.indexer.service import IndexService
from app.core.vault import Vault


@pytest.fixture
def index(tmp_path: Path) -> Fts5Index:
    return Fts5Index(db_path=tmp_path / "index.db")


@pytest.fixture
def svc(tmp_path: Path) -> tuple[Vault, IndexService]:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "检索方案.md").write_bytes("这篇文章介绍中文分词方案对比，Docker 部署实践。".encode("utf-8"))
    (root / "其它.md").write_bytes("与检索无关的日常记录。".encode("utf-8"))
    vault = Vault(root=root)
    backend = Fts5Index(db_path=tmp_path / "index.db")
    return vault, IndexService(vault=vault, backend=backend)


# ---------- 全量构建与中文命中 ----------

def test_build_and_chinese_hit(svc: tuple[Vault, IndexService]) -> None:
    _, svc = svc
    info = svc.full_rebuild()
    assert info["total"] == 2
    assert svc.count("分词") == 1
    assert svc.count("中文") == 1
    rows = svc.search_rows("分词", 10, 0)
    assert rows[0].path == "检索方案.md"


def test_chinese_multiword_and(svc: tuple[Vault, IndexService]) -> None:
    """搜「中文分词」要求文档同时含「中文」和「分词」两个词。"""
    _, svc = svc
    svc.full_rebuild()
    assert svc.count("中文分词") == 1
    assert svc.count("检索方案") == 1


def test_english_case_insensitive(svc: tuple[Vault, IndexService]) -> None:
    _, svc = svc
    svc.full_rebuild()
    assert svc.count("docker") == 1  # 原文 Docker（大写）也应命中
    assert svc.count("DOCKER") == 1


def test_title_searchable(svc: tuple[Vault, IndexService]) -> None:
    _, svc = svc
    svc.full_rebuild()
    assert svc.count("检索方案") == 1


def test_no_match(svc: tuple[Vault, IndexService]) -> None:
    _, svc = svc
    svc.full_rebuild()
    assert svc.count("不存在的词xyz") == 0
    assert svc.search_rows("不存在的词xyz", 10, 0) == []


# ---------- 增量更新 ----------

def test_incremental_upsert(svc: tuple[Vault, IndexService]) -> None:
    vault, svc = svc
    svc.full_rebuild()
    (vault.root / "检索方案.md").write_bytes("新增了向量检索技术讨论。".encode("utf-8"))
    svc.update_paths({"检索方案.md"})
    assert svc.count("向量检索") == 1
    assert svc.count("分词") == 0  # 旧内容已被替换


def test_incremental_remove(svc: tuple[Vault, IndexService]) -> None:
    vault, svc = svc
    svc.full_rebuild()
    (vault.root / "其它.md").unlink()
    svc.update_paths({"其它.md"})
    assert svc.count("日常记录") == 0
    assert svc.backend.total_docs() == 1


def test_rebuild_is_idempotent(svc: tuple[Vault, IndexService]) -> None:
    _, svc = svc
    svc.full_rebuild()
    svc.full_rebuild()
    assert svc.backend.total_docs() == 2


# ---------- 特殊字符与分词语义 ----------

def test_query_with_special_chars_no_error(index: Fts5Index, tmp_path: Path) -> None:
    """含 - 的查询不应被 FTS5 当作 NOT 运算符。"""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_bytes("docker-compose 配置详解".encode("utf-8"))
    vault = Vault(root=root)
    svc = IndexService(vault=vault, backend=index)
    svc.full_rebuild()
    assert svc.count("docker-compose") == 1
    assert svc.count("docker -compose") >= 0  # 不抛异常


def test_tokenize_query_special_chars() -> None:
    q = tokenize_query("docker-compose")
    assert q == "docker compose"  # 纯标点 '-' 被过滤，保留 AND 语义


def test_status(svc: tuple[Vault, IndexService]) -> None:
    _, svc = svc
    svc.full_rebuild()
    status = svc.status()
    assert status["state"] == "ready"
    assert status["totalFiles"] == 2
    assert status["backend"] == "fts5"
    assert status["lastFullAt"]
