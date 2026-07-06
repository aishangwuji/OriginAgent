# SQLite 迁移接入实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已有 SQLite store 类接入运行时装配并切换消费者，对尚无 SQLite 实现的存储先写实现再接入，最终消除 JSONL 作为主要持久化存储。

**Architecture:** 现有 SQLite store 类继承 `AppendOnlyMigrator`/`ReadModifyWriteMigrator`，通过 `migrate()` 从存量 JSONL 导入数据。启动时在 `agent_loop_components.py` 中创建实例 + 调用 migrate，然后替换消费者中对 JSONL 的读写为 SQLite 操作。

**Tech Stack:** Python 3.12, sqlite3, aiosqlite (现有基础设施)

---

## Phase 1: 接入已有 SQLite Store

### 当前状态

29 个 SQLite store 类已定义但**未在任何位置 import 或实例化**。对应的 JSONL 消费者仍在使用文件 I/O。SQLite store 分布在：

| 文件 | Stores 数 | 模式 |
|------|-----------|------|
| `agent/tier1_stores_sqlite.py` | 12 | AppendOnlyMigrator |
| `agent/tier2_stores_sqlite.py` | 4 | AppendOnly+RMW+独立 |
| `agent/tier3_stores_sqlite.py` | 3 | ReadModifyWriteMigrator+AppendOnly |
| `agent/meta_cognition_audit_sqlite.py` | 7 | AppendOnlyMigrator |
| `agent/cognitive_audit_sqlite.py` | 2 | AppendOnlyMigrator |
| `agent/subagent_records_sqlite.py` | 3 | AppendOnlyMigrator |
| `agent/active_intents_sqlite.py` | 1 | AppendOnlyMigrator |
| `agent/reminders_sqlite.py` | 1 | AppendOnlyMigrator |
| `agent/domain_pack_events_sqlite.py` | 1 | AppendOnlyMigrator |
| `agent/skill_lifecycle_sqlite.py` | 1 | AppendOnlyMigrator |
| `agent/tools/audit_sqlite.py` | 1 | AppendOnlyMigrator |
| `agent/facts.py` (FactEventsSqlite) | 1 | AppendOnlyMigrator |

### 接入策略

**统一入口**：新建 `OriginAgent/storage/sqlite_stores.py` 作为 SQLite store 工厂，集中管理所有 store 的创建、迁移初始化、生命周期和配置路径。`agent_loop_components.py` 通过工厂获取 store 实例。

**双写策略**：Phase 1 中保留 JSONL 写入不变，SQLite store 只做 `migrate()` + 读操作。此时 SQLite 是"读优化副本"。后续阶段关停 JSONL 写入。

---

### Task 1: 新建 SQLite Store 工厂

**Files:**
- Create: `OriginAgent/storage/sqlite_stores.py`
- Modify: (none yet, consumers wired in later tasks)

**Interfaces:**
- Produces: `SqliteStoreFactory` class with `create_all(workspace) -> SqliteStoreRegistry` and per-store accessors

- [ ] **Step 1: 创建 `OriginAgent/storage/sqlite_stores.py`**

```python
"""Central factory for all SQLite-backed stores.

Creates, migrates, and provides access to every SQLite store.
AgentLoopComponents imports from here instead of instantiating
individual stores inline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from OriginAgent.storage.jsonl_migration import MigrationResult
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect

# ── Tier 1 stores (append-only) ─────────────────────────────────────
from OriginAgent.agent.tier1_stores_sqlite import (
    ThoughtFramesSqlite,
    ThoughtJournalsSqlite,
    CausalEdgesSqlite,
    SimulationTracesSqlite,
    SimulationFeedbackSqlite,
    EvolutionSandboxCacheSqlite,
    EvolutionOutcomesSqlite,
    EvolutionDependenciesSqlite,
    EvolutionHealthHistorySqlite,
    EvolutionTrialLogsSqlite,
    EvolutionConfigPatchesSqlite,
    MetaProgrammingCompilationsSqlite,
)

# ── Tier 2 stores (append-only / RMW) ───────────────────────────────
from OriginAgent.agent.tier2_stores_sqlite import (
    MemoryCandidatesSqlite,
    SchedulerRunsSqlite,
    DeliberationCyclesSqlite,
    IdempotencyKeysSqlite,
)

# ── Tier 3 stores (RMW) ─────────────────────────────────────────────
from OriginAgent.agent.tier3_stores_sqlite import (
    FactRelationStoreSqlite,
    ReviewProposalStoreSqlite,
    ReviewProposalEventStoreSqlite,
)

# ── Specialized stores ──────────────────────────────────────────────
from OriginAgent.agent.meta_cognition_audit_sqlite import (
    MetaCognitionTriggersSqlite,
    MetaCognitionDecisionsSqlite,
    MetaCognitionJournalsSqlite,
    MetaCognitionReflectionsSqlite,
    MetaCognitionConfidenceTracesSqlite,
    MetaCognitionPatternsSqlite,
    MetaCognitionEvolutionSeedsSqlite,
)
from OriginAgent.agent.cognitive_audit_sqlite import (
    CognitiveEventsSqlite,
    CognitiveDecisionsSqlite,
)
from OriginAgent.agent.subagent_records_sqlite import (
    SubagentTasksSqlite,
    SubagentLifecycleSqlite,
    SubagentToolsSqlite,
)
from OriginAgent.agent.active_intents_sqlite import ActiveIntentsSqlite
from OriginAgent.agent.tools.audit_sqlite import ToolAuditSqlite
from OriginAgent.agent.domain_pack_events_sqlite import DomainPackEventsSqlite
from OriginAgent.agent.skill_lifecycle_sqlite import SkillLifecycleSqlite
from OriginAgent.agent.reminders_sqlite import RemindersSqlite
from OriginAgent.agent.facts import FactEventsSqlite

# ── Evolution ledger (already wired, re-export for convenience) ─────
from OriginAgent.evolution.ledger_sqlite import SqliteEvolutionLedger


def _migrate_one(store: Any, label: str) -> None:
    """Run migrate() on *store* and log the result."""
    if not hasattr(store, "migrate"):
        return
    try:
        result: MigrationResult = store.migrate()
        if result.records_imported or result.records_skipped:
            logger.info(
                "SQLite migration [{}]: {} imported, {} skipped{}",
                label,
                result.records_imported,
                result.records_skipped,
                f" ({result.error})" if result.error else "",
            )
    except Exception:
        logger.exception("SQLite migration [{}] failed", label)


@dataclass
class SqliteStoreRegistry:
    """Container for all SQLite store instances."""

    # Tier 1
    thought_frames: ThoughtFramesSqlite
    thought_journals: ThoughtJournalsSqlite
    causal_edges: CausalEdgesSqlite
    simulation_traces: SimulationTracesSqlite
    simulation_feedback: SimulationFeedbackSqlite
    evolution_sandbox_cache: EvolutionSandboxCacheSqlite
    evolution_outcomes: EvolutionOutcomesSqlite
    evolution_dependencies: EvolutionDependenciesSqlite
    evolution_health_history: EvolutionHealthHistorySqlite
    evolution_trial_logs: EvolutionTrialLogsSqlite
    evolution_config_patches: EvolutionConfigPatchesSqlite
    meta_programming_compilations: MetaProgrammingCompilationsSqlite

    # Tier 2
    memory_candidates: MemoryCandidatesSqlite
    scheduler_runs: SchedulerRunsSqlite
    deliberation_cycles: DeliberationCyclesSqlite
    idempotency_keys: IdempotencyKeysSqlite

    # Tier 3
    fact_relations: FactRelationStoreSqlite
    review_proposals: ReviewProposalStoreSqlite
    review_proposal_events: ReviewProposalEventStoreSqlite

    # Meta-cognition audit
    meta_triggers: MetaCognitionTriggersSqlite
    meta_decisions: MetaCognitionDecisionsSqlite
    meta_journals: MetaCognitionJournalsSqlite
    meta_reflections: MetaCognitionReflectionsSqlite
    meta_confidence_traces: MetaCognitionConfidenceTracesSqlite
    meta_patterns: MetaCognitionPatternsSqlite
    meta_evolution_seeds: MetaCognitionEvolutionSeedsSqlite

    # Cognitive audit
    cognitive_events: CognitiveEventsSqlite
    cognitive_decisions: CognitiveDecisionsSqlite

    # Subagent records
    subagent_tasks: SubagentTasksSqlite
    subagent_lifecycle: SubagentLifecycleSqlite
    subagent_tools: SubagentToolsSqlite

    # Misc specialized
    active_intents: ActiveIntentsSqlite
    tool_audit: ToolAuditSqlite
    domain_pack_events: DomainPackEventsSqlite
    skill_lifecycle: SkillLifecycleSqlite
    reminders: RemindersSqlite
    fact_events: FactEventsSqlite


class SqliteStoreFactory:
    """Creates all SQLite stores and runs one-time migration."""

    @staticmethod
    def create_all(workspace: Path) -> SqliteStoreRegistry:
        w = Path(workspace)
        memory_dir = w / "memory"

        # Tier 1
        thought_frames = ThoughtFramesSqlite(w)
        thought_journals = ThoughtJournalsSqlite(w)
        causal_edges = CausalEdgesSqlite(w)
        simulation_traces = SimulationTracesSqlite(w)
        simulation_feedback = SimulationFeedbackSqlite(w)
        evolution_sandbox_cache = EvolutionSandboxCacheSqlite(w)
        evolution_outcomes = EvolutionOutcomesSqlite(w)
        evolution_dependencies = EvolutionDependenciesSqlite(w)
        evolution_health_history = EvolutionHealthHistorySqlite(w)
        evolution_trial_logs = EvolutionTrialLogsSqlite(w)
        evolution_config_patches = EvolutionConfigPatchesSqlite(w)
        meta_programming_compilations = MetaProgrammingCompilationsSqlite(w)

        # Tier 2
        memory_candidates = MemoryCandidatesSqlite(w)
        scheduler_runs = SchedulerRunsSqlite(w)
        deliberation_cycles = DeliberationCyclesSqlite(w)
        idempotency_keys = IdempotencyKeysSqlite(w)

        # Tier 3
        fact_relations = FactRelationStoreSqlite(w)
        review_proposals = ReviewProposalStoreSqlite(w)
        review_proposal_events = ReviewProposalEventStoreSqlite(w)

        # Meta-cognition (7 tables in metacognition_audit directory)
        meta_triggers = MetaCognitionTriggersSqlite(w)
        meta_decisions = MetaCognitionDecisionsSqlite(w)
        meta_journals = MetaCognitionJournalsSqlite(w)
        meta_reflections = MetaCognitionReflectionsSqlite(w)
        meta_confidence_traces = MetaCognitionConfidenceTracesSqlite(w)
        meta_patterns = MetaCognitionPatternsSqlite(w)
        meta_evolution_seeds = MetaCognitionEvolutionSeedsSqlite(w)

        # Cognitive audit
        cognitive_events = CognitiveEventsSqlite(w)
        cognitive_decisions = CognitiveDecisionsSqlite(w)

        # Subagent records
        subagent_tasks = SubagentTasksSqlite(w)
        subagent_lifecycle = SubagentLifecycleSqlite(w)
        subagent_tools = SubagentToolsSqlite(w)

        # Misc specialized
        active_intents = ActiveIntentsSqlite(w)
        tool_audit = ToolAuditSqlite(w)
        domain_pack_events = DomainPackEventsSqlite(w)
        skill_lifecycle = SkillLifecycleSqlite(w)
        reminders = RemindersSqlite(w)
        fact_events = FactEventsSqlite(w)

        registry = SqliteStoreRegistry(
            # Tier 1
            thought_frames=thought_frames,
            thought_journals=thought_journals,
            causal_edges=causal_edges,
            simulation_traces=simulation_traces,
            simulation_feedback=simulation_feedback,
            evolution_sandbox_cache=evolution_sandbox_cache,
            evolution_outcomes=evolution_outcomes,
            evolution_dependencies=evolution_dependencies,
            evolution_health_history=evolution_health_history,
            evolution_trial_logs=evolution_trial_logs,
            evolution_config_patches=evolution_config_patches,
            meta_programming_compilations=meta_programming_compilations,
            # Tier 2
            memory_candidates=memory_candidates,
            scheduler_runs=scheduler_runs,
            deliberation_cycles=deliberation_cycles,
            idempotency_keys=idempotency_keys,
            # Tier 3
            fact_relations=fact_relations,
            review_proposals=review_proposals,
            review_proposal_events=review_proposal_events,
            # Meta-cognition
            meta_triggers=meta_triggers,
            meta_decisions=meta_decisions,
            meta_journals=meta_journals,
            meta_reflections=meta_reflections,
            meta_confidence_traces=meta_confidence_traces,
            meta_patterns=meta_patterns,
            meta_evolution_seeds=meta_evolution_seeds,
            # Cognitive
            cognitive_events=cognitive_events,
            cognitive_decisions=cognitive_decisions,
            # Subagent
            subagent_tasks=subagent_tasks,
            subagent_lifecycle=subagent_lifecycle,
            subagent_tools=subagent_tools,
            # Misc
            active_intents=active_intents,
            tool_audit=tool_audit,
            domain_pack_events=domain_pack_events,
            skill_lifecycle=skill_lifecycle,
            reminders=reminders,
            fact_events=fact_events,
        )

        # Run all migrations (idempotent)
        _migrate_one(thought_frames, "thought_frames")
        _migrate_one(thought_journals, "thought_journals")
        _migrate_one(causal_edges, "causal_edges")
        _migrate_one(simulation_traces, "simulation_traces")
        _migrate_one(simulation_feedback, "simulation_feedback")
        _migrate_one(evolution_sandbox_cache, "evolution_sandbox_cache")
        _migrate_one(evolution_outcomes, "evolution_outcomes")
        _migrate_one(evolution_dependencies, "evolution_dependencies")
        _migrate_one(evolution_health_history, "evolution_health_history")
        _migrate_one(evolution_trial_logs, "evolution_trial_logs")
        _migrate_one(evolution_config_patches, "evolution_config_patches")
        _migrate_one(meta_programming_compilations, "meta_programming_compilations")
        _migrate_one(memory_candidates, "memory_candidates")
        _migrate_one(scheduler_runs, "scheduler_runs")
        _migrate_one(deliberation_cycles, "deliberation_cycles")
        _migrate_one(idempotency_keys, "idempotency_keys")
        _migrate_one(fact_relations, "fact_relations")
        _migrate_one(review_proposals, "review_proposals")
        _migrate_one(review_proposal_events, "review_proposal_events")
        _migrate_one(meta_triggers, "meta_triggers")
        _migrate_one(meta_decisions, "meta_decisions")
        _migrate_one(meta_journals, "meta_journals")
        _migrate_one(meta_reflections, "meta_reflections")
        _migrate_one(meta_confidence_traces, "meta_confidence_traces")
        _migrate_one(meta_patterns, "meta_patterns")
        _migrate_one(meta_evolution_seeds, "meta_evolution_seeds")
        _migrate_one(cognitive_events, "cognitive_events")
        _migrate_one(cognitive_decisions, "cognitive_decisions")
        _migrate_one(subagent_tasks, "subagent_tasks")
        _migrate_one(subagent_lifecycle, "subagent_lifecycle")
        _migrate_one(subagent_tools, "subagent_tools")
        _migrate_one(active_intents, "active_intents")
        _migrate_one(tool_audit, "tool_audit")
        _migrate_one(domain_pack_events, "domain_pack_events")
        _migrate_one(skill_lifecycle, "skill_lifecycle")
        _migrate_one(reminders, "reminders")
        _migrate_one(fact_events, "fact_events")

        return registry


__all__ = ["SqliteStoreFactory", "SqliteStoreRegistry"]
```

- [ ] **Step 2: 运行 ruff 检查**

```bash
uv run ruff check OriginAgent/storage/sqlite_stores.py
```
Expected: All checks passed (imports verified)

- [ ] **Step 3: 运行一次性测试确认所有 import 正常**

```bash
uv run python -c "from OriginAgent.storage.sqlite_stores import SqliteStoreFactory; print('OK')"
```
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/storage/sqlite_stores.py
git commit -m "feat(storage): add SqliteStoreFactory as central SQLite store factory"
```

---

### Task 2: Wire 工厂到 AgentLoop 启动装配

**Files:**
- Modify: `OriginAgent/agent/agent_loop_components.py:43-53` (import + Step 2 section)

- [ ] **Step 1: 在 `agent_loop_components.py` 中添加 import**

在第 43 行后添加 import（在末尾、`__all__` 之前）：

```python
from OriginAgent.storage.sqlite_stores import SqliteStoreFactory
```

- [ ] **Step 2: Step 2 段落后添加 SQLite store 初始化**

在 `values["_reminder_store"] = ReminderStore(workspace)` 之后（第 404 行后）添加：

```python
    # ── SQLite stores (migrate from JSONL) ─────────────────────────
    values["_sqlite_stores"] = SqliteStoreFactory.create_all(workspace)
```

- [ ] **Step 3: 验证 ruff 通过**

```bash
uv run ruff check OriginAgent/agent/agent_loop_components.py
```
Expected: All checks passed

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/agent_loop_components.py
git commit -m "feat: wire SqliteStoreFactory into agent loop startup assembly"
```

---

### Task 3: 将 SQLite Stores 注入 RuntimeDependencies

**Files:**
- Modify: `OriginAgent/agent/agent_runtime.py:207-215` (add `sqlite_stores` field)

- [ ] **Step 1: 在 `RuntimeDependencies` 中添加字段**

在 `# Misc` 区域（`turn_orchestrator` 字段之后）添加：

```python
    # SQLite stores
    sqlite_stores: Any = None  # SqliteStoreRegistry
```

- [ ] **Step 2: 在 `agent_loop.py` 中注入到 dependencies**

在 `AgentLoop._init_runtime_delegation()` 或对应位置：

```python
RuntimeDependencies(
    ...
    sqlite_stores=values.get("_sqlite_stores"),
    ...
)
```

- [ ] **Step 3: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/agent_loop.py
git commit -m "feat: inject SqliteStoreRegistry into RuntimeDependencies"
```

---

### Task 4: 切换 ThoughtSubstrate 使用 SQLite

**Files:**
- Modify: `OriginAgent/agent/thought_substrate_store.py:30-185`
- Test: `tests/agent/test_thought_substrate.py`

**Interfaces:**
- Consumes: `SqliteStoreRegistry.thought_frames`, `.thought_journals`

**当前状态：** `ThoughtSubstrate` 直接读写 `frames.jsonl` / `journals.jsonl`。SQLite 版本 `ThoughtFramesSqlite`/`ThoughtJournalsSqlite` 已有带索引的查询。

- [ ] **Step 1: 添加 SQLite 可选参数**

```python
class ThoughtSubstrate:
    def __init__(
        self,
        workspace: Path,
        *,
        max_frames_per_session: int = 500,
        sampling_rate: float = 1.0,
        audit: Any = None,
        sqlite_frames: Any = None,      # ThoughtFramesSqlite | None
        sqlite_journals: Any = None,     # ThoughtJournalsSqlite | None
    ) -> None:
        ...
        self._sqlite_frames = sqlite_frames
        self._sqlite_journals = sqlite_journals
```

- [ ] **Step 2: 读方法优先走 SQLite**

```python
def recent_frames(self, *, limit: int = 200) -> list[dict[str, Any]]:
    if self._sqlite_frames is not None:
        return self._sqlite_frames.recent(limit=limit)
    # fallback to existing JSONL read logic
    ...
```

对 `recent_journals()`、`by_session()` 做同样处理。

- [ ] **Step 3: 写方法双写 JSONL + SQLite**

```python
def _write_frame(self, frame: dict) -> None:
    # existing JSONL append logic...
    if self._sqlite_frames is not None:
        self._sqlite_frames.append(frame)  # 需要 add append method
```

- [ ] **Step 4: 在 `agent_loop_components.py` 中传入 SQLite store**

```python
_thought_substrate = ThoughtSubstrate(
    workspace=workspace,
    max_frames_per_session=...,
    sampling_rate=...,
    audit=values["_meta_cognition_audit"],
    sqlite_frames=values["_sqlite_stores"].thought_frames,
    sqlite_journals=values["_sqlite_stores"].thought_journals,
)
```

- [ ] **Step 5: 运行测试**

```bash
uv run pytest tests/agent/test_thought_substrate.py -v --basetemp=C:\Users\15216\AppData\Local\Temp\pytest-sqlite
```
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/agent/thought_substrate_store.py OriginAgent/agent/agent_loop_components.py
git commit -m "feat: wire ThoughtSubstrate to SQLite frames/journals with dual-write"
```

---

### Task 5: 切换 MetaCognitionAuditLedger 使用 SQLite

**Files:**
- Modify: `OriginAgent/agent/meta_cognition_audit.py`
- Modify: `OriginAgent/agent/agent_loop_components.py:566`

- [ ] **Step 1: 修改 `JsonlMetaCognitionAuditLedger` 支持 SQLite 后备**

```python
class JsonlMetaCognitionAuditLedger:
    def __init__(
        self,
        workspace: Path,
        *,
        sqlite_stores: Any = None,  # {triggers,decisions,journals,...}
    ):
        ...
        self._sqlite = sqlite_stores
```

读方法（`recent_triggers()`, `recent_reflections()` 等）优先查询 SQLite：

```python
def recent_journals(self, *, limit: int = 200) -> list[dict[str, Any]]:
    if self._sqlite and self._sqlite.meta_journals:
        return self._sqlite.meta_journals.recent(limit=limit)
    return self._read_jsonl(self._journals_path, limit=limit)
```

写方法（`append_journal()`, `append_reflection()` 等）双写 JSONL + SQLite。

- [ ] **Step 2: 在 `agent_loop_components.py` 中传入**

```python
values["_meta_cognition_audit"] = JsonlMetaCognitionAuditLedger(
    workspace,
    sqlite_stores=values["_sqlite_stores"],
)
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/agent/test_meta_cognition_*.py -v --basetemp=C:\Users\15216\AppData\Local\Temp\pytest-sqlite
```
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/meta_cognition_audit.py OriginAgent/agent/agent_loop_components.py
git commit -m "feat: wire MetaCognitionAuditLedger to SQLite with dual-write"
```

---

### Task 6: 切换 CognitiveAuditLedger 使用 SQLite

**Files:**
- Modify: `OriginAgent/agent/cognitive_audit.py`
- Modify: `OriginAgent/agent/agent_loop_components.py:555`

模式与 Task 5 相同：读优先 SQLite，写双写。传入 `SqliteStoreRegistry` 中的 `cognitive_events` / `cognitive_decisions`。

- [ ] **Step 1: 修改 `JsonlCognitiveAuditLedger`**

```python
class JsonlCognitiveAuditLedger:
    def __init__(
        self,
        workspace: Path,
        *,
        sqlite_events: Any = None,
        sqlite_decisions: Any = None,
    ):
```

- [ ] **Step 2: 在 `agent_loop_components.py` 中传入**

```python
values["_cognitive_audit"] = JsonlCognitiveAuditLedger(
    workspace,
    sqlite_events=values["_sqlite_stores"].cognitive_events,
    sqlite_decisions=values["_sqlite_stores"].cognitive_decisions,
)
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/agent/test_cognitive_*.py -v --basetemp=C:\Users\15216\AppData\Local\Temp\pytest-sqlite
```

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/cognitive_audit.py OriginAgent/agent/agent_loop_components.py
git commit -m "feat: wire CognitiveAuditLedger to SQLite with dual-write"
```

---

### Task 7: 切换 ToolAuditSink 使用 SQLite

**Files:**
- Modify: `OriginAgent/agent/tools/audit.py`
- Modify: `OriginAgent/agent/agent_loop_components.py:418`

- [ ] **Step 1: 修改 `JsonlToolAuditSink` 支持 SQLite 写入**

```python
class JsonlToolAuditSink:
    def __init__(self, workspace: Path, *, sqlite_audit: Any = None):
        ...
        self._sqlite = sqlite_audit
    
    def write(self, entry: dict) -> None:
        # existing JSONL append...
        if self._sqlite is not None:
            self._sqlite.append(entry)
```

- [ ] **Step 2: 在 `agent_loop_components.py` 中传入**

```python
sink = JsonlToolAuditSink(
    workspace,
    sqlite_audit=values["_sqlite_stores"].tool_audit,
)
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/agent/tools/test_audit* -v --basetemp=C:\Users\15216\AppData\Local\Temp\pytest-sqlite
```

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/tools/audit.py OriginAgent/agent/agent_loop_components.py
git commit -m "feat: wire ToolAuditSink to SQLite with dual-write"
```

---

### Task 8: 切换 ReminderStore 使用 SQLite

**Files:**
- Modify: `OriginAgent/agent/reminders.py`
- Modify: `OriginAgent/agent/agent_loop_components.py:404`

- [ ] **Step 1: 修改 `ReminderStore`**

```python
class ReminderStore:
    def __init__(self, workspace: Path, *, sqlite_store: Any = None):
        ...
        self._sqlite = sqlite_store
```

读方法优先 SQLite，写方法双写。

- [ ] **Step 2: 在 `agent_loop_components.py` 中传入**

```python
values["_reminder_store"] = ReminderStore(
    workspace,
    sqlite_store=values["_sqlite_stores"].reminders,
)
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/agent/test_reminders*.py -v --basetemp=C:\Users\15216\AppData\Local\Temp\pytest-sqlite
```

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/reminders.py OriginAgent/agent/agent_loop_components.py
git commit -m "feat: wire ReminderStore to SQLite with dual-write"
```

---

### Task 9: 切换 ActiveIntentsService 使用 SQLite

**Files:**
- Modify: `OriginAgent/agent/active_intents.py`
- Modify: `OriginAgent/agent/agent_loop_components.py:556`

- [ ] **Step 1: 修改 `ActiveIntentService`**

```python
class ActiveIntentService:
    def __init__(self, ..., sqlite_store: Any = None):
        ...
```

- [ ] **Step 2: 在 `agent_loop_components.py` 中传入**

- [ ] **Step 3: 运行测试并提交**

---

### Task 10: 其他剩余 Consumer 切换

**Files:**
- Modify: `OriginAgent/agent/subagent_records.py` + sqlite params
- Modify: `OriginAgent/agent/domain_pack_governance.py` + sqlite params
- Modify: `OriginAgent/agent/skill_lifecycle.py` + sqlite params
- Modify: `OriginAgent/agent/facts.py` (fact_events, fact_relations)

每个消费者模式相同：构造函数加 `sqlite_*` 可选参数，读优先查 SQLite，写双写。在 `agent_loop_components.py` 中传入对应 store。

- [ ] **Step 1-3: 逐一切换并测试提交**

---

## Phase 2: 为尚缺 SQLite 实现的存储编写 + 接入

### Task 11: FactStore SQLite

**Files:**
- Create: `OriginAgent/agent/facts_sqlite.py`
- Modify: `OriginAgent/storage/sqlite_stores.py` (import + create)
- Modify: `OriginAgent/agent/facts.py` (consumer switch)

- [ ] **Step 1: 写 `FactStoreSqlite`**

```python
"""SQLite-backed FactStore — replaces memory/facts.jsonl.

FactStore is a current-state store (RMW), not append-only.
Schema: facts table with upsert by fact_id.
"""

class FactStoreSqlite(ReadModifyWriteMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS facts (
            fact_id         TEXT PRIMARY KEY,
            session_key     TEXT NOT NULL DEFAULT '',
            content         TEXT NOT NULL DEFAULT '',
            fact_type       TEXT NOT NULL DEFAULT 'observation',
            confidence      REAL NOT NULL DEFAULT 0.5,
            scope           TEXT NOT NULL DEFAULT 'session',
            owner_id        TEXT NOT NULL DEFAULT '',
            source          TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
            metadata_json   TEXT NOT NULL DEFAULT '{}',
            payload_json    TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_facts_session ON facts(session_key, created_at);
        CREATE INDEX IF NOT EXISTS idx_facts_owner ON facts(owner_id, scope);
        CREATE INDEX IF NOT EXISTS idx_facts_type ON facts(fact_type);
        CREATE INDEX IF NOT EXISTS idx_facts_scope ON facts(scope, owner_id);
    """
    ...
```

- [ ] **Step 2: 实现完整查询 API**

```python
def read_all(self) -> list[dict]:
def by_session(self, session_key: str, *, limit=200) -> list[dict]:
def by_scope(self, scope: str, owner_id: str | None = None) -> list[dict]:
def get(self, fact_id: str) -> dict | None:
def upsert(self, fact: dict) -> dict:
def delete(self, fact_id: str) -> bool:
def search(self, query: str, *, limit=20) -> list[dict]:
def count(self) -> int:
```

- [ ] **Step 3: 注册到 `SqliteStoreFactory.create_all()`**

- [ ] **Step 4: 切换 `FactStore` 使用 SQLite（同 Task 4-8 模式）**

- [ ] **Step 5: 运行现有 fact 测试**

```bash
uv run pytest tests/agent/test_fact_store.py -v --basetemp=C:\Users\15216\AppData\Local\Temp\pytest-sqlite
```

- [ ] **Step 6: Commit**

```bash
git commit -m "feat: add FactStoreSqlite and wire into runtime"
```

---

### Task 12: BDI DesireStore + PlanLibrary SQLite

**Files:**
- Create: `OriginAgent/bdi/desire_store_sqlite.py`
- Create: `OriginAgent/bdi/plan_library_sqlite.py`
- Modify: `OriginAgent/storage/sqlite_stores.py`
- Modify: `OriginAgent/bdi/desire_store.py` (consumer switch)
- Modify: `OriginAgent/bdi/plan_library.py` (consumer switch)

- [ ] **Step 1: 写 `DesireStoreSqlite`**

```python
class DesireStoreSqlite(ReadModifyWriteMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS desires (
            desire_id   TEXT PRIMARY KEY,
            session_key TEXT NOT NULL DEFAULT '',
            desire_type TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'pending',
            priority    INTEGER NOT NULL DEFAULT 0,
            content     TEXT NOT NULL DEFAULT '',
            source      TEXT NOT NULL DEFAULT '',
            context_json TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        ...
    """
```

- [ ] **Step 2: 写 `PlanLibrarySqlite`**

```python
class PlanLibrarySqlite(ReadModifyWriteMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS plans (
            plan_id      TEXT PRIMARY KEY,
            desire_type  TEXT NOT NULL DEFAULT '',
            name         TEXT NOT NULL DEFAULT '',
            description  TEXT NOT NULL DEFAULT '',
            steps_json   TEXT NOT NULL DEFAULT '[]',
            is_active    INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            fail_count   INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        ...
    """
```

- [ ] **Step 3: 注册到工厂 + 切换消费者**

- [ ] **Step 4: Commit**

---

### Task 13: MemoryStore 核心 SQLite

**Files:**
- Create: `OriginAgent/memory/store_sqlite.py`
- Modify: `OriginAgent/storage/sqlite_stores.py`
- Modify: `OriginAgent/memory/store.py` (consumer switch)

覆盖 6 个 JSONL 文件：memcells, episodes, foresights, agent_cases, profiles, events

- [ ] **Step 1: 写 `MemoryStoreSqlite`**

```python
class MemoryStoreSqlite:
    """Single SQLite DB replacing 6 JSONL files in memory/store.py."""
    
    DDL = """
        CREATE TABLE IF NOT EXISTS memory_records (
            record_id   TEXT PRIMARY KEY,
            kind        TEXT NOT NULL,  -- memcell|episode|foresight|agent_case|profile|event
            session_key TEXT NOT NULL DEFAULT '',
            scope       TEXT NOT NULL DEFAULT 'user',
            owner_id    TEXT NOT NULL DEFAULT '',
            content     TEXT NOT NULL DEFAULT '',
            summary     TEXT NOT NULL DEFAULT '',
            confidence  REAL NOT NULL DEFAULT 0.5,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            payload_json  TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_mr_kind ON memory_records(kind, created_at);
        CREATE INDEX IF NOT EXISTS idx_mr_session ON memory_records(session_key, kind);
        ...
    """
```

- [ ] **Step 2: 注册到工厂 + 切换**

- [ ] **Step 3: Commit**

---

### Task 14: Audit 存储 + OpportunitySignalStore SQLite

**Files:**
- Create: `OriginAgent/agent/audit_sqlite.py`
- Create: `OriginAgent/agent/evolution_signals_sqlite.py`
- Modify: `OriginAgent/storage/sqlite_stores.py`
- Modify: `OriginAgent/agent/audit.py` (consumer)
- Modify: `OriginAgent/agent/evolution.py` (consumer)

- [ ] **Step 1: `AuditStoreSqlite` — 替换 action_decisions / confirmation_events / permission_decisions 三个 JSONL**

- [ ] **Step 2: `OpportunitySignalStoreSqlite` — 替换 opportunity_signals.jsonl**

- [ ] **Step 3: 注册 + 切换 + 提交**

---

## 测试策略

| 测试范围 | 方式 | 时机 |
|----------|------|------|
| SQLite store 单元测试 | `tests/storage/test_tier*_stores_sqlite.py` | 每次提交前 |
| 消费者切换测试 | 各 consumer 的已有测试 + `--basetemp` | Task 切换后 |
| 迁移幂等性 | 调用两次 `migrate()` 验证不报错 | Task 1 完成后 |
| 双写正确性 | 写 JSONL → 读 SQLite 验证一致 | 各 Task |
| ruff 检查 | `ruff check` | 每次提交前 |

---

## 风险和缓解

| 风险 | 缓解 |
|------|------|
| 双写导致性能下降 | SQLite 写入在独立连接中，WAL 模式，异步不阻塞主循环 |
| 存量 JSONL 数据不一致 | `migrate()` 幂等，每次启动都自动运行 |
| FactStore 切换影响面大 | 保留 JSONL 读取 fallback，逐步切换 |
| BDI DesireStore/PlanLibrary 写路径复杂 | 用 `ReadModifyWriteMigrator` 基类，保持事务原子性 |

---

## 第一响应任务

如果先执行 Phase 1，建议 Task 1 → Task 2 → Task 4 → Task 5 → Task 6 → Task 7 → Task 8 的顺序，因为前面 tasks 提供基础设施，后面 tasks 逐个切换消费者、每次可验证。

---

Plan complete and saved to `docs/superpowers/plans/2026-07-06-sqlite-migration-wiring.md`.

**执行方式选择：**

1. **Subagent-Driven（推荐）** - 每个 Task 派发独立 subagent，task 间 review，快速迭代
2. **Inline Execution** - 在当前会话中逐 task 执行

选哪种？
