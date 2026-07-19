# Prompt Cache 命中率与租户注册管道修复 Spec

## Why

本次会话过程中通过 gitnexus + 代码核验暴露出三个相互关联但独立的系统性问题：

1. **缓存命中率接近 0**：DeepSeek 的自动前缀缓存因 `runtime_context` 中 `current_time` 秒级精度被破坏，从第一个字节起即 miss；`_apply_cache_control` 仅对 Anthropic 启用，DeepSeek 完全跳过缓存控制注入分支。这导致每条消息全量 KV cache 重算，输入 token 成本翻倍。
2. **租户注册管道未打通**：`claim_pairing` 方法在 [tenant.py:109-121](file:///d:\Demo\OpenHome\OriginAgentclient\OriginAgent\identity\tenant.py#L109-L121) 实现但无运行时调用方，导致配对批准后的用户卡在 `__pairing_pending__` 状态，无法成为正式 Tenant，进而无法启用 BDI、无法获得独立 workspace、无法享受 PlanLibrary 模板缓存。
3. **owner_id 跨身份污染**（**已修复，作为前置依赖项**）：`working_memory.load()` 的旧逻辑 `if identity is not None and not snapshot.owner_id` 在 `owner_id="cron"` 已存在时不覆盖真实用户身份，导致用户消息以 cron 所有者处理。本修复已落地（[working_memory.py:147-162](file:///d:\Demo\OpenHome\OriginAgentclient\OriginAgent\agent\working_memory.py#L147-L162)）并通过 5 个测试用例验证，作为本次 spec 的前置已完成项。

三个问题在因果链上互相关联：缓存失效使 BDI 启用的边际成本过高；租户管道断裂使用户无法升级到正式 Tenant；owner_id 污染使 cron 与用户会话状态混淆。需要一次性整体规划，分阶段实施。

## What Changes

### Phase 1: Prompt Cache 命中率修复（P0）

- **MODIFIED** `ContextBuilder.build_runtime_context_text`：`current_time` 字段从秒级降为分钟级（截断到 `HH:MM`），用于进入 system prompt 的部分；保留秒级时间字段供工具调用与 BDI deadline 计算使用
- **MODIFIED** `ContextBuilder.build_messages` 与 `ContextAssemblerV2.assemble`：调整 block 顺序，把稳定性梯度从"易变在前"改为"稳定在前、易变在后"，让 DeepSeek 自动前缀缓存命中稳定段
- **MODIFIED** `ProviderSpec` 与 `OpenAICompatProvider._apply_cache_control` 的判定：将 DeepSeek 显式纳入缓存控制分支（虽然 DeepSeek 无需显式标记，但保留接口一致性，并为未来切换 provider 留扩展点）
- **NEW** 在 `event.llm.response` 日志中显式输出 `cached_tokens` 字段，用于观测缓存命中率

### Phase 2: 租户认领管道打通（P1）

- **NEW** 在 `CommandRouter` 或等价的命令分发入口接入 `/pairing claim <tenant_id>` 命令，调用 `TenantRegistry.claim_pairing` 完成绑定
- **NEW** 在 `__pairing_pending__` 状态的会话首次进入时，向用户回复可认领的 tenant 列表与认领命令提示
- **NEW** `claim_pairing` 成功后，触发 BDI 引擎的懒加载（`_init_bdi_engine_for_tenant`），并迁移 `__pairing_pending__` workspace 中的会话数据到目标 tenant 的 workspace
- **NEW** 在 `tenants.yaml` 中允许声明 `claimable_by_pairing: true` 的 tenant，仅这些 tenant 可被认领（防止用户冒充他人）

### Phase 3: 非极客友好的注册辅助（P2，可延后）

- **NEW** CLI 安装向导新增"添加家庭成员"步骤，引导 owner 填写 Telegram sender_id 并自动写入 `tenants.yaml`
- **NEW** `tenants.yaml` 支持运行时增量写入（避免重启服务才能生效）
- **NEW** WebUI 显示 pending 配对请求，支持一键批准+认领

## Impact

### Affected Specs
- `fix-context-assembly-pollution`（Phase 1 的 block 顺序调整与该 spec 有重叠，需交叉核对避免冲突）
- `fix-bdi-core-defects`（Phase 2 的 BDI 懒加载触发与该 spec 相关）
- `fix-cron-session-key-routing`（owner_id 修复已部分解决该 spec 的会话状态污染问题）
- `restore-and-wire-design-modules`（Phase 2 的 claim_pairing 接入与该 spec 的"模块接入"主题一致）

### Affected Code
- `OriginAgent/agent/context.py`（runtime_context 时间精度与 block 顺序）
- `OriginAgent/agent/context_assembler_v2.py`（block 拼装顺序）
- `OriginAgent/providers/openai_compat_provider.py`（缓存控制分支判定）
- `OriginAgent/providers/registry.py`（DeepSeek spec 的 supports_prompt_caching 标记）
- `OriginAgent/identity/tenant.py`（claim_pairing 已存在，需补接入点）
- `OriginAgent/identity/resolver.py`（`__pairing_pending__` 状态的首次提示）
- `OriginAgent/agent/agent_host.py`（claim_pairing 后触发 BDI 懒加载）
- `OriginAgent/cli/commands.py` 或 `CommandRouter`（接入 `/pairing claim` 子命令）
- `OriginAgent/pairing/store.py`（可能需要扩展返回 tenant_id 等信息）
- `tests/agent/test_context_assembly.py`（新增缓存友好性测试）
- `tests/identity/test_resolver.py`（新增 claim 命令测试）

## ADDED Requirements

### Requirement: Prompt Cache 命中率可观测
系统 SHALL 在每次 LLM 调用的响应日志中输出归一化的 `cached_tokens` 字段，便于运维监控缓存命中率趋势。

#### Scenario: 缓存命中可观测
- **WHEN** 系统调用 DeepSeek 或 Anthropic 等 LLM 并收到响应
- **THEN** `event.llm.response` 日志包含 `cached_tokens` 字段（0 表示未命中，>0 表示命中字数）
- **AND** 该字段经过 provider 归一化（`prompt_tokens_details.cached_tokens` / `cached_tokens` / `prompt_cache_hit_tokens` 三种格式统一）

### Requirement: runtime_context 时间精度分层
系统 SHALL 在 system prompt 中只暴露分钟级时间精度，而工具调用与 BDI 内部决策仍可使用秒级精度。

#### Scenario: 同分钟内多轮对话缓存命中
- **WHEN** 用户在同一分钟内发送两条消息
- **THEN** 两条消息的 system prompt 第一个 block（runtime_context）的 `current_time` 字段逐字节相同
- **AND** DeepSeek 自动前缀缓存命中第二个 block 起的所有稳定内容

#### Scenario: 工具调用仍可获取秒级时间
- **WHEN** cron 工具需要精确到秒的触发时间
- **THEN** 工具实现通过 `datetime.now()` 获取系统时间，不依赖 prompt 中的 `current_time`
- **AND** BDI deliberation 内部 deadline 判定使用独立时钟，不依赖 prompt 时间

### Requirement: Block 顺序按稳定性梯度排列
系统 SHALL 按"稳定在前、易变在后"的梯度排列 system prompt 与 user content blocks，最大化前缀缓存的稳定段长度。

#### Scenario: 稳定段前置
- **WHEN** ContextAssemblerV2 拼装上下文 block
- **THEN** 顺序为：static_system_instructions → capability_boundaries → always_skills → user_profile → archived_session_summary → closed_episode_summaries → prewarm_seed → working_memory → world_state → continuity → recovered_continuity → recent_history → memory_retrieval → session_search → runtime_context → internal_event → user_text
- **AND** runtime_context 不再位于第一个 block

### Requirement: 租户认领命令
系统 SHALL 在 `__pairing_pending__` 状态下接受 `/pairing claim <tenant_id>` 命令，完成 sender 到 tenant 的绑定。

#### Scenario: 配对批准后认领成功
- **WHEN** 一个已通过 `pairing approve` 的 sender 发送 `/pairing claim dad`
- **AND** tenant_id="dad" 在 `tenants.yaml` 中声明了 `claimable_by_pairing: true`
- **THEN** `TenantRegistry.claim_pairing(channel, sender_id, "dad")` 被调用
- **AND** 该 sender 的 `__pairing_pending__` workspace 数据迁移到 `tenant:dad` 的 workspace
- **AND** 下次该 sender 发消息时，`_init_bdi_engine_for_tenant` 被触发
- **AND** 系统回复"认领成功，欢迎爸爸"

#### Scenario: 不可认领的 tenant
- **WHEN** sender 发送 `/pairing claim admin`
- **AND** tenant_id="admin" 未声明 `claimable_by_pairing: true`
- **THEN** 系统拒绝认领，回复"该身份不可通过配对认领"
- **AND** 不修改任何状态

#### Scenario: 重复认领幂等
- **WHEN** 已绑定的 sender 再次发送 `/pairing claim <其他 tenant_id>`
- **THEN** 系统拒绝，回复"你已绑定为 X，如需切换请联系 owner"
- **AND** 不修改状态

### Requirement: `__pairing_pending__` 状态首次进入提示
系统 SHALL 在 sender 首次进入 `__pairing_pending__` 状态时，主动回复可认领的 tenant 列表与认领命令格式。

#### Scenario: 首次进入提示
- **WHEN** 一个 sender 通过 `pairing approve` 但尚未 claim
- **AND** 该 sender 发送任意消息
- **THEN** 系统回复包含"可认领身份"列表（仅 `claimable_by_pairing: true` 的 tenant）
- **AND** 提示格式为 `/pairing claim <tenant_id>`

## MODIFIED Requirements

### Requirement: ProviderSpec supports_prompt_caching
原 `supports_prompt_caching` 仅用于 OpenRouter 与 Anthropic 路径判定。修改后，该字段同时作为 DeepSeek 等"自动前缀缓存型 provider"的标记，触发缓存友好性提示日志（而非注入 cache_control，因 DeepSeek 不需要显式标记）。

#### Scenario: DeepSeek 启用缓存可观测
- **WHEN** ProviderSpec.name == "deepseek" 且 supports_prompt_caching=True
- **THEN** 不注入 `cache_control` 标记（DeepSeek 自动命中）
- **AND** 在 `event.llm.response` 中输出 `cached_tokens` 字段
- **AND** 缓存命中率为 0 时输出 WARN 日志提示运维检查 prompt 前缀稳定性

## REMOVED Requirements

### Requirement: runtime_context 必须位于 system prompt 第一位
**Reason**: 该设计破坏 DeepSeek 自动前缀缓存，导致每条消息全量重算 KV cache。原设计意图（让 LLM 第一时间知道时间、actor、scope）可通过 block 顺序后移但仍保留在 system prompt 内实现，不损失功能。
**Migration**: runtime_context 移至 system prompt 末尾附近，仍在 user content 之前；标签化结构（`<runtime_context>...</runtime_context>`）保持不变，LLM 仍可读取。
