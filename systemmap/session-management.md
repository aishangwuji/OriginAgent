# Session Management 会话管理

> **covered_paths**: ["OriginAgent/session/"]
> **last_verified_commit**: (待填充: 执行 `git rev-parse HEAD` 获取)
> **last_verified**: (待填充: 对应时间戳)
> **schema_version**: 1

## 概述

Session Management 负责对话会话的全生命周期管理：创建、持久化、检索、归档、搜索与冷存储。它是 Agent 记忆系统的基础设施层，支撑上层的 Episode 管理、连续性检查点、工作记忆桥接等认知能力。

## 核心实体

### 1. Session (会话)
**文件**: `OriginAgent/session/manager.py:193` (`Session` dataclass)

**属性**:
- `key: str` — 会话键，格式 `channel:chat_id` (如 `telegram:123456`)
- `messages: list[dict]` — 消息列表，每条包含 role、content、timestamp、episode_id 等
- `episodes: list[Episode]` — Episode 列表（主题区块划分）
- `active_episode_index: int` — 当前活跃 Episode 索引
- `created_at / updated_at: datetime` — 创建/更新时间
- `metadata: dict` — 扩展元数据（含 `_episode_summaries`、`_continuity_runtime_identity_v1` 等）
- `last_consolidated: int` — 已归档到冷存储的消息数

**关键方法**:
- `ensure_active_episode()` — 确保存在活跃 Episode，不存在则创建
- `start_new_episode(label, working_memory)` — 关闭当前 Episode，开启新 Episode，可选归档工作记忆快照
- `close_active_episode(working_memory)` — 仅关闭当前 Episode
- `get_history(max_messages, max_tokens, include_timestamps)` — 获取未归档消息用于 LLM 上下文
- `get_hot_history(max_turns, max_tokens, include_timestamps)` — 按用户轮次获取热历史（跨已归档边界）
- `get_episode_history(episode_id, max_tokens, include_timestamps)` — 获取特定 Episode 原始消息
- `add_message(role, content, **kwargs)` — 追加消息，自动打标 episode_id
- `enforce_file_cap(on_archive, limit)` — 消息数超限时触发冷归档 + 修剪
- `retain_recent_legal_suffix(max_messages)` — 保留合法的最近消息后缀（工具调用完整性对齐）

### 2. Episode (对话片段/主题块)
**文件**: `OriginAgent/session/manager.py:180` (`Episode` dataclass)

**属性**:
- `episode_id: str` — UUID
- `label: str` — 人类可读标签
- `started_at / ended_at: datetime` — 开始/结束时间
- `msg_start / msg_end: int` — 在 `messages` 中的切片范围 [start, end)
- `status: str` — `"active" | "closed"`

**状态机**:
```
ACTIVE ──→ CLOSED  (start_new_episode / close_active_episode)
```

**摘要存储**: Episode 关闭时调用 `_store_episode_summary`，写入 `session.metadata["_episode_summaries"]`（最新 5 条），包含：
- preview（首/尾用户消息预览）
- tone（语气分析：terse/inquisitive/emphatic/detailed/casual/brief/neutral）
- key_quotes（最多 3 条关键用户原话）
- working_memory_snapshot（可选：current_goal, current_plan, open_loops, active_constraints）

### 3. SessionManager (会话管理器)
**文件**: `OriginAgent/session/manager.py:992` (`SessionManager` class)

**职责**:
- 会话缓存（内存 `_cache: dict[key, Session]`）
- 磁盘持久化（JSONL 格式，原子写入：tmp + fsync + os.replace + dir-fsync）
- 会话列表查询（含 WebUI transcript 合并）
- 并发控制（per-session `asyncio.Lock`）
- 遗留路径迁移（`~/.OriginAgent/sessions/` → workspace）
- 损坏文件修复（`_repair` 跳过坏行）

**持久化格式** (JSONL):
```
Line 1: {"_type": "metadata", "key": "...", "created_at": "...", "updated_at": "...", "metadata": {...}, "last_consolidated": 0, "episodes": [...], "active_episode_index": 0}
Line 2+: {"role": "user", "content": "...", "timestamp": "...", "episode_id": "..."}
```

**原子写入重试**: Windows `PermissionError` (WinError 5) 最多重试 3 次，指数退避 (10ms, 50ms, 200ms)。

### 4. SessionColdArchiveStore (冷归档存储)
**文件**: `OriginAgent/session/cold_archive.py:31` (`SessionColdArchiveStore` class)

**职责**: 会话消息超出 `FILE_MAX_MESSAGES=2000` 时，将被修剪的前缀追加归档到只读存储。

**存储布局**:
```
workspace/memory/session_cold_archive/
  2026-07.jsonl   # 按月分文件
  2026-08.jsonl
  ...
```

**归档记录结构**:
```json
{
  "schema_version": "originagent.session_cold_archive.v1",
  "archive_id": "sha256_32chars",
  "session_key": "telegram:123456",
  "reason": "session_file_cap",
  "archived_at": "2026-07-19T10:30:00.123456",
  "message_count": 150,
  "messages": [...],
  "content_hash": "sha256:..."
}
```

**并发控制**: `filelock.FileLock` 保护同月文件追加写入。

**完整性**: `content_hash` = `sha256(json_dumps(messages, sort_keys=True))`，用于后续校验。

### 5. SessionSearchIndexService (会话搜索索引)
**文件**: `OriginAgent/session/search_index.py:141` (`SessionSearchIndexService` class)

**职责**: 基于 SQLite FTS5 的全文检索索引（仅存脱敏后的文本），支持 literal/hybrid/semantic 三种模式。

**索引来源** (`SUPPORTED_INDEX_SOURCES`):
- `sessions` — 热会话消息
- `history` — 历史会话
- `webui` — WebUI transcript
- `facts` — FactStore 事实
- `cold` — 冷归档
- `episodes` — Episode 摘要
- `foresights` — 预见
- `agent_cases` — Agent 案例
- `profiles` — 用户画像

**核心组件**:
- `SearchTextNormalizer` — 文本归一化分词（英文标识符拆分、CJK jieba 分词 + n-gram）
- `redact_memory_text` — 脱敏（API Key、Bearer Token、AKIA、密码字段等）
- 增量重建：按 `updated_at` 时间戳增量更新，避免全量重建
- `refresh_index()` / `rebuild_index()` — 手动/自动触发重建

**查询流程**:
1. `query_tokens` → FTS MATCH → 候选行
2. `rapidfuzz` 重排序（可选）
3. 片段生成（240 字符窗口，高亮匹配词）
4. 按 `SOURCE_PRIORITY` 合并去重

### 6. SessionSearchService (会话搜索服务)
**文件**: `OriginAgent/session/search.py:141` (类定义起始)

**职责**: 统一搜索入口，聚合多源（literal 回退 + FTS 索引），支持 `records` / `memory_blocks` 两种返回形态。

## 关键流程

### 消息写入流程
```
用户消息/Assistant响应
    → Session.add_message()
    → Episode.msg_end 自增
    → SessionManager.save() (异步/定期/关闭时)
    → JSONL 原子写入 (tmp → replace → dir-fsync)
```

### 冷归档触发流程
```
Session.enforce_file_cap(limit=2000)
    → _recent_legal_suffix() 保留合法后缀
    → 被修剪前缀 → SessionColdArchiveStore.archive(reason="session_file_cap")
    → 追加写入 月度 JSONL (FileLock + fsync)
    → Session.last_consolidated 前移
```

### Episode 切分流程
```
AgentLoop/外部调用 start_new_episode(label, working_memory)
    → 当前 Episode status=closed, ended_at=now
    → _store_episode_summary() 生成摘要 (含 tone, quotes, 可选 working_memory_snapshot)
    → 创建新 Episode (msg_start=msg_end=当前消息数)
    → 新 Episode 成为 active_episode_index
```

### 搜索查询流程
```
用户查询 "打开灯"
    → SessionSearchService.search(query, mode="hybrid", sources=[...])
    → literal: 直接扫描 JSONL (近 30 天窗口优先)
    → fts: SessionSearchIndexService.query() (SQLite FTS5)
    → 合并去重 → 片段生成 → 返回 SearchResponse
```

## 多租户隔离

- **会话键格式**: `tenant:{tenant_id}:{channel}:{chat_id}` 或 `websocket:{chat_id}`
- **工作区隔离**: `workspace/tenants/{tenant_id}/sessions/` 独立存储
- **SessionManager 实例**: 每租户独立实例（通过 `tenant_workspace()` 获取路径）
- **列表合并**: `list_sessions()` 合并 WebSocket 会话 + WebUI transcript，按 `updated_at` 倒序

## 连续性集成

- **Checkpoint 恢复**: `continuity_runtime_identity_v1` 存储在 `metadata` 中，包含 `user_id`、`device_id`、`updated_at`
- **Episode 桥接**: Episode 关闭时可选归档 `working_memory_snapshot`（Phase 6 耦合修复 B）
- **热历史跨归档**: `get_hot_history()` 遍历全量 `messages`（含已归档部分），按用户轮次计数

## 边界与异常

| 场景 | 处理 |
|------|------|
| JSONL 文件损坏 | `_repair()` 跳过坏行，尽量恢复；失败返回 None 触发新建 |
| Windows 文件锁冲突 | 原子写入重试 3 次 (WinError 5)；目录 fsync 跳过 PermissionError |
| 搜索索引陈旧 | `index_stale` 标记 + 后台增量刷新；`rebuild_on_start` 启动时全量重建 |
| 冷归档并发追加 | `FileLock` 同一文件串行化；不同月份文件天然无冲突 |
| Episode 索引重建 | `_rebuild_episode_indices()` 在消息修剪后调用，钳制 msg_start/msg_end |
| 孤儿 tool_result | `find_legal_message_start()` 向前扩展到上一个 user turn，保证 tool_call/result 配对完整 |

## 关键配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `FILE_MAX_MESSAGES` | 2000 | 单会话文件消息上限，超限触发冷归档 |
| `SESSION_COLD_ARCHIVE_DIR` | `memory/session_cold_archive` | 冷归档相对路径 |
| `SESSION_COLD_ARCHIVE_SCHEMA_VERSION` | `originagent.session_cold_archive.v1` | 归档 schema 版本 |
| `INDEX_DB_RELATIVE_PATH` | `memory/session_search.sqlite3` | FTS 索引数据库路径 |
| `DEFAULT_INDEX_SOURCES` | `["sessions", "history", "webui"]` | 默认索引源 |
| `RECENT_SCAN_WINDOW_DAYS` | 30 | 直接扫描的近期窗口 |
| `MAX_LIMIT` | 50 | 搜索单次返回上限 |

## 已知技术债

| 编号 | 标题 | 优先级 | 状态 |
|------|------|--------|------|
| TD-2026-001 | Strangler Fig 子容器迁移停滞 Phase 0 | P2 | 已确认 |
| TD-2026-010 | 三处静默吞异常影响可观测性 | P0 | 待评估 |

## 陈旧度门槛

本文件涉及的源码路径：`OriginAgent/session/`。当 `git log <last_verified_commit>..HEAD -- OriginAgent/session/` 有新提交时，必须重新核验并更新 `last_verified_commit`。