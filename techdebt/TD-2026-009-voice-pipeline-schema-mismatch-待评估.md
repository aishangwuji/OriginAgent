---
schema_version: 1
---

# TD-2026-009: VoicePipeline 启动条件未读取 schema 字段 tools.voice.enabled

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-16T19:30:00+08:00 |
| 发现人 | Agent 排查 |
| 关联Spec | 无(排查发现) |
| 关联规则 | 规则6(单一数据源)、规则17(禁止硬编码) |
| 优先级 | P3 |
| 状态 | 待评估 |

## 详细描述
`config/schema.py:1935` 定义了 `VoicePipelineConfig.enabled` 字段(默认 `False`),但 `channels/manager.py:123-136` 中 `VoicePipeline` 的初始化条件**未直接读取 `tools.voice.enabled`**,而是基于 audio 配置触发。

这意味着 `tools.voice.enabled` 字段在代码中**未见直接读取**——用户在配置中设置 `tools.voice.enabled = true` 可能不会产生预期效果,实际启动取决于 audio 子配置。

## 影响范围
- **影响文件**:`OriginAgent/config/schema.py:1935`、`OriginAgent/channels/manager.py:123-136`
- **影响功能**:VoicePipeline 启动逻辑
- **潜在风险**:用户配置 `tools.voice.enabled = true` 后行为与预期不符;schema 字段冗余可能导致维护混乱

## 复现/验证路径
1. 搜索 `tools.voice.enabled` 或 `voice_cfg.enabled` 的读取点
2. 对比 `channels/manager.py:123-136` 的实际启动条件
3. 在配置中设置 `tools.voice.enabled = true` 但不配置 audio,观察 VoicePipeline 是否启动

## 修复方案(可选)
方案 A:统一启动条件为读取 `tools.voice.enabled`,audio 子配置作为细节参数
方案 B:移除 `tools.voice.enabled` 字段(若实际不需要),以 audio 配置为唯一启动源
方案 C:在 config 校验中检查 `tools.voice.enabled` 与 audio 配置的一致性,不一致时 warning

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-16 | Agent 排查 | 待人工复核:实际启动逻辑与 schema 字段的关系 |
