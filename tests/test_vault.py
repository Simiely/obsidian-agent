"""M1：Vault 访问核心测试（目录树 / 读写编码容错 / 忽略规则 / 路径安全 / 监听）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.vault import FileTooLarge, PathNotAllowed, Vault, VaultError, VaultWatcher


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    (root / "Projects" / "Obsidian").mkdir(parents=True)
    # 注意：Windows 上 Path.write_text 会做 \n→\r\n 转换，fixture 统一用 write_bytes 保证确定性
    (root / "Projects" / "Obsidian" / "插件开发.md").write_bytes("# 插件开发\n\n正文\n".encode())
    (root / "日记.md").write_bytes("# 日记\n".encode())
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "workspace.json").write_bytes(b"{}")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.md").write_bytes(b"x")
    (root / "附件.png").write_bytes(b"\x89PNG fake")
    (root / ".hidden.md").write_bytes(b"hidden")
    return Vault(root=root)


# ---------- 目录树 / 忽略 ----------


def test_tree_basic(vault: Vault) -> None:
    nodes = vault.tree()
    names = {n["name"] for n in nodes}
    assert "日记.md" in names
    assert "附件.png" in names
    assert "Projects" in names
    assert ".obsidian" not in names
    assert "node_modules" not in names
    assert ".hidden.md" not in names


def test_tree_nested(vault: Vault) -> None:
    proj = next(n for n in vault.tree() if n["name"] == "Projects")
    assert proj["type"] == "dir"
    obs = next(n for n in vault.tree(proj["path"]) if n["name"] == "Obsidian")
    files = {f["name"] for f in vault.tree(obs["path"])}
    assert "插件开发.md" in files


def test_walk_md_excludes_ignored(vault: Vault) -> None:
    rels = {rel for rel, _ in vault.walk_md()}
    assert "日记.md" in rels
    assert "Projects/Obsidian/插件开发.md" in rels
    assert ".obsidian/workspace.json" not in rels
    assert "node_modules/x.md" not in rels
    assert ".hidden.md" not in rels


def test_is_ignored(vault: Vault) -> None:
    assert vault.is_ignored(".obsidian/workspace.json")
    assert vault.is_ignored("node_modules/x.md")
    assert not vault.is_ignored("日记.md")
    assert not vault.is_ignored("Projects/Obsidian/插件开发.md")


# ---------- 读写与编码（坑 #6） ----------


def test_read_utf8(vault: Vault) -> None:
    content = vault.read("日记.md")
    assert content.text == "# 日记\n"
    assert content.meta.encoding == "utf-8-sig" or content.meta.encoding == "utf-8"


def test_read_utf8_bom(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "bom.md").write_bytes(b"\xef\xbb\xbf" + "# BOM 标题\n".encode())
    v = Vault(root=root)
    assert v.read("bom.md").text == "# BOM 标题\n"


def test_read_gbk(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "gbk.md").write_bytes("旧笔记：中文内容".encode("gbk"))
    v = Vault(root=root)
    assert v.read("gbk.md").text == "旧笔记：中文内容"


def test_write_preserves_crlf(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "win.md").write_bytes("# 标题\r\n第一行\r\n".encode())
    v = Vault(root=root)
    v.write("win.md", "# 新标题\n第二行")
    raw = (root / "win.md").read_bytes()
    assert raw == "# 新标题\r\n第二行".encode()  # 换行保留 CRLF
    assert b"\xef\xbb\xbf" not in raw  # 无 BOM


def test_write_rejects_non_md(vault: Vault) -> None:
    with pytest.raises(PathNotAllowed):
        vault.write("附件.png", "x")


def test_write_above_max_bytes(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    f = root / "big.md"
    f.write_bytes(b"x" * 100)
    v = Vault(root=root, max_file_bytes=50)
    with pytest.raises(FileTooLarge):
        v.write("big.md", "y")


def test_create_and_delete(vault: Vault) -> None:
    vault.create("新笔记.md", "# 新\n")
    assert (vault.root / "新笔记.md").is_file()
    with pytest.raises(FileExistsError):
        vault.create("新笔记.md")
    vault.delete("新笔记.md")
    assert not (vault.root / "新笔记.md").exists()


# ---------- 路径安全（坑 #7） ----------


@pytest.mark.parametrize(
    "bad",
    [
        "../secret.txt",
        "..\\secret.txt",
        "../../etc/passwd",
        "C:/Windows/win.ini",
        "D:\\x.md",
        "/etc/passwd",
    ],
)
def test_resolve_rejects_escape(vault: Vault, bad: str) -> None:
    with pytest.raises(PathNotAllowed):
        vault.resolve_safe_path(bad, md_only=False)


def test_resolve_ok_inside(vault: Vault) -> None:
    p = vault.resolve_safe_path("Projects/Obsidian/插件开发.md", must_exist=True)
    assert p == (vault.root / "Projects/Obsidian/插件开发.md").resolve()


def test_resolve_missing_raises(vault: Vault) -> None:
    with pytest.raises(FileNotFoundError):
        vault.resolve_safe_path("不存在.md", must_exist=True)


# ---------- 监听（debounce） ----------


def test_watcher_debounce(tmp_path: Path) -> None:
    import time

    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("a", encoding="utf-8")
    v = Vault(root=root)
    got: list[set] = []

    def on_change(paths: set) -> None:
        got.append(paths)

    w = VaultWatcher(v, debounce_seconds=0.4, on_change=on_change)
    w.start()
    try:
        (root / "a.md").write_text("a2", encoding="utf-8")
        (root / "b.md").write_text("b", encoding="utf-8")
        deadline = time.time() + 5
        while time.time() < deadline and not got:
            time.sleep(0.1)
        assert got, "回调应被触发"
        assert len(got) == 1, "debounce 应合并为一次回调"
        assert "a.md" in got[0] and "b.md" in got[0]
    finally:
        w.stop()


def test_vault_root_must_exist(tmp_path: Path) -> None:
    with pytest.raises(VaultError):
        Vault(root=tmp_path / "nope")
