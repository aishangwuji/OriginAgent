"""BDI Deliberation Engine — continuous Belief→Desire→Intention reasoning loop."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.models import (
    BDICycleRecord,
    DeliberationIntention,
    DeliberationResult,
    Desire,
    DesirePriority,
    DesireStatus,
    IntentionStack,
    StackFrame,
    now_iso,
)
from OriginAgent.bdi.plan_library import PlanLibrary
from OriginAgent.bdi.world_state_watcher import WorldStateWatcher
from OriginAgent.utils.helpers import ensure_dir

_DELIBERATION_SYSTEM_PROMPT = """You are the BDI Deliberation Engine of OriginAgent.
Your job is to evaluate the agent's active desires and decide what to do next.

You have access to:
- **Desires**: active goals/commitments the agent has made to its user
- **Beliefs**: current facts, world state, user profile, recent episodes
- **Suspended Intentions**: previously interrupted tasks that may be resumable

For each desire, decide one of:
1. **Form an intention** — create a concrete action to advance the desire
2. **Mark satisfied** — the desire is done
3. **Suspend** — the desire is blocked (e.g., waiting for a dependency, interrupted by higher priority)
4. **Cancel** — the desire is no longer relevant
5. **Skip** — no action needed right now

Rules:
- A desire with an approaching or past deadline gets elevated priority.
- If forming an intention, choose the most appropriate action type.
- If you have no active desires, that's OK — respond with empty intentions.
- Be specific about WHY you made each decision.

Available action types and their payloads:
- send_message: {"text": "<message>", "channel": "<channel_name>"} — notify user
- exec: {"command": "<shell command>"} — run a shell command
- web_search: {"query": "<search query>"} — search the web
- web_fetch: {"url": "<url>"} — fetch a URL
- system: {"action": "<custom action>", "params": {}} — system-level action
"""

_DELIBERATION_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "deliberate",
            "description": "Report deliberation decision after evaluating active desires.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Step-by-step reasoning about which desires to act on and why.",
                    },
                    "intentions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "desire_id": {"type": "string"},
                                "action": {"type": "string"},
                                "scope": {"type": "string"},
                                "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                                "reasoning": {"type": "string"},
                                "payload": {"type": "object"},
                            },
                            "required": ["desire_id", "action", "scope", "reasoning"],
                        },
                    },
                    "desires_to_satisfy": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Desire IDs that are complete",
                    },
                    "desires_to_suspend": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Desire IDs to pause (will be pushed onto IntentionStack)",
                    },
                    "desires_to_cancel": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Desire IDs that are no longer relevant",
                    },
                    "next_check_at": {
                        "type": ["string", "null"],
                        "description": "ISO timestamp for when to check again (null = default interval)",
                    },
                },
                "required": ["reasoning", "intentions", "desires_to_satisfy",
                            "desires_to_suspend", "desires_to_cancel"],
            },
        },
    }
]


class DeliberationEngine:
    """Continuous BDI deliberation loop with full BDI-native primitives.

    Integrates three BDI core mechanisms:

    1. **IntentionStack** — nested suspend/resume. When a desire is suspended,
       the current intention is pushed onto the stack. When the interrupt
       clears, the stack pops and the desire resumes as ACTIVE.

    2. **WorldStateWatcher** — event-driven reactivity. Subscribes to the
       event bus for critical belief changes (smoke alarm, door open) and
       triggers immediate reconsideration via ``trigger_now()``.

    3. **PlanLibrary** — cached means-ends reasoning. Desires matching known
       plans skip the LLM call entirely. Plans are auto-learned from
       successful LLM deliberations.

    Usage::

        engine = DeliberationEngine(
            workspace=Path("./workspace"),
            store=desire_store,
            provider=llm_provider,
            model="anthropic/claude-sonnet-4-6",
        )
        await engine.start()
        # ... agent runs ...
        engine.stop()
    """

    def __init__(
        self,
        *,
        workspace: Path,
        store: DesireStore,
        provider: Any,                    # LLMProvider
        model: str,
        enabled: bool = True,
        interval_s: int = 120,
        max_desires_per_cycle: int = 10,
        auto_create_from_foresight: bool = True,
        on_intention: Any | None = None,   # Callable[[DeliberationIntention], Awaitable[None]]
        event_bus: Any | None = None,      # MessageBus for WorldStateWatcher
        shared_space: Any = None,          # SharedSpace | None — cross-tenant shared state
        sqlite_stores: Any = None,
        cron_bridge: Any = None,            # CronDesireBridge | None
    ) -> None:
        self.workspace = Path(workspace)
        self._store = store
        self._provider = provider
        self._model = model
        self._enabled = enabled
        self._interval_s = interval_s
        self._max_desires_per_cycle = max_desires_per_cycle
        self._auto_create_from_foresight = auto_create_from_foresight
        self._on_intention = on_intention
        self._on_cycle_complete = None  # Optional callback: Callable[[DeliberationResult], Awaitable[None]]

        self._running = False
        self._task: asyncio.Task | None = None
        self._audit_dir = self.workspace / "memory" / "bdi"
        self._audit_path = self._audit_dir / "cycles.jsonl"
        ensure_dir(self._audit_dir)

        # ── BDI-native primitives ──────────────────────────────────────
        self._intention_stack = IntentionStack(max_depth=10)
        self._plan_library = PlanLibrary(
            self.workspace,
            sqlite_store=sqlite_stores.plans if sqlite_stores else None,
            jsonl_fallback_enabled=False,
        )
        self._shared_space = shared_space
        self._cron_bridge = cron_bridge

        self._watcher = WorldStateWatcher(
            engine=self,
            event_bus=event_bus,
            cooldown_s=5.0,
            enabled=enabled,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self._enabled:
            logger.info("BDI: DeliberationEngine disabled")
            return
        if self._running:
            logger.warning("BDI: DeliberationEngine already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        await self._watcher.start()
        logger.info("BDI: DeliberationEngine started (every {}s, watcher={})",
                     self._interval_s, self._watcher._enabled)

    def stop(self) -> None:
        self._watcher.stop()
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("BDI: DeliberationEngine stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval_s)
                if self._running:
                    await self.run_cycle()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("BDI: deliberation cycle error")

    # ------------------------------------------------------------------
    # Core cycle
    # ------------------------------------------------------------------

    async def run_cycle(self) -> DeliberationResult:
        """Execute one full BDI deliberation cycle.

        0. Check IntentionStack for resumable suspended desires
        1. Collect active desires
        2. Auto-sync foresights → desires
        3. Prioritize
        4. Split: PlanLibrary cache hits vs LLM-needed
        5. LLM deliberation for unmatched desires
        6. Apply status transitions (with IntentionStack push/pop)
        7. Learn new plans from LLM results
        8. Emit intentions for execution
        """
        cycle_id = f"bdi_{uuid.uuid4().hex[:12]}"
        started_at = now_iso()

        if not self._enabled:
            result = DeliberationResult(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=now_iso(),
                desires_evaluated=0,
                reasoning="Engine disabled.",
            )
            self._persist_cycle(cycle_id, started_at, result, "skipped")
            return result

        # 0. Check for resumable suspended intentions
        await self._check_resumptions()

        # 1. Optionally auto-create desires from foresight records
        # MUST run before the empty-desires check — otherwise a cold start
        # with foresights but no desires will never create its first desire.
        if self._auto_create_from_foresight:
            await self._sync_foresights([])

        # 2. Collect active desires
        desires = self._store.list_deliberable()
        if not desires:
            result = DeliberationResult(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=now_iso(),
                desires_evaluated=0,
                reasoning="No active desires to evaluate.",
            )
            self._persist_cycle(cycle_id, started_at, result, "skipped")
            return result

        desires_before = len(desires)

        # 3. Re-sync foresights (now with dedup against existing desires)
        if self._auto_create_from_foresight:
            await self._sync_foresights(desires)
            desires = self._store.list_deliberable()

        # 3. Cap to max per cycle, prioritize by priority and deadline
        desires = self._prioritize(desires)[:self._max_desires_per_cycle]

        # ── 4. PlanLibrary: split cache hits from LLM-needed ──────────
        cached_intentions: list[DeliberationIntention] = []
        llm_desires: list[Desire] = []

        for desire in desires:
            match = self._plan_library.match(desire)
            if match is not None:
                intent = DeliberationIntention(
                    desire_id=desire.desire_id,
                    action=match.plan.action,
                    scope=match.plan.scope,
                    trigger="deliberation:plan_cache",
                    risk="low",
                    reasoning=f"Plan cache: {match.reasoning}",
                    payload=dict(match.plan.payload_template),
                )
                cached_intentions.append(intent)
                logger.debug("BDI: plan cache hit — desire={} plan={} confidence={:.2f}",
                             desire.desire_id, match.plan.plan_id, match.confidence)
            else:
                llm_desires.append(desire)

        # ── 4.5 Auto-override failing cron jobs (Phase 4) ─────────────
        if self._cron_bridge is not None and desires:
            cron_beliefs = self._gather_beliefs().get("cron", {})
            for fj in cron_beliefs.get("failing_jobs", []):
                if fj.get("consecutive_failures", 0) >= 3:
                    cid = fj.get("cron_job_id", "")
                    did = fj.get("desire_id", "")
                    for d in desires:
                        if d.desire_id == did:
                            logger.info(
                                "BDI: auto-override cron {} (desire={}, {} failures)",
                                cid, did, fj.get("consecutive_failures"),
                            )
                            self._cron_bridge.disable_cron_job(cid)
                            cached_intentions.append(DeliberationIntention(
                                desire_id=did,
                                action="send_message",
                                scope="system",
                                trigger="deliberation:cron_override",
                                risk="low",
                                reasoning=f"Cron {cid} failed {fj.get('consecutive_failures')} times; BDI overriding.",
                                payload={"text": d.content, "alternative_to_cron": True},
                            ))
                            break

        # ── 5. LLM deliberation for unmatched desires ──────────────────
        llm_intentions: list[DeliberationIntention] = []
        reasoning = ""
        updated_ids: list[str] = []

        if llm_desires:
            beliefs = self._gather_beliefs()
            # Include suspended stack state in the prompt
            if not self._intention_stack.is_empty:
                beliefs["suspended_intentions"] = [
                    c.desire_id for c in self._intention_stack.list_resumable()
                ]
            user_prompt = self._build_prompt(llm_desires, beliefs)

            try:
                response = await self._provider.chat_with_retry(
                    messages=[
                        {"role": "system", "content": _DELIBERATION_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    tools=_DELIBERATION_TOOL,
                    model=self._model,
                )
            except Exception as exc:
                result = DeliberationResult(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=now_iso(),
                    desires_evaluated=len(llm_desires) + len(cached_intentions),
                    reasoning="",
                    error=str(exc),
                )
                self._persist_cycle(cycle_id, started_at, result, "error")
                return result

            if not response.should_execute_tools or not response.has_tool_calls:
                result = DeliberationResult(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=now_iso(),
                    desires_evaluated=len(llm_desires) + len(cached_intentions),
                    reasoning="LLM did not produce tool calls.",
                )
                self._persist_cycle(cycle_id, started_at, result, "completed")
                return result

            args = response.tool_calls[0].arguments
            reasoning = args.get("reasoning", "")

            # Parse LLM intentions
            for raw in args.get("intentions", []):
                try:
                    intent = DeliberationIntention(
                        desire_id=raw["desire_id"],
                        action=raw["action"],
                        scope=raw.get("scope", "system"),
                        trigger="deliberation",
                        risk=raw.get("risk", "low"),
                        reasoning=raw.get("reasoning", ""),
                        payload=raw.get("payload", {}),
                    )
                    llm_intentions.append(intent)
                except KeyError:
                    logger.warning("BDI: skipping malformed intention: {}", raw)

            # ── 6. Apply status transitions ────────────────────────────
            for did in args.get("desires_to_satisfy", []):
                try:
                    updated = self._store.update(did, status=DesireStatus.SATISFIED,
                                                  reasoning=reasoning)
                    if updated:
                        updated_ids.append(did)
                        # Pop from intention stack if it was suspended
                        self._intention_stack.pop()
                except ValueError:
                    logger.warning("BDI: invalid transition for desire {} (SATISFIED)", did)

            for did in args.get("desires_to_suspend", []):
                try:
                    # Find the matching intention to push onto stack
                    matching = next((i for i in llm_intentions if i.desire_id == did), None)
                    updated = self._store.update(did, status=DesireStatus.SUSPENDED,
                                                  reasoning=reasoning)
                    if updated:
                        updated_ids.append(did)
                        if matching:
                            frame = StackFrame(
                                desire_id=did,
                                intention=matching,
                                suspended_at=now_iso(),
                                suspend_reason=reasoning[:200],
                                original_priority=updated.priority.value,
                            )
                            self._intention_stack.push(frame)
                            logger.info("BDI: pushed desire {} to intention stack (depth={})",
                                        did, self._intention_stack.depth)
                except (ValueError, OverflowError) as e:
                    logger.warning("BDI: suspend failed for desire {} — {}", did, e)

            for did in args.get("desires_to_cancel", []):
                try:
                    updated = self._store.update(did, status=DesireStatus.CANCELLED,
                                                  reasoning=reasoning)
                    if updated:
                        updated_ids.append(did)
                except ValueError:
                    logger.warning("BDI: invalid transition for desire {} (CANCELLED)", did)

            # ── 7. Learn new plans from LLM-generated intentions ───────
            for desire in llm_desires:
                for intent in llm_intentions:
                    if intent.desire_id == desire.desire_id:
                        try:
                            self._plan_library.learn(desire=desire, intention=intent)
                        except Exception:
                            logger.debug("BDI: plan library learn skipped for {}", desire.desire_id)

        # ── 8. Merge cached + LLM intentions, emit ─────────────────────
        all_intentions = cached_intentions + llm_intentions

        # Also handle cache-hit desires: mark them as having been acted on
        for intent in cached_intentions:
            try:
                self._store.update(intent.desire_id, status=DesireStatus.ACTIVE,
                                    reasoning="Plan cache execution")
            except ValueError:
                pass

        if all_intentions and self._on_intention:
            for intent in all_intentions:
                try:
                    await self._on_intention(intent)
                except Exception:
                    logger.exception("BDI: on_intention handler error for desire {}", intent.desire_id)

        finished_at = now_iso()
        result = DeliberationResult(
            cycle_id=cycle_id,
            started_at=started_at,
            finished_at=finished_at,
            desires_evaluated=len(desires),
            intentions=all_intentions,
            desires_updated=updated_ids,
            reasoning=reasoning,
            next_check_at=None,
            model_used=self._model,
        )
        self._persist_cycle(cycle_id, started_at, result, "completed",
                            desires_before=desires_before)

        # ── 9. Fire on_cycle_complete callback (CS-004) ─────────────
        if self._on_cycle_complete is not None:
            try:
                await self._on_cycle_complete(result)
            except Exception:
                logger.exception("BDI: on_cycle_complete callback failed")

        return result

    def set_on_cycle_complete(self, callback: Any) -> None:
        """Register a callback invoked after every successful BDI cycle."""
        self._on_cycle_complete = callback

    async def trigger_now(self) -> DeliberationResult:
        """Manually trigger a deliberation cycle from outside the loop."""
        return await self.run_cycle()

    # ------------------------------------------------------------------
    # IntentionStack — resume check
    # ------------------------------------------------------------------

    async def _check_resumptions(self) -> None:
        """Check the IntentionStack for desires that can be resumed.

        When the interrupt that caused a suspend clears (e.g., user returns
        home, door closes, higher-priority task completes), pop the stack
        and transition the desire back to ACTIVE.
        """
        if self._intention_stack.is_empty:
            return

        candidates = self._intention_stack.list_resumable()
        for candidate in candidates:
            desire = self._store.get(candidate.desire_id)
            if desire is None:
                # Orphaned stack entry — remove it
                self._intention_stack.pop()
                logger.info("BDI: removed orphaned stack entry for desire {}", candidate.desire_id)
                continue

            if desire.status == DesireStatus.SUSPENDED:
                # Check if dependencies are now satisfied
                deps_satisfied = True
                for dep_id in desire.dependencies:
                    dep = self._store.get(dep_id)
                    if dep and not dep.is_terminal:
                        deps_satisfied = False
                        break

                if deps_satisfied:
                    try:
                        self._store.update(candidate.desire_id,
                                            status=DesireStatus.ACTIVE,
                                            reasoning="Resumed from IntentionStack")
                        self._intention_stack.pop()
                        logger.info("BDI: resumed desire {} from IntentionStack (depth={})",
                                    candidate.desire_id, self._intention_stack.depth)
                    except ValueError:
                        logger.warning("BDI: could not resume desire {}", candidate.desire_id)

    # ------------------------------------------------------------------
    # Belief gathering
    # ------------------------------------------------------------------

    def _gather_beliefs(self) -> dict[str, Any]:
        """Collect current Belief state for the LLM prompt.

        Reads from memory store: active foresights, recent episodes, profiles,
        and the fact graph if enabled.
        """
        beliefs: dict[str, Any] = {
            "current_time": now_iso(),
            "foresight_count": 0,
            "episode_count": 0,
            "profile_count": 0,
            "intention_stack_depth": self._intention_stack.depth,
        }
        try:
            from OriginAgent.memory.store import NearlineMemoryStore
            ms = NearlineMemoryStore(self.workspace)
            from OriginAgent.memory.models import MemoryLayerSummary
            summary = ms.read_summary() if hasattr(ms, "read_summary") else MemoryLayerSummary()
            beliefs["foresight_count"] = summary.foresight_count
            beliefs["episode_count"] = summary.episode_count
            beliefs["profile_count"] = summary.profile_count
            beliefs["status"] = summary.status
        except Exception:
            beliefs["status"] = "unavailable"

        # Pull shared space if available (cross-tenant facts and devices)
        if self._shared_space is not None:
            beliefs["shared_facts"] = self._shared_space.shared_facts()
            beliefs["shared_devices"] = self._shared_space.device_domains

        # Pull cron observation data if CronDesireBridge is wired
        if self._cron_bridge is not None:
            beliefs["cron"] = self._cron_bridge.get_observation_beliefs()

        return beliefs

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _prioritize(self, desires: list[Desire]) -> list[Desire]:
        """Sort desires by priority (highest first), then by deadline urgency."""
        def sort_key(d: Desire) -> tuple[int, int]:
            overdue_bonus = -1000 if d.is_overdue else 0
            return (overdue_bonus - d.priority.value, 0)
        return sorted(desires, key=sort_key)

    async def _sync_foresights(self, existing: list[Desire]) -> None:
        """Create Desire records from unlinked ForesightRecords."""
        existing_fs_ids = {d.source_foresight_id for d in existing if d.source_foresight_id}
        try:
            from OriginAgent.memory.store import NearlineMemoryStore
            ms = NearlineMemoryStore(self.workspace)
            foresights = ms.list_foresights(limit=50) if hasattr(ms, "list_foresights") else []
            for fs in foresights:
                fs_id = getattr(fs, "foresight_id", "")
                if fs_id and fs_id not in existing_fs_ids:
                    desire = Desire(
                        desire_id=f"desire_fs_{fs_id}",
                        owner_id=getattr(fs, "owner_id", "unknown"),
                        session_key=getattr(fs, "session_key", ""),
                        content=getattr(fs, "content", ""),
                        status=DesireStatus.PENDING,
                        priority=DesirePriority.MEDIUM,
                        deadline_at=getattr(fs, "end_at", None),
                        source_foresight_id=fs_id,
                    )
                    self._store.add(desire)
                    logger.info("BDI: auto-created Desire {} from Foresight {}", desire.desire_id, fs_id)
        except Exception:
            logger.debug("BDI: foresight sync skipped (memory store unavailable)")

    def _build_prompt(self, desires: list[Desire], beliefs: dict[str, Any]) -> str:
        lines = [
            f"Current Time: {beliefs.get('current_time', now_iso())}",
            f"Memory Status: foresights={beliefs.get('foresight_count', '?')}, "
            f"episodes={beliefs.get('episode_count', '?')}",
            f"IntentionStack depth: {beliefs.get('intention_stack_depth', 0)}",
            "",
            "## Active Desires (sorted by priority)",
            "",
        ]
        for i, d in enumerate(desires, 1):
            overdue = " ⚠️ OVERDUE" if d.is_overdue else ""
            deadline = f"\n  Deadline: {d.deadline_at}" if d.deadline_at else ""
            lines.append(
                f"{i}. [{d.priority.name}] {d.content} (id: {d.desire_id}, "
                f"status: {d.status.value}, evals: {d.evaluation_count}){overdue}{deadline}"
            )
            if d.constraints:
                lines.append(f"   Constraints: {', '.join(d.constraints)}")
            if d.dependencies:
                lines.append(f"   Dependencies: {', '.join(d.dependencies)}")

        # Show suspended intentions from stack
        if not self._intention_stack.is_empty:
            lines.append("")
            lines.append("## Suspended Intentions (IntentionStack)")
            for candidate in self._intention_stack.list_resumable():
                lines.append(
                    f"- {candidate.desire_id}: {candidate.suspend_reason[:80]} "
                    f"(suspended at {candidate.suspended_at})"
                )

        # Show shared space beliefs if available
        shared_facts = beliefs.get("shared_facts")
        if shared_facts:
            lines.append("")
            lines.append(f"## Shared Facts ({len(shared_facts)} visible to all tenants)")
            for f in shared_facts:
                content = f.get("content", str(f))
                lines.append(f"- {content[:200]}")

        shared_devices = beliefs.get("shared_devices")
        if shared_devices:
            lines.append("")
            lines.append(f"## Shared Device Domains ({', '.join(shared_devices)})")

        # Show cron observation data if available
        cron = beliefs.get("cron")
        if cron and cron.get("bridge_enabled"):
            lines.append("")
            lines.append("## Cron Job Observations")
            lines.append(f"Active linked jobs: {cron.get('active_jobs', 0)}")
            failing = cron.get("failing_jobs", [])
            if failing:
                lines.append(f"Failing jobs (consecutive_failures >= 2): {cron.get('failing_count', 0)}")
                for fj in failing:
                    lines.append(
                        f"- cron job {fj.get('cron_job_id')} linked to desire "
                        f"{fj.get('desire_id')} — {fj.get('consecutive_failures')} failures"
                    )
                lines.append("")
                lines.append(
                    "To disable a failing cron job, form a system intention with "
                    'payload={"action": "disable_cron", "cron_job_id": "<id>"}. '
                    "BDI will then manage the linked desire directly."
                )

        if not desires:
            lines.append("(no active desires)")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Audit persistence
    # ------------------------------------------------------------------

    def _persist_cycle(
        self,
        cycle_id: str,
        started_at: str,
        result: DeliberationResult,
        status: str,
        desires_before: int = 0,
    ) -> None:
        """Atomically append a BDICycleRecord to the audit log.

        Uses temp-file + fsync + rename + dir-fsync for crash-safe durability,
        consistent with the project convention (see desire_store.py, memory.py).
        """
        active_after = self._store.count_by_status().get(DesireStatus.ACTIVE, 0)

        record = BDICycleRecord(
            cycle_id=cycle_id,
            started_at=started_at,
            finished_at=result.finished_at,
            status=status,
            desires_before=desires_before,
            desires_after_active=active_after,
            intentions_formed=result.intentions_formed,
            intentions_executed=0,
            intentions_failed=0,
            desire_ids_evaluated=[],
            desire_ids_updated=result.desires_updated,
            model_used=result.model_used,
            token_usage=result.token_usage,
            error=result.error,
        )

        try:
            ensure_dir(self._audit_dir)
            line = json.dumps(record.to_json(), ensure_ascii=False) + "\n"

            existing = self._audit_path.read_text(encoding="utf-8") if self._audit_path.exists() else ""

            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self._audit_dir),
                delete=False,
                suffix=".tmp",
            )
            try:
                tmp.write(existing)
                tmp.write(line)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp.close()
                os.replace(tmp.name, str(self._audit_path))

                try:
                    dir_fd = os.open(str(self._audit_dir), os.O_RDONLY)
                    os.fsync(dir_fd)
                    os.close(dir_fd)
                except OSError:
                    pass
            except Exception:
                Path(tmp.name).unlink(missing_ok=True)
                raise
        except Exception:
            logger.exception("BDI: failed to persist cycle audit record")
