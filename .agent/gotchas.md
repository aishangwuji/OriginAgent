# 常见注意事项

## 不要使用 `ruff format`

《贡献指南》（`CONTRIBUTING.md`）中虽然提到了 `ruff format`，但**请勿执行该命令**——它会破坏 Git 追溯记录。仅可使用 `ruff check`。

## 配置 `${VAR}` 变量引用

`config/loader.py` 会在加载配置时解析 `config.json` 中的 `${VAR}` 格式占位符。**这并非**类 Shell 默认值语法。如果环境变量缺失，`load_config` 会抛出 `ValueError` 异常，智能体将回退到默认配置。

合法用法示例：
```json
{ "providers": { "openrouter": { "apiKey": "${OPENROUTER_KEY}" } } }
```

## Windows 兼容性

nanobot 明确支持 Windows 系统。需留意以下关键差异：
- 在 Windows 上，`ExecTool` 使用 `cmd /c` 而非 `sh -c`（见 `shell.py`）。
- 命令行入口 `cli/commands.py` 启动时强制将标准输出/标准错误设为 UTF-8，以兼容表情符号与多语言输入。
- MCP 标准输入输出服务命令已针对 Windows 路径分隔符做标准化处理（见 `mcp.py`）。
- 路径操作**必须**统一使用 `pathlib.Path`，不要默认使用 `/` 作为路径分隔符。

## 提示词模板

智能体系统提示词、场景专属指令均存放在 `nanobot/templates/` 目录下，为 Jinja2 格式的 Markdown 文件（如 `identity.md`、`platform_policy.md`、`HEARTBEAT.md`、`SOUL.md` 等）。
修改这些文件对智能体行为的影响，等同于修改 Python 代码。模板由 `utils/prompt_templates.py` 统一加载。

工具描述、技能定义、回放会话历史同样会影响大模型行为。对这些内容的改动需视同运行时代码变更：尽量缩小改动范围，尽可能补充针对性回归测试，同时避免让模型记住并重复内部标记、本地路径或工具调用文本。

## 上下文污染会持续留存

写入内存、会话历史或提示词输入的所有内容，都可能被复用至后续大模型调用中。时间戳、本地媒体路径、工具调用回显、原始降级日志等元数据，在作为模型模仿示例前，必须做范围限制与内容清洗。

## 心跳虚拟工具调用

心跳服务（`heartbeat/service.py`）不解析大模型自由文本输出，而是向对话中注入一个虚拟 `heartbeat` 工具，支持 `action: skip | run` 两种动作。
第一阶段为结构化决策；第二阶段仅在动作为 `run` 时才执行。新增周期性后台巡检任务时，需遵循这种**虚拟工具调用**模式，不要采用字符串匹配方式。

## 技能作为扩展入口

内置技能存放于 `nanobot/skills/` 目录，采用 Markdown + YAML 前置元数据格式。
属于**知识能力**而非代码逻辑的智能体功能，应通过技能方式扩展，不要硬编码到智能体主循环中。外部技能可发布到 ClawHub 并从该平台安装。

## 会话写入原子性

`agent/memory.py` 采用**原子方式**写入 `history.jsonl`（临时文件 + 磁盘同步 + 重命名 + 目录磁盘同步），可保证程序崩溃时数据不丢失。**禁止**直接改用普通 `open(..., "w")` 覆盖写入方式。
