---
schema_version: 1
---

# TD-2026-005: RobotG1Config schema 字段无任何实现读取

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-16T19:30:00+08:00 |
| 发现人 | Agent 排查 |
| 关联Spec | 无(排查发现) |
| 关联规则 | 规则38(业务全景先行与系统认知持久化) |
| 优先级 | P2 |
| 状态 | 待评估 |

## 详细描述
`RobotG1Config`(`config/schema.py:341-365`)定义了 5 个字段(`enabled`/`mcp_endpoint`/`tool_timeout_seconds`/`perception_enabled`/`perception_mode`),但全代码库搜索 `robot_g1.enabled`、`defaults.robot_g1`、`robot_g1.perception` 等**零匹配**——没有任何代码读取这些字段。

这是规则 38.1 所指"残缺版图"的典型案例:schema 字段已定义但代码中完全没有对应的实现逻辑。`domain_packs/robot/CAPABILITIES.md` 明确写 "P5B+ G1 Integration Placeholders"、"future wiring points, not active Phase 5A behavior",说明这是分阶段占位,但 schema 字段的存在会误导用户以为配置这些字段会产生效果。

## 影响范围
- **影响文件**:`OriginAgent/config/schema.py:341-365`、`OriginAgent/domain_packs/robot/`
- **影响功能**:Unitree G1 机器人集成(未实现)
- **潜在风险**:用户配置 `robot_g1.enabled = true` 后无任何效果且无告警,违反规则 14(关键假设断言化);schema 字段与实现不同步,违反规则 21

## 复现/验证路径
1. 在 config 中设置 `agents.defaults.robot_g1.enabled = true`
2. 启动 agent,观察是否有任何 robot_g1 相关行为(当前:无)
3. 全代码库搜索 `robot_g1` — 仅 schema 定义处匹配,无读取点

## 修复方案(可选)
方案 A:保留 schema 字段,但在 config 校验(`config/doctor.py`)中对 `robot_g1.enabled = true` 发出 warning,提示"此功能为 P5B+ 占位,当前未实现"
方案 B:移除 schema 字段,直到 P5B+ 真正实现时再添加(规则 32 最小化实现)
方案 C:保持现状,但在 schema 字段 docstring 中明确标注"未实现,仅占位"

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-16 | Agent 排查 | 待人工裁决:保留占位 / 移除 / 加 warning |
