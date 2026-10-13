# Spec: WebUI 租户管理界面

## 背景
当前用户必须手工编辑 `~/.originagent/config.json` 的 `gateway.tenants.tenants` 数组来配置租户（owner 预绑定、家人 claimable 等），体验差。用户希望在 WebUI 中通过图形界面管理租户。

## 现状
- **前端**：`webui/` 是 Vite + React 18 + Radix UI + Tailwind 项目，已有 `SettingsView.tsx` 含多个子组件（LearningSettings、TieredRoutingSettings 等），`lib/api.ts` 有完整 API 封装模式
- **后端**：`gateway/rest_api.py` 是 WebUI 的 REST API 后端，使用 if-elif 链分发路由，已有 `/api/settings`、`/api/skills`、`/api/domains` 等 endpoints
- **持久化**：`config/loader.py:save_config` 已存在但非原子写入
- **TenantRegistry**：`identity/tenant.py` 只在启动时从 config 加载，无运行时增量

## What Changes

### 后端（Python）

1. **TenantRegistry 运行时增量**（`identity/tenant.py`）
   - 新增 `add_tenant(tc: TenantConfig) -> Tenant`：运行时添加租户，更新 `_tenants` 与 `_by_channel_sender`
   - 新增 `update_tenant(tenant_id: str, tc: TenantConfig) -> Tenant`：更新租户配置
   - 新增 `delete_tenant(tenant_id: str) -> bool`：删除租户（禁止删除 guest 与当前绑定 sender 的 tenant）
   - 新增 `to_config() -> TenantsConfig`：导出为可持久化的 config 对象

2. **save_config 原子写入**（`config/loader.py`）
   - 改造为临时文件 + `os.replace` 原子写入
   - 保留原 JSON 格式与字段顺序（by_alias=True）

3. **REST API endpoints**（`gateway/rest_api.py`）
   - `GET /api/tenants` — 列出所有租户（含 bindings、claimable_by_pairing、bdi_enabled）
   - `POST /api/tenants` — 新增租户
   - `PATCH /api/tenants/{tenant_id}` — 更新租户
   - `DELETE /api/tenants/{tenant_id}` — 删除租户
   - 所有写操作调用 `tenant_registry.add/update/delete_tenant` + `save_config` 持久化
   - 复用现有 `_handle_settings_update` 的鉴权模式（bearer token）

### 前端（React）

4. **类型定义**（`webui/src/lib/types.ts`）
   - 新增 `Tenant`、`TenantBinding`、`TenantsListResponse` 类型

5. **API 封装**（`webui/src/lib/api.ts`）
   - 新增 `fetchTenants()`、`createTenant()`、`updateTenant()`、`deleteTenant()` 函数

6. **租户管理组件**（`webui/src/components/settings/TenantsSettings.tsx`）
   - 表格展示：tenant_id、display_name、bindings、claimable_by_pairing、bdi_enabled
   - 新增按钮 → 弹出 Dialog 表单
   - 编辑按钮 → 弹出 Dialog 表单（预填充）
   - 删除按钮 → 确认对话框
   - 复用现有 `ui/button`、`ui/dialog`、`ui/input` 等组件

7. **集成到 SettingsView**（`webui/src/components/settings/SettingsView.tsx`）
   - 在 settings 列表中新增"租户管理"条目，点击展开 `TenantsSettings`

8. **i18n**（`webui/src/i18n/locales/`）
   - 在 `zh-CN/common.json` 与 `en/common.json` 新增翻译 key

### 测试

9. **后端测试**（`tests/gateway/test_tenants_api.py`）
   - `test_list_tenants`、`test_create_tenant`、`test_update_tenant`、`test_delete_tenant`
   - `test_delete_bound_tenant_rejected`（不能删除已绑定的）
   - `test_save_config_atomic`（原子写入）

10. **前端测试**（`webui/src/tests/tenants-settings.test.tsx`）
    - 渲染、新增、编辑、删除流程

### 构建

11. **重新构建 dist**
    - `cd webui && npm run build`
    - 验证 `OriginAgent/web/dist/index.html` 更新

## 验收标准
- WebUI 设置页有"租户管理"入口
- 能在 UI 上新增、编辑、删除租户
- 修改后 `config.json` 自动更新，无需重启
- 删除已绑定 sender 的租户会被拒绝（安全边界）
- 所有测试通过

## 范围外
- WebUI 登录鉴权（复用现有 bearer token）
- 租户顺序拖拽（按 tenant_id 字母序即可）
- 批量导入/导出（留待 Phase 3.3）

## 合规
- 规则33（手术式变更）：每个 diff 可追溯到具体需求
- 规则32（最小化实现）：不新增抽象层，复用现有 SettingsView 模式
- 规则34（验证先行）：测试与实现同步
- 规则18（安全边界）：删除租户前校验无绑定 sender
- 规则12（幂等）：重复创建相同 tenant_id 返回 409 Conflict
