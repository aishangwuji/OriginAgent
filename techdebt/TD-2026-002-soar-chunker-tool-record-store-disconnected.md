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

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-15 | 观雪 | 确认为技术债,当前优雅降级可接受,后续需接入实际 record store |
