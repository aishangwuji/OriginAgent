---
schema_version: 1
---

# TD-2026-018: MCP 启动时无重试循环，单次失败即放弃

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-18T15:03:00+08:00 |
| 发现人 | assistant（spec fix-cron-runtime-and-context-gaps Issue 7 排查） |
| 关联Spec | .trae/specs/fix-cron-runtime-and-context-gaps/spec.md |
| 关联规则 | 规则10（重连/重试三要素——退避策略/最大重试次数/主动心跳） |
| 优先级 | P3 |
| 状态 | 待评估 |

## 详细描述
网关启动时 MCP 服务器 `home_assistant` 连接失败，日志显示：

```
14:58:17 | WARNING | - | MCP server 'home_assistant': streamable HTTP endpoint unreachable, skipping
14:58:17 | WARNING | - | No MCP servers connected successfully (will retry next message)
```

**现有逻辑**：
- `OriginAgent/agent/tools/mcp.py:681-690`：`streamableHttp` 传输类型下，`_probe_http_url(cfg.url)` 失败后仅记录 WARNING `"streamable HTTP endpoint unreachable, skipping"`，随即 `server_stack.aclose()` 并返回 `status="skipped"`，跳过该 server。
- `OriginAgent/agent/tools/mcp.py:563` `connect_mcp_servers` 函数本身无重试封装，单次探测失败即放弃。
- `OriginAgent/agent/agent_host.py:286-295`：当 `stacks` 为空时，记录 WARNING `"No MCP servers connected successfully (will retry next message)"` 后直接 `return`，结束本次 MCP runtime cycle。

**缺失（违反规则10 三要素）**：
1. **退避策略**：无指数退避/固定退避，单次失败立即放弃，无任何 `await asyncio.sleep(...)` 重试间隔。
2. **最大重试次数/熔断机制**：启动阶段无"最多重试 N 次"上限，也无熔断器。
3. **主动心跳/存活检测**：无后台 health-check 任务定期探测不可达的 MCP server；`"will retry next message"` 仅在**下次 message 触发**时重新进入 `_run_mcp_runtime`（被动重试），不是启动时的主动重试——若网关启动后长时间无 message，MCP 能力将持续不可用。

**注**：MCP 工具/资源/prompt 调用层面已有"单次重试 + 1s backoff"（`mcp.py:257-292` 等处），但这是**已连接后**的工具调用重试，与**启动连接阶段**的重试是两回事，不构成对本技术债的覆盖。

## 影响范围
- **影响文件**：
  - `OriginAgent/agent/tools/mcp.py:563`（`connect_mcp_servers` 函数定义）
  - `OriginAgent/agent/tools/mcp.py:681-690`（`_probe_http_url` 失败分支，单次 skip 无重试）
  - `OriginAgent/agent/agent_host.py:270-295`（`_run_mcp_runtime`，空 stacks 仅 WARNING 后 return）
- **影响功能**：MCP 服务器短暂不可达时（如启动顺序问题导致 HA MCP server 尚未起好、瞬时网络抖动、DNS 暂时解析失败）会导致整次启动无 MCP 能力，需重启网关或等待下次 message 触发被动重试才能恢复。
- **潜在风险**：
  - 依赖 MCP 的智能家居控制等域功能完全瘫痪，用户感知为"工具不可用"。
  - 启动顺序敏感：若 MCP server 启动慢于网关，网关首次探测必然失败，无重试机制兜底。
  - 与规则10 三要素显式冲突，属可审计的合规缺口。

## 复现/验证路径
1. 在配置中添加一个不可达的 MCP 服务器 URL，例如：
   ```yaml
   mcp:
     servers:
       home_assistant:
         transport: streamableHttp
         url: http://localhost:9999/mcp
         headers:
           Authorization: Bearer test-token
   ```
2. 启动网关：`.\.venv\Scripts\python.exe -m OriginAgent.cli`（或既有启动入口）。
3. 观察启动日志：仅出现一次 `WARNING | MCP server 'home_assistant': streamable HTTP endpoint unreachable, skipping` 与一次 `No MCP servers connected successfully (will retry next message)`，**无任何重试日志**，无退避等待。
4. 不发送任何 message，等待 60s/300s/600s，确认无后台重连尝试。
5. 发送一条 message，确认此时才触发被动重试（重新进入 `_run_mcp_runtime`）。

## 修复方案（可选）
在 MCP 启动连接逻辑中加入规则10 三要素：
1. **退避重试**：在 `connect_mcp_servers` 的 `_probe_http_url` 失败分支外层包裹重试循环，采用指数退避（如 1s/2s/4s），最多 3 次；单 server 重试独立，不影响其他 server 并发连接。
2. **最大重试次数**：达到上限后放弃该 server（保持现有 skip 行为），但在 snapshot 中记录 `retry_exhausted` 状态供观测。
3. **主动心跳/后台重连**：在 `agent_host._run_mcp_runtime` 中，对于启动时 skip 的 server，注册一个后台 health-check 任务，每 60s 尝试重连；成功后动态注册其 tools/resources/prompts（需评估与 `ToolRegistry` 运行时注册的兼容性，可能涉及规则7 状态变化审计）。
4. 参考规则10 三要素实现，并在清单中显式声明对规则9（session token 隔离——后台重连任务与原 runtime cycle 的回调隔离）的合规性。

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-18 | assistant | 登记为待评估，需人工确认是否需要代码修复（当前 spec 范围内仅作配置问题处理，用户已确认不修复代码） |
