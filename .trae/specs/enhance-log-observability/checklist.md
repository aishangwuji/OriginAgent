# Checklist

## Task 1: log_event attrs 可见化

- [x] `log_event` 输出消息包含 attrs（`event.xxx | key=value key2=value2` 格式）
- [x] 无 attrs 时不附加 `|`
- [x] 值为 None 或空字符串的 attr 不出现在消息中
- [x] `logger.bind` 仍绑定所有 attrs（JSON sink 不受影响）
- [x] 测试 `tests/utils/test_tracing.py` 全部通过（17 passed）

## Task 2: 关键事件 attrs 增强

- [x] `llm.request` 包含 `stream` 和 `message_count`
- [x] `tools.execute` 包含 `tool_names`（逗号分隔）
- [x] `run.complete` 包含 `elapsed_ms`
- [x] 测试 `tests/agent/test_runner_logging.py` 全部通过（3 passed）

## 通用

- [ ] 每个 Task 对应独立原子 commit（规则37）— 待提交时验证
- [x] 无投机性设计/过度抽象（规则32）
- [x] 无任务范围外的越界改动（规则33）
- [x] 测试先于实现编写（规则34）— 两个 Sub-Agent 均确认 TDD 红→绿
- [x] 无硬编码敏感信息（规则18）
