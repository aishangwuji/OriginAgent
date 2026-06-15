# RQ-007 任务包：视觉快照采集与本地留存策略

Date: 2026-06-12
Status: Completed
Scope: Phase 2 `P2A` path-first snapshot ingress 与本地留存边界冻结

## title

`RQ-007` 视觉快照采集与本地留存策略

## goal

把现有 provider-neutral attachments ingress 与当前 `P2A` 文件快照原型收敛成统一的 snapshot ingress 规则，使 OriginAgent 可以稳定地把本地文件路径、sidecar metadata 与运行时 identity / scope 绑定为可查询的 `SceneSnapshot` 记录。

本任务包完成后，系统应能稳定回答：

1. 哪些目录、命名和 sidecar 约定构成 `P2A` snapshot ingress 的合法入口。
2. 文件什么时候算“已完成可消费”，什么时候还只是 producer 临时产物。
3. 最小身份归属、scope 和 provenance 如何从 producer metadata 注入 snapshot。
4. snapshot 留存、引用和清理边界在哪里。

## scope

本任务包覆盖以下内容：

1. 冻结 `workspace/uploads/<source>/` 与 `workspace/inbox/<source>/` 的 snapshot ingress 角色边界。
2. 定义 `path-first` 到 `SceneSnapshot` 的最小构建路径：
   - workspace-visible media path
   - optional sidecar metadata
   - runtime identity / scope attribution
   - snapshot materialization
3. 定义 producer 完成写入的最小规则：
   - `.part` 临时文件
   - 原子 rename
   - sidecar 与媒体文件同 basename
4. 定义最小 sidecar metadata 约定：
   - `captured_at`
   - `summary`
   - `objects`
   - `relationships`
   - `uncertainties`
   - `confidence`
   - `source`
   - `scope`
   - `owner_id`
   - `device_id`
   - `provenance`
5. 定义 snapshot 引用与留存边界：
   - prompt 中只注入摘要，不注入原始媒体明细
   - snapshot 必须能回溯到原始路径或等价证据
   - 清理策略应晚于 prompt 消费与 inspection 路径
6. 定义 `P2A` 阶段允许支持的最小来源范围：
   - `uploads/`
   - `inbox/`
   - 已存在的 user turn media path

## non_goals

本任务包不覆盖以下内容：

1. 目录 watcher、文件轮询或长期驻留 ingress 进程实现。
2. 真正的摄像头驱动接入。
3. 音频流、视频流或高频传感器流接入。
4. 大规模文件生命周期治理系统。
5. 分布式对象存储或远程 blob 存储设计。
6. 深查本身的推理逻辑。

## dependencies

1. [`docs/attachments-ingress.md`](./attachments-ingress.md)
2. [`docs/continuity_memory_phase2_plan.md`](./continuity_memory_phase2_plan.md)
3. [`docs/rq-006-perception-layered-data-models.md`](./rq-006-perception-layered-data-models.md)
4. 当前 `P2A` 原型实现：
   - [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py)
   - [OriginAgent/utils/attachments.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/utils/attachments.py)
   - [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)

## files_or_modules

本任务包预期主要触达文档层：

1. [`docs/attachments-ingress.md`](./attachments-ingress.md)
2. [`docs/continuity_memory_phase2_plan.md`](./continuity_memory_phase2_plan.md)
3. [`docs/rq-007-visual-snapshot-ingress-retention.md`](./rq-007-visual-snapshot-ingress-retention.md)

后续实现预计主要影响：

1. [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py)
2. [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
3. [OriginAgent/utils/attachments.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/utils/attachments.py)
4. 测试目录：
   - `tests/agent/`
   - `tests/tools/`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. `P2A` snapshot ingress 的目录约定、完成写入规则和 sidecar 规则已冻结。
2. 已明确从 local media path 到 `SceneSnapshot` 的最小构建链路。
3. 已明确最小身份归属、scope 与 provenance 如何注入 snapshot。
4. 已明确 snapshot 与原始媒体路径之间的可追溯关系必须保留。
5. 已明确当前阶段只做 path-first ingress，不把它误写成真实感知设备接入。
6. `RQ-008`、`RQ-009` 和 `RQ-010` 可直接基于本任务包继续定义工具、读模型与主线桥接规则。

## tests

本任务包为文档冻结任务，不要求立即新增自动化测试。

完成时应形成的后续测试约束包括：

1. ingress path 合法性测试：
   - `uploads/`
   - `inbox/`
   - sidecar basename 匹配
2. sidecar metadata 读取测试：
   - 缺省字段回退
   - confidence 范围
   - provenance 合并
3. snapshot 构建测试：
   - 原始路径可回溯
   - `owner_id` / `device_id` / `scope` 注入正确
4. 重复 ingest 去重测试。

## rollback_plan

若本任务包结论被证明不适合后续真实 ingress，回滚方式应为：

1. 保留 `path-first` 合同作为兼容入口。
2. 在新版本中显式记录新 ingress 方式与当前目录规则的兼容关系。
3. 不允许在未更新文档前，让 producer 私自演化出新的 sidecar 或目录语义。

## open_questions

1. `uploads/` 与 `inbox/` 是否需要在后续阶段区分不同保留周期。
2. sidecar 是否应在后续阶段强制声明 `producer` / `ingest_method`，还是继续允许运行时回退。
3. `P2A` 是否需要尽早为目录级 quota 与清理策略预留字段，但先不实现。
