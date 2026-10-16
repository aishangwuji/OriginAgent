# Gateway 网关

> **covered_paths**: ["OriginAgent/gateway/", "OriginAgent/channels/websocket.py", "OriginAgent/channels/routes/"]
> **last_verified_commit**: (待填充: 执行 `git rev-parse HEAD` 获取)
> **last_verified**: (待填充: 对应时间戳)
> **schema_version**: 1

## 概述

Gateway 是 OriginAgent 的 HTTP 入口层，承担 WebUI 服务、REST API、WebSocket 多路复用、静态资源分发、认证鉴权、运行时控制面等职责。它基于 `websockets` 库实现自带 HTTP 服务器（默认端口 18790），从 `WebSocketChannel` 中提取而来，避免 God Class。

## 核心组件

### 1. RestApi (REST API 处理器)
**文件**: `OriginAgent/gateway/rest_api.py:442` (`RestApi` class)

**职责**: 处理所有 `/api/*` 路由，委托给 `WebSocketChannel` 访问运行时状态。

**路由表** (按 `dispatch()` 顺序):

| 路由 | 方法 | 处理器 | 说明 |
|------|------|--------|------|
| `/webui/bootstrap` | GET | `_handle_webui_bootstrap` | 签发 WebUI 访问令牌 |
| `/api/sessions` | GET | `_handle_sessions_list` | 列出会话 (合并 WS + WebUI transcript) |
| `/api/sessions/{key}/messages` | GET | `_handle_session_messages` | 获取会话消息 |
| `/api/sessions/{key}/webui-thread` | GET | `_handle_webui_thread_get` | WebUI 线程视图 |
| `/api/sessions/{key}/delete` | POST | `_handle_session_delete` | 删除会话 |
| `/api/settings` | GET | `_handle_settings` | 获取全量设置 (含 providers、runtime_controls、voice、mcp) |
| `/api/settings/update` | POST | `_handle_settings_update` | 更新设置 (model、provider、web_search 等) |
| `/api/settings/provider/update` | POST | `_handle_settings_provider_update` | 更新 provider 配置 |
| `/api/settings/provider/models` | GET | `_handle_settings_provider_models` | 获取 provider 模型列表 |
| `/api/settings/web-search/update` | POST | `_handle_settings_web_search_update` | 更新 Web 搜索配置 |
| `/api/settings/learning/background-review/update` | POST | `_handle_settings_learning_background_review_update` | 更新后台审查配置 |
| `/api/settings/runtime/update` | POST | `_handle_settings_runtime_update` | 更新 runtime profile |
| `/api/settings/local-awareness/audio/update` | POST | `_handle_settings_local_awareness_audio_update` | 更新本地语音配置 |
| `/api/settings/mcp/upsert` | POST | `_handle_settings_mcp_upsert` | 创建/更新 MCP 服务器 |
| `/api/settings/mcp/home-assistant/upsert` | POST | `_handle_settings_mcp_home_assistant_upsert` | 创建/更新 HA MCP |
| `/api/settings/mcp/delete` | POST | `_handle_settings_mcp_delete` | 删除 MCP 服务器 |
| `/api/commands` | GET | `_handle_commands` | 内置命令面板 (支持 i18n) |
| `/api/self` | GET | `_handle_self` | SelfModelService 聚合视图 |
| `/api/reviews` | GET | `_handle_reviews_list` | 后台审查提案列表 |
| `/api/reviews/{id}` | GET | `_handle_review_detail` | 审查详情 |
| `/api/reviews/{id}/{action}` | POST | `_handle_review_action` | 审查动作 (apply/approve/reject/defer) |
| `/api/skills` | GET | `_handle_skills_list` | 技能列表 |
| `/api/skills/{name}` | GET | `_handle_skill_detail` | 技能详情 |
| `/api/skills/{name}/{action}` | POST | `_handle_skill_action` | 技能动作 (verify/activate/deprecate/reject/always) |
| `/api/domains` | GET | `_handle_domains_list` | 域包列表 |
| `/api/domains/{id}` | GET | `_handle_domain_detail` | 域包详情 |
| `/api/domains/{id}/{action}` | POST | `_handle_domain_action` | 域包动作 (upgrade/enable/disable/activate/deactivate/uninstall/eval) |
| `/api/domains/install` | POST | `_handle_domains_install` | 安装域包 |
| `/api/tenants` | GET | `_handle_tenants_list` | 租户列表 |
| `/api/tenants/create` | POST | `_handle_tenants_create` | 创建租户 |
| `/api/tenants/{id}/update` | PATCH | `_handle_tenants_update` | 更新租户 (重建 bindings 防泄漏) |
| `/api/tenants/{id}/delete` | DELETE | `_handle_tenants_delete` | 删除租户 (拒绝 guest/有 bindings) |
| `/api/approvals` | GET | `_handle_approvals_list` | 跨会话审批列表 (按 owner_id 过滤) |
| `/api/approvals/{id}/approve\|reject` | POST | `_handle_approval_action` | 审批动作 (D10 owner_id 校验) |
| `/api/cognition/status` | GET | `handle_meta_cognition_status` | 元认知状态 (委托 `channels/routes/cognition.py`) |
| `/api/evolution/status` | GET | `handle_evolution_status` | 演化状态 |
| `/api/evolution/signals` | GET | `handle_evolution_signals` | 演化信号列表 |
| `/api/evolution/signals/{id}/suppress\|resume` | POST | `handle_signal_action` | 信号抑制/恢复 |
| `/api/media/{tenant_id}/{path}` | GET | `_handle_media` | 媒体文件代理 (租户隔离) |

**依赖注入模式**: 通过 `channel` 访问：
- `self._ch.config` — 配置对象
- `self._ch._session_manager` — 会话管理器
- `self._ch._runtime_introspection` — 运行时自省
- `self._ch._gateway_auth` — 认证鉴权
- `self._ch._confirmation_manager` — 确认管理器
- `self._ch._identity_resolver` — 身份解析器
- `self._ch._grant_store` — 授权存储

### 2. GatewayAuth (网关认证)
**文件**: `OriginAgent/gateway/auth.py:1` (`GatewayAuth` class)

**职责**: 双 Token 体系管理。

**Token 类型**:
- **API Token** (`config.token`) — 长期静态 Token，用于服务间调用
- **Issued Token** (`nbwt_...`) — 短期动态 Token，`/webui/bootstrap` 签发，TTL 可配 (`config.token_ttl_s`)

**核心方法**:
- `check_api_token(request)` — 验证 `Authorization: Bearer <token>` 或 `?token=` 查询参数
- `register_dual_token(token, ttl)` — 注册双 Token (API + Issued 共用命名空间)
- `purge_expired_issued_tokens()` / `purge_expired_api_tokens()` — 清理过期
- `total_token_count` — 当前未过期 Token 总数 (上限 `_MAX_ISSUED_TOKENS=1000`)

**安全**: `_check_api_token` 复用于所有 `/api/*` 端点；跨 owner 审批在 `ConfirmationManager.resolve_user_reply` 层二次拦截 (D10)。

### 3. WebSocketChannel (WebSocket 通道)
**文件**: `OriginAgent/channels/websocket.py:1` (`WebSocketChannel` class)

**职责**: WebSocket 连接管理、消息收发、静态文件服务、HTTP 路由分发。

**核心能力**:
- **连接生命周期**: `connect()` → `handler()` → `disconnect()`
- **消息多路复用**: 单连接承载多会话 (`chat_id` 路由)
- **静态资源**: `dist/` 目录服务 (SPA fallback `/index.html`)
- **HTTP 分发**: 非 WS 升级请求 → `RestApi.dispatch()` → 404/静态文件
- **心跳**: `config.gateway.heartbeat.enabled` 控制 ping/pong
- **租户感知**: `IdentityResolver` 解析 `channel:chat_id` → `tenant_id`

**启动流程**:
```python
channel = WebSocketChannel(config)
await channel.start()  # 绑定端口，启动 TCP 监听
```

### 4. 媒体服务器
**文件**: `OriginAgent/gateway/media_server.py:1` (`MediaServer` class)

**职责**: 租户隔离的媒体文件 HTTP 代理。

**路由**: `/api/media/{tenant_id}/{path}` → `workspace/tenants/{tenant_id}/{path}`

**安全**: 路径遍历防护 (`path.resolve().is_relative_to(tenant_dir)`)、租户隔离 (仅允许访问自己 tenant_id 下的文件)。

### 5. 认知路由
**文件**: `OriginAgent/channels/routes/cognition.py:1`

**职责**: 元认知与演化相关的 REST 端点，从 `RestApi` 委托调用。

**端点**:
- `handle_meta_cognition_status` — `/api/cognition/status`: 触发器统计、反射记录、置信度分布、模式发现
- `handle_evolution_status` — `/api/evolution/status`: 模块状态、验证结果、遥测摘要
- `handle_evolution_signals` — `/api/evolution/signals`: 信号队列 (pending/suppressed/active)
- `handle_signal_action` — `/api/evolution/signals/{id}/suppress|resume`: 信号控制

## 关键流程

### WebUI Bootstrap 流程
```
浏览器访问 http://127.0.0.1:18790
    → 静态文件 index.html (SPA)
    → JS 调用 GET /webui/bootstrap
    → _check_api_token (localhost 允许无 token)
    → 生成 nbwt_... token (TTL=config.token_ttl_s)
    → 返回 {token, ws_path, expires_in, model_name, runtime_mode, config_doctor}
    → JS 建立 WS 连接 ws://127.0.0.1:18790/ws?token=nbwt_...
    → WebSocketChannel.handler() 验证 token → 绑定 chat_id
```

### 设置更新流程
```
PUT /api/settings/update?model=xxx&provider=xxx
    → _check_api_token
    → _load_config() 获取可变配置对象
    → 修改 defaults.model / defaults.provider 等字段
    → save_config(config) (原子写入: tmp + os.replace)
    → 返回更新后的 settings payload
    → 部分配置需重启生效 (requires_restart=true)
```

### 租户管理流程
```
POST /api/tenants/create {tenant_id, display_name, bdi_enabled, claimable_by_pairing}
    → _check_api_token
    → TenantRegistry.add_tenant(TenantConfig) (RLock 保护)
    → save_config() 持久化 (tenant_id 唯一性约束 → 409 Conflict)
    → 返回创建的 tenant 记录

PATCH /api/tenants/{id}/update {display_name, bdi_enabled, bindings: [...]}
    → _check_api_token
    → TenantRegistry.update_tenant(id, TenantConfig) (先清空旧 bindings 再注册新)
    → save_config()
    → 返回更新后的 tenant 记录

DELETE /api/tenants/{id}
    → _check_api_token
    → 拒绝 guest / 有 bindings 的租户 (400)
    → TenantRegistry.delete_tenant(id) (级联删 session 文件 + workspace 目录)
    → save_config()
```

### 跨会话审批流程
```
GET /api/approvals?owner_id=tenant:owner
    → _check_api_token
    → _get_confirmation_manager().list_pending_for_owner(owner_id)
    → 先 expire_old() 清理过期 → 按 owner_id 过滤
    → owner_id=None 仅返回历史遗留 (无 owner_id) 记录

POST /api/approvals/{id}/approve?owner_id=tenant:owner
    → _check_api_token
    → _get_confirmation_manager().resolve_user_reply(id, "yes", caller_actor_id=owner_id)
    → D10 校验: request.owner_id != caller_actor_id → 403 approval_denied_owner_mismatch
    → 成功 → 返回 updated confirmation
```

## WebSocket 协议

**连接建立**:
```
WS 升级: GET /ws?token=nbwt_xxx&chat_id=123
    → 验证 token (GatewayAuth)
    → 解析 chat_id → session_key = "websocket:123"
    → IdentityResolver.resolve("websocket", "123") → Tenant
    → 绑定 connection → session_key
```

**消息格式**:
```json
// Client → Server
{"type": "user_message", "content": "hello", "chat_id": "123"}

// Server → Client
{"type": "assistant_message", "content": "Hi!", "chat_id": "123"}
{"type": "tool_call", "tool": "shell", "args": {...}, "chat_id": "123"}
{"type": "tool_result", "tool": "shell", "result": {...}, "chat_id": "123"}
{"type": "progress", "message": "Thinking...", "chat_id": "123"}
{"type": "error", "message": "Tool failed", "chat_id": "123"}
```

**主动推送**: Agent 可通过 `MessageTool` 向任意 `channel:chat_id` 推送消息，经 `MessageBus` → `WebSocketChannel` 分发。

## 静态资源服务

- **构建产物**: `webui/dist/` (执行 `cd webui && bun run build` 生成)
- **服务路径**: `/` → `dist/index.html` (SPA fallback)
- **资源路径**: `/assets/*` → `dist/assets/*`
- **开发模式**: `bun run dev` (Vite HMR) 代理 API/WS 到 Gateway

## 配置项

| 配置路径 | 默认值 | 说明 |
|----------|--------|------|
| `gateway.host` | `"127.0.0.1"` | 绑定地址 |
| `gateway.port` | `18790` | 监听端口 |
| `gateway.heartbeat.enabled` | `true` | WS 心跳 |
| `gateway.heartbeat.interval_s` | `30` | ping 间隔 |
| `gateway.tiered_router.enabled` | `false` | 分层路由 |
| `gateway.tiered_router.default_tier` | `"default"` | 默认层级 |
| `token` | `""` | 静态 API Token |
| `token_issue_secret` | `""` | bootstrap 签名密钥 |
| `token_ttl_s` | `3600` | 动态 Token TTL (秒) |

## 安全边界

1. **认证**: 所有 `/api/*` (除 `/webui/bootstrap` localhost) 需有效 Token
2. **租户隔离**: 
   - 会话列表仅返回当前租户会话 (`session_key` 前缀匹配)
   - 媒体代理强制 `tenant_id` 前缀校验
   - 租户 CRUD 拒绝跨租户操作
3. **审批授权**: D10 `owner_id` 字段 + `resolve_user_reply` 二次校验
4. **配置写入**: `save_config` 原子写入 (tmp + replace)，防半写快照
5. **路径遍历**: `_ui_media_kind_for_path` + `resolve().is_relative_to()` 防御

## 与其他子系统集成

| 子系统 | 集成点 |
|--------|--------|
| Session Management | `session_manager.list_sessions()`、`read_session_file()` |
| Identity | `IdentityResolver` 解析 WS 连接身份；`TenantRegistry` REST CRUD |
| Confirmation | `ConfirmationManager` 审批列表/动作；`grant_store` 权限查询 |
| Evolution | `EvolutionControlPlane` 状态/信号查询 |
| MetaCognition | `MetaCognitionFacade` 状态查询 |
| Skills/Domains | `SkillsLoader`、`DomainPackGovernanceService` 列表/详情/动作 |
| Config | `save_config`、`config.doctor`、`runtime_mode` summary |

## 已知技术债

| 编号 | 标题 | 优先级 | 状态 |
|------|------|--------|------|
| TD-2026-001 | Strangler Fig 子容器迁移停滞 Phase 0 | P2 | 已确认 |

## 陈旧度门槛

本文件涉及的源码路径：`OriginAgent/gateway/`、`OriginAgent/channels/websocket.py`、`OriginAgent/channels/routes/`。当 `git log <last_verified_commit>..HEAD -- <covered_paths>` 有新提交时，必须重新核验并更新 `last_verified_commit`。