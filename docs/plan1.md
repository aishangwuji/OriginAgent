# Plan 1：把当前代码库产品化为 OpenHome

> 目标：第一阶段先完成“产品身份替换 + 默认配置收敛 + Home Assistant 最小闭环”。
> OpenHome 是项目最终呈现的产品，不是附属模式。

---

## 1. 本阶段目标

完成以下变化：

- 项目默认身份改为 OpenHome。
- 默认系统提示词、workspace 模板、README、命令行文案都面向“本地 AI 智能家居管家”。
- 默认配置面向 OpenHome：Home Assistant、Telegram、模型、workspace。
- 接入 Home Assistant 最小工具集。
- 通过 Telegram 完成一次自然语言设备控制。
- 不把现有 webui 当成 v1 产品前端，只保留为可选开发调试/配置控制台。
- 为后续主动触发、onboarding、飞牛 fpk 留好结构。

---

## 2. 改造原则

- **产品身份优先**：默认 bot name、图标、说明、模板、Docker 服务名都改成 OpenHome。
- **少动核心循环**：`AgentLoop`、`MessageBus`、`ChannelManager`、MCP、provider 能复用就复用。
- **改默认，不做模式开关**：不要设计 `openhome.enabled` 之类的旁路开关。
- **不扩 WebUI 范围**：v1 不做类似 OpenClaw 的完整前端，主入口先收敛到 Telegram。
- **先闭环，后漂亮**：第一阶段先跑通 HA 查询/控制，不急着做 onboarding 和规则市场。
- **保留合规信息**：LICENSE、第三方声明、依赖说明要保留。

---

## 3. 要改的代码位置

优先改：

- `openhome/pyproject.toml`
- `openhome/README.md`
- `openhome/openhome/__init__.py`
- `openhome/openhome/cli/commands.py`
- `openhome/openhome/config/schema.py`
- `openhome/openhome/config/paths.py`
- `openhome/openhome/templates/SOUL.md`
- `openhome/openhome/templates/USER.md`
- `openhome/openhome/templates/TOOLS.md`
- `openhome/openhome/templates/memory/MEMORY.md`
- `openhome/Dockerfile`
- `openhome/docker-compose.yml`
- `openhome/entrypoint.sh`
- `openhome/webui/README.md`
- `openhome/openhome/home/ha_client.py`
- `openhome/openhome/home/ha_mcp.py`
- `openhome/tests/home/`

尽量不改：

- `openhome/openhome/agent/loop.py`
- `openhome/openhome/channels/telegram.py`
- `openhome/openhome/channels/manager.py`
- `openhome/openhome/agent/tools/mcp.py`
- `openhome/openhome/providers/*`
- `openhome/webui/src/*`

原因：这些是稳定底座，第一阶段站在它们上面完成产品化。
`webui/src/*` 暂不作为 v1 主线改造对象，避免把第一阶段扩成完整前端产品。

---

## 4. 任务拆分

### Step 1：产品身份替换

目标：运行起来看到的是 OpenHome。

任务：

- 项目名改为 `openhome` 或 `openhome-ai`。
- CLI script 统一为 `openhome`。
- `__logo__`、`__version__`、默认 `botName`、默认 `botIcon` 改为 OpenHome。
- CLI help、status、gateway 启动文案改成 OpenHome。
- Docker service/container 名称改成 `openhome-gateway`。
- README 首页改为 OpenHome 产品说明。

验收：

- 执行 `openhome --help` 展示 OpenHome。
- 启动 gateway 时日志展示 OpenHome。
- Telegram 回复中的 bot 身份是 OpenHome。

---

### Step 2：默认配置改成 OpenHome 配置

建议默认配置方向：

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.openhome/workspace",
      "botName": "OpenHome",
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

- 默认 workspace 改成 `~/.openhome/workspace`。
- 默认 config path 改成 `~/.openhome/config.json`。
- 增加顶层 `homeAssistant` schema。
- 增加默认 rules/logs 路径。
- 保持环境变量解析能力。

验收：

- 新用户第一次运行生成 `~/.openhome/config.json`。
- `homeAssistant` 配置能被环境变量解析。
- `openhome status` 能显示 OpenHome 关键状态。

---

### Step 3：OpenHome workspace 模板

目标：系统提示词默认就是智能家居管家。

任务：

- 重写 `SOUL.md`：OpenHome 是本地 AI 智能家居管家，重视隐私、安全和家庭上下文。
- 重写 `USER.md`：家庭成员、房间、作息、温度偏好、通知偏好。
- 重写 `TOOLS.md`：HA 工具使用规则，执行前先确认实体，危险动作要谨慎。
- 重写 `memory/MEMORY.md`：家庭设备、习惯、自动化规则、重要安全偏好。

验收：

- 新 workspace 的 system prompt 明确 OpenHome 身份。
- 用户说“打开灯”时，模型知道这是家居设备控制场景。

---

### Step 4：Home Assistant REST Client

新增：

- `openhome/openhome/home/__init__.py`
- `openhome/openhome/home/ha_client.py`

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

- `openhome/openhome/home/ha_mcp.py`

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
        "args": ["-m", "openhome.home.ha_mcp"],
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

- Telegram 消息进入 OpenHome。
- OpenHome 通过 HA MCP 查询/控制设备。
- HA 成功后，Telegram 收到自然语言确认。

---

### Step 7：WebUI 边界收敛

任务：

- 在 README 和计划文档中明确：v1 不做类似 OpenClaw 的完整 WebUI。
- 现有 `webui/` 保留为可选开发调试、会话观察、基础配置检查入口。
- 不新增复杂前端页面、设备大屏、家庭仪表盘、规则市场。
- v1 安装和验收路径不依赖 webui。

验收：

- 用户不启动 webui 也能完成 OpenHome 核心闭环。
- 文档不会把 webui 描述成 v1 必需入口。
- 后续如要做前端，另开计划定义“家庭配置台/设备状态页/记忆管理页”。

---

## 5. 测试清单

单元测试：

- 默认 config path 指向 `~/.openhome`。
- `homeAssistant` schema 默认值。
- HA client 请求头和错误处理。
- service allowlist。
- HA MCP 工具参数校验。

集成测试：

- mock HA `/api/states`。
- mock HA `/api/services/{domain}/{service}`。
- mock MCP tool call。
- CLI `openhome status` 文案正确。

手工测试：

- `openhome --help`。
- `openhome onboard` 或初始化配置。
- `openhome gateway`。
- Telegram allowlist 生效。
- HA 查询和控制成功。
- 不启动 webui 时，核心 Telegram + HA 闭环仍可用。

---

## 6. 完成定义

Plan 1 完成后：

- 项目运行入口和用户可见身份是 OpenHome。
- 默认配置、workspace、模板都面向智能家居管家。
- Home Assistant 最小 MCP 工具可用。
- Telegram 能完成一次真实设备查询和控制。
- v1 范围不包含完整 WebUI，现有 webui 只作为可选调试/配置控制台。
- 后续 Plan 2 可以在这个基础上做主动触发引擎。
