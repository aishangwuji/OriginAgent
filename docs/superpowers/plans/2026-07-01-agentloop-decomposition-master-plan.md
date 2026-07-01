# AgentLoop Decomposition — Master Architecture Plan

> **Status:** Phase 1 ✅ | Phase 2a ✅ | Phase 2b ⬜ | Phase 3 ⬜

**Goal:** Transform `AgentLoop` from a 2966-line "God Object" into a ~300-line Facade that delegates to three focused internal components: `SessionStateHolder` (state), `AgentHost` (lifecycle), `AgentRuntime` (routing).

**Principle:** External API zero-change. Internal decomposition radical.

---

## Target Architecture

```
外部调用者 (tests, CLI, SDK, Channels)
    ↓
AgentLoop（门面 Facade ~300 行）
    ├── AgentHost          ← 基础设施生命周期（MCP / BDI / Provider / 后台任务）
    ├── AgentRuntime       ← 无状态消息路由（构造 TurnContext，驱动 TurnOrchestrator）
    ├── SessionStateHolder ← 会话隔离状态容器（_last_* 全部按 session_key 收拢）
    └── AgentServiceContainer ← 已有的服务容器（不变）
```

---

## Phase Breakdown

### Phase 1: SessionStateHolder ✅

**文件：** `session_state.py` (新建), `loop.py` (修改)

- 创建 `SessionScopedState` dataclass + `SessionStateHolder`（双检锁，TTL 过期）
- 10 个 `_last_*` 字段全部迁移到 session-keyed 容器
- 内部 `_record_*` 方法双重写入（直接属性 + holder）
- 内部读取从 `self._last_*` 迁移到 `self._state_holder.get(session_key).last_*`
- 生命周期清理：`drop()` 在 `_clear_pending_user_turn`，`expire_stale()` 在 `close_mcp`

**结果：** `loop.py` 减少 ~15 行；消除跨会话状态污染风险。

---

### Phase 2a: AgentHost — Infrastructure Lifecycle ✅

**文件：** `agent_host.py` (新建), `loop.py` (修改)

移出内容：
| 子系统 | 行数 | 说明 |
|--------|------|------|
| MCP 生命周期 | ~80 | `_connect_mcp`, `_run_mcp_runtime`, `_close_mcp` + 12 个状态字段 |
| 后台任务 | ~10 | `_schedule_background`, `_background_tasks` |
| 生命周期状态机 | ~20 | `_running`, `start()`, `stop()`, `shutdown()`, `_active_intent_task` |
| **总计** | **~110** | |

**接口设计：**
- `AgentHostDependencies` — frozen dataclass，为 Phase 2b 预留扩展槽
- `AgentLoop` 保留 `_last_*` 属性用于向后兼容（`build_loop_components` → `setattr` 仍可设置）
- `_start_active_intent_loop` / `_schedule_background` 有 `_host` 回退，避免通过 `__new__` 绕过 `__init__` 的测试被破坏

---

### Phase 2b: AgentHost — Provider + BDI + Transcription ⬜

**文件：** `agent_host.py` (修改), `loop.py` (修改)

移出内容：
| 子系统 | 行数 | 说明 |
|--------|------|------|
| Provider 管理 | ~60 | `_apply_provider_snapshot`, `_refresh_provider_snapshot`, `set_model_preset` |
| BDI 引擎 | ~65 | `_desire_store`, `_bdi_engine`, `_inner_monologue_engine`, `_on_bdi_intention` |
| 转录 | ~25 | `_build_transcription_provider`, `_transcription_provider`, `_local_awareness_backend` |
| **总计** | **~150** | |

**关键挑战：** `_apply_provider_snapshot` 需要同时更新 `runner`、`subagents`、`consolidator`、`dream` 等多个对象——需要将这些引用注入到 `AgentHostDependencies` 中。

**设计要点：**
- `AgentHostDependencies` 追加字段：`runner`, `subagents`, `consolidator`, `dream`, `auxiliary_router`, `background_review`, `provider`, `model`
- `AgentHost.provider_snapshot` 成为 provider 状态的唯一真实来源
- `AgentLoop._refresh_provider_snapshot()` 变为委托给 `self._host` 的兼容外壳

---

### Phase 3: AgentRuntime — Stateless Message Router ⬜

**文件：** `agent_runtime.py` (新建), `loop.py` (修改)

将剩余的 ~2000 行中与 "消息处理" 直接相关的逻辑提取到一个无状态类中：

移出内容：
| 子系统 | 行数 | 说明 |
|--------|------|------|
| 消息构建 | ~200 | `_build_initial_messages`, `_build_prompt_self_model`, `_build_tool_context` |
| Turn 执行 | ~180 | `_run_agent_loop` |
| 消息分发 | ~50 | `_dispatch_command_inline`, `process_direct`, `_process_message` |
| 工具上下文 | ~15 | `_set_tool_context` |
| Outbound 组装 | ~40 | `_assemble_outbound` |
| Meta-cognition | ~150 | `_install_meta_cognition_observer`, `_record_meta_trigger`, `_scan_meta_triggers_for_turn`, `_schedule_meta_cognition_reflection`, `_reflect_meta_cognition_turn` |
| 工作内存 | ~50 | `_update_working_memory_from_turn` |
| 连续性/持久化 | ~80 | `_save_continuity_checkpoint`, `_persist_user_message_early`, `_save_turn` |
| 后台调度 | ~30 | `_schedule_background_review`, `_schedule_curator_review`, `_schedule_nearline_memory` |
| 认知 | ~50 | `_collect_cognitive_candidates`, `_build_cognitive_runtime_context`, `_write_cognitive_event_to_working_memory` |
| **总计** | **~845** | |

**设计要点：**
- `AgentRuntime` 构造函数接受 `SessionStateHolder` 作为依赖项（从 AgentLoop 传入）
- 每个方法接收显式的 `session_key` 参数，而非从 `self._last_*` 读取
- AgentLoop 的最终形式：~300 行的纯委托 + 兼容性外壳

---

## 最终 AgentLoop 结构（Phase 3 之后）

```python
class AgentLoop:
    """Facade — 委托给 AgentHost + AgentRuntime + SessionStateHolder."""

    def __init__(self, ...):
        # 1. build_loop_components() — 创建所有服务（不变）
        # 2. SessionStateHolder — 会话隔离状态（Phase 1）
        # 3. AgentHost — 基础设施生命周期（Phase 2a + 2b）
        # 4. AgentRuntime — 消息路由（Phase 3）

    # 公共 API — 全部委托，零行为
    async def run(self):           await self._host.start(); ...
    def stop(self):                self._host.stop()
    async def process_direct(...): return await self._runtime.process(...)
    @classmethod
    def from_config(cls, ...):     ...  # 工厂方法，不变
    @classmethod
    def from_options(cls, ...):    ...  # 工厂方法，不变
```

---

## 不变约束（所有阶段）

- `AgentLoop.__init__`, `from_config`, `from_options`, `run()`, `process_direct()` 签名**不得更改**
- 所有现有测试**必须无需修改即可通过**
- `getattr(loop, "_last_*")` 模式**必须继续有效**（introspection, action_summary, MyTool, tests）
- `SimpleNamespace(_last_*=...)` 模式**必须继续有效**（tests）
- `AgentLoop.__new__(AgentLoop)` 绕行模式**必须继续有效**（单元测试）
