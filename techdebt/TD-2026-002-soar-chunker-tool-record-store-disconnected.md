# TD-2026-002: SoarChunker tool_record_store 未接入

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-15T22:00:00+08:00 |
| 发现人 | 观雪 |
| 关联Spec | .trae/specs/restore-and-wire-design-modules/spec.md |
| 关联规则 | 规则32(最小化实现) |
| 优先级 | P2 |
| 状态 | 已确认 |

## 详细描述
SoarChunker 在 `agent_loop_components.py` 中被构造时,`tool_record_store` 参数传入了 `None`。这导致 `_extract_tool_sequence` 方法始终返回空列表(优雅降级),SoarChunker 无法从子代理工具记录中提取实际的工具序列。

当前 Soar chunking 功能可以工作(基于 `result_summary` 构造 digest),但工具序列为空,降低了技能候选的质量。SubagentManager 内部有 `_records` 属性(JsonlSubagentRecordStore),但未暴露为公共接口供 SoarChunker 使用。

## 影响范围
- **影响文件**: `OriginAgent/agent/agent_loop_components.py`, `OriginAgent/agent/soar_chunker.py`, `OriginAgent/agent/subagent.py`
- **影响功能**: Soar 在线块化 / 技能形成
- **潜在风险**: 技能候选缺少工具序列信息,编译出的技能可能不够精确

## 复现/验证路径
1. 检查 `agent_loop_components.py` 中 SoarChunker 构造:`tool_record_store=None`
2. 触发子代理成功解决障碍,检查 SoarChunker.chunk_from_success 产生的 digest 中 `tool_sequence` 为空列表
3. 运行 `pytest tests/agent/test_soar_chunker.py` — 测试通过(因为测试 mock 了 tool_record_store)

## 修复方案(可选)
1. 在 SubagentManager 上添加公共属性暴露内部的 record store
2. 或在 SubagentManager 构造时接收一个共享的 record store,同时传给 SoarChunker
3. 注意循环依赖:SoarChunker 在 SubagentManager 构造后才注入,但 tool_record_store 需要在 SubagentManager 构造时就可用

## 阻塞发现（2026-07-16 修复尝试）
状态：**修复方案 1 不足以真正修复缺陷，已停止，未修改任何代码。**

经核实，原修复方案（"暴露 record store + 传入 SoarChunker"）存在两层未预见的障碍，强行实施只会产生"接线已通但工具序列仍为空"的伪修复（违反规则25）：

1. **Store 缺少 SoarChunker 所需的查询方法**：`SoarChunker._extract_tool_sequence`（soar_chunker.py L117）通过 `getattr` 依次尝试 `list_by_task` / `recent_for_task` / `list_for_task` / `get_by_task` 四个方法。全代码库搜索确认：这四个方法**未在任何地方定义**。`JsonlSubagentRecordStore`（subagent_records.py）只有 `append_task` / `append_lifecycle` / `append_tool` / `recent_task_summary`，无任何按 subagent_id 查询 tool 记录的方法；底层 sqlite store 仅暴露 `recent(limit=...)`。因此即便把 store 传入，`_extract_tool_sequence` 仍返回空列表，行为与 `None` 完全一致。现有 `test_soar_chunker.py` 之所以通过，是因为它用 MagicMock 伪造了 `list_by_task`（L74）。

2. **返回类型不匹配**：`JsonlSubagentRecordStore._iter_jsonl` 返回 `dict[str, Any]`（L269-286），而 `SoarChunker._extract_tool_sequence` 用属性访问取值（`getattr(r, "tool_name", None)`、`getattr(r, "started_at", "")`，L129/L137）。dict 的属性访问恒为 None/""，故即便补一个返回原始 dict 的查询方法，工具序列仍为空——查询方法必须重构为返回 `SubagentToolRecord` 实例（或 SoarChunker 需兼容 dict）。

**已澄清的非阻塞项**：
- 构造顺序有利，无循环依赖：SubagentManager 在 agent_loop_components.py L475 构造（`self.records` 在 __init__ L155 即创建），SoarChunker 在 L640 构造，L644 后置注入。故 L640 处 `values["subagents"].records` 已可用，机械接线可行。
- TD 原文称"SubagentManager 内部有 `_records` 属性"与代码不符：实际属性名为 `self.records`（**已为公共属性**，无下划线）。因此"添加公共属性"这步本身是冗余的——可直接用 `subagent_manager.records`。任务示例 `return self._records` 中的 `self._records` 在 SubagentManager 中并不存在。

**真正修复所需（超出本次任务声明的规则33范围）**：
- 在 `subagent_records.py` 的 `JsonlSubagentRecordStore` 上新增查询方法（如 `list_tools_by_subagent(subagent_id) -> list[SubagentToolRecord]`），从 tools.jsonl / sqlite_tools 过滤并重构为 `SubagentToolRecord` 实例；
- 该方法名需落入 SoarChunker 的 getattr 探测列表，或在 SoarChunker 侧改用确定的方法名；
- 补一个用真实 `JsonlSubagentRecordStore`（非 mock）的端到端测试，验证工具序列非空——这才是能锁住本缺陷的断言（规则14）。

需人工裁决：是否授权把改动范围扩展到 `subagent_records.py`，或改采其它方案。

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-15 | 观雪 | 确认为技术债,当前优雅降级可接受,后续需接入实际 record store |
| 2026-07-16 | 修复尝试 Agent (GLM-5.2) | 停止修复：原方案不足以修复缺陷（store 无 SoarChunker 可调用的查询方法 + 返回类型不匹配），未改代码，待人工裁决是否扩展范围至 subagent_records.py |
