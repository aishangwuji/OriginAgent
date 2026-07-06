<div align="center">
  <p>
    <img src="https://img.shields.io/badge/python-%E2%89%A53.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <a href="https://github.com/aishangwuji/OriginAgent/graphs/commit-activity" target="_blank">
      <img alt="Commits last month" src="https://img.shields.io/github/commit-activity/m/aishangwuji/OriginAgent?labelColor=%20%2332b583&color=%20%2312b76a"></a>
    <a href="https://github.com/aishangwuji/OriginAgent/issues?q=is%3Aissue+is%3Aclosed" target="_blank">
      <img alt="Issues closed" src="https://img.shields.io/github/issues-search?query=repo%3Aaishangwuji%2FOriginAgent%20is%3Aissue%20is%3Aclosed&label=issues%20closed&labelColor=%20%237d89b0&color=%20%235d6b98"></a>
  </p>
</div>

**OriginAgent** 是一个轻量级、可演化的 AI Agent 运行时。它围绕 **消息总线** 和 **分解式 Agent 核心** 构建，将聊天频道、LLM 提供商、工具集、元认知自省、BDI 推理解耦、记忆管理与演化控制整合进一个可读的异步 Python 运行时中。

> 本项目最初 fork 自 [HKUDS/nanobot](https://github.com/HKUDS/nanobot)，在此之上持续独立演化，已形成独立的架构体系。

---

## 📢 News

- **2026-07** — 多租户身份框架 & SharedSpace 上线；SQLite 持久化全面迁移（Tier-3 覆盖）；Agent 循环分解 Phase 4 收尾。
- **2026-06** — WebUI 新增元认知仪表盘、演化信号管理面板；Gateway 新增元认知与演化信号 HTTP 端点；运行时安全全面加固。

---

## 🏗️ 架构

### 核心数据流

```
频道 (15+) ──→ MessageBus ──→ AgentLoop ──→ AgentRuntime ──→ AgentRunner ──→ LLM 提供商 (30+)
                                      (Facade)   (路由)          (对话循环)
                                      │              │               │
                                     AgentHost    TurnPipeline  工具执行 (25+)
                                  (基础设施)    · 上下文组装      · 文件系统
                                  · MCP 连接   · 认知扫描        · Shell 沙箱
                                  · BDI 引擎   · 治理审计        · Web 搜索/抓取
                                  · 提供商切换  · 连续性检查      · MCP 服务器
                                  · 转写服务   · 元认知反射      · 子 Agent …
                                  · 后台任务   · 认知调度
                                               · 记忆候选桥接
```

### Agent 核心分解

AgentLoop 已被分解为三个专注的内部组件，消除了原始「上帝对象」：

```
AgentLoop（Facade，~2434 行）
  公开 API（run、stop、process_direct），不包含业务逻辑，
  所有逻辑委托给 AgentRuntime + AgentHost。

├── AgentRuntime（~1187 行）← 无状态消息路由器
│   所有上下文作为显式参数传入，不在 self 上存储可变状态。
│   · 对话轮次管道（构建消息 → 运行 Agent → 组装出站）
│   · 元认知触发、反射、快速路径
│   · 认知调度（候选→事件→工作记忆）
│   · 轮次后处理（后台审查、策展、近线记忆）
│   · 连续性检查点与会话恢复
│   · 消息分发与持久化
│
├── AgentHost（~543 行）← 基础设施生命周期
│   · MCP 连接生命周期管理
│   · LLM 提供商管理（快照、预设切换）
│   · BDI 推理解耦引擎
│   · 转写提供商管理
│   · 后台任务调度与排空
│
└── SessionStateHolder（~96 行）← 会话作用域暂存
     · 线程安全的跨轮次暂存（_last_* scratchpad）
     · TTL 过期自动清理
```

### 关键子系统

| 子系统 | 位置 | 职责 |
|--------|------|------|
| **Agent 核心** | `agent/loop.py` | 分解式 Facade，委托路由与基础设施 |
| **Agent 运行时** | `agent/agent_runtime.py` | 无状态消息路由、元认知、认知调度 |
| **Agent 宿主** | `agent/agent_host.py` | MCP/提供商/BDI 生命周期管理 |
| **对话执行** | `agent/runner.py` | LLM 多轮对话循环 + 工具执行 |
| **LLM 提供商** | `providers/` | 30+ 提供商，统一接口，自动检测与匹配 |
| **聊天频道** | `channels/` | 15+ 平台集成，自动发现与热加载 |
| **工具集** | `agent/tools/` | 25+ 工具，含沙箱 Shell、MCP、子 Agent 等 |
| **配置系统** | `config/` | Pydantic 模型，环境变量插值，`ORIGINAGENT__` 前缀覆盖 |
| **消息总线** | `bus/` | 异步 `asyncio.Queue` 解耦频道与核心 |
| **会话管理** | `session/` | 每会话 `history.jsonl` 持久化，上下文压缩，Episode 管理 |
| **记忆系统** | `agent/memory.py` + `memory/` | Dream 两阶段合并、近线管道、滚动压缩、治理 |
| **演化系统** | `evolution/` | 模块激活、能力门控、状态分支、验证恢复、遥测 |
| **元认知** | `agent/meta_cognition_*.py` | 触发器采集、反射、置信度追踪、模式发现 |
| **BDI 推理** | `bdi/` | 信念-欲望-意图推理解耦引擎 |
| **认知系统** | `agent/cognitive_*.py` | 认知循环、调度、事件管道、工作记忆 |
| **连续性** | `agent/action_continuity.py` | Episode 管理、Checkpoint/恢复 |
| **安全** | `security/` | SSRF 防护、工作区隔离、Capability 快照、策略执行 |
| **多租户** | `session/manager.py` tenant 基础设施 | 租户隔离、SharedSpace 跨租户共享 |
| **领域包** | `domain_packs/` | 智能家居、机器人等场景专属工具与运行时 |
| **API 服务** | `api/` | OpenAI 兼容 `/v1/chat/completions` 和 `/v1/models` |
| **Gateway** | `web/` + `channels/websocket.py` | 内置 HTTP 服务器（端口 18790），WebUI + WS 多路复用 + REST |
| **WebUI** | `webui/` | React 18 + TypeScript + Vite + Tailwind + shadcn/ui |
| **国际化** | `i18n/` | 多语言支持 |
| **技能系统** | `skills/` + `agent/skill_bootstrapper.py` | 运行时技能发现与编译 |

---

## ✨ 核心特性

### 🧠 元认知自省系统

Agent 对自己的思考过程进行实时自省：

- **触发器采集** — 自动检测工具失败、用户纠正、任务完成等事件
- **结构反射** — 每次对话轮次后，LLM 驱动的反思（策略总结、假设记录、根因分析）
- **置信度追踪** — 记录 Agent 对自己输出的置信度评分与不确定性原因
- **错误模式发现** — 跨轮次聚合相似错误，识别重复模式
- **演化种子生成** — 从反射中提取可操作的自我改进机会
- **快速路径** — 相似上下文时跳过冗长反射，直接复用上次结果

### ⚖️ BDI 推理解耦引擎

基于 **信念-欲望-意图（Belief-Desire-Intention）** 架构的独立推理层：

- **信念存储** — 世界状态的事实性知识库
- **欲望评估** — 条件触发的目标生成（时间、事件、状态变化）
- **意图栈** — 意图的优先级管理、合并、延续与终止
- **计划库** — 预定义方案与动态规划的组合
- **前瞻推理** — 对「如果...会怎样」场景进行评估

### 🔄 认知系统

Agent 在对话之外的自主认知活动：

- **认知循环** — 后台持续运行的认知处理循环
- **认知调度** — 基于优先级和冷却的认知事件调度
- **工作记忆** — 会话绑定的结构化工作集（当前目标、开放循环、待决问题）
- **感知融合** — 认知事件的感知层融合与消歧
- **世界模型** — 跨会话的世界状态摘要与查询

### 📋 连续性系统

对话轮次与跨会话的连续性管理：

- **Episode 管理** — 将对话划分为主题区块，支持标签与摘要
- **Checkpoint 恢复** — 中断后还原对话骨架上下文
- **连续性桥接** — 跨会话的身份、上下文与工作记忆桥接
- **自动上下文压缩** — 历史消息的自动精简与归档

### 🏠 多租户架构

家庭多用户场景的全面支持：

- **租户身份** — 用户与设备级别的身份解析与隔离
- **租户工作区** — 每个租户独立的记忆、会话与 BDI 目录
- **SharedSpace** — 跨租户的共享事实和设备（智能家居场景）
- **会话降级** — 未知用户安全降级到默认权限

### 🧩 记忆系统

多层级记忆架构：

- **Dream 两阶段合并** — 原子写入（temp + fsync + rename + dir-fsync）保证崩溃安全
- **近线记忆管道** — 异步的记忆处理与事实提取管道
- **滚动压缩** — 会话历史的自动滚动分段与压缩
- **记忆治理** — 记忆保留策略、质量阈值与过期管理
- **Episode 桥接** — Episode 关闭时的记忆快照归档

### 🧬 演化系统

受管控的 Agent 行为自我改进：

- **演化模块** — 模块化的行为单元，支持激活、停用、回滚
- **能力门控** — 细粒度的工具/网络/文件系统权限控制
- **状态分支** — 演化变更的分支管理，合并预览与冲突检测
- **验证恢复** — 演化后自动验证，失败时自动回滚
- **遥测记录** — 完整审计追踪，SQLite 散列链完整性保证
- **核准门** — 需要用户显式确认的演化激活

---

## 📦 安装

### 从源码安装

```bash
git clone https://github.com/aishangwuji/OriginAgent.git
cd OriginAgent
pip install -e ".[dev]"
```

### 使用 uv（推荐）

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

提供商通过模型名称关键词自动匹配，也可手动指定。支持运行中提供商预设切换。

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
| **文件系统** | 读/写/编辑/列表文件，工作区强制隔离 |
| **Shell** | 沙箱化命令执行 |
| **网络** | Web 搜索、Web 抓取（SSRF 防护） |
| **MCP** | MCP 服务器发现、连接与工具调用 |
| **子 Agent** | 生成子 Agent 处理多步复杂任务 |
| **记忆** | 会话记忆搜索、事实查询与管理 |
| **定时任务** | Cron 定时任务调度 |
| **内容** | Notebook 编辑、文档内容提取 |
| **媒体** | 图片生成 |
| **运行时** | 自检 (MyTool)、运行时状态、演化控制 |
| **领域** | 本地硬件感知、领域包加载 |
| **通信** | 用户询问、异步消息发送 |
| **治理** | 工具审计、能力快照、长期任务管理 |

---

## 🛡️ 安全

- **SSRF 防护** — 所有出站 HTTP 请求经过 `security/network.py` 校验，阻止内网 IP、链路本地地址、云元数据端点
- **工作区隔离** — 文件系统工具通过 `_resolve_path` 强制限定在工作区范围内
- **Capability 快照** — 跨边界工具（文件、网络、Shell、消息）需显式授权
- **策略执行** — 路径遍历、工作区外访问、私有 URL 访问被硬策略阻止
- **Capability 授权** — 细粒度的能力授权存储与策略评估
- **工具审计** — 每次工具调用记录到结构化 JSONL 审计日志
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

WebUI 技术栈：Vite + React 18 + TypeScript + Tailwind 3 + shadcn/ui。包含聊天界面、会话管理、**元认知仪表盘**（触发器日志、反射记录、置信度追踪、模式发现）、**演化信号面板**（演化活动、信号队列）、**学习设置**（演化策略、模块激活）和国际化支持。

---

## 🏷️ 分支策略

| 分支 | 用途 |
|------|------|
| `main` | 稳定版本（bug 修复、文档、小改进） |
| `nightly` | 实验特性（新功能、重构、API 变更） |

稳定特性从 `nightly` cherry-pick 到 `main`（约每周一次），不做整体合并。

---

## 🤝 贡献

PR 欢迎。代码库注重可读性与分解式架构，核心循环在分解后的 `agent/` 目录中一目了然。

- Python 3.11+，全异步 (asyncio)
- 测试：`pytest --cov=OriginAgent --cov-report=term`
- 代码检查：`ruff check OriginAgent/`
- 新功能优先发往 `nightly` 分支

---

## 📄 License

MIT License. 详见 [LICENSE](./LICENSE)。
