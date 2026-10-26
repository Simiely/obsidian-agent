# CHANGELOG

版本号遵循语义化版本；未发布内容记在 `Unreleased`，发布时移入具体版本。
逐条原始记录见 [`docs/07-更新日志.md`](docs/07-更新日志.md)，本文件只做摘要。

## Unreleased

### 2026-08-14 · 修复与平台优化 ✅

- 修复 CI 构建失败：test job 补 `setup-node` + `npm ci && npm run build`；`dist` 缺失时相关用例 `pytest.skip`
- Docker 磁盘过滤：容器内「切换笔记库」只展示 bind 进来的挂载盘（`/vault`、`/data`、额外挂载），不再列出容器内部目录
- 备份目录落挂载盘：`docker-compose.yml` 增加 `BACKUP_DIR=/data/backups`，避免容器重建即丢
- 文档同步（README / 04 配置 / 03 快速开始 / `.env.example`）；全量 `pytest` 156 passed

### 2026-08-02 · 模块化 S25 ✅

- `App.vue` 内联逻辑下沉为 `useHashRouter` / `useVaultSwitcher` / `useCreateDoc`（410→316 行）
- `md.js` 渲染与 DOM 增强分离（`mdEnhance.js`）；`DocView` 的 TOC 抽成 `useToc.js`
- 清理误留的临时脚本；回归验证文档打开 / hash 路由 / TOC 高亮 / 备份 tab 全通过

### 2026-08-01 · 备份管理 ✅

- 默认备份目录改为项目根 `backups/`，Docker 里挂载到 `/app/backups`
- 删除快照改异步：`DELETE` 立即返回 202，后台清理，前端 3s 轮询
- 自动备份间隔默认 30 分钟（可调 1–1440）

## 0.1.0 - 待发布（首个 MVP）

- 里程碑 M0–M6 已落地：Docker 骨架 / Vault 核心 / FTS5 + jieba 检索 / 四路由 API / 前端四视图 / Agent（工具 + HITL + safety）/ 测试补全
- 发布前待办：镜像真机验证（沙箱无 docker，本地未验）、发布说明整理、`pyproject` 版本与本文档对齐

## 相关文档

- [README](README.md) ｜ [DEVELOPMENT](DEVELOPMENT.md) ｜ [AGENTS](AGENTS.md)
- [docs/07-更新日志.md](docs/07-更新日志.md)（原始逐条记录）
