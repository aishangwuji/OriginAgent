# Plan 1：把当前代码库产品化为 OriginAgent

> 目标：第一阶段先完成“产品身份替换 + 默认配置收敛 + Home Assistant 最小闭环”。
> OriginAgent 是项目最终呈现的产品，不是附属模式。

---

## 1. 本阶段目标

完成以下变化：

- 项目默认身份改为 OriginAgent。
- 默认系统提示词、workspace 模板、README、命令行文案都面向“本地 AI 智能家居管家”。
- 默认配置面向 OriginAgent：Home Assistant、Telegram、模型、workspace。
- 接入 Home Assistant 最小工具集。
- 通过 Telegram 完成一次自然语言设备控制。
- 不把现有 webui 当成 v1 产品前端，只保留为可选开发调试/配置控制台。
- 为后续主动触发、onboarding、飞牛 fpk 留好结构。

---

## 2. 改造原则

- **产品身份优先**：默认 bot name、图标、说明、模板、Docker 服务名都改成 OriginAgent。
- **少动核心循环**：`AgentLoop`、`MessageBus`、`ChannelManager`、MCP、provider 能复用就复用。
- **改默认，不做模式开关**：不要设计 `originagent.enabled` 之类的旁路开关。
- **不扩 WebUI 范围**：v1 不做类似 OpenClaw 的完整前端，主入口先收敛到 Telegram。
- **先闭环，后漂亮**：第一阶段先跑通 HA 查询/控制，不急着做 onboarding 和规则市场。
- **保留合规信息**：LICENSE、第三方声明、依赖说明要保留。

---

## 3. 要改的代码位置

优先改：

- `originagent/pyproject.toml`
- `originagent/README.md`
- `originagent/originagent/__init__.py`
- `originagent/originagent/cli/commands.py`
- `originagent/originagent/config/schema.py`
- `originagent/originagent/config/paths.py`
- `originagent/originagent/templates/SOUL.md`
- `originagent/originagent/templates/USER.md`
- `originagent/originagent/templates/TOOLS.md`
- `originagent/originagent/templates/memory/MEMORY.md`
- `originagent/Dockerfile`
- `originagent/docker-compose.yml`
- `originagent/entrypoint.sh`
- `originagent/webui/README.md`
- `originagent/originagent/home/ha_client.py`
- `originagent/originagent/home/ha_mcp.py`
- `originagent/tests/home/`

尽量不改：

- `originagent/originagent/agent/loop.py`
- `originagent/originagent/channels/telegram.py`
- `originagent/originagent/channels/manager.py`
- `originagent/originagent/agent/tools/mcp.py`
- `originagent/originagent/providers/*`
- `originagent/webui/src/*`

原因：这些是稳定底座，第一阶段站在它们上面完成产品化。
`webui/src/*` 暂不作为 v1 主线改造对象，避免把第一阶段扩成完整前端产品。

---

## 4. 任务拆分

### Step 1：产品身份替换

目标：运行起来看到的是 OriginAgent。

任务：

- 项目名改为 `originagent` 或 `originagent-ai`。
- CLI script 统一为 `originagent`。
- `__logo__`、`__version__`、默认 `botName`、默认 `botIcon` 改为 OriginAgent。
- CLI help、status、gateway 启动文案改成 OriginAgent。
- Docker service/container 名称改成 `originagent-gateway`。
- README 首页改为 OriginAgent 产品说明。

验收：

- 执行 `originagent --help` 展示 OriginAgent。
- 启动 gateway 时日志展示 OriginAgent。
- Telegram 回复中的 bot 身份是 OriginAgent。

---

### Step 2：默认配置改成 OriginAgent 配置

建议默认配置方向：

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.originagent/workspace",
      "botName": "OriginAgent",
      "botIcon": "Home",
      "timezone": "Asia/Shanghai",
      "provider": "deepseek",
      "model": "deepseek-chat"
    }
  },
  "homeAssistant": {
    "url": "",
    "token": "${HA_TOKEN}",
    "verifySsl": true,
    "serviceAllowlist": [
      "light.turn_on",
      "light.turn_off",
      "switch.turn_on",
      "switch.turn_off",
      "climate.set_temperature"
    ]
  },
  "channels": {
    "telegram": {
      "enabled": false,
      "token": "${TELEGRAM_TOKEN}",
      "allowFrom": []
    }
  }
}
```

任务：

- 默认 workspace 改成 `~/.originagent/workspace`。
- 默认 config path 改成 `~/.originagent/config.json`。
- 增加顶层 `homeAssistant` schema。
- 增加默认 rules/logs 路径。
- 保持环境变量解析能力。

验收：

- 新用户第一次运行生成 `~/.originagent/config.json`。
- `homeAssistant` 配置能被环境变量解析。
- `originagent status` 能显示 OriginAgent 关键状态。

---

### Step 3：OriginAgent workspace 模板

目标：系统提示词默认就是智能家居管家。

任务：

- 重写 `SOUL.md`：OriginAgent 是本地 AI 智能家居管家，重视隐私、安全和家庭上下文。
- 重写 `USER.md`：家庭成员、房间、作息、温度偏好、通知偏好。
- 重写 `TOOLS.md`：HA 工具使用规则，执行前先确认实体，危险动作要谨慎。
- 重写 `memory/MEMORY.md`：家庭设备、习惯、自动化规则、重要安全偏好。

验收：

- 新 workspace 的 system prompt 明确 OriginAgent 身份。
- 用户说“打开灯”时，模型知道这是家居设备控制场景。

---

### Step 4：Home Assistant REST Client

新增：

- `originagent/originagent/home/__init__.py`
- `originagent/originagent/home/ha_client.py`

最小能力：

- `validate_connection()`
- `get_state(entity_id)`
- `get_states()`
- `search_entities(query)`
- `call_service(domain, service, target, data)`

安全要求：

- `call_service` 必须检查 allowlist。
- 默认不允许危险服务，比如删除、重启、解锁门锁、关闭安防等。
- 错误信息要能给模型读懂，但不要泄露 token。

验收：

- mock HA 能通过单元测试。
- HA 不可达时返回清晰错误。
- 未授权 service 不执行。

---

### Step 5：Home Assistant MCP 工具

新增：

- `originagent/originagent/home/ha_mcp.py`

暴露工具：

- `ha_get_state`
- `ha_search_entities`
- `ha_call_service`

默认 MCP 配置：

```json
{
  "tools": {
    "mcpServers": {
      "home_assistant": {
        "command": "python",
        "args": ["-m", "originagent.home.ha_mcp"],
        "env": {
          "HA_URL": "${HA_URL}",
          "HA_TOKEN": "${HA_TOKEN}"
        },
        "toolTimeout": 15,
        "enabledTools": ["*"]
      }
    }
  }
}
```

验收：

- `AgentLoop` 能注册 HA 工具。
- 模型能调用 `mcp_home_assistant_ha_get_state`。
- 模型能调用 `mcp_home_assistant_ha_call_service`。
- 工具返回足够短，不把 HA 全量状态塞爆上下文。

---

### Step 6：Telegram 最小闭环

任务：

- 默认文档指导用户配置 Telegram Bot Token。
- `allowFrom` 必须明确配置，不允许默认开放控制家庭设备。
- 用现有 Telegram channel，不重写。
- 手工测试三个句子。

测试句子：

- “客厅灯现在是什么状态？”
- “打开客厅灯。”
- “把客厅空调设到 26 度。”

验收：

- Telegram 消息进入 OriginAgent。
- OriginAgent 通过 HA MCP 查询/控制设备。
- HA 成功后，Telegram 收到自然语言确认。

---

### Step 7：WebUI 边界收敛

任务：

- 在 README 和计划文档中明确：v1 不做类似 OpenClaw 的完整 WebUI。
- 现有 `webui/` 保留为可选开发调试、会话观察、基础配置检查入口。
- 不新增复杂前端页面、设备大屏、家庭仪表盘、规则市场。
- v1 安装和验收路径不依赖 webui。

验收：

- 用户不启动 webui 也能完成 OriginAgent 核心闭环。
- 文档不会把 webui 描述成 v1 必需入口。
- 后续如要做前端，另开计划定义“家庭配置台/设备状态页/记忆管理页”。

---

## 5. 测试清单

单元测试：

- 默认 config path 指向 `~/.originagent`。
- `homeAssistant` schema 默认值。
- HA client 请求头和错误处理。
- service allowlist。
- HA MCP 工具参数校验。

集成测试：

- mock HA `/api/states`。
- mock HA `/api/services/{domain}/{service}`。
- mock MCP tool call。
- CLI `originagent status` 文案正确。

手工测试：

- `originagent --help`。
- `originagent onboard` 或初始化配置。
- `originagent gateway`。
- Telegram allowlist 生效。
- HA 查询和控制成功。
- 不启动 webui 时，核心 Telegram + HA 闭环仍可用。

---

## 6. 完成定义

Plan 1 完成后：

- 项目运行入口和用户可见身份是 OriginAgent。
- 默认配置、workspace、模板都面向智能家居管家。
- Home Assistant 最小 MCP 工具可用。
- Telegram 能完成一次真实设备查询和控制。
- v1 范围不包含完整 WebUI，现有 webui 只作为可选调试/配置控制台。
- 后续 Plan 2 可以在这个基础上做主动触发引擎。
