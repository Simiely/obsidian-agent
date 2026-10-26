# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-10-26 · v0.1.0（未发布 MVP）· 补齐根目录四件套
> **更新文档/代码后，请更新此行**（日期 + 版本 + 摘要），并在 CHANGELOG 追加版本

## 技术栈

- 后端：**Python 3.11+ / FastAPI + uvicorn**，`pydantic-settings` 读 `.env`，**mypy strict**
- 索引：**SQLite FTS5 + jieba 预分词**（默认）；`core/indexer/base.py` 留 Meilisearch 接口
- Agent：**Pydantic AI v1.x**（不是 2.x，API 差异巨大）+ 工具函数 + HITL 批准
- 前端：**Vue 3 + Vite**，markdown-it + 自研 Obsidian 方言插件（`frontend/src/md.js`）
- 部署：Docker / compose，非 root + PUID/PGID；mount `/vault`（笔记库）与 `/data`（索引+备份）

## 关键坑（改代码前必看）

- **SQLite 连接不能跨线程共用**：CI 实测 Linux 段错误；Windows 上并发关闭连接会 access violation → 每线程独立连接，用完即关
- **jieba 分词结果不能直接拼进 FTS5 MATCH**：连字符会被拆成独立标点 token，混入后可能变成 NOT 运算符 → 分词后过滤标点，必要时给 token 加引号
- **FTS5 空表 `count(*)` 返回 NULL 而不是 0** → 一律 `COALESCE(..., 0)`
- **`Path.write_text` 在 Windows 会做 `\n` → `\r\n` 转换** → 需要逐字节一致时用 `newline=""` 或二进制写
- **Docker 里的默认备份目录是容器可写层**，重建容器即丢 → compose 显式 `BACKUP_DIR=/data/backups`
- **`core/` 与 `agent/` 禁止 import FastAPI**：分层单向 `api → agent → core`，否则单测与替换框架都会被绑死
- **前端 dist 不入库**：CI 必须先 `npm ci && npm run build`，否则 `test_static_index_served` 会在干净环境 404

## 约定

- 中文注释；公共 API 必须写 docstring（含参数），工具 docstring 会被 Pydantic AI 直接当 schema 用
- 提交信息用 `feat(scope) / fix(scope) / docs / test / chore` 前缀
- 任何写操作都经过 `agent/safety.py`：路径必须在 vault 内、禁写 `.obsidian/` 等目录、写前备份
- 新增功能必须有测试；修 bug 先写复现用例
- 未发布内容记 `Unreleased`，发布时才移动为具体版本号

## 常用命令

```bash
# 后端
cd app && python -m venv .venv && .venv\Scripts\activate
pip install -e "..[dev]"
pytest tests/ -v
ruff check . && ruff format --check . && mypy .

# 前端
cd frontend && npm ci && npm run build

# 部署
docker compose up -d
```

## 详细规则（按需 @引用）

- @docs/02-架构设计.md（分层、模块职责、数据流、选型依据）
- @docs/08-踩坑记录.md（40+ 条实测坑，按索引/部署/Agent/前端分区）
- @docs/06-开发指南.md ｜ @docs/04-配置参考.md ｜ @docs/05-API参考.md
