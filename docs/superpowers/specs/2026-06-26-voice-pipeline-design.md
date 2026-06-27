# Voice Pipeline Design: Ears & Mouth for OriginAgent

**Date**: 2026-06-26
**Status**: Draft
**Author**: ASWJ

## 1. Overview

为 OriginAgent 添加真实语音交互能力，实现"耳朵"（语音识别/STT）和"嘴巴"（语音合成/TTS），支持三种交互场景：语音消息、WebUI 实时语音、桌面本地助手。

### 1.1 技术选型

| 组件 | 方案 | 已有基础 |
|------|------|----------|
| STT (耳朵) | 火山引擎 openspeech ASR "bigmodel" | `VolcengineTranscriptionProvider` |
| TTS (嘴巴) | 火山引擎 Seed-TTS 2.0 | `_speak_text_volcengine` |
| LLM (大脑) | DeepSeek (OpenAI-compat) | 已有 provider |

### 1.2 三种交互模式

| 模式 | 描述 | 优先级 |
|------|------|--------|
| **语音消息** | 渠道发送语音文件 → STT 转文字 → Agent 处理 → 可选 TTS 回复 | P1（最高） |
| **WebUI 实时语音** | 浏览器录音 → WebSocket 推流 → 实时 STT → Agent → 实时 TTS → 播放 | P2 |
| **桌面本地助手** | Windows 后台聆听 + 热键/唤醒词激活 → 持续语音对话 | P3 |

## 2. 架构设计

### 2.1 整体架构

```
┌──────────────────────────────────────────────────────────────┐
│                      入口层 (Input Sources)                    │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐  │
│  │ WebUI       │  │ Desktop      │  │ Channel Voice Msg   │  │
│  │ MediaRecorder│  │ WASAPI       │  │ (Telegram/QQ/etc.)  │  │
│  └──────┬──────┘  └──────┬───────┘  └──────────┬──────────┘  │
│         │                │                      │             │
│         ▼                ▼                      ▼             │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              VoicePipeline (voice/pipeline.py)        │    │
│  │                                                       │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │    │
│  │  │ AudioCapture │  │ StreamSTT    │  │ StreamTTS   │  │    │
│  │  │ (采集/分帧)  │→│ (流式识别)   │  │ (流式合成)  │  │    │
│  │  └──────────────┘  └──────┬───────┘  └──────┬─────┘  │    │
│  │                           │                  │        │    │
│  │                           ▼                  │        │    │
│  │                    ┌──────────────┐          │        │    │
│  │                    │ AgentLoop    │──────────┘        │    │
│  │                    │ (文本→文本)  │                    │    │
│  │                    └──────────────┘                    │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 核心模块

#### VoicePipeline (`OriginAgent/voice/pipeline.py`)

语音处理主流程，管理一个完整语音对话回合：

```python
class VoicePipeline:
    """语音处理管道: 采集 → STT → Agent → TTS → 播放"""

    def __init__(self, stt: StreamSTT, tts: StreamTTS, bus: MessageBus)
    async def process_audio(self, audio_stream: AsyncIterator[bytes]) -> VoiceResult
    async def process_text_response(self, text: str, play_audio: bool) -> Path | None
    async def run_turn(self, audio_input: AudioInput) -> VoiceTurn
```

#### StreamSTT (`OriginAgent/voice/stt.py`)

流式语音识别，对已有 `VolcengineTranscriptionProvider` 做流式封装：

```python
class StreamSTT:
    """流式语音识别基类"""
    async def feed(self, audio_chunk: bytes) -> str | None  # 增量识别文本
    async def flush(self) -> str                             # 获取最终结果
    async def transcribe_file(self, file_path: Path) -> str  # 文件模式（传统）

class VolcengineStreamSTT(StreamSTT):
    """火山引擎流式 ASR - 基于 openspeech WebSocket API"""
    # ws://openspeech.bytedance.com/api/v3/auc/bigmodel/stream
```

#### StreamTTS (`OriginAgent/voice/tts.py`)

流式语音合成，扩展现有 `_speak_text_volcengine`：

```python
class StreamTTS:
    """流式语音合成基类"""
    async def synthesize(self, text: str, voice: str | None = None) -> Path
    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]

class VolcengineStreamTTS(StreamTTS):
    """火山引擎流式 TTS - 基于 openspeech unidirectional API"""
    # https://openspeech.bytedance.com/api/v3/tts/unidirectional
```

### 2.3 VoiceChannel (`OriginAgent/channels/voice.py`)

继承 `BaseChannel`，作为语音场景的统一管道：

```python
class VoiceChannel(BaseChannel):
    """语音通道 - 同时支持语音消息和实时语音"""
    name = "voice"
    display_name = "Voice"

    # 语音消息处理（P1）
    async def handle_voice_message(self, chat_id: str, audio_path: Path) -> None

    # 实时语音会话（P2）
    async def start_voice_session(self, chat_id: str) -> str  # 返回 session_id
    async def feed_audio(self, session_id: str, chunk: bytes) -> None
    async def stop_voice_session(self, session_id: str) -> None
```

### 2.4 WebUI 组件

- **VoiceInputButton**: 录音按钮，支持按住说话 / 点击切换模式
- **VoiceOutputPlayer**: 播放 TTS 音频，支持自动播放
- **VoiceSessionIndicator**: 实时语音会话状态指示

### 2.5 配置扩展

**向后兼容**：新增 `VoicePipelineConfig` 不替代已有 `LocalAwarenessAudioConfig`。
`LocalAwarenessAudioConfig` 继续作为 WebUI 设置面板的数据源（`_settings_voice_payload`），
`VoicePipelineConfig` 面向语音管道运行时行为。两者共享 `tts_voice` 和 `max_record_seconds` 等字段。

```python
class VoicePipelineConfig(Base):
    """语音管道配置"""
    enabled: bool = False
    stt_provider: str = "volcengine"       # volcengine | openai | groq
    tts_provider: str = "volcengine"        # volcengine
    tts_voice: str = "zh_female_santong"    # 火山引擎音色
    tts_sample_rate: int = 24000
    auto_play_response: bool = True         # 自动播放 agent 语音回复
    voice_activity_timeout_ms: int = 1500   # VAD 静音超时
    max_record_seconds: int = 60

class VoiceChannelConfig(Base):
    """语音通道配置"""
    enabled: bool = False
    voice_message_mode: bool = True         # P1: 语音消息
    realtime_mode: bool = False             # P2: 实时语音
    desktop_mode: bool = False              # P3: 桌面助手
```

## 3. 数据流

### 3.1 P1: 语音消息流程

```
用户发送语音文件
    │
    ▼
Channel (Telegram/QQ/WebUI)
    │  InboundMessage(media=[audio_path])
    ▼
VoicePipeline.handle_voice_message(audio_path)
    │
    ├─► StreamSTT.transcribe_file(audio_path) ──► text
    │
    ├─► MessageBus.publish(text) ──► AgentLoop ──► response_text
    │
    ├─► StreamTTS.synthesize(response_text) ──► audio_output_path
    │
    └─► Channel.send(OutboundMessage + media=[audio_output])
```

### 3.2 P2: WebUI 实时语音流程

```
浏览器                                   服务器
  │                                        │
  │── WebSocket: {"type":"voice_start"} ──►│
  │                                        │ VoicePipeline.create_session()
  │◄── {"type":"voice_ready"} ────────────│
  │                                        │
  │── 二进制 audio chunk ─────────────────►│ StreamSTT.feed(chunk)
  │── 二进制 audio chunk ─────────────────►│ StreamSTT.feed(chunk)
  │── ...                                  │   → 增量识别文本累积
  │── {"type":"voice_end"} ───────────────►│ StreamSTT.flush()
  │                                        │   → 最终文本
  │                                        │
  │                                        │ AgentLoop.process(text)
  │                                        │   → DeepSeek 推理
  │                                        │
  │                                        │ StreamTTS.synthesize_stream()
  │◄── 二进制 TTS audio chunk ─────────────│
  │◄── 二进制 TTS audio chunk ─────────────│
  │◄── {"type":"voice_done"} ─────────────│
  │                                        │
  ▼ 浏览器播放                               │
```

### 3.3 P3: 桌面助手流程

```
本地进程 (OriginAgent voice daemon)
    │
    ├─► AudioCapture (WASAPI loopback)
    │    │ 持续采集音频，VAD 检测语音活动
    │    │
    │    ├─► 热键触发 或 唤醒词 "嘿 OriginAgent"
    │    │
    │    ▼
    │    StreamSTT.feed(chunk) ... flush()
    │    │
    │    ▼
    │    AgentLoop (via local WebSocket)
    │    │
    │    ▼
    │    StreamTTS.synthesize() → AudioPlayback
    │
    └─► 持续循环，直到用户结束会话
```

## 4. 文件规划

### 新增文件

| 文件 | 描述 |
|------|------|
| `OriginAgent/voice/__init__.py` | Voice 包入口 |
| `OriginAgent/voice/pipeline.py` | VoicePipeline 主流程 |
| `OriginAgent/voice/stt.py` | StreamSTT 基类 + VolcengineStreamSTT |
| `OriginAgent/voice/tts.py` | StreamTTS 基类 + VolcengineStreamTTS |
| `OriginAgent/voice/audio_capture.py` | 音频采集（WASAPI/webrtcvad） |
| `OriginAgent/voice/audio_playback.py` | 音频播放 |
| `OriginAgent/channels/voice.py` | VoiceChannel |
| `OriginAgent/config/voice_schema.py` | 语音配置 Pydantic 模型 |
| `webui/src/components/voice/VoiceInputButton.tsx` | 录音按钮 |
| `webui/src/components/voice/VoiceOutputPlayer.tsx` | TTS 播放器 |
| `webui/src/components/voice/VoiceSessionIndicator.tsx` | 会话状态指示 |
| `webui/src/hooks/useVoiceRecorder.ts` | 录音 hook |
| `webui/src/hooks/useAudioPlayer.ts` | 播放 hook |

### 修改文件

| 文件 | 改动 |
|------|------|
| `OriginAgent/config/schema.py` | 添加 VoicePipelineConfig, VoiceChannelConfig |
| `OriginAgent/channels/websocket.py` | 添加 WebSocket 语音消息处理、实时语音路由 |
| `OriginAgent/channels/base.py` | 扩展语音消息支持 |
| `OriginAgent/agent/loop.py` | 添加语音回合处理钩子 |
| `webui/src/App.tsx` | 注册语音组件 |
| `webui/src/components/thread/ThreadComposer.tsx` | 集成 VoiceInputButton |

## 5. 错误处理

| 场景 | 处理 |
|------|------|
| STT 失败 | 返回空文本，提示用户重试或打字 |
| TTS 失败 | 仅返回文本回复，不播放音频 |
| 网络超时 | 自动重试（已有 `_MAX_RETRIES=3`），失败后降级 |
| 语音过长 | `max_record_seconds` 截断，已有 `_MAX_AUDIO_BYTES` |
| 并发语音 | 按 session 排队，同一会话串行处理 |
| 无音频输入 | VAD 检测静音，超时后自动结束录音 |

## 6. 测试策略

| 层 | 测试内容 |
|-----|----------|
| 单元测试 | StreamSTT 帧处理、StreamTTS 文本分段、VoicePipeline 状态机 |
| 集成测试 | STT 文件识别（已有 `test_transcription.py`）、TTS 合成验证 |
| E2E 测试 | WebUI 录音→识别→回复→播放 全流程 |
| Mock | STT/TTS provider 使用 mock，避免测试依赖外部 API |

## 7. 安全考量

- 音频文件存储在 workspace 内，受现有安全策略约束
- TTS API Key 从环境变量读取，不写入配置文件
- 桌面助手仅在 localhost 监听，不暴露网络端口
- VAD 音频数据仅在内存中处理，不落盘

## 8. 实施计划

| 阶段 | 内容 | 估算 |
|------|------|------|
| **Phase 1: 语音消息** | VoicePipeline + StreamSTT/TTS 文件模式 + VoiceChannel + 渠道集成 | 核心 |
| **Phase 2: WebUI 实时** | WebSocket 语音流协议 + 前端录音/播放组件 + 流式 STT | 增强 |
| **Phase 3: 桌面助手** | AudioCapture (WASAPI) + 热键/唤醒词 + 后台守护进程 | 扩展 |
