# OriginAgent v1 开发任务清单与技术落地方案

> 状态：历史迁移参考。项目对外身份已经统一为 OriginAgent；智能家居能力后续应作为领域插件保留，而不是核心 Agent 架构身份。

> 目标：把当前代码库直接产品化为 OriginAgent。
> OriginAgent 不是一个可选模式，而是这个项目最终对用户呈现的唯一产品身份。

---

## 1. v1 产品边界

### v1 必须完成

- 项目默认名称、命令、配置目录、workspace、模板、Docker 服务名统一为 OriginAgent。
- OriginAgent 默认定位为“运行在本地 NAS 上的 AI 智能家居管家”。
- Telegram 作为第一入口可用。
- Home Assistant 最小工具集可用：搜索实体、读取状态、调用服务。
- 用户可以通过自然语言完成一次真实设备查询和控制。
- 主动触发基础版可监听 HA 状态变化并推送通知。
- Docker Compose 可运行完整闭环，为飞牛 fpk 做准备。
- 明确 v1 不建设完整产品 WebUI；现有 webui 仅作为可选开发调试/配置控制台保留。

### v1 暂不做

- 语音输入输出。
- 摄像头视觉感知和人脸识别。
- 多家庭成员声纹/人脸区分。
- 自动规律提取和长期家庭画像。
- 场景模板市场。
- 飞牛相册深度接入。
- 类似 OpenClaw 的完整 WebUI 或桌面式控制台。

### 隐私表述

v1 对外表述统一为：

> 家庭配置、设备日志、记忆和规则默认保存在本地 NAS；模型可选择云端 API 或本地 Ollama，用户在配置过程中明确选择。

---

## 2. 目标架构

```text
Telegram / optional Web console
  ↓
OriginAgent gateway
  ├─ ChannelManager
  ├─ AgentLoop
  ├─ OriginAgent workspace profile
  ├─ Home Assistant MCP tools
  ├─ Provider fallback
  ├─ Dream / memory
  └─ OriginAgent event ingress
        ↑
        │
OriginAgent trigger
  ├─ HA WebSocket listener
  ├─ rules.yaml
  ├─ cooldown / dedupe
  └─ action dispatcher
```

关键判断：

- OriginAgent 直接复用现有 agent loop、message bus、channel、MCP、memory、provider、Docker 能力。
- 不设计 `originagent.enabled` 之类的模式开关。
- 默认配置、默认模板、默认命令都以 OriginAgent 为中心。
- v1 主入口是 Telegram；现有 webui 不作为 OriginAgent 的核心产品入口。
- 主动触发可以先同进程接入 gateway，稳定后再拆成独立进程。

---

## 3. 目录与命名目标

目标目录：

```text
originagent/
├── pyproject.toml
├── README.md
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── originagent/
│   ├── __init__.py
│   ├── cli/
│   ├── config/
│   ├── agent/
│   ├── channels/
│   ├── providers/
│   ├── templates/
│   └── originagent/
│       ├── ha_client.py
│       ├── ha_mcp.py
│       ├── trigger.py
│       ├── rules.py
│       └── actions.py
├── tests/
└── fpk/
```

用户侧默认路径：

```text
~/.originagent/
├── config.json
├── workspace/
│   ├── SOUL.md
│   ├── USER.md
│   ├── TOOLS.md
│   └── memory/MEMORY.md
├── rules.yaml
├── behavior.jsonl
└── trigger_events.jsonl
```

---

## 4. 模块任务拆分

### A. 产品身份替换

目标：运行、安装、阅读、日志、回复里看到的都是 OriginAgent。

任务：

- 项目包名改为 `originagent-ai` 或 `originagent`。
- CLI 命令统一为 `originagent`。
- 默认 `botName` 改为 `OriginAgent`。
- 默认 `botIcon` 改为适合智能家居的符号。
- CLI help、gateway 启动文案、status 文案统一改为 OriginAgent。
- README 改成 OriginAgent 产品说明。
- Docker 镜像、服务名、容器名统一为 OriginAgent。

验收标准：

- `originagent --help` 展示 OriginAgent。
- `originagent gateway` 启动日志展示 OriginAgent。
- Telegram 回复不再出现旧产品身份。
- 新用户不会感知到底层历史来源。

---

### B. 默认配置收敛

目标：默认配置就是智能家居场景，而不是通用个人助理。

建议配置：

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
      "climate.set_temperature",
      "media_player.*"
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

- 默认 config path 改为 `~/.originagent/config.json`。
- 默认 workspace 改为 `~/.originagent/workspace`。
- 增加顶层 `homeAssistant` 配置。
- 增加 `rulesPath`、日志路径、默认通知目标。
- 保持环境变量引用机制可用。

验收标准：

- 新用户第一次初始化生成 `~/.originagent/config.json`。
- 配置能解析 HA URL、HA Token、Telegram Token。
- `originagent status` 能显示 HA、Telegram、模型、workspace 状态。

---

### C. OriginAgent workspace profile

目标：系统提示词默认就是家庭管家。

任务：

- 重写 `SOUL.md`：OriginAgent 的身份、安全边界、主动但克制的通知风格。
- 重写 `USER.md`：家庭成员、房间、作息、温度偏好、通知偏好。
- 重写 `TOOLS.md`：HA 工具调用准则、危险动作确认规则、实体搜索策略。
- 重写 `memory/MEMORY.md`：家庭设备、自动化规则、长期偏好、重要安全信息。
- 初始化 workspace 时只创建缺失文件，避免覆盖用户编辑过的内容。

验收标准：

- 新 workspace 的 system prompt 明确 OriginAgent 身份。
- 用户说“打开灯”时，模型知道这是家庭设备控制。
- 危险动作会被谨慎处理。

---

### D. Home Assistant MCP 工具

目标：让 OriginAgent 通过安全工具访问 HA，而不是让模型拼 HTTP 请求。

工具集合：

- `ha_search_entities(query: string)`
- `ha_get_state(entity_id: string)`
- `ha_get_states(entity_ids: string[])`
- `ha_call_service(domain: string, service: string, target?: object, data?: object)`
- `ha_validate_connection()`

安全要求：

- `ha_call_service` 必须检查 service allowlist。
- 默认不允许重启、删除、解锁门锁、安防关闭等高风险服务。
- 工具返回短文本或结构化摘要，避免把 HA 全量状态塞进上下文。
- 服务调用写入 `~/.originagent/behavior.jsonl`。

默认 MCP 配置：

```json
{
  "tools": {
    "mcpServers": {
      "home_assistant": {
        "command": "python",
        "args": ["-m", "originagent.originagent.ha_mcp"],
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

验收标准：

- 模型可以搜索实体。
- 模型可以读取实体状态。
- 模型可以调用允许的 HA 服务。
- 未授权服务不会执行。

---

### E. Telegram 第一入口与 WebUI 边界

目标：用户先通过手机消息完成日常控制。

任务：

- 默认文档引导创建 Telegram Bot。
- `allowFrom` 不允许默认开放。
- `originagent status` 显示 Telegram allowlist 状态。
- 提供 OriginAgent 示例命令。
- 不把现有 webui 产品化为类似 OpenClaw 的完整界面。
- 如保留 webui，只定位为开发调试、会话观察、基础配置检查入口。
- v1 安装、使用、验收路径不依赖 webui。

验收标准：

- allowlist 外用户无法控制设备。
- 用户发送“打开客厅灯”后可以完成 HA 调用。
- HA 调用结果会自然回复到 Telegram。
- 计划和文档不会暗示 v1 需要完整 WebUI。

---

### F. 主动触发基础版

目标：监听 HA 状态变化，并按规则触发通知或动作。

事件流：

```text
HA WebSocket state_changed
  → rule match
  → cooldown / dedupe
  → action dispatch
```

rules.yaml 示例：

```yaml
rules:
  - id: co2_high_notify
    name: CO2 超标提醒
    enabled: true
    trigger:
      entity_id: sensor.co2
      condition: "float(value) > 1000"
    guard:
      cooldown_s: 1800
    action:
      type: notify
      message: "CO2 浓度 {{ value }} ppm 超标，建议开窗。"

  - id: night_motion_judge
    name: 深夜移动判断
    enabled: true
    trigger:
      entity_id: binary_sensor.motion
      condition: "state == 'on' and hour >= 23"
    guard:
      cooldown_s: 900
    action:
      type: model_judge
      prompt: "深夜检测到移动，请结合家庭上下文判断是否通知主人。"
```

动作类型：

- `notify`：直接发送通知。
- `model_judge`：交给 OriginAgent 判断是否需要通知或执行动作。
- `ha_service`：直接调用 HA 服务，必须过 allowlist。
- `log_only`：只记录。

验收标准：

- HA 状态变化 1 秒内进入规则引擎。
- 冷却时间内不重复刷屏。
- CO2 示例能发 Telegram 通知。
- 模型不可用时，确定性规则仍可工作。

---

### G. Provider fallback

目标：DeepSeek 不可用时降级到 Claude，再降级到 Ollama。

任务：

- 增加 `FallbackProvider`。
- 区分 transient error、鉴权失败、余额不足、上下文过长。
- 记录本轮实际使用的 provider。
- 降级失败时给用户清晰提示。

验收标准：

- 主 provider mock 超时后自动切备用 provider。
- 本地模型未开启时不会假装可用。
- 不影响工具调用和规则层。

---

### H. Docker 与飞牛 fpk

目标：为飞牛应用商店准备一键安装。

开发期 compose：

```yaml
services:
  originagent-gateway:
    build: ./originagent
    command: ["gateway", "--config", "/home/originagent/.originagent/config.json"]
    volumes:
      - ./data/originagent:/home/originagent/.originagent
    ports:
      - "18790:18790"
      - "8765:8765"

  originagent-trigger:
    build: ./originagent
    command: ["trigger", "--config", "/home/originagent/.originagent/config.json"]
    volumes:
      - ./data/originagent:/home/originagent/.originagent
```

验收标准：

- 容器重启后配置、记忆、规则、日志不丢。
- `/health` 返回 ok。
- 飞牛安装后 15 分钟内完成基础配置。

---

## 5. 四周排期

### Week 1：身份替换与 HA 最小工具

- 产品名、CLI、默认路径、模板、README、Docker 服务名统一为 OriginAgent。
- 增加 `homeAssistant` 配置。
- 实现 HA REST client。
- 实现 HA MCP 最小工具。
- Telegram 手工完成查询和控制。

交付物：

- OriginAgent 作为默认产品身份运行。
- HA 查询和控制闭环可用。

### Week 2：主动触发基础版

- 实现 HA WebSocket listener。
- 实现 rules.yaml parser。
- 实现安全 condition evaluator。
- 实现 cooldown/dedupe。
- 实现 `notify`、`model_judge`、`ha_service`。

交付物：

- CO2/夜间移动/低温规则可运行。
- 主动通知能到 Telegram。

### Week 3：配置体验与 fallback

- 增加 OriginAgent status。
- 增加基础配置检查。
- 实现 provider fallback。
- 增加行为日志。
- 将 webui 定位写清楚：可选开发调试/配置控制台，不进入 v1 主路径。
- 增加关键单元测试和集成测试。

交付物：

- 用户能快速定位配置问题。
- 模型主服务异常时能降级。

### Week 4：打包与发布准备

- 完善 Docker Compose。
- 准备 fpk manifest。
- 写安装文档、默认规则说明、故障排查。
- 干净环境测试。
- 修复稳定性问题。

交付物：

- 飞牛社区测试版。

---

## 6. 完成定义

v1 可以发布的标准：

- 用户看到的是 OriginAgent，而不是旧产品身份。
- 默认配置目录为 `~/.originagent`。
- Telegram 能查询和控制 HA 灯、开关、空调/温控。
- 至少 3 条默认主动规则可运行。
- 自动执行类规则默认关闭，通知类规则默认开启。
- v1 不依赖完整 WebUI；webui 即使不可用也不阻塞核心使用。
- 模型不可用时，确定性规则层继续工作。
- 配置、规则、日志、记忆保存在本地。
