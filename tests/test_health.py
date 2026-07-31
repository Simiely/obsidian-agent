"""应用入口冒烟（M0 用例，M3 改为 create_app 工厂注入临时配置）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "首页.md").write_bytes("# 首页\n".encode("utf-8"))
    settings = Settings(
        vault_path=root,
        data_dir=tmp_path / "data",
        watch_enabled=False,
        backup_schedule="",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["indexBackend"] == "fts5"


def test_version(client: TestClient) -> None:
    r = client.get("/api/version")
    assert r.status_code == 200
    assert "version" in r.json()


def test_static_index_served(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "Obsidian Agent" in r.text


def test_bad_config_placeholder(tmp_path: Path) -> None:
    """vault 不存在时模块级 app 应是占位（health 500），且 import 不崩。"""
    import app.main as m

    assert m.app is not None
    from fastapi.testclient import TestClient as TC

    r = TC(m.app).get("/api/health")
    assert r.status_code == 500
