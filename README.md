<div align="center">
  <p>
    <img src="https://img.shields.io/badge/python-%E2%89%A53.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <a href="https://github.com/aishangwuji/OriginAgent/graphs/commit-activity" target="_blank">
      <img alt="Commits last month" src="https://img.shields.io/github/commit-activity/m/aishangwuji/OriginAgent?labelColor=%20%2332b583&color=%20%2312b76a"></a>
    <a href="https://github.com/aishangwuji/OriginAgent/issues?q=is%3Aissue%20is%3Aclosed" target="_blank">
      <img alt="Issues closed" src="https://img.shields.io/github/issues-search?query=repo%3Aaishangwuji%2FOriginAgent%20is%3Aissue%20is%3Aclosed&label=issues%20closed&labelColor=%20%237d89b0&color=%20%235d6b98"></a>
  </p>
</div>

**OriginAgent** 是一个轻量级、可演化的 AI Agent 运行时。它将聊天频道、LLM 提供商、工具集、记忆系统和演化控制整合进一个可读的异步 Agent 循环中，运行在你的本地环境里。

> 本项目最初 fork 自 [HKUDS/nanobot](https://github.com/HKUDS/nanobot)，在此之上持续独立演化，已形成独立的架构体系。

---

## 📢 News

- **2026-06** — WebUI 新增元认知仪表盘、演化信号管理、学习设置面板；Gateway 新增元认知与演化信号 HTTP 端点；运行时安全全面加固。

---

## 🏗️ 架构

OriginAgent 围绕消息总线和 Agent 循环构建，各子系统通过异步队列解耦：

```
频道 (15+) ──→ MessageBus ──→ AgentLoop ──→ AgentRunner ──→ LLM 提供商 (30+)
                    │                │               │
                    │           Turn Pipeline   工具执行 (25+)
                    │           · 上下文组装       · 文件系统
                    │           · 认知扫描         · Shell 沙箱
                    │           · 治理审计         · Web 搜索
                    │           · 连续性检查       · MCP 服务器
                    │                             · 子 Agent …
                    │
                    └──── 出站消息 ←──────────────┘
```

### 核心数据流

1. **频道** (`channels/`) 从外部平台接收消息，发布 `InboundMessage` 到异步 `MessageBus`
2. **AgentLoop** (`agent/loop.py`) 消费入站消息，管理会话密钥，协调 Turn Pipeline（上下文组装 → 认知扫描 → 治理审计 → 连续性恢复）
3. **AgentRunner** (`agent/runner.py`) 执行 LLM 多轮对话：发送消息 → 接收工具调用 → 沙箱执行 → 流式响应
4. 响应发布为 `OutboundMessage`，由对应频道投递回用户

### 关键子系统

| 子系统 | 目录 | 职责 |
|--------|------|------|
| **Agent 核心** | `agent/` | AgentLoop、AgentRunner、Turn Pipeline、Hooks、内省 |
| **LLM 提供商** | `providers/` | 30+ 提供商，统一接口，自动检测与匹配 |
| **聊天频道** | `channels/` | 15+ 平台集成，自动发现与热加载 |
| **工具集** | `agent/tools/` | 25+ 工具，含沙箱 Shell、MCP、子 Agent、Web 搜索等 |
| **配置系统** | `config/` | Pydantic 模型，环境变量插值，`ORIGINAGENT__` 前缀覆盖 |
| **消息总线** | `bus/` | 异步 `asyncio.Queue` 解耦频道与核心 |
| **记忆系统** | `agent/memory.py` | 两阶段 Dream 记忆合并，原子写入（temp + fsync + rename） |
| **会话管理** | `session/` | 每会话 `history.jsonl` 持久化，上下文压缩，TTL 自动清理 |
| **演化系统** | `evolution/` | 模块激活、能力门控、状态分支、验证恢复、遥测记录 |
| **安全** | `security/` | SSRF 防护、工作区隔离、Capability 快照、策略执行 |
| **领域包** | `domain_packs/` | 智能家居、机器人等场景专属工具与运行时 |
| **API 服务** | `api/` | OpenAI 兼容 `/v1/chat/completions` 和 `/v1/models` 端点 |
| **Gateway** | `web/` + `channels/websocket.py` | 内置 HTTP 服务器（端口 18790），提供 WebUI SPA、WebSocket 多路复用、REST API |
| **WebUI** | `webui/` | React 18 + TypeScript + Vite + Tailwind 3 + shadcn/ui |
| **国际化** | `i18n/` | 多语言支持 |
| **技能包** | `skills/` | GitHub、Cron、图片生成、长期目标、记忆等可扩展技能 |

---

## 📦 安装

### 从源码安装

```bash
git clone https://github.com/aishangwuji/OriginAgent.git
cd OriginAgent
pip install -e ".[dev]"
```

### 使用 uv

```bash
cd OriginAgent
uv sync --all-extras
```

### 安装频道扩展（可选）

```bash
pip install -e ".[discord,wecom,matrix]"
```

---

## 🚀 快速开始

### 1. 初始化配置

```bash
originagent onboard
```

交互式向导会引导你选择 LLM 提供商和模型。

### 2. 手动配置 (`~/.originagent/config.json`)

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "provider": "openrouter",
      "model": "anthropic/claude-sonnet-4-6"
    }
  }
}
```

支持通过 `${ENV_VAR}` 占位符引用环境变量，或使用 `ORIGINAGENT__PROVIDERS__OPENROUTER__API_KEY` 格式的环境变量覆盖。

### 3. 开始对话

```bash
originagent agent
```

### 4. 启用 WebUI

```json
{ "channels": { "websocket": { "enabled": true } } }
```

```bash
originagent gateway
```

浏览器访问 `http://127.0.0.1:18790`

---

## 🔌 支持的提供商 (30+)

| 类别 | 提供商 |
|------|--------|
| **国际厂商** | Anthropic、OpenAI、OpenAI Codex、GitHub Copilot、Google Gemini、Mistral、Groq |
| **云平台** | Azure OpenAI、AWS Bedrock、NVIDIA NIM |
| **网关** | OpenRouter、HuggingFace、AiHubMix、SiliconFlow (硅基流动) |
| **国内厂商** | DeepSeek、智谱 GLM、通义千问 (DashScope)、Kimi (Moonshot)、MiniMax、阶跃星辰 (StepFun)、火山引擎、BytePlus、百度千帆、小米 MIMO |
| **本地部署** | Ollama、vLLM、LM Studio、Atomic Chat、OpenVINO、自定义 OpenAI 兼容端点 |

提供商通过模型名称关键词自动匹配，也可手动指定。网关类提供商（OpenRouter 等）可路由任意模型。

---

## 💬 支持的频道 (15+)

| 类别 | 频道 |
|------|------|
| **国际 IM** | Telegram、Discord、Slack、Matrix、WhatsApp、MSTeams |
| **国内 IM** | 飞书、企业微信、微信、钉钉、QQ |
| **通用** | WebSocket、Email、MoChat |

频道通过 `pkgutil` 扫描 + entry-point 插件自动发现。每个频道文件自包含、独立可读。

---

## 🧰 内置工具 (25+)

| 类别 | 工具 |
|------|------|
| **文件系统** | 读/写/编辑/列表文件，工作区隔离 |
| **Shell** | 沙箱化命令执行 |
| **网络** | Web 搜索、Web 抓取（SSRF 防护） |
| **MCP** | MCP 服务器连接与工具调用 |
| **子 Agent** | 生成子 Agent 处理多步复杂任务 |
| **记忆** | 会话记忆搜索与管理 |
| **定时任务** | Cron 定时任务调度 |
| **内容** | Notebook 编辑、内容读取 |
| **媒体** | 图片生成 |
| **运行时** | 自检 (MyTool)、运行时状态、演化控制 |
| **领域** | 本地硬件感知、领域包加载 |
| **通信** | 用户询问、消息发送 |
| **治理** | 工具审计、能力快照、长期任务管理 |

---

## 🧠 演化系统

OriginAgent 内置受管控的演化运行时，允许 Agent 行为在安全边界内持续优化：

- **演化模块** — 模块化的 Agent 行为单元，支持激活、停用、回滚
- **能力门控** — 细粒度的工具/网络/文件系统权限控制
- **状态分支** — 演化变更的分支管理，支持合并预览与冲突检测
- **记忆保险库** — 记忆的导出、导入、校验与检查
- **验证恢复** — 演化后的自动验证，失败时自动回滚
- **遥测记录** — 演化操作的完整审计追踪

---

## 🛡️ 安全

- **SSRF 防护** — 所有出站 HTTP 请求经过 `security/network.py` 校验，阻止内网 IP、链路本地地址、云元数据端点
- **工作区隔离** — 文件系统工具通过 `_resolve_path` 强制限定在工作区范围内
- **Capability 快照** — 跨边界工具（文件、网络、Shell、消息）需显式授权
- **策略执行** — 路径遍历、工作区外访问、私有 URL 访问被硬策略阻止
- **原子写入** — 记忆文件通过 temp-file + fsync + rename + dir-fsync 保证崩溃安全
- **密钥管理** — 无硬编码密钥，支持环境变量和 `${ENV_VAR}` 占位符

---

## 🧪 开发 WebUI

```bash
cd webui
bun install
bun run dev          # Vite HMR，API/WS 代理到 Gateway
bun run build        # 构建到 ../OriginAgent/web/dist/
bun run test
```

WebUI 技术栈：Vite + React 18 + TypeScript + Tailwind 3 + shadcn/ui。包含聊天界面、会话管理、元认知仪表盘、演化信号面板、学习设置和国际化支持。

---

## 🏷️ 分支策略

| 分支 | 用途 |
|------|------|
| `main` | 稳定版本（bug 修复、文档、小改进） |
| `nightly` | 实验特性（新功能、重构、API 变更） |

稳定特性从 `nightly` cherry-pick 到 `main`（约每周一次），不做整体合并。

---

## 🤝 贡献

PR 欢迎。代码库注重可读性，核心循环在 `agent/loop.py` 和 `agent/runner.py` 中一目了然。

- Python 3.11+，全异步 (asyncio)
- 测试：`pytest --cov=OriginAgent --cov-report=term`
- 代码检查：`ruff check OriginAgent/`
- 新功能优先发往 `nightly` 分支

---

## 📄 License

MIT License. 详见 [LICENSE](./LICENSE)。
