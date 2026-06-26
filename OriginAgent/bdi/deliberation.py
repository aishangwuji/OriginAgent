"""BDI Deliberation Engine — continuous Belief→Desire→Intention reasoning loop."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from OriginAgent.bdi.models import (
    BDICycleRecord,
    DeliberationIntention,
    DeliberationResult,
    Desire,
    DesirePriority,
    DesireStatus,
    now_iso,
)
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.utils.helpers import ensure_dir


_DELIBERATION_SYSTEM_PROMPT = """You are the BDI Deliberation Engine of OriginAgent.
Your job is to evaluate the agent's active desires and decide what to do next.

You have access to:
- **Desires**: active goals/commitments the agent has made to its user
- **Beliefs**: current facts, world state, user profile, recent episodes

For each desire, decide one of:
1. **Form an intention** — create a concrete action to advance the desire
2. **Mark satisfied** — the desire is done
3. **Suspend** — the desire is blocked (e.g., waiting for a dependency)
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
                        "description": "Desire IDs to pause",
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
    """Continuous BDI deliberation loop.

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

        self._running = False
        self._task: asyncio.Task | None = None
        self._audit_dir = self.workspace / "memory" / "bdi"
        self._audit_path = self._audit_dir / "cycles.jsonl"
        ensure_dir(self._audit_dir)

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
        logger.info("BDI: DeliberationEngine started (every {}s)", self._interval_s)

    def stop(self) -> None:
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
        """Execute one full BDI deliberation cycle."""
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

        # 1. Collect active desires
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

        # 2. Optionally auto-create desires from foresight records
        if self._auto_create_from_foresight:
            await self._sync_foresights(desires)

        # 3. Cap to max per cycle, prioritize by priority and deadline
        desires = self._prioritize(desires)[:self._max_desires_per_cycle]

        # 4. Build prompt with Belief context
        beliefs = self._gather_beliefs()
        user_prompt = self._build_prompt(desires, beliefs)

        # 5. Call LLM for deliberation
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
                desires_evaluated=len(desires),
                reasoning="",
                error=str(exc),
            )
            self._persist_cycle(cycle_id, started_at, result, "error")
            return result

        # 6. Parse LLM output
        if not response.should_execute_tools or not response.has_tool_calls:
            result = DeliberationResult(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=now_iso(),
                desires_evaluated=len(desires),
                reasoning="LLM did not produce tool calls.",
            )
            self._persist_cycle(cycle_id, started_at, result, "completed")
            return result

        args = response.tool_calls[0].arguments
        reasoning = args.get("reasoning", "")

        # 7. Parse intentions
        intentions = []
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
                intentions.append(intent)
            except KeyError:
                logger.warning("BDI: skipping malformed intention: {}", raw)

        # 8. Apply status transitions
        updated_ids: list[str] = []
        for did in args.get("desires_to_satisfy", []):
            updated = self._store.update(did, status=DesireStatus.SATISFIED,
                                          reasoning=reasoning)
            if updated:
                updated_ids.append(did)
        for did in args.get("desires_to_suspend", []):
            updated = self._store.update(did, status=DesireStatus.SUSPENDED,
                                          reasoning=reasoning)
            if updated:
                updated_ids.append(did)
        for did in args.get("desires_to_cancel", []):
            updated = self._store.update(did, status=DesireStatus.CANCELLED,
                                          reasoning=reasoning)
            if updated:
                updated_ids.append(did)

        # 9. Emit intentions for execution
        if intentions and self._on_intention:
            for intent in intentions:
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
            intentions=intentions,
            desires_updated=updated_ids,
            reasoning=reasoning,
            next_check_at=args.get("next_check_at"),
            model_used=self._model,
        )
        self._persist_cycle(cycle_id, started_at, result, "completed",
                            desires_before=desires_before)
        return result

    async def trigger_now(self) -> DeliberationResult:
        """Manually trigger a deliberation cycle from outside the loop."""
        return await self.run_cycle()

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
        }
        # Try to read from the nearline memory store
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
        """Append a BDICycleRecord to the audit log."""
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
            import os
            with open(self._audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("BDI: failed to persist cycle audit record")
