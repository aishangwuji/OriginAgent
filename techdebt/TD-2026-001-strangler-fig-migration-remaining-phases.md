# TD-2026-001: Strangler Fig 迁移剩余阶段未完成

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-15T22:00:00+08:00 |
| 发现人 | 观雪 |
| 关联Spec | .trae/specs/restore-and-wire-design-modules/spec.md |
| 关联规则 | 规则17(禁止硬编码)、规则24(迭代式实现) |
| 优先级 | P2 |
| 状态 | 已确认 |

## 详细描述
Strangler Fig 迁移目前只完成了 Phase 0(双路径共存):5 个子容器 dataclass(CoreServices/MetaCognitionServices/MemoryServices/BackgroundServices/StoreServices)已定义在 `agent_runtime.py` 中,并在 `loop.py` 的 `RuntimeDependencies(...)` 构造中与平铺字段共存。但所有消费者(agent_runtime.py 等)仍通过平铺字段访问依赖,子容器字段未被任何消费者读取。

后续阶段需要逐步将消费者从平铺字段迁移到子容器,最终移除平铺字段。这是一项渐进式迁移,每一步都需保证双路径一致性。

## 影响范围
- **影响文件**: `OriginAgent/agent/agent_runtime.py`, `OriginAgent/agent/loop.py`, `tests/agent/test_runtime_dependencies_strangler_fig.py`
- **影响功能**: AgentRuntime 依赖访问路径
- **潜在风险**: 迁移停滞会导致双路径长期共存,增加维护成本和状态分歧风险

## 复现/验证路径
1. 运行 `pytest tests/agent/test_runtime_dependencies_strangler_fig.py` — Phase 0 验证通过
2. 搜索 `deps.tools` / `deps.core.tools` — 确认消费者仍使用平铺字段
3. 后续阶段验证:逐步将 `deps.tools` 替换为 `deps.core.tools`,运行回归测试

## 修复方案(可选)
- Phase 1: 将 agent_runtime.py 中的平铺字段访问迁移到子容器(如 `d.tools` → `d.core.tools`)
- Phase 2: 将 loop.py 中的平铺字段构造移除,仅保留子容器构造
- Phase 3: 移除 RuntimeDependencies 的平铺字段定义
- 每阶段需运行完整回归测试,确保双路径一致性

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-15 | 观雪 | 确认为技术债,Phase 0 已完成,后续阶段需按迭代推进 |
