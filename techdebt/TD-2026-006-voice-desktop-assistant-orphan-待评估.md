---
schema_version: 1
---

# TD-2026-006: DesktopVoiceAssistant 与 AudioCapture 完整实现但未接入主线

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-16T19:30:00+08:00 |
| 发现人 | Agent 排查 |
| 关联Spec | 无(排查发现) |
| 关联规则 | 规则36(未接入模块处置) |
| 优先级 | P2 |
| 状态 | 待评估 |

## 详细描述
`voice/desktop_assistant.py` 的 `DesktopVoiceAssistant` 类是一个完整的桌面语音助手客户端(WebSocket 连接本地 gateway + push-to-talk + STT + TTS),`voice/capture.py` 的 `AudioCapture` 类提供带 VAD 的麦克风采集。两者均为完整实现,但:

- 主 CLI(`cli/commands.py`)**未注册**任何 voice/desktop 命令
- `voice/__init__.py` **未导出** `DesktopVoiceAssistant`、`AudioCapture`
- 仅有 `if __name__ == "__main__"` 独立入口(`python -m OriginAgent.voice.desktop_assistant`)
- **无任何测试覆盖**(`tests/voice/` 下无 test_capture/test_desktop*)

## 影响范围
- **影响文件**:`OriginAgent/voice/desktop_assistant.py`、`OriginAgent/voice/capture.py`
- **影响功能**:桌面语音助手客户端
- **潜在风险**:代码存在但无人维护,可能随主线 API 演进而失效;无测试保护,无法发现回归

## 复现/验证路径
1. 搜索 `cli/commands.py` 中是否有 voice/desktop 命令 — 当前:无
2. 搜索 `from OriginAgent.voice.desktop_assistant import` 的生产代码引用 — 仅自身 `__main__` 块
3. 运行 `python -m OriginAgent.voice.desktop_assistant` — 可独立运行,但不属于主线入口

## 修复方案(可选)
方案 A:在 `cli/commands.py` 添加 `voice` 子命令,调用 `DesktopVoiceAssistant.run()`,并补测试
方案 B:明确标注为"独立工具,非主线一部分",在文件头 docstring 中声明,可选移至 `scripts/` 目录
方案 C:删除(若已废弃)——需用户明确确认,按规则 36.3 处置

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-16 | Agent 排查 | 待人工裁决:接入主线 / 标注为独立工具 / 删除 |
