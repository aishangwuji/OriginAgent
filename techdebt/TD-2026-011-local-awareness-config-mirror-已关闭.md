---
schema_version: 1
---

# TD-2026-011: LocalAwareness 配置镜像潜在违反单一数据源

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-16T19:30:00+08:00 |
| 发现人 | Agent 排查 |
| 关联Spec | 无(排查发现) |
| 关联规则 | 规则6(单一数据源,禁止部分遵守) |
| 优先级 | P3 |
| 状态 | 已关闭 |

## 详细描述
`agent/local_awareness.py:344-354` 的 `LocalAwarenessSummary` 中存在一组与 `ToolsConfig.LocalAwarenessConfig`(`config/schema.py:1898-2020`)字段同名的 bool 字段:
- `camera_enabled` / `screen_enabled` / `audio_input_enabled` / `audio_output_enabled` / `transcription_enabled` / `tts_enabled` / `media_enabled` 等

这构成规则 6"单一数据源"的潜在违规:同一组配置语义在两处独立维护,可能出现 drift(例如 schema 默认值变更后,`LocalAwarenessSummary` 的镜像未同步)。

需要人工复核:这两条路径是否会出现 drift?`LocalAwarenessSummary` 的字段是从 schema 实时派生(可接受),还是独立硬编码(违规)?

## 影响范围
- **影响文件**:`OriginAgent/agent/local_awareness.py:344-354`、`OriginAgent/config/schema.py:1898-2020`
- **影响功能**:本地感知配置
- **潜在风险**:若为独立硬编码,配置默认值变更后两处不同步,导致行为与配置不一致

## 复现/验证路径
1. 读取 `local_awareness.py:344-354` 的 `LocalAwarenessSummary` 字段定义
2. 对比 `schema.py:1898-2020` 的 `LocalAwarenessConfig` 字段默认值
3. 检查 `LocalAwarenessSummary` 字段是否从 `LocalAwarenessConfig` 派生,还是独立硬编码
4. 若为独立硬编码,修改 schema 默认值后观察 `LocalAwarenessSummary` 是否同步(预期:不同步)

## 修复方案(可选)
方案 A:若为独立硬编码,改为从 `LocalAwarenessConfig` 派生(单一数据源)
方案 B:若已为派生关系,在代码注释中明确说明数据源关系,消除歧义
方案 C:若两者语义不同(如 summary 是运行时状态,config 是配置),重命名以区分语义

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-16 | Agent 排查 | 待人工复核:是否为独立硬编码?若是则违反规则 6 |
| 2026-07-16 | Agent 核查 | 关闭:部分属实但不违规。字段同名属实,但构造点(local_awareness.py:1338-1365)全部从 LocalAwarenessConfig 派生,dataclass 默认值运行时不生效。已在 LocalAwarenessSummary 类添加 docstring 澄清派生关系(方案 B)。 |
