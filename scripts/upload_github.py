"""GitHub 批量上传脚本：Contents API 逐文件推送（绕过 git 传输端口限制）。

用法：GH_TOKEN=ghp_xxx python scripts/upload_github.py [repo_name] [private]
token 仅经环境变量传入，绝不写入任何文件。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN = os.environ["GH_TOKEN"]
ROOT = Path(__file__).resolve().parent.parent
REPO_NAME = sys.argv[1] if len(sys.argv) > 1 else "obsidian-agent"
PRIVATE = len(sys.argv) > 2 and sys.argv[2].lower() in ("private", "true", "1")
DESCRIPTION = (
    "Dockerized Obsidian AI workspace: browse, edit, full-text search and AI agent over your vault"
)

EXCLUDE_DIRS = {
    "node_modules",
    "dist",
    "data",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
}
EXCLUDE_FILES = {".env"}
BRANCH = "main"

API = "https://api.github.com"


def req(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"message": raw.decode("utf-8", "ignore")[:200]}


def collect_files() -> list[Path]:
    out: list[Path] = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if rel.name in EXCLUDE_FILES:
            continue
        out.append(rel)
    return out


def main() -> None:
    # 1. 当前用户
    st, me = req("GET", f"{API}/user")
    assert st == 200, f"无法获取用户信息: {me}"
    owner = me["login"]
    print(f"owner: {owner} ({me.get('name') or ''})")

    # 2. 创建仓库（已存在则继续）
    st, body = req(
        "POST",
        f"{API}/user/repos",
        {
            "name": REPO_NAME,
            "description": DESCRIPTION,
            "private": PRIVATE,
        },
    )
    if st in (200, 201):
        print(f"仓库已创建: {body['html_url']}")
    elif st == 422:
        print(f"仓库已存在（{body.get('message')}），继续推送")
    else:
        print(f"建仓失败: {st} {body}")

    # 3. 逐文件推送（存在则先取 sha 再更新，否则新建）
    files = collect_files()
    print(f"待推送文件: {len(files)}")
    ok = fail = 0
    for rel in files:
        content = base64.b64encode((ROOT / rel).read_bytes()).decode()
        url = f"{API}/repos/{owner}/{REPO_NAME}/contents/{urllib.parse.quote(rel.as_posix())}"
        # 查已存在文件 → 取 sha（更新必须携带）
        st, body = req("GET", url)
        sha = body.get("sha") if st == 200 else None
        st, body = req(
            "PUT",
            url,
            {
                "message": f"Update {rel.as_posix()}" if sha else f"Add {rel.as_posix()}",
                "content": content,
                "branch": BRANCH,
                **({"sha": sha} if sha else {}),
            },
        )
        if st in (200, 201):
            ok += 1
        else:
            fail += 1
            print(f"  FAIL({st}): {rel} -> {body.get('message')}")
    print(f"完成: 成功 {ok}，失败 {fail}")
    print(f"仓库地址: https://github.com/{owner}/{REPO_NAME}")


if __name__ == "__main__":
    main()
