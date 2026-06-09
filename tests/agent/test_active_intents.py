from __future__ import annotations

import asyncio
import json
from datetime import timedelta, timezone, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.active_intents import ActiveIntentConfig, ActiveIntentService
from OriginAgent.agent.confirmation import ConfirmationRequest, PendingConfirmationStore
from OriginAgent.agent.facts import FactStore
from OriginAgent.bus.events import InboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.config.schema import AgentDefaults, NearlineMemoryConfig
from OriginAgent.providers.base import LLMProvider, LLMResponse
from OriginAgent.session.goal_state import GOAL_STATE_KEY
from OriginAgent.session.manager import SessionManager


class FakeProvider(LLMProvider):
    def __init__(self, response: LLMResponse):
        super().__init__()
        self.response = response

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat(self, **kwargs):
        return self.response

    async def chat_with_retry(self, **kwargs):
        return self.response


def _make_service(
    tmp_path: Path,
    *,
    enabled: bool = True,
    nearline_enabled: bool | None = None,
) -> tuple[ActiveIntentService, MessageBus, SessionManager]:
    bus = MessageBus()
    sessions = SessionManager(tmp_path)
    service = ActiveIntentService(
        workspace=tmp_path,
        bus=bus,
        sessions=sessions,
        confirmation_store=PendingConfirmationStore(tmp_path),
        fact_store=FactStore(tmp_path),
        config=ActiveIntentConfig(
            enabled=enabled,
            interval_seconds=30,
            session_cooldown_seconds=300,
            intent_cooldown_seconds=300,
            max_messages_per_session_per_pass=1,
        ),
        nearline_memory_config=(
            NearlineMemoryConfig(enabled=nearline_enabled)
            if nearline_enabled is not None
            else None
        ),
    )
    return service, bus, sessions


@pytest.mark.asyncio
async def test_active_intents_disabled_skips_emission(tmp_path: Path) -> None:
    service, _bus, sessions = _make_service(tmp_path, enabled=False)
    session = sessions.get_or_create("cli:test")
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "Finish the report",
        "started_at": "2026-05-28T10:00:00",
    }
    sessions.save(session)

    emitted = await service.process_session(
        "cli:test",
        active_task_count=0,
        running_subagents=0,
    )

    assert emitted == []
    recent = service.ledger.recent()
    assert recent[-1]["outcome"] == "skipped"
    assert recent[-1]["suppression_reason"] == "disabled"


@pytest.mark.asyncio
async def test_active_intents_emit_goal_nudge(tmp_path: Path) -> None:
    service, bus, sessions = _make_service(tmp_path, enabled=True)
    session = sessions.get_or_create("cli:test")
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "Finish the report",
        "ui_summary": "Report",
        "started_at": "2026-05-28T10:00:00",
    }
    sessions.save(session)

    emitted = await service.process_session(
        "cli:test",
        active_task_count=0,
        running_subagents=0,
    )

    assert len(emitted) == 1
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=0.2)
    assert isinstance(msg, InboundMessage)
    assert msg.metadata["injected_event"] == "active_intent"
    assert msg.metadata["active_intent_type"] == "goal_nudge"
    assert "unfinished sustained goal" in msg.content


@pytest.mark.asyncio
async def test_active_intents_suppress_repeated_goal_nudge_within_cooldown(tmp_path: Path) -> None:
    service, _bus, sessions = _make_service(tmp_path, enabled=True)
    session = sessions.get_or_create("cli:test")
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "Finish the report",
        "ui_summary": "Report",
        "started_at": "2026-05-28T10:00:00",
    }
    sessions.save(session)

    first = await service.process_session(
        "cli:test",
        active_task_count=0,
        running_subagents=0,
    )
    second = await service.process_session(
        "cli:test",
        active_task_count=0,
        running_subagents=0,
    )

    assert len(first) == 1
    assert second == []
    recent = service.ledger.recent()
    assert recent[-1]["outcome"] == "suppressed"
    assert recent[-1]["suppression_reason"] in {"session_cooldown", "intent_cooldown"}


@pytest.mark.asyncio
async def test_active_intents_emit_pending_confirmation_nudge(tmp_path: Path) -> None:
    service, bus, sessions = _make_service(tmp_path, enabled=True)
    session = sessions.get_or_create("cli:test")
    sessions.save(session)
    service.confirmation_store.upsert(ConfirmationRequest(
        confirmation_id="c1",
        kind="action_confirmation",
        status="pending",
        prompt="Approve deployment?",
        action="deploy",
        scope="workspace",
        trigger="user",
        risk="medium",
        requested_by="agent",
        decision_reason="pending",
        presence_status="unknown",
        related_fact_ids=[],
        created_at="2026-05-28T10:00:00+00:00",
        expires_at="2026-05-29T10:00:00+00:00",
        metadata={"session_key": "cli:test"},
    ))

    emitted = await service.process_session(
        "cli:test",
        active_task_count=0,
        running_subagents=0,
    )

    assert len(emitted) == 1
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=0.2)
    assert msg.metadata["active_intent_type"] == "pending_confirmation_nudge"
    assert "Pending confirmation follow-up" in msg.content


@pytest.mark.asyncio
async def test_active_intents_skip_busy_session(tmp_path: Path) -> None:
    service, _bus, sessions = _make_service(tmp_path, enabled=True)
    session = sessions.get_or_create("cli:test")
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "Finish the report",
        "started_at": "2026-05-28T10:00:00",
    }
    sessions.save(session)

    emitted = await service.process_session(
        "cli:test",
        active_task_count=1,
        running_subagents=0,
    )

    assert emitted == []
    recent = service.ledger.recent()
    assert recent[-1]["outcome"] == "skipped"
    assert recent[-1]["suppression_reason"] == "active_tasks"


@pytest.mark.asyncio
async def test_active_intents_do_not_consume_legacy_reminder_store_records(tmp_path: Path) -> None:
    from OriginAgent.agent.reminders import ReminderRecord, ReminderStore

    service, _bus, sessions = _make_service(tmp_path, enabled=True)
    session = sessions.get_or_create("cli:test")
    sessions.save(session)
    due_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    store = ReminderStore(tmp_path)
    store.upsert(ReminderRecord.create(
        session_key="cli:test",
        channel="cli",
        chat_id="test",
        content="Legacy reminder",
        due_at=due_at,
        reminder_id="legacy-reminder",
    ))

    emitted = await service.process_session(
        "cli:test",
        active_task_count=0,
        running_subagents=0,
    )

    assert emitted == []
    recent = service.ledger.recent()
    assert recent == []


@pytest.mark.asyncio
async def test_active_intents_emit_due_foresight_nudge_with_cooldown(tmp_path: Path) -> None:
    service, bus, sessions = _make_service(tmp_path, enabled=True)
    session = sessions.get_or_create("cli:test")
    sessions.save(session)
    foresight_path = tmp_path / "memory" / "nearline" / "foresights.jsonl"
    foresight_path.parent.mkdir(parents=True, exist_ok=True)
    foresight_path.write_text(
        json.dumps(
            {
                "foresight_id": "fo_1",
                "memcell_id": "mem_1",
                "session_key": "cli:test",
                "owner_id": "user",
                "content": "I will send the draft tomorrow morning.",
                "evidence": "User said they will send the draft tomorrow morning.",
                "start_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                "end_at": None,
                "timestamp": "2026-06-05T12:00:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    first = await service.process_session(
        "cli:test",
        active_task_count=0,
        running_subagents=0,
    )
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=0.2)
    second = await service.process_session(
        "cli:test",
        active_task_count=0,
        running_subagents=0,
    )

    assert len(first) == 1
    assert msg.metadata["active_intent_type"] == "foresight_nudge"
    assert "future plan or commitment is now due" in msg.content
    assert second == []
    recent = service.ledger.recent()
    assert recent[-1]["outcome"] == "suppressed"
    assert recent[-1]["suppression_reason"] in {"session_cooldown", "intent_cooldown"}


@pytest.mark.asyncio
async def test_active_intents_ignore_foresight_when_nearline_disabled(tmp_path: Path) -> None:
    service, _bus, sessions = _make_service(tmp_path, enabled=True, nearline_enabled=False)
    session = sessions.get_or_create("cli:test")
    sessions.save(session)
    foresight_path = tmp_path / "memory" / "nearline" / "foresights.jsonl"
    foresight_path.parent.mkdir(parents=True, exist_ok=True)
    foresight_path.write_text(
        json.dumps(
            {
                "foresight_id": "fo_1",
                "memcell_id": "mem_1",
                "session_key": "cli:test",
                "owner_id": "user",
                "content": "I will send the draft tomorrow morning.",
                "evidence": "User said they will send the draft tomorrow morning.",
                "start_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                "end_at": None,
                "timestamp": "2026-06-05T12:00:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    emitted = await service.process_session(
        "cli:test",
        active_task_count=0,
        running_subagents=0,
    )

    assert emitted == []


@pytest.mark.asyncio
async def test_active_intents_ignore_foresight_when_nearline_pipeline_is_disabled(tmp_path: Path) -> None:
    service, _bus, sessions = _make_service(tmp_path, enabled=True)
    service._nearline_memory_config = NearlineMemoryConfig(enabled=True, pipeline_enabled=False)
    session = sessions.get_or_create("cli:test")
    sessions.save(session)
    foresight_path = tmp_path / "memory" / "nearline" / "foresights.jsonl"
    foresight_path.parent.mkdir(parents=True, exist_ok=True)
    foresight_path.write_text(
        json.dumps(
            {
                "foresight_id": "fo_1",
                "memcell_id": "mem_1",
                "session_key": "cli:test",
                "owner_id": "user",
                "content": "I will send the draft tomorrow morning.",
                "evidence": "User said they will send the draft tomorrow morning.",
                "start_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                "end_at": None,
                "timestamp": "2026-06-05T12:00:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    emitted = await service.process_session(
        "cli:test",
        active_task_count=0,
        running_subagents=0,
    )

    assert emitted == []


@pytest.mark.asyncio
async def test_agent_loop_does_not_start_active_intent_loop_when_disabled(tmp_path: Path) -> None:
    from OriginAgent.agent.loop import AgentLoop

    loop = AgentLoop(
        bus=MessageBus(),
        provider=FakeProvider(LLMResponse(content="ok", finish_reason="stop")),
        workspace=tmp_path,
        model="fake-model",
    )

    loop._start_active_intent_loop()

    assert loop._active_intent_task is None
    assert loop.introspection.cognition_summary()["scheduler"]["mode"] == "disabled"


@pytest.mark.asyncio
async def test_agent_loop_starts_active_intent_loop_when_enabled(tmp_path: Path) -> None:
    from OriginAgent.agent.loop import AgentLoop

    loop = AgentLoop(
        bus=MessageBus(),
        provider=FakeProvider(LLMResponse(content="ok", finish_reason="stop")),
        workspace=tmp_path,
        model="fake-model",
        allow_agent_initiated_messages=True,
    )
    loop.active_intents.process_session = AsyncMock(return_value=[])
    loop.cognitive_loop = type(loop.cognitive_loop)(
        config=loop.cognitive_loop.config,
        session_keys_provider=loop.active_intents.session_keys,
        active_task_count_provider=loop._active_task_count,
        running_subagents_provider=loop.subagents.get_running_count_by_session,
        session_processor=loop.active_intents.process_session,
    )
    loop._running = True

    loop._start_active_intent_loop()
    await asyncio.sleep(0)

    assert loop._active_intent_task is not None
    assert loop.introspection.cognition_summary()["scheduler"]["mode"] == "fallback"
    loop._active_intent_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop._active_intent_task


@pytest.mark.asyncio
async def test_agent_loop_prefers_cron_backed_cognitive_scheduler(tmp_path: Path) -> None:
    from OriginAgent.agent.loop import AgentLoop
    from OriginAgent.cron.service import CronService

    cron = CronService(tmp_path / "cron" / "jobs.json")
    cron.on_job = AsyncMock()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=FakeProvider(LLMResponse(content="ok", finish_reason="stop")),
        workspace=tmp_path,
        model="fake-model",
        allow_agent_initiated_messages=True,
        cron_service=cron,
    )

    loop._start_active_intent_loop()

    assert loop._active_intent_task is None
    job = cron.get_job("cognitive_scheduler")
    assert job is not None
    assert job.payload.kind == "system_event"
    cognition = loop.introspection.cognition_summary()
    assert cognition["scheduler"]["mode"] == "cron"
    assert cognition["scheduler"]["registered"] is True


def test_agent_defaults_active_intents_disabled_by_default() -> None:
    defaults = AgentDefaults()
    assert defaults.allow_agent_initiated_messages is False


@pytest.mark.asyncio
async def test_agent_loop_cognitive_pass_emits_due_reminder_and_writes_working_memory(tmp_path: Path) -> None:
    from OriginAgent.agent.loop import AgentLoop
    from OriginAgent.agent.reminders import ReminderRecord

    loop = AgentLoop(
        bus=MessageBus(),
        provider=FakeProvider(LLMResponse(content="ok", finish_reason="stop")),
        workspace=tmp_path,
        model="fake-model",
        allow_agent_initiated_messages=True,
    )
    session = loop.sessions.get_or_create("cli:test")
    loop.sessions.save(session)
    due_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    loop._reminder_store.upsert(ReminderRecord.create(
        session_key="cli:test",
        channel="cli",
        chat_id="test",
        content="Follow up on the current plan",
        due_at=due_at,
        reminder_id="r-1",
    ))

    decisions = await loop._run_cognitive_pass_for_session(
        "cli:test",
        active_task_count=0,
        running_subagents=0,
    )

    assert any(item.outcome == "emitted" for item in decisions)
    msg = await asyncio.wait_for(loop.bus.consume_inbound(), timeout=0.2)
    assert msg.metadata["injected_event"] == "cognitive_event"
    assert msg.metadata["cognitive_event_type"] == "scheduled_reminder"
    snapshot = loop.working_memory.inspect(session)
    assert "Follow up on the current plan" in snapshot["attention_items"]
    assert "Is this due reminder still relevant and ready to act on?" in snapshot["pending_questions"]
    reminder = loop._reminder_store.get("r-1")
    assert reminder is not None
    assert reminder.status == "fired"
    cognition = loop.introspection.cognition_summary()
    assert cognition["latest_scan"]["session_key"] == "cli:test"
    assert cognition["latest_scan"]["emitted_count"] == 1


@pytest.mark.asyncio
async def test_agent_loop_processes_cognitive_event_as_internal_event(tmp_path: Path) -> None:
    from OriginAgent.agent.loop import AgentLoop

    provider = MagicMock(spec=FakeProvider(LLMResponse(content="ok", finish_reason="stop")))
    provider.get_default_model.return_value = "fake-model"
    provider.generation.max_tokens = 4096
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="Handled.", finish_reason="stop"))
    provider.chat_stream_with_retry = AsyncMock(return_value=LLMResponse(content="Handled.", finish_reason="stop"))

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="fake-model",
    )

    result = await loop._process_message(
        InboundMessage(
            channel="system",
            sender_id="agent_cognitive",
            chat_id="cli:test",
            content="Reminder: Follow up on the current plan",
            metadata={
                "injected_event": "cognitive_event",
                "cognitive_event_type": "scheduled_reminder",
                "cognitive_event_id": "reminder:r-1",
            },
            session_key_override="cli:test",
        )
    )

    assert result is not None
    continuity = loop.introspection.continuity_summary()
    assert "Reminder: Follow up on the current plan" in continuity["working_memory"]["attention_items"]
    assert "internal_event" in continuity["last_context_assembly"]["block_kinds"]
