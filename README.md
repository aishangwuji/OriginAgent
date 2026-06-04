
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

## 本项目 fork 自 [HKUDS/nanobot](https://github.com/HKUDS/nanobot)，在此基础上持续演化。

**OriginAgent** 是一个轻量级 AI Agent 运行时，基于 nanobot 核心构建。它设计运行在你的本地环境中，连接聊天频道、MCP 工具、技能包和领域能力，并通过简洁的 Agent 循环协调多轮对话中的任务执行。


## 📢 News

- **2026-06-03** 🚀 项目全面翻新，更新文档与配置，持续演进中。

## 💡 Key Features

- **超轻量级**：稳定长运行的 Agent 行为，核心代码精简可读。
- **研究友好**：代码库简洁到足以学习、修改和扩展。（使用Agent助手前提下）
- **开箱即用**：聊天频道、API、记忆、MCP 都已内置。
  
## 📦 安装

### 从源码安装

```bash
git clone https://github.com/aishangwuji/OriginAgent.git
cd OriginAgent
pip install -e .
```

安装后即可在命令行使用 `originagent` 命令（或 `OriginAgent`）。


### 使用 `uv`

```bash

# 在项目内使用 uv 管理依赖
cd OriginAgent
uv sync --all-extras
originagent gateway
```

## 🚀 快速开始

**1. 初始化配置**

```bash
originagent onboard
```

交互式向导会引导你选择 LLM 提供商和模型。

**2. 手动配置** (`~/.originagent/config.json`)

*设置 API 密钥*（以 OpenRouter 为例）：

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  }
}
```

*设置默认模型*：

```json
{
  "agents": {
    "defaults": {
      "provider": "openrouter",
      "model": "anthropic/claude-sonnet-4-6"
    }
  }
}
```

**3. 开始对话**

```bash
originagent agent
```

## 🧪 WebUI

> WebUI 已打包在 wheel 中。启用 WebSocket 频道后运行 `originagent gateway` 即可直接访问。

**1. 启用 WebSocket 频道**

```json
{ "channels": { "websocket": { "enabled": true } } }
```

**2. 启动网关**

```bash
originagent gateway
```

**3. 打开浏览器访问 `http://127.0.0.1:18790`**

如需开发 WebUI（Vite HMR），参见 [webui/README.md](./webui/README.md)。

## 🏗️ 架构

OriginAgent 围绕一个简洁的 Agent 循环构建：消息从聊天应用进入，LLM 决定何时调用工具，记忆和技能仅在需要时作为上下文引入。这让核心路径保持可读且易于扩展。

### 核心数据流

1. **频道** 接收外部平台消息，发布 `InboundMessage` 事件到异步 `MessageBus`
2. **AgentLoop** 消费入站消息，构建上下文，协调每一轮对话
3. **AgentRunner** 执行 LLM 多轮对话（发送消息 -> 接收工具调用 -> 执行工具 -> 流式响应）
4. 响应发布为 `OutboundMessage` 事件返回对应的频道

### 支持的功能

| 类别 | 内容 |
|------|------|
| **LLM 提供商** | Anthropic、OpenAI、Azure、Bedrock、GitHub Copilot、OpenRouter、DeepSeek、Kimi、Qwen、MiniMax、Google Gemini、Ollama、LM Studio 等 |
| **聊天频道** | Telegram、Discord、Slack、飞书、微信、企业微信、钉钉、QQ、Matrix、WhatsApp、Email、WebSocket |
| **工具集** | 文件系统、Shell 执行、Web 搜索/抓取、MCP 服务器、定时任务、Notebook 编辑、子 Agent 生成、图片生成、记忆管理等 |
| **记忆系统** | 两阶段 Dream 记忆合并，原子写入（temp + fsync + rename），崩溃安全 |
| **会话管理** | 每会话历史持久化、上下文压缩、TTL 自动清理 |
| **API** | OpenAI 兼容 `/v1/chat/completions` 和 `/v1/models` 端点 |
| **演化控制** | 受管控的演化控制面，支持试运行、回滚快照、健康评分与依赖图 |

## 🤝 贡献

PR 欢迎！代码库有意保持精简可读。

```text
# 分支策略
main    - 稳定版本（bug 修复、小改进）
nightly - 实验特性（新功能、可能破坏性变更）
```

## 📄 License

MIT License. 详见 [LICENSE](./LICENSE)。
