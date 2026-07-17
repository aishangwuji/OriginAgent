---
schema_version: 1
---

# TD-2026-016: 工具执行鲁棒性缺陷（D1-D13）

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-17T23:10:00+08:00 |
| 发现人 | assistant（P2/P3 工具鲁棒性审查） |
| 关联Spec | 无 |
| 关联规则 | 规则11（错误处理必须先分类）、规则14（关键假设断言化）、规则18（安全边界） |
| 优先级 | P2（D4/D7/D8/D9 影响工具错误可读性） / P3（D1/D2/D3/D12/D13） |
| 状态 | 待评估 |

## 详细描述
在 P1-A（web_search 循环修复）过程中，对工具执行链路做了鲁棒性
审查，发现以下 13 个缺陷（按严重度分级）。本次 P1-A/P1-B 修复未
触及这些缺陷，登记为技术债待后续处理。

### P2 — 工具错误消息对 LLM 不友好（D4/D7/D8/D9）

- **D4** `read_file`（`OriginAgent/agent/tools/filesystem.py`）：
  文件不存在时直接抛 `FileNotFoundError` 原始异常，LLM 看到的是
  Python 堆栈而非"文件 X 不存在，请检查路径"。
- **D5** `read_file`：权限错误时抛 `PermissionError` 原始异常，
  无操作建议（如"检查文件权限或换一个路径"）。
- **D6** `read_file`：二进制文件读取后无优雅降级，直接返回乱码
  或抛 `UnicodeDecodeError`。
- **D7** `exec`（`OriginAgent/agent/tools/shell.py`）：命令失败时
  返回原始 stderr，无结构化前缀（如 `[ERROR] exit code N:`），
  LLM 难以区分"命令不存在"与"命令执行失败"。
- **D8** `exec`：超时时返回的消息不含已执行部分输出，LLM 无法判断
  命令是卡死还是仍在执行。
- **D9** `exec`：无命令注入防护提示——虽然 shell=True 由调用方决定，
  但工具层未对明显危险模式（`rm -rf /`、`chmod 777`）发出警告。

**修复方向**：工具错误消息加结构化前缀
（`[ERROR_KIND] brief: detail | suggestion`），让 LLM 能据此选择
重试/换路径/放弃。

### P3 — 工具错误分类未接入（D1/D2/D3）

- **D1** `OriginAgent/agent/error_classifier.py`：`TOOL_FAILURE`
  分类是死代码——没有任何调用方使用它，工具错误全部走通用
  `Exception` 路径。
- **D2** 同上：`error_classifier.classify()` 对工具超时、文件不存在、
  权限错误等均返回未分类的默认值，无法驱动规则11的错误分类重试。
- **D3** `ToolResult` / `ToolError` 数据类迁移未开始——工具返回值
  仍为裸字符串，无法区分"工具执行成功但结果为空"与"工具执行失败"。

### P3 — 无工具级重试预算与瞬态错误自动重试（D12/D13）

- **D12** 无工具级重试预算：每个工具调用失败后由 LLM 决定是否
  重试，无工具层自动退避机制。对瞬态错误（网络抖动、文件锁）
  导致不必要的 LLM 往返。
- **D13** 无瞬态错误自动重试：`web_search`、`web_fetch` 等网络
  工具遇到 5xx/超时时不自动重试，直接返回错误给 LLM，LLM 可能
  以不同关键词重试（P1-A 修复的正是此场景的副作用）。

## 影响范围
- **影响文件**：
  - `OriginAgent/agent/tools/filesystem.py`（D4/D5/D6）
  - `OriginAgent/agent/tools/shell.py`（D7/D8/D9）
  - `OriginAgent/agent/error_classifier.py`（D1/D2）
  - `OriginAgent/agent/tools/base.py` 或类似（D3 ToolResult/ToolError）
  - `OriginAgent/agent/runner.py` 工具执行循环（D12/D13）
- **影响功能**：工具执行链路的错误处理质量
- **潜在风险**：
  - LLM 因错误消息不清晰而反复重试（P1-A 已通过断路器缓解，
    但根因未消除）
  - 瞬态错误浪费 LLM 调用预算
  - 安全相关（D9）无命令注入防护提示

## 复现/验证路径
- D4：调用 `read_file` 读取不存在的路径，观察返回的错误消息格式
- D7：调用 `exec` 执行 `nonexistent_command`，观察返回消息
- D1：grep `TOOL_FAILURE` 在代码库中的使用——仅定义处，无调用方
- D12/D13：观察生产日志中 `web_search` 失败后的 LLM 行为

## 修复方案（可选）
建议分阶段：
1. **P2 先行**（D4/D7/D8/D9）：工具错误消息加结构化前缀，
   独立于 D1/D2/D3 的分类架构。每个工具独立修复，可并行。
2. **P3 跟进**（D1/D2/D3）：设计 `ToolResult`/`ToolError` 数据类，
   接入 `error_classifier`，让工具错误驱动规则11的分类重试。
3. **P3 最后**（D12/D13）：在工具执行循环中加入瞬态错误自动重试
   （退避 + 最大重试次数），与 P1-A 的断路器互补。

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-17 | assistant | 登记为待评估，P2 部分（D4-D9）建议优先处理 |
