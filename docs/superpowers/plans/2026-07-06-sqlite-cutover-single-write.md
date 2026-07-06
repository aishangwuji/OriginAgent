# Implementation Plan: SQLite Cutover — Single-Write Phase

## Overview

Remove the dual-write performance tax by making SQLite the primary source of truth for all stores. JSONL becomes an optional, fsync-free cold backup. FileLock is removed when SQLite is active. Each store falls back gracefully to JSONL when SQLite is unavailable.

## Current Architecture (per store)

```
Read:  SQLite first (try/except) -> JSONL fallback
Write: JSONL (atomic, fsync) + SQLite (commit fsync) -> 2 sync disk I/Os
Lock:  FileLock (for JSONL) + SQLite BEGIN IMMEDIATE -> double contention
```

## Target Architecture (per store)

```
Read:  SQLite only (no fallback needed)
Write: SQLite only (single commit fsync)
Lock:  nullcontext() — SQLite WAL handles concurrency
Cold:  Optional JSONL, buffered I/O only (no os.fsync)
```

---

## Phase 0: Shared Infrastructure

### Step 0.1 — `StorageConfig` in `config/schema.py`

Add a Pydantic model with `jsonl_fallback_enabled: bool = True`. Wire into root `Config` object.

### Step 0.2 — `storage/jsonl_fallback.py` (NEW)

Three shared helpers:
1. **`locked(sqlite_store, lock_path)`** — context manager: returns `nullcontext()` when SQLite active, else `FileLock`
2. **`write_jsonl_cold(path, records_fn)`** — writes JSONL with buffered I/O, NO `os.fsync`
3. **`should_write_jsonl(config)`** — returns True only when config flag is set

---

## Phase 1: P0 — FactStore (highest impact)

### Step 1.1 — `facts.py` `FactStore._locked()`

```python
def _locked(self):
    if self._sqlite_facts is not None:
        return contextlib.nullcontext()
    if self._lock_factory is not None:
        return self._lock_factory()
    return FileLock(str(self._lock_file))
```

### Step 1.2 — `facts.py` `FactStore._write_records_unlocked()`

Reorder: SQLite primary → optional JSONL cold (no fsync) → fallback to JSONL fsync on SQLite error.

### Step 1.3 — `facts.py` `FactEventStore` + `FactRelationStore`

Same SQLite-primary pattern. `FactRelationStore` needs new `sqlite_store` constructor param.

---

## Phase 2: P1 — All RMW Stores (6 files)

| Step | File | Class | Key method |
|------|------|-------|-----------|
| 2.1 | `reminders.py` | `ReminderStore` | `upsert()`, `_write_all_unlocked()` |
| 2.2 | `bdi/desire_store.py` | `DesireStore` | `_read_modify_write()`, `_write_all_unlocked()` |
| 2.3 | `bdi/plan_library.py` | `PlanLibrary` | `_save()`, `_load()` |
| 2.4 | `agent/evolution.py` | `OpportunitySignalStore` | `_write_all_unlocked()` |
| 2.5 | `agent/facts.py` | `FactRelationStore` | add `sqlite_store` param + `upsert()` |
| 2.6 | `agent_loop_components.py` | wiring | thread `fact_relations` SQLite store |

Each: `_locked()` → nullcontext; write SQLite first; JSONL optional (no fsync).

---

## Phase 3: P1 — All Append-Only Stores (11 files)

| Step | File | Class |
|------|------|-------|
| 3.1 | `audit.py` | `AuditLogger._log()` |
| 3.2 | `active_intents.py` | `JsonlActiveIntentLedger.append()` |
| 3.3 | `cognitive_audit.py` | `JsonlCognitiveAuditLedger` |
| 3.4 | `meta_cognition_audit.py` | `JsonlMetaCognitionAuditLedger` |
| 3.5 | `thought_substrate_store.py` | `ThoughtSubstrate` |
| 3.6 | `subagent_records.py` | `JsonlSubagentRecordStore` |
| 3.7 | `tools/audit.py` | `JsonlToolAuditSink.record()` |
| 3.8 | `memory/store.py` | `NearlineMemoryStore` |
| 3.9 | `skill_lifecycle.py` | `SkillLifecycleStore` |
| 3.10 | `domain_pack_governance.py` | `DomainPackGovernanceService` |
| 3.11 | `facts.py` | `FactEventStore.append()` |

Each: remove `os.fsync` from JSONL path when SQLite active; reorder so SQLite write comes first.

---

## Phase 4: P2 — Startup Validation + Safety Net

### Step 4.1 — `storage/sqlite_stores.py`

After migration, compare record counts between SQLite and JSONL. Log WARNING if >0.1% divergence.

### Step 4.2 — `agent_loop_components.py`

Thread `StorageConfig` from root config into store factory, so `jsonl_fallback_enabled` is respected.

### Step 4.3 — Guard for missing SQLite

Already handled — existing `try/except` patterns in every read path.

---

## File Change Summary

**New:** `storage/jsonl_fallback.py` + `config/schema.py` (StorageConfig)

**Modified (22 files):**
- Config: `schema.py`
- Infrastructure: `sqlite_stores.py`, `agent_loop_components.py`
- P0: `facts.py`
- P1 RMW: `reminders.py`, `evolution.py`, `audit.py`, `desire_store.py`, `plan_library.py`
- P1 Append: `active_intents.py`, `cognitive_audit.py`, `meta_cognition_audit.py`, `thought_substrate_store.py`, `subagent_records.py`, `tools/audit.py`, `skill_lifecycle.py`, `domain_pack_governance.py`, `memory/store.py`

---

## Risk Assessment

| Risk | Sev | Mitigation |
|------|-----|-----------|
| asyncio + sqlite3 blocking | Med | Short single-row queries; WAL mode; `to_thread` if needed |
| Concurrent Dream contention | Med | WAL + busy_timeout=10000; batch-commit minimizes window |
| Rollback difficulty | Low | `jsonl_fallback_enabled: true` keeps JSONL cold backup |
| Audit hash chain integrity | Low | Hash chain stored in payload_json, storage-agnostic |
| FactRelationStore missing SQLite | High | Step 2.5 explicitly adds it (gap from Phase 2) |

---

## Success Criteria

- [ ] FactStore writes ONLY to SQLite when SQLite active (no JSONL fsync)
- [ ] FactStore `_locked()` returns `nullcontext()` when SQLite active
- [ ] All stores follow same single-write pattern
- [ ] `jsonl_fallback_enabled: false` disables ALL JSONL writes
- [ ] Startup validation logs WARNING on SQLite/JSONL count mismatch
- [ ] All existing tests pass without modification
- [ ] Config flag can toggle behavior without code changes
