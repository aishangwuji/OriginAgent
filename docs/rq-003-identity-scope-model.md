# RQ-003 任务包：身份层与作用域模型设计

Date: 2026-06-09
Status: Proposed
Scope: Phase 1 identity 与 scope 最小模型

## title

`RQ-003` 身份层与作用域模型设计

## goal

在当前运行时基础上补齐最小 identity / scope 骨架，使上下文项能够回答两个问题：

1. 这条信息属于谁。
2. 这条信息应该在哪个作用域内可见。

## scope

本任务包覆盖以下内容：

1. 在现有 `ActorResolver` 之上定义最小 identity 表达：
   - `user_id`
   - `session_id`
   - `device_id`
   - 可选保留 `actor_id`
2. 定义 Phase 1 最小 scope 集合：
   - `user`
   - `session`
   - `task`
3. 定义 identity 解析与 scope 决策的接口边界：
   - `IdentityResolver` 或等价适配层
   - `ScopeResolver` 或等价过滤层
4. 定义默认作用域规则：
   - 工作记忆默认 `session` 或 `task`
   - 用户偏好默认 `user`
   - 临时任务残留默认 `task`
5. 定义传播安全规则：
   - 默认禁止自动向更宽作用域传播
   - 仅允许显式 `allow_propagation` 的对象执行提升
6. 定义 Phase 1 的过滤语义：
   - 上下文组装时按 identity + scope 可见性过滤
   - 检索结果若缺少 scope 元信息，则默认保守处理

## non_goals

本任务包不覆盖以下内容：

1. `household`、`global`、`room` 等更大 scope 的完整治理。
2. 跨端漫游预热。
3. 长期记忆晋升策略。
4. 复杂 ACL、权限继承图、隐私策略引擎。
5. 感知设备身份注册中心。

## dependencies

1. [`docs/rq-002-continuity-memory-os-boundary.md`](./rq-002-continuity-memory-os-boundary.md)
2. [`docs/continuity_memory_phase1_plan.md`](./continuity_memory_phase1_plan.md)
3. 当前运行时的 [OriginAgent/agent/identity.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/identity.py)

## files_or_modules

本任务包后续实现预计主要触达：

1. [OriginAgent/agent/identity.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/identity.py)
2. [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py)
3. [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
4. [OriginAgent/session/manager.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/session/manager.py)
5. [OriginAgent/agent/facts.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/facts.py)
6. [OriginAgent/session/search_index.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/session/search_index.py)
7. 候选新模块：`OriginAgent/agent/scope.py`
8. 测试目录：
   - `tests/agent/`
   - `tests/session/`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. 当前回合运行时可以稳定产出最小 identity 描述对象。
2. `user`、`session`、`task` 三种 scope 的语义和默认使用场景已固定。
3. 上下文项、事实项或工作记忆项至少可以附带 scope 元信息。
4. 组装上下文前可以按 scope 做可见性过滤。
5. 默认禁止 `session -> user` 自动提升的规则已明确落地。
6. 对缺少 scope 元数据的旧对象，已定义保守回退策略。

## tests

后续实现至少应覆盖以下测试：

1. identity 解析测试：
   - 同一用户跨多个 session 时 `user_id` 稳定。
   - 不同会话可区分 `session_id`。
2. scope 过滤测试：
   - `session` 级对象不会出现在另一个 session 的上下文中。
   - `task` 级对象不会默认泄漏到 session 全局工作记忆。
3. 传播规则测试：
   - 未声明 `allow_propagation` 的对象不会自动提升到 `user`。
   - 显式允许传播的对象可按规则晋升。
4. 兼容性测试：
   - 现有 `ActorResolver` 路径在未启用新能力时仍可工作。

## rollback_plan

若实现引入过多耦合或兼容问题，回滚方式应为：

1. 保留 `ActorResolver` 现有输出契约。
2. 通过 feature flag 或适配层关闭新 scope 过滤逻辑。
3. 保留 identity/scope 元数据字段，但允许旧运行路径忽略它们。
4. 先回退过滤逻辑，不回退已冻结的文档术语。

## open_questions

1. `device_id` 在没有真实设备接入的通道里，Phase 1 应如何生成或留空。
2. `session_id` 是否直接复用 `session_key`，还是单独引入内部标识。
3. `FactStore` 现有 `scope` 字段是否足以承载 Phase 1 scope 语义，还是需要并行字段。
4. 缺少 identity 的系统事件默认归属于 `system` 还是挂到当前 session。
