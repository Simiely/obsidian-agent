# Obsidian Agent

> 一个 Docker 化的 Obsidian 知识库 AI 助手 —— 指定 vault 路径即可浏览、编辑、全文检索所有 Markdown 文档，并通过 AI Agent 用自然语言操作你的笔记。

![Status](https://img.shields.io/badge/status-可用-green) ![License](https://img.shields.io/badge/license-MIT-green)

---

## 这是什么？

一个自托管的 Web 应用，运行在 Docker 容器里。你只需要告诉它你的 Obsidian 库（vault）在宿主机上的路径，它就能：

- 📂 **浏览** —— 网页版的文件树，快速定位任何文档
- 📖 **阅读** —— 完美渲染 Obsidian 语法（frontmatter、`[[]]` 链接、callout、Mermaid）
- ✏️ **编辑** —— 在浏览器里编辑 md，保留原始语法不被破坏
- 🔍 **检索** —— 全文索引 + 中文分词，文档内**每一个字**都能搜到，带高亮和跳转定位
- 🤖 **AI Agent** —— 用自然语言指挥它："找所有关于 Docker 的笔记并总结"、"把这篇文档的标题改成 X"、"在日记里追加今天的待办"
- 🗜️ **备份** —— 定时整库快照（硬链接增量）+ 单文件版本历史 + 一键恢复，数据错了能救回

所有数据都在你自己的机器上，LLM 可接 DeepSeek / OpenAI / Kimi / 本地 Ollama。

---

## 快速上手（三步）

```bash
# 1. 配置（指定你的 Obsidian 库路径和 LLM）
cp .env.example .env
#    编辑 .env：VAULT_PATH=D:/MyVault   LLM_PROVIDER=deepseek  LLM_API_KEY=sk-xxx

# 2. 启动
docker compose up -d

# 3. 打开
#    浏览器访问 http://localhost:8080 → 等待索引完成 → 开始使用
```

详细步骤见 [docs/03-快速开始.md](docs/03-快速开始.md)。

---

## 📚 文档导航（从这里开始，不迷路）

| 文档 | 用途 | 何时读 |
|---|---|---|
| **[docs/01-功能说明.md](docs/01-功能说明.md)** | 全部功能清单（MVP / 规划中）、用户流程示例 | 想了解"它能做什么" |
| **[docs/02-架构设计.md](docs/02-架构设计.md)** | 模块划分、技术选型、数据流、目录结构 | 想改代码/加功能前必读 |
| **[docs/03-快速开始.md](docs/03-快速开始.md)** | 从零跑起来（Docker + 本地开发两种方式） | 第一次部署 |
| **[docs/04-配置参考.md](docs/04-配置参考.md)** | 全部环境变量、配置项说明 | 调整行为时 |
| **[docs/05-API参考.md](docs/05-API参考.md)** | 后端 REST API 端点、请求/响应示例 | 前端开发 / 二次开发 |
| **[docs/06-开发指南.md](docs/06-开发指南.md)** | 本地开发环境、代码规范、测试、如何加新模块 | 准备写代码 |
| **[docs/07-更新日志.md](docs/07-更新日志.md)** | CHANGELOG：版本记录与更新内容 | 每次版本变更后 |
| **[docs/08-踩坑记录.md](docs/08-踩坑记录.md)** | 编程遇到的坑：现象、原因、解决方案 | 遇到问题先翻这里 |
| **[docs/09-技术调研与方案审核.md](docs/09-技术调研与方案审核.md)** | 关键选型调研与决策记录（索引/渲染/Agent 框架） | 想了解"为什么这么选" |
| **[docs/10-代码梳理与重构计划.md](docs/10-代码梳理与重构计划.md)** | 全量代码梳理 + 模块化重构计划（R0-R6） | 想了解代码现状/参与重构 |

> **首次阅读顺序**：README → 01 功能说明 → 02 架构设计 → 03 快速开始 → 其余按需查阅（09 是选型决策记录，改架构前先看）。

---

## 项目结构总览

```
obsidian-agent/
├── README.md               # 本文档（总导航）
├── docs/                   # 📚 文档体系（9 篇，见上表）
├── docker-compose.yml      # 容器编排（app + 可选 meilisearch / ignis）
├── Dockerfile              # 多阶段构建（前端 + Python 后端）
├── .env.example            # 环境变量模板
├── app/                    # Python 后端（FastAPI）
│   ├── main.py             # 应用入口
│   ├── config.py           # 配置加载（pydantic-settings）
│   ├── core/               # 领域核心（不依赖 Web 框架）
│   │   ├── vault.py        #   vault 访问：树/读写/监听/忽略规则
│   │   ├── markdown.py     #   md 解析：frontmatter/wikilink/结构
│   │   └── indexer/        #   索引模块（可插拔）
│   │       ├── base.py     #     索引抽象接口
│   │       ├── fts5.py     #     SQLite FTS5 + jieba 实现（默认）
│   │       └── meili.py    #     Meilisearch 实现（可选）
│   │   └── search.py       #   检索服务（高亮/分页/定位）
│   │   └── backup.py       #   快照备份/定时/保留/恢复
│   ├── agent/              # 🤖 AI Agent（Pydantic AI）
│   │   ├── llm/            #   模型配置层（DeepSeek/OpenAI/Kimi/Ollama）
│   │   ├── tools/          #   类型化工具：读/写/搜/列目录
│   │   ├── loop.py         #   运行器：会话/HITL 批准
│   │   └── safety.py       #   编辑安全：备份/diff/路径白名单
│   └── api/                # REST API 路由
├── frontend/               # 🖥 前端（Vue3 + Vite，markdown-it 渲染 Obsidian 语法）
│   └── src/
│       ├── App.vue         #   编排层（业务全在 composables）
│       ├── components/     #   视图组件（DocView / FileTree / BackupPanel / ...）
│       ├── use*.js         #   composables（useVaultDocs / useHashRouter / useToc / ...）
│       ├── api/            #   后端接口封装
│       └── md.js + mdEnhance.js   # 渲染 + DOM 增强分离
├── data/                   # 索引与备份数据（Docker volume）
└── tests/                  # pytest 测试（含中文检索/Obsidian 语法用例）
```

---

## 状态与路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| **规划** | 文档体系、架构设计、任务清单 | ✅ 当前阶段 |
| M0 | Docker 骨架（compose + 镜像 + 配置） | ⬜ 待开发 |
| M1 | Vault 访问核心层 | ⬜ 待开发 |
| M2 | 全文索引与检索 | ⬜ 待开发 |
| M3 | 后端 API 层 | ⬜ 待开发 |
| M4 | 前端浏览页面 | ⬜ 待开发 |
| M5 | AI Agent 集成 | ⬜ 待开发 |
| M6 | 测试、文档完善、v0.1.0 发布 | ⬜ 待开发 |

详细里程碑见 [docs/02-架构设计.md](docs/02-架构设计.md#路线图) 与 [docs/07-更新日志.md](docs/07-更新日志.md)。

---

## 核心设计原则

1. **不破坏你的库** —— 编辑永远保留原始 md 语法；写文件前自动备份 + diff 预览（Agent 操作强制）。
2. **可插拔** —— 索引引擎、LLM 提供商都走抽象接口，换实现不动业务代码。
3. **模块化** —— 核心层不依赖 Web 框架，单测好写，维护简单。
4. **中文友好** —— 全文检索内置中文分词，检索体验对标 Obsidian 自带搜索。
5. **数据自有** —— vault、索引、备份全在本机，LLM key 只进 .env 不进镜像。

---

## License

MIT
