---
schema_version: 1
---

# TD-2026-008: 多租户路径 InnerMonologueEngine 未接入

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-16T19:30:00+08:00 |
| 发现人 | Agent 排查 |
| 关联Spec | 无(排查发现) |
| 关联规则 | 规则7(状态变化审计)、规则16(领域模型与适配器隔离) |
| 优先级 | P2 |
| 状态 | 待评估 |

## 详细描述
`agent/agent_host.py` 中存在两条 BDI 引擎初始化路径:
- **单租户路径**(`_init_bdi_engine`):实例化 `InnerMonologueEngine` 并注册 `set_on_cycle_complete` 回调 — 已完整接入
- **多租户路径**(`_init_bdi_engine_for_tenant`):**未实例化** `InnerMonologueEngine`

代码注释(`agent_host.py:113-131`)明确记录了 3 个差距:
1. InnerMonologueEngine 未接入多租户路径
2. config 透传缺失
3. CronDesireBridge 存储位置不正确

注释中列出了迁移条件:"一旦多租户路径补齐这 3 个差距并有集成测试覆盖,legacy 路径可移除"。

## 影响范围
- **影响文件**:`OriginAgent/agent/agent_host.py:113-131` 及 `_init_bdi_engine_for_tenant` 方法
- **影响功能**:多租户场景下的内部独白引擎
- **潜在风险**:若多租户被启用,内部独白功能缺失,可能导致 BDI 循环行为不一致

## 复现/验证路径
1. 读取 `agent_host.py:113-131` 的注释,确认 3 个差距
2. 对比 `_init_bdi_engine` 与 `_init_bdi_engine_for_tenant` 的实现,确认后者缺少 IME 实例化
3. 搜索 `InnerMonologueEngine` 的实例化点 — 仅单租户路径

## 修复方案(可选)
1. 在 `_init_bdi_engine_for_tenant` 中实例化 `InnerMonologueEngine`,注册 `set_on_cycle_complete` 回调
2. 透传所需 config
3. 修正 `CronDesireBridge` 存储位置
4. 补多租户路径的集成测试
5. 迁移条件满足后移除 legacy 单租户路径

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-16 | Agent 排查 | 待人工确认:多租户是否近期启用?若否,缺口可接受 |
