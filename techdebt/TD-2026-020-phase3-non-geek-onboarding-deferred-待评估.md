---
schema_version: 1
---

# TD-2026-020: Phase 3 非极客友好注册辅助未实现

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-18T22:30:00+08:00 |
| 发现人 | assistant（spec fix-prompt-cache-and-tenant-onboarding Phase 3 延后） |
| 关联Spec | .trae/specs/fix-prompt-cache-and-tenant-onboarding/spec.md |
| 关联规则 | 规则38（业务全景先行——用户注册管道的完整版图未覆盖） |
| 优先级 | P3 |
| 状态 | 待评估 |

## 详细描述
Phase 2 已打通 claim_pairing 管道（`/pairing claim <tenant_id>` 命令 + BDI 懒加载 + workspace 迁移 + 首次进入提示），但完整 onboarding 体验仍未覆盖：

1. **CLI 安装向导无"添加家庭成员"步骤**：当前 `tenants.yaml` 必须手工编辑，非极客用户无法完成租户注册。安装流程（`cli/setup` 系列）只配 API key、provider，不碰 tenant 配置。
2. **tenants.yaml 无运行时增量写入**：`TenantRegistry._register_from_config` 只在启动时加载，运行时新增 Tenant 需要重启服务。
3. **WebUI 无 pending 配对管理界面**：用户必须通过 DM 手敲 `/pairing approve <code>` 和 `/pairing claim <tenant_id>`，无图形化操作。

## 影响范围
- **影响文件**：
  - `OriginAgent/cli/commands.py` 或 `OriginAgent/cli/setup*.py`（安装向导）
  - `OriginAgent/identity/tenant.py`（`TenantRegistry` 支持运行时添加 Tenant）
  - 前端 WebUI 相关文件（待识别）
- **影响功能**：非极客用户的首次 onboarding 体验；运行时租户管理
- **潜在风险**：当前用户必须编辑 YAML 才能注册，限制了系统普及度。无安全风险（pairing approve 流程已有）。

## 复现/验证路径
1. 启动新安装的 OriginAgent
2. 尝试不编辑 `tenants.yaml`，仅通过 Telegram 发消息注册为正式 Tenant
3. 观察：目前只能停留在 guest 或 `__pairing_pending__` 状态，无法通过 UI/向导完成正式 Tenant 注册

## 修复方案（可选）
### 方案 A：CLI 安装向导扩展
- 在 `cli/setup` 中新增"添加家庭成员"步骤
- 引导填写 display_name、channel、sender_id
- 自动写入 `tenants.yaml`，默认 `claimable_by_pairing=false`（owner 显式绑定）或 `claimable_by_pairing=true`（允许家人通过配对认领）

### 方案 B：TenantRegistry 运行时增量写入
- 修改 `TenantRegistry` 支持 `add_tenant(tenant: Tenant)` 方法
- 持久化到 `tenants.yaml`（原子写入，参考 `pairing/store.py` 的 `_write_text_atomic` 模式）
- 测试：运行时添加 Tenant 后重启服务仍可见

### 方案 C：WebUI pending 管理
- 在 WebUI 显示 pending 配对请求列表（复用 `pairing.list_pending()`）
- 支持一键 approve + 引导选择认领到哪个 tenant
- 复用 `/pairing approve` 与 `/pairing claim` 底层逻辑

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-18 | assistant | 标记为待评估，建议在下个迭代周期排期 |
