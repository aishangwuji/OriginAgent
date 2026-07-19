# Checklist

## Phase 0: 前置已完成项
- [x] working_memory.py load() owner_id 覆盖逻辑修复已落地
- [x] 5 个 owner_id 相关测试用例全部通过
- [x] message.py 路径 A 发送日志已补充
- [x] agent_runtime.py 路径 B 抑制日志已补充
- [x] commands.py cron Path B delivery 日志已补充
- [x] 既有测试无新增回归（仅 2 个既存 Windows-specific 失败）

## Phase 1: Prompt Cache 命中率修复
- [x] `current_time` 在 system prompt 中为分钟级精度（HH:MM）
- [x] 秒级字段不进入 system prompt（改为 `time_precision` 注释字段）
- [x] cron 工具实现不依赖 prompt 中的时间（通过 `datetime.now()` 获取，已核验）
- [x] BDI deliberation 不依赖 prompt 中的时间（通过内部时钟获取，已核验）
- [x] block 顺序按稳定性梯度排列（稳定在前、易变在后）
- [x] runtime_context 不再位于 system prompt 第一个 block
- [x] runtime_context 仍在 system prompt 内（LLM 仍可读取 actor/scope/trigger）
- [x] DeepSeek ProviderSpec 显式设置 `supports_prompt_caching=True`（registry.py L288-302）
- [x] DeepSeek 路径不注入 `cache_control` 标记（避免无意义字段）
- [x] `event.llm.response` 日志输出归一化 `cached_tokens` 字段（runner.py L868-882）
- [x] 缓存命中率为 0 时输出 WARN 日志提示运维检查（5 分钟去重）
- [x] 端到端测试：同分钟内对话 cached_tokens 一致性已验证（字节级断言）
- [x] 端到端测试：跨分钟对话，稳定段仍部分命中（前缀字节级一致）
- [x] 回归测试：`tests/agent/test_context_assembly.py` 5 个全部通过
- [x] 回归测试：`tests/providers/test_cached_tokens.py` 14 个全部通过
- [x] gitnexus impact 分析由于环境限制不可用，改用静态分析确认爆炸半径 LOW

## Phase 2: 租户认领管道打通
- [x] `TenantConfig` 新增 `claimable_by_pairing: bool = False` 字段（schema.py:1647）
- [x] `Tenant` dataclass 新增对应字段（tenant.py:26-27）
- [x] `TenantRegistry._register_from_config` 传递该字段（tenant.py:63）
- [x] `pairing/store.py` 的 `handle_pairing_command` 支持 `claim` 子命令（签名扩展为 keyword-only 参数）
- [x] `/pairing claim <tenant_id>` 校验 tenant 存在且 claimable
- [x] `/pairing claim <tenant_id>` 校验 sender 已通过 pairing approve
- [x] `/pairing claim <tenant_id>` 校验 sender 未已绑定（幂等性，幂等检查先于授权检查）
- [x] claim 成功后通过 `on_claim_success` callback 触发 `_init_bdi_engine_for_tenant`
- [x] claim 成功后迁移 `__pairing_pending__` workspace 的 session 文件到目标 tenant workspace
- [x] 不迁移 DesireStore/CronObservationStore（`__pairing_pending__` tenant `bdi_enabled=False`，无此数据）
- [x] 通过 callback 解耦 pairing 层与 agent 层（规则16 领域隔离）
- [x] `__pairing_pending__` 状态首次发消息时回复可认领 tenant 列表（loop.py `_maybe_show_claim_hint`）
- [x] 同一 sender 只提示一次（pairing.json 新增 `hint_shown` 字段持久化去重）
- [x] 测试：合法认领成功
- [x] 测试：未授权 tenant（claimable_by_pairing=False）拒绝
- [x] 测试：未批准配对的 sender 拒绝
- [x] 测试：已绑定 sender 重复 claim 拒绝
- [x] 测试：claim 后 BDI 引擎已初始化
- [x] 测试：claim 后 workspace 数据已迁移
- [x] 测试：同一 sender 只提示一次
- [x] 测试：bus 投递失败时不记录 dedup（下次重试）
- [x] 测试：已 claim 的 sender 不再解析为 `__pairing_pending__`（resolver 路径）
- [x] 回归测试：`tests/identity/test_resolver.py` 19 个全部通过
- [x] 回归测试：`tests/integration/test_multi_tenant.py` 通过
- [x] 回归测试：`tests/pairing/` 31 个全部通过（11 claim_command + 10 claim_migration + 9 claim_hint + 1 既有）

## Phase 3: 非极客友好注册辅助（已延后）
- [x] 已登记技术债 TD-2026-020-phase3-non-geek-onboarding-deferred-待评估.md
- [ ] CLI 安装向导新增"添加家庭成员"步骤（延后）
- [ ] TenantRegistry 支持运行时添加 Tenant（延后）
- [ ] WebUI 显示 pending 配对请求列表（延后）

## 合规性检查（规则27）
- [x] 红线规则确认：规则3（边界校验）— `/pairing claim` 入口校验 tenant_id 存在性与 claimable 标志
- [x] 红线规则确认：规则9（session token 隔离）— 未涉及异步资源 open/close 成对操作
- [x] 红线规则确认：规则12（幂等）— claim 命令已设计幂等性，已绑定 sender 再次 claim 被拒绝
- [x] 红线规则确认：规则14（关键假设断言化）— 缓存命中率假设已落地为端到端测试，幂等性假设已通过测试 `test_bdi_already_initialized_is_noop` 锁定
- [x] 红线规则确认：规则18（安全边界）— claim 限制为 claimable_by_pairing=True 的 tenant；提示消息不泄露非 claimable tenant 信息
- [x] 高风险推演：规则5（缓存值生命周期）— runtime_context 时间精度修改直接影响 prompt 缓存，已通过端到端测试验证
- [x] 高风险推演：规则7（状态审计）— claim_pairing 修改 TenantRegistry 状态，写入路径单一，已验证幂等性
- [x] 高风险推演：规则8（异步时序）— BDI 懒加载与 claim 之间无时序竞争，callback 同步调用
- [x] 高风险推演：规则16（领域隔离）— pairing 层通过 callback 调用 agent 层，不直接依赖 agent_host
- [x] 改动范围合规性：所有 diff 可追溯到具体需求点（spec.md 的 What Changes 章节）
- [x] 技术债声明：Phase 3 延后项已登记 TD-2026-020-phase3-non-geek-onboarding-deferred-待评估.md
- [x] 系统认知同步：已更新 systemmap/domain-overview.md 新增第 12 节"身份与安全"扩展与第 13 节"Prompt Cache 策略"
- [x] 独立核验要求（规则31）：本次变更触及红线闭集（规则3/12/18），合并前需由独立于本会话的核验方核对清单中至少一项声称的代码行号
