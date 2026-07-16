---
schema_version: 1
---

# TD-2026-007: AudioPlayback 类仅被独立脚本使用,未接入主线

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-16T19:30:00+08:00 |
| 发现人 | Agent 排查 |
| 关联Spec | 无(排查发现) |
| 关联规则 | 规则36(未接入模块处置) |
| 优先级 | P3 |
| 状态 | 待评估 |

## 详细描述
`voice/audio.py` 文件中存在两个主要符号:
- `save_audio_data_url` 函数 — **已接入主线**,被 `channels/websocket.py:1322` 导入使用
- `AudioPlayback` 类 — **未接入主线**,仅被 `voice/desktop_assistant.py` 和 `tests/voice/test_audio.py` 引用

`AudioPlayback` 提供跨平台音频播放(Windows 用 winsound,其他平台 no-op),测试覆盖完整。但它没有被接入 `VoicePipeline` 或 `channels/voice.py` 的 TTS 回放路径,主线 TTS 回放不依赖此类。

## 影响范围
- **影响文件**:`OriginAgent/voice/audio.py`
- **影响功能**:音频回放(仅桌面助手使用)
- **潜在风险**:低。功能正确,有测试。仅是接入范围问题。

## 复现/验证路径
1. 搜索 `AudioPlayback` 的导入 — 仅 `voice/desktop_assistant.py:50` 和 `tests/voice/test_audio.py`
2. 搜索 `VoicePipeline` 是否使用 `AudioPlayback` — 否

## 修复方案(可选)
方案 A:将 `AudioPlayback` 接入 `VoicePipeline` 的 TTS 回放路径(若需要本地音频输出)
方案 B:保持现状,`AudioPlayback` 作为桌面助手专用工具存在
方案 C:若桌面助手被处置(见 TD-2026-006),此类可一并处置

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-16 | Agent 排查 | 待人工裁决,与 TD-2026-006 关联 |
