# G1 人形机器人 Agent 集成设计方案

> 版本: 1.0  
> 日期: 2026-06-15  
> 目标机器人: Unitree G1 (EDU)  
> 基础框架: OriginAgent + unitree_sdk2_python

---

## 目录

1. [项目背景与目标](#1-项目背景与目标)
2. [系统架构](#2-系统架构)
3. [硬件拓扑与通信链路](#3-硬件拓扑与通信链路)
4. [MCP Server 详细设计](#4-mcp-server-详细设计)
5. [OriginAgent Domain Pack 设计](#5-originagent-domain-pack-设计)
6. [Agent Loop 改造：PERCEIVE 阶段](#6-agent-loop-改造perceive-阶段)
7. [分阶段实施计划](#7-分阶段实施计划)
8. [测试与验收策略](#8-测试与验收策略)
9. [风险与缓解措施](#9-风险与缓解措施)
10. [附录](#10-附录)

---

## 1. 项目背景与目标

### 1.1 背景

宇树 G1 人形机器人具备 23~29 自由度、D435i 深度相机、LIVOX-MID360 激光雷达、4 麦克风阵列、灵巧手 Dex3-1 等丰富传感器，搭载 Jetson Orin NX 作为开发计算单元。传统开发模式下，G1 通过预编排脚本执行固定动作序列。

本项目旨在将 G1 接入 OriginAgent 框架，使 LLM 作为机器人的"大脑"进行高层决策，G1 自身的控制系统作为"小脑"负责实时运动协调，实现自然语言驱动的自主机器人控制。

### 1.2 目标

- **短目标**：通过 OriginAgent 以自然语言指令控制 G1 完成站、坐、走、挥手等基本动作
- **中目标**：G1 通过摄像头+多模态 LLM 实现环境感知 → 语义理解 → 自主决策闭环
- **长目标**：G1 融合视觉、雷达、灵巧手等多模态感知，在复杂环境中执行自主任务

### 1.3 核心设计原则

| 原则 | 说明 |
|------|------|
| **大脑-小脑分层** | LLM 做高层决策，G1 内控系统做实时运动协调 |
| **边缘处理** | Jetson Orin NX 上做传感器数据预处理，Agent 消费语义化结果 |
| **多模态作为管道** | 摄像头/雷达等原始数据 → 多模态 LLM → 结构化 JSON → Agent 主 LLM |
| **安全自主** | 硬件级安全终止（IMU 摔倒检测等）独立于 Agent，不依赖 LLM 响应速度 |
| **渐进交付** | 4 个 Phase 逐步交付，每阶段可独立测试和验证 |

---

## 2. 系统架构

### 2.1 三层架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        大脑 (Brain)                               │
│  OriginAgent (运行于开发机)                                       │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Agent Loop (改造后)                                        │  │
│  │  RESTORE → COMPACT → COMMAND → BUILD → ★PERCEIVE★ → RUN    │  │
│  │                                      │                      │  │
│  │                              ┌───────┴───────┐              │  │
│  │                              │ 多模态管道      │              │  │
│  │                              │ · 采样摄像头帧   │              │  │
│  │                              │ · 调多模态 LLM  │              │  │
│  │                              │ · JSON 注入上下文│              │  │
│  │                              └───────────────┘              │  │
│  │                                                              │  │
│  │  ┌──────────────────────────────────────────────────────┐   │  │
│  │  │  Domain Pack (robot_g1)                               │   │  │
│  │  │  · CAPABILITIES.md — G1 能力边界描述                    │   │  │
│  │  │  · Skills — 复合动作编排                                │   │  │
│  │  │  · Runtime — 动作规划器 + 安全门                        │   │  │
│  │  └──────────────────────────────────────────────────────┘   │  │
│  └────────────────────────────────────────────────────────────┘  │
│                           │ MCP over SSE (跨网络)                 │
└───────────────────────────┼──────────────────────────────────────┘
                            │
┌───────────────────────────┼──────────────────────────────────────┐
│                   小脑/感知 (Cerebellum & Senses)                  │
│  G1 Jetson Orin NX (192.168.123.164)                             │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  unitree-mcp-server (Python FastMCP)                        │  │
│  │                                                              │  │
│  │  ┌────────────────┐  ┌──────────────┐  ┌────────────────┐  │  │
│  │  │  运动控制工具集   │  │  感知工具集    │  │  状态/安全工具集 │  │  │
│  │  │  · connect     │  │  · look()    │  │  · get_state   │  │  │
│  │  │  · stand       │  │  · scan_env()│  │  · safety_check │  │  │
│  │  │  · walk        │  │  · get_depth │  │  · battery      │  │  │
│  │  │  · wave_hand   │  │  · hear()    │  │  · temperature  │  │  │
│  │  │  · sit...      │  │              │  │                 │  │  │
│  │  └───────┬────────┘  └──────┬───────┘  └───────┬─────────┘  │  │
│  └──────────┼──────────────────┼──────────────────┼─────────────┘  │
│             │ DDS domain 0     │ (本地回环)         │               │
│             ▼                  ▼                    ▼               │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  G1 运控单元 (不对外开放)                                    │  │
│  │  · LocoClient 高层 API  → 行走/站立/坐下                      │  │
│  │  · ArmActionClient     → 16 种预设手势                       │  │
│  │  · DDS 低层通道         → rt/lowcmd / rt/arm_sdk            │  │
│  │  · 安全终止             → IMU/温度/电池/超限检查               │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  G1 硬件传感器层                                             │  │
│  │  · D435i 深度相机    → USB → Jetson                         │  │
│  │  · LIVOX-MID360 雷达 → Ethernet → Jetson                    │  │
│  │  · 4 麦克风阵列      → I2S → Jetson                         │  │
│  │  · Dex3-1 灵巧手     → DDS → rt/dex3/left|right/state       │  │
│  │  · 关节双编码器      → DDS → rt/lowstate                    │  │
│  │  · 电池 BMS          → DDS → rt/lf/bmsstate                 │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流路径

```
A. 运动控制路径（低延迟）
   用户指令 → Agent 推理 → MCP SSE → Jetson → LocoClient → DDS → G1 关节

B. 感知路径（多模态）
   D435i 帧 → Jetson 采样 → 多模态 LLM → JSON 语义描述 → PERCEIVE → Agent 推理

C. 安全路径（硬实时，独立于 Agent）
   IMU 异常 → G1 内部 termination check → 自动 ZeroTorque（~50ms 响应）
```

---

## 3. 硬件拓扑与通信链路

### 3.1 网络拓扑

```
┌─────────────────┐         同一局域网          ┌──────────────────┐
│   开发机         │◄──── Ethernet / WiFi ────►│  G1 Jetson Orin  │
│  OriginAgent     │      MCP over SSE         │   NX (164)       │
│  192.168.123.222 │    SSH (开发/调试)          │  192.168.123.164 │
└─────────────────┘                            └────────┬─────────┘
                                                         │ DDS domain 0
                                                         │ (本地回环)
                                                 ┌───────┴───────┐
                                                 │ G1 运控单元    │
                                                 │  192.168.123.161│
                                                 └───────────────┘
```

### 3.2 关键网络参数

| 项目 | 值 |
|------|------|
| 开发机 IP | 192.168.123.222 (静态，同网段即可) |
| G1 运控单元 | 192.168.123.161 (SSH: unitree/123) |
| G1 开发计算单元 (Jetson) | 192.168.123.164 (SSH: unitree/123) |
| DDS Domain ID (物理机) | 0 |
| DDS Domain ID (仿真) | 1 |
| 有线接口 | eth0 |
| MCP 传输协议 | SSE (Server-Sent Events) |
| MCP Server 端口 | 18791 (建议，避免与 OA Gateway 18790 冲突) |

### 3.3 G1 硬件规格参考

| 项目 | 规格 |
|------|------|
| 自由度 | 23 (基础) / 29 (含手腕) |
| 关节电机最大扭矩 | 120 N.m |
| 手臂最大负载 | 约 3 Kg (EDU) |
| 续航 | 约 2h (9000mAh 电池) |
| D435i 深度相机 | RGB + 双目红外，支持像素级深度对齐 |
| LIVOX-MID360 激光雷达 | 360° 水平 FOV，59° 垂直 FOV |
| 灵巧手 Dex3-1 | 7 自由度，9 阵列触觉传感器，10g-2500g 感知范围 |
| 扬声器 | 5W |
| 麦克风 | 4 阵列 |
| Jetson Orin NX | 8 核 A78AE @2GHz, 1024 核 GPU, 16GB 显存, 2TB 存储 |

### 3.4 关节索引与限位

G1 共 29 个可控关节 (索引 0-28)：

| 范围 | 索引 | 部位 |
|------|------|------|
| 0-5 | LeftHipPitch ~ LeftAnkleRoll | **左腿** |
| 6-11 | RightHipPitch ~ RightAnkleRoll | **右腿** |
| 12-14 | WaistYaw / Roll / Pitch | **腰部** (23DOF 时锁定) |
| 15-21 | LeftShoulderPitch ~ LeftWristYaw | **左臂** |
| 22-28 | RightShoulderPitch ~ RightWristYaw | **右臂** |

完整限位表见 `unitree_sdk2/example/g1/low_level/` 或硬件手册。

---

## 4. MCP Server 详细设计

### 4.1 概述

MCP Server `unitree-sdk2-mcp` 运行在 G1 Jetson Orin NX 上，通过 SSE 协议暴露 G1 全部能力给 OriginAgent。

**定位**：小脑/感知层的统一接口层。不承载 LLM 推理，不承载业务逻辑，只做能力的原子化封装。

**依赖**：
- `mcp[fastmcp]>=1.0.0` — MCP 协议框架
- `unitree_sdk2_python` — 宇树官方 Python SDK (本地已存在)
- `numpy` / `opencv-python` — 图像与数据处理

### 4.2 工具清单

#### 4.2.1 连接生命周期 (3 个)

| 工具名 | 参数 | 返回值 | 说明 |
|--------|------|--------|------|
| `connect_robot` | `domain_id=0`, `interface="eth0"` | `{"status":"ok","robot":"g1"}` | 初始化 DDS、LocoClient、ArmActionClient、音频、状态订阅 |
| `disconnect_robot` | — | `{"status":"ok"}` | 安全断开所有连接，清理 DDS participant |
| `get_connection_status` | — | `{"connected":bool, "dds_quality":...}` | 连接状态和 DDS 质量 |

#### 4.2.2 FSM 状态控制 (4 个)

| 工具名 | 参数 | 等效 LocoClient | FSM ID | 说明 |
|--------|------|-----------------|--------|------|
| `zero_torque` | — | `ZeroTorque()` | 0 | 电机断电，紧急停止 |
| `damp` | — | `Damp()` | 1 | 阻尼模式(重力补偿) |
| `sit` | — | `Sit()` | 3 | 坐下 |
| `squat2stand` | — | `Squat2StandUp()` | 706 | 从蹲姿站起 |
| `lie2stand` | — | `Lie2StandUp()` | 702 | 从躺姿站起 |
| `start` | — | `Start()` | 500 | 启动行走状态 |

#### 4.2.3 运动控制 (3 个)

| 工具名 | 参数 | 说明 |
|--------|------|------|
| `move` | `vx: float [-0.5, 0.5]`, `vy: float [-0.3, 0.3]`, `vyaw: float [-0.5, 0.5]` | 持续速度控制，直到 `stop_move` |
| `stop_move` | — | 速度归零，回到站立 |
| `get_state` | — | FSM 模式、关节位置、电池、IMU、安全状态 |

#### 4.2.4 手臂控制 (2 个)

| 工具名 | 参数 | 说明 |
|--------|------|------|
| `wave_hand` | `turn_flag: bool=False` | LocoClient 预设挥手 |
| `shake_hand` | `stage: int=-1` | LocoClient 预设握手 (-1=自动切换) |

#### 4.2.5 手臂动作库 (1 个)

| 工具名 | 参数 | 说明 |
|--------|------|------|
| `execute_arm_action` | `action_id: int \| action_name: str` | 执行 16 个预设动作之一 |

支持的动作名称：`release arm`, `two-hand kiss`, `left kiss`, `right kiss`, `hands up`, `clap`, `high five`, `hug`, `heart`, `right heart`, `reject`, `right hand up`, `x-ray`, `face wave`, `high wave`, `shake hand`

#### 4.2.6 语音控制 (2 个)

| 工具名 | 参数 | 说明 |
|--------|------|------|
| `speak` | `text: str`, `speaker_id: int=0` | TTS 语音合成 |
| `set_volume` | `volume: int [0-100]` | 设置扬声器音量 |
| `led_control` | `r: int, g: int, b: int` | LED 灯颜色控制 |

#### 4.2.7 视觉感知 (2 个) — Phase 2+

| 工具名 | 参数 | 说明 |
|--------|------|------|
| `look` | `question: str = "描述当前场景"` | 拍摄 → 多模态 LLM → JSON 语义描述 |
| `get_depth` | `area: str = "center"` | 获取指定区域深度距离 |

#### 4.2.8 安全检查 (1 个)

| 工具名 | 参数 | 说明 |
|--------|------|------|
| `safety_check` | — | 运行全部安全检查，返回告警列表 |

检查项：姿态异常、关节超速、电机过热、低电量、连接超时

### 4.3 MCP Resources

| Resource URI | 类型 | 说明 |
|-------------|------|------|
| `unitree://g1/state` | JSON | 完整机器人状态（FSM 模式、关节位置、电池、IMU） |
| `unitree://g1/status` | JSON | 连接状态、DDS 质量 |
| `unitree://g1/joints` | JSON | 关节限位表、当前角度 |
| `unitree://g1/safety` | JSON | 安全检查结果 |
| `unitree://g1/environment` | JSON | 环境感知摘要 (Phase 2+) |

### 4.4 安全限值

| 参数 | 限值 | 理由 |
|------|------|------|
| vx (前进/后退) | ±0.5 m/s | G1 人形，保守起步，后续可放开 |
| vy (横向) | ±0.3 m/s | 人形横向移动能力有限 |
| vyaw (旋转) | ±0.5 rad/s | 安全优先 |
| arm_sdk weight | 0.0 ~ 1.0 | 0=禁用，>0=启用手臂 SDK 控制 |

---

## 5. OriginAgent Domain Pack 设计

### 5.1 结构

```
OriginAgent/domain_packs/robot_g1/
├── domain_pack.yaml       # 清单 manifest
├── CAPABILITIES.md         # G1 能力语义描述
├── tools/
│   └── g1.py              # Domain tools (如需要)
├── skills/
│   └── g1-operations/
│       └── SKILL.md       # "如何操作 G1 机器人" skill
└── runtime/
    ├── __init__.py
    ├── contribution.py    # 运行时贡献
    ├── robot_backend.py   # UnitreeG1Backend (真实后端)
    ├── robot_safety.py    # RobotActionSafetyGate (重写)
    └── robot_actions.py   # G1 动作类型定义
```

### 5.2 domain_pack.yaml

```yaml
id: robot_g1
name: Unitree G1 Robot
version: 1.0.0
enabled: true
description: "宇树 G1 人形机器人控制域包"

capabilities:
  - motion_control           # 运动控制
  - arm_gestures             # 手臂手势
  - voice_output             # 语音输出
  - visual_perception        # 视觉感知 (Phase 2+)
  - environment_awareness    # 环境感知 (Phase 2+)
  - dexterous_hand           # 灵巧手 (Phase 3+)

activation:
  triggers:
    - robot
    - unitree
    - g1
    - 机器人
    - 宇树

tools: []  # MCP Server 提供 tool，此处不重复定义

skills:
  - g1-operations

runtime:
  module: runtime.contribution
  factory: build_runtime_contribution
```

### 5.3 CAPABILITIES.md

```
## Domain Pack: robot_g1

你可以在 OriginAgent 中控制一台宇树 G1 人形机器人。

### 可用能力
- **运动控制**: 站起(squat2stand)、坐下(sit)、行走(move)、停止(stop_move)、紧急停止(zero_torque)
- **手臂手势**: 挥手(wave_hand)、握手(shake_hand)、16种预设手臂动作(execute_arm_action)
- **语音输出**: TTS 语音合成(speak)、音量控制(set_volume)、LED 灯控制(led_control)
- **状态读取**: 实时 FSM 模式、关节位置、电池电量、IMU 数据、安全检查

### 安全约束
- 最大前进/后退速度: 0.5 m/s
- 最大横向速度: 0.3 m/s
- 最大旋转速度: 0.5 rad/s
- 所有运动操作前必须先建立连接
- 紧急停止使用 zero_torque

### 典型操作序列
1. connect_robot()
2. squat2stand() → 等待 2 秒让机器人完全站起
3. start() → 进入行走就绪状态
4. move(vx=0.3) 或 wave_hand() 或 speak("你好")
5. stop_move() → sit() → disconnect_robot() 安全结束
```

### 5.4 robot_backend.py (Phase 1 重写)

```python
# 替代现有的 DryRunRobotBackend
class UnitreeG1Backend:
    """通过 MCP Server 发送真实 G1 指令的后端"""

    def __init__(self):
        self._mcp_client = None  # 连接到 Jetson MCP Server

    async def connect(self, jetson_url: str):
        """建立 SSE 连接到 Jetson MCP Server"""
        ...

    async def execute_action(self, action: RobotAction) -> ActionResult:
        """将域包动作翻译为 MCP 工具调用"""
        ...
```

### 5.5 robot_safety.py (重写)

```python
class RobotActionSafetyGate:
    """G1 安全门 — 全面安全检查后放行"""

    async def check(self, action: RobotAction, state: RobotState) -> SafetyVerdict:
        checks = []

        # 关节限位检查
        if action.type == "move_joint":
            if not self._within_limits(action, state):
                checks.append(("deny", "关节位置超限"))

        # 状态依赖检查
        if action.type == "move":
            if state.fsm_mode not in (500,):  # 需要 Start() 之后才能走
                checks.append(("deny", "未处于行走状态"))

        # 温度检查
        if any(motor.temperature[1] > 120 for motor in state.motors):
            checks.append(("warn", "电机绕组温度过高"))

        # 电池检查
        if state.battery < 20:
            checks.append(("warn", "电量低于 20%"))

        return self._aggregate(checks)
```

---

## 6. Agent Loop 改造：PERCEIVE 阶段

### 6.1 改造目标

在 Agent Loop 的 `BUILD`（上下文组装）和 `RUN`（LLM 推理）之间插入 `PERCEIVE` 阶段，在此阶段自动采样摄像头帧、调用多模态 LLM、将结构化感知结果注入上下文。

### 6.2 当前 Loop 状态

```python
# OriginAgent/agent/agent_turn_pipeline.py
class TurnState(enum.IntEnum):
    RESTORE = auto()    # 恢复会话
    COMPACT = auto()    # 上下文压缩
    COMMAND = auto()    # 命令处理
    BUILD = auto()      # 构建消息
    RUN = auto()        # LLM 推理 + 工具执行 ← 感知应该在此前
    SAVE = auto()       # 保存会话
    AUTOMATION = auto() # 自动化执行
    RESPOND = auto()    # 响应
    DONE = auto()       # 完成
```

### 6.3 改造后 Loop

```python
class TurnState(enum.IntEnum):
    RESTORE = auto()
    COMPACT = auto()
    COMMAND = auto()
    BUILD = auto()      # 构建消息（含已有 media）
    PERCEIVE = auto()   # ★ 新增：感知阶段（采样、理解、注入）
    RUN = auto()        # LLM 推理（上下文已包含感知结果）
    SAVE = auto()
    AUTOMATION = auto()
    RESPOND = auto()
    DONE = auto()
```

### 6.4 PERCEIVE 阶段伪代码

```python
async def _run_perceive(self):
    """Perception phase: 在 LLM 推理前采集环境信息"""

    # 1. 是否启用感知（由 Domain Pack / config 控制）
    if not self._perception_enabled:
        return

    # 2. 检查是否有活跃的摄像头
    perception_tool = self._get_mcp_tool("g1", "look")
    if not perception_tool:
        return

    # 3. 调用多模态感知 (MCP tool 内部调多模态 LLM)
    perception_result = await perception_tool.execute(
        question="详细描述当前场景，包含人员、障碍物、距离信息"
    )

    # 4. 解析返回的 JSON 结构化描述
    #   {"scene": "...", "persons": [...], "obstacles": [...]}

    # 5. 注入到系统提示中，供 RUN 阶段消费
    self._perception_context = perception_result

    # 6. (后续) 汇总到世界模型 / 短期记忆
    await self._world_model.ingest(perception_result)
```

### 6.5 BUILD 阶段的配合修改

在 `BUILD` 阶段组装 system prompt 时，如果 `_perception_context` 存在，追加一段：

```
### 当前环境感知

{_perception_context}
```

这样 Agent 主 LLM 在推理时已经"看到"了环境，可以直接决策。

### 6.6 可选：非每轮感知

PERCEIVE 阶段不需要每轮都执行。可根据以下条件决定是否触发：

- **定时触发**：每 N 秒自动执行一次
- **事件触发**：用户指令涉及环境相关（"前面有人吗"）
- **状态触发**：机器人正在移动中（需要持续环境反馈）

这通过 `perception_policy` 配置控制：

```python
class PerceptionConfig:
    mode: Literal["always", "on_demand", "interval", "motion"] = "on_demand"
    interval_seconds: int = 5      # interval 模式下的采样间隔
    max_frames_per_turn: int = 1   # 每轮最大帧数
    multimodal_model: str = ""     # 多模态 LLM 模型名（复用 OA provider）
```

---

## 7. 分阶段实施计划

### Phase 1：基础运动控制 MVP

**目标**：通过 OriginAgent 以自然语言控制 G1 完成站、坐、走、停

**范围**：

| 做 | 不做 |
|----|------|
| MCP Server: 连接管理 + FSM 控制 + 运动控制 | ❌ 视觉感知 |
| MCP Server: 状态读取 + 安全检查 | ❌ 灵巧手 |
| Domain Pack: CAPABILITIES.md + Skills | ❌ 音频控制 |
| Domain Pack: 安全门(重写) | ❌ Agent Loop 改造 |
| Domain Pack: 真实后端(连接 MCP) | ❌ 多模态 |
| OA 配置：SSE 连接 Jetson | ❌ 上下文管理改造 |
| 基础测试框架搭建 | |

**收口测试**：
```
1. 开发机上 MCP Server 单机测试（不连 G1，模拟返回值）
2. 局域网内 SSE 连接测试（开发机 ↔ Jetson）
3. 真实 G1 冒烟测试：stand → walk → wave_hand → sit → disconnect
4. OriginAgent 端到端：自然语言指令 → G1 执行
```

**预计改动文件**：

| 文件 | 改动类型 |
|------|---------|
| `unitree-sdk2-mcp/src/` | 新建(重用现有骨架，重写 bridge 为真实 DDS) |
| `unitree-sdk2-mcp/pyproject.toml` | 修改依赖 |
| `OriginAgent/domain_packs/robot/` | 重写整个 domain pack 为 robot_g1 |
| `OriginAgent/config/schema.py` | 配置 MCP SSE endpoint |
| `OriginAgent/agent/agent_tool_setup.py` | 注册新的 domain tools |

---

### Phase 2：视觉感知与 PERCEIVE 阶段

**目标**：G1 通过摄像头+多模态 LLM 理解环境，Agent 根据视觉信息做决策

**范围**：

| 做 | 不做 |
|----|------|
| MCP Server: `look()` 工具（D435i 采样 → 多模态） | ❌ 激光雷达 |
| MCP Server: `get_depth()` 工具 | ❌ 灵巧手精细控制 |
| OA Agent Loop: `PERCEIVE` 阶段 (插在 BUILD 和 RUN 之间) | ❌ 声源定位 |
| OA Agent Loop: 感知上下文自动注入 | ❌ 实时视频流 |
| OA 配置: PerceptionConfig | |
| D435i 深度对齐（RGB + 距离融合） | |

**收口测试**：
```
1. Jetson 上 D435i 采样测试（确认摄像头工作正常）
2. MCP look() 在 Jetson 上单独测试（采样 + 多模态 LLM 返回 JSON）
3. SSE 跨网络调用 look() 测试
4. OriginAgent PERCEIVE 阶段集成测试（感知结果自动注入上下文）
5. 端到端：用户"前面有人吗" → look() → Agent 决策 → G1 动作
```

**预计改动文件**：

| 文件 | 改动类型 |
|------|---------|
| `unitree-sdk2-mcp/src/vision/` | 新建视觉模块 |
| `OriginAgent/agent/agent_turn_pipeline.py` | 修改：添加 PERCEIVE 状态 |
| `OriginAgent/agent/loop.py` | 修改：实现 _run_perceive() |
| `OriginAgent/agent/context.py` | 修改：感知上下文注入 |
| `OriginAgent/config/schema.py` | 新增：PerceptionConfig |

---

### Phase 3：灵巧手 + 音频 + 上下文管理

**目标**：Agent 控制灵巧手抓取、语音交互、长时运行的上下文管理

**范围**：

| 做 | 不做 |
|----|------|
| MCP Server: Dex3-1 灵巧手控制（HandState_ 读取 + 手指控制） | ❌ 激光雷达 SLAM |
| MCP Server: 音频 TTS（speak/set_volume） | ❌ 自主导航 |
| MCP Server: 16 种预设手臂动作（复用 G1ArmActionClient） | ❌ 多机协作 |
| MCP Server: 带触觉反馈的抓取 | |
| OA: 传感器上下文优先级管理（ephemeral telemetry ring buffer） | |
| OA: 上下文压缩策略优化（保留关键事件，丢弃高频传感器历史） | |

**收口测试**：
```
1. 灵巧手状态读取测试（HandState_ 触觉传感器读数）
2. 灵巧手预设抓取测试（捏、握、指等）
3. 语音合成测试
4. 长时运行 30 分钟，验证上下文管理不退化
5. 端到端：用户"把杯子拿起来" → look()找杯子 → move()走过去 → grip()
```

---

### Phase 4：激光雷达 + 世界模型 + 自主导航

**目标**：G1 在复杂环境中自主导航、避障、持续建图

**范围**：

| 做 | 不做 |
|----|------|
| MCP Server: MID360 雷达数据处理（点云 → 障碍物地图） | ❌ 多机器人编队 |
| MCP Server: 避障集成（结合 Move + 雷达） | |
| OA: 世界模型（WorldModel）模块 | |
| OA: 长期记忆 + 空间记忆（G1 走过的地方能记住） | |
| 多模态与雷达数据融合（深度相机+雷达互补） | |

**收口测试**：
```
1. 雷达点云读取 + 障碍物聚类测试
2. 避障行走测试（房间内从 A 到 B 自动避开障碍物）
3. 世界模型：G1 走一遍房间后能回答"XXX在哪里"
4. 端到端：用户"去厨房拿瓶水" → 自主导航 → 视觉识别水瓶 → 抓取 → 返回
```

---

### Phase 时间线估计

| Phase | 内容 | 估计工期 | 前置 |
|-------|------|---------|------|
| **Phase 1** | 基础运动控制 | 1-2 周 | unitree_sdk2_python 已安装 |
| **Phase 2** | 视觉感知 + PERCEIVE | 2-3 周 | Phase 1 |
| **Phase 3** | 灵巧手 + 音频 + 上下文 | 2-3 周 | Phase 1 |
| **Phase 4** | 雷达 + 世界模型 + 导航 | 3-4 周 | Phase 2, Phase 3 |

---

## 8. 测试与验收策略

### 8.1 测试层级

```
层级 1: 单元测试 (开发机)
  · MCP Server 各 tool 的逻辑测试（mock DDS）
  · Domain Pack 安全门逻辑测试
  · PERCEIVE 阶段状态机测试

层级 2: 集成测试 (局域网)
  · MCP SSE 连接测试（开发机 ↔ Jetson）
  · G1 真实动作测试（站点、行走、挥手）
  · D435i 采样测试

层级 3: 端到端测试 (真实 G1)
  · 自然语言 → G1 动作
  · 环境感知 → 决策 → 动作
  · 安全场景：异常时自动终止

层级 4: 长时稳定性 (真实 G1)
  · 30 分钟连续运行
  · 上下文不退化
  · 内存无泄漏
```

### 8.2 各阶段验收标准

#### Phase 1 验收

```
[P1-01] MCP Server 可以 SSE 方式启动并接受连接
[P1-02] connect → squat2stand → start → move → stop_move → sit → disconnect 完整流程 OK
[P1-03] wave_hand 挥手动作 OK
[P1-04] safety_check 能正确报告状态
[P1-05] OriginAgent 配 SSE 后能看到 G1 工具列表
[P1-06] OriginAgent 上输入"站起来往前走两步" → G1 执行
[P1-07] emergency_stop 能在 1 秒内响应
```

#### Phase 2 验收

```
[P2-01] look() 返回结构化 JSON 描述（含场景、人员、距离）
[P2-02] get_depth() 返回指定区域的米数
[P2-03] PERCEIVE 阶段自动注入感知上下文
[P2-04] Agent 推理时能看到环境描述并据此决策
[P2-05] 端到端：用户"前面有人吗" → G1 看 → 回答"有，2米处"
```

#### Phase 3 验收

```
[P3-01] 灵巧手状态读取（每个手指角度 + 触觉传感器值）
[P3-02] 执行预设抓取动作（捏取、握取）
[P3-03] speak() 语音合成清晰可听
[P3-04] 30 分钟会话上下文不退化
[P3-05] 16 种手臂动作全部可触发
```

#### Phase 4 验收

```
[P4-01] 激光雷达障碍物检测正确
[P4-02] G1 能自主避障行走
[P4-03] 世界模型能回答"XXX在哪里"
[P4-04] 端到端：跨房间自主导航任务
```

### 8.3 回滚策略

每个 Phase 交付前在当前分支打 tag (`g1-phase1-v1.0`)，若新 Phase 出现问题可快速回退到上一 Phase 基线。

---

## 9. 风险与缓解措施

### 9.1 技术风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| DDS 网络不稳定导致连接中断 | 中 | 高 | MCP 自动重连 + G1 内置安全终止独立运行 |
| 多模态 LLM 延迟高（>5s） | 中 | 中 | 非阻塞调用；可降级到上次有效的感知结果 |
| 上下文窗口快速填满 | 高 | 高 | Phase 3 实现传感器优先级管理；高频数据使用 ring buffer |
| Jetson 上运行 MCP Server 资源不足 | 低 | 中 | MCP Server 资源占用极低；大模型推理不在 Jetson 上 |
| G1 执行动作时 LLM 正在推理 | 低 | 中 | G1 动作执行独立进行，LLM 推理不阻塞机械执行 |

### 9.2 安全风险

| 风险 | 缓解 |
|------|------|
| LLM 决策导致 G1 执行危险动作 | 硬件级安全终止（IMU/温度/超限）独立于 Agent |
| 网络断连导致失控 | G1 内控系统在 DDS 超时后自动进入安全模式 |
| 多模态误识别导致错误决策 | 安全门在 Domain Pack 层二次校验 |
| 命令冲突（多个指令同时到达） | LocoClient 本身有 FSM 状态保护，非法转换自动拒绝 |

### 9.3 边界情况处理

| 场景 | 行为 |
|------|------|
| G1 电量 < 20% | Agent 收到安全检查告警，建议返回充电 |
| 关节温度 > 120°C | 安全门阻止高强度动作，发出告警 |
| DDS 连接超时 > 1s | G1 进入安全模式，Agent 收到断连通知 |
| 多模态 LLM 超时/失败 | 使用上次有效的感知结果，或通知 Agent"视觉暂时不可用" |
| FSM 转换拒绝 | LocoClient 返回错误码，Agent 重试安全序列 |

---

## 10. 附录

### A. 关键文件路径

| 组件 | 路径 |
|------|------|
| MCP Server 源码 | `D:\Demo\OpenHome\OriginAgentclient\unitree-sdk2-mcp\src\` |
| G1 关节索引定义 | `unitree_sdk2\example\g1\low_level\g1_low_level_example.py` |
| LocoClient 源码 | `unitree_sdk2_python\unitree_sdk2py\g1\loco\g1_loco_client.py` |
| ArmActionClient 源码 | `unitree_sdk2_python\unitree_sdk2py\g1\arm\g1_arm_action_client.py` |
| AudioClient 源码 | `unitree_sdk2_python\unitree_sdk2py\g1\audio\g1_audio_client.py` |
| DDS IDL (LowCmd/LowState) | `unitree_sdk2_python\unitree_sdk2py\idl\unitree_hg\msg\dds_\` |
| ChannelFactory | `unitree_sdk2_python\unitree_sdk2py\core\channel.py` |
| OA Agent Loop | `OriginAgent\agent\loop.py` |
| OA Turn Pipeline | `OriginAgent\agent\agent_turn_pipeline.py` |
| OA Domain Pack 框架 | `OriginAgent\agent\domain_packs.py` |
| OA MCP 封装 | `OriginAgent\agent\tools\mcp.py` |
| 现有 robot domain pack | `OriginAgent\domain_packs\robot\` |

### B. 配置示例 (OriginAgent config.yaml)

```yaml
tools:
  mcp_servers:
    unitree_g1:
      type: sse
      url: "http://192.168.123.164:18791/sse"
      tool_timeout: 30
      enabled_tools: ["*"]

domainPacks:
  enabled: true
  active:
    - robot_g1

perception:
  enabled: false  # Phase 2 开启
  mode: on_demand
  multimodal_model: "claude-sonnet-4-6"  # 复用 OA provider
```

### C. 术语表

| 术语 | 说明 |
|------|------|
| DDS | Data Distribution Service，G1 使用的实时通信中间件 (Cyclone DDS) |
| FSM | Finite State Machine，G1 的运动控制状态机 |
| LocoClient | G1 高层运动控制 API（行走、站立、坐下） |
| ArmActionClient | G1 手臂动作 API（16 种预设手势） |
| SSE | Server-Sent Events，MCP 的跨网络传输协议 |
| PERCEIVE | 新增的 Agent Loop 阶段，在 LLM 推理前采集环境感知 |
| LowCmd/LowState | G1 低层 DDS 消息类型，直接控制关节电机 |
| termination check | G1 内置安全检查（姿态、速度、温度、电量、连接） |
