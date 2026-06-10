from __future__ import annotations

import json
from pathlib import Path

import pytest

from OriginAgent.config.schema import NearlineMemoryConfig
from OriginAgent.memory.pipeline import NearlineMemoryPipeline
from OriginAgent.session.manager import Session


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


@pytest.mark.asyncio
async def test_nearline_pipeline_generates_episode_for_regular_user_turn(tmp_path) -> None:
    pipeline = NearlineMemoryPipeline(
        tmp_path,
        config=NearlineMemoryConfig(enabled=True, pipeline_enabled=True),
    )
    session = Session(key="websocket:chat1")
    session.messages = [
        {
            "role": "user",
            "content": "Please remember that I prefer concise replies.",
            "timestamp": "2026-06-05T10:00:00+08:00",
            "sender_id": "user-1",
        },
        {
            "role": "assistant",
            "content": "Understood.",
            "timestamp": "2026-06-05T10:00:02+08:00",
        },
    ]

    result = await pipeline.process_turn(
        session=session,
        channel="websocket",
        chat_id="chat1",
        actor_id="user-1",
        turn_id="turn-1",
    )

    assert result.status == "ok"
    assert result.episodes_written == 1
    episodes = _load_jsonl(tmp_path / "memory" / "nearline" / "episodes.jsonl")
    assert len(episodes) == 1
    assert "prefer concise replies" in episodes[0]["summary"]
    assert episodes[0]["goal_summary"]
    assert isinstance(episodes[0]["decisions"], list)
    assert isinstance(episodes[0]["constraints"], list)
    assert isinstance(episodes[0]["open_loops"], list)
    assert isinstance(episodes[0]["key_events"], list)
    assert isinstance(episodes[0]["time_range"], dict)


@pytest.mark.asyncio
async def test_nearline_pipeline_persists_events_and_session_cursors(tmp_path) -> None:
    pipeline = NearlineMemoryPipeline(
        tmp_path,
        config=NearlineMemoryConfig(enabled=True, pipeline_enabled=True),
    )
    session = Session(key="websocket:cursor-chat")
    session.messages = [
        {
            "role": "user",
            "content": "Please remember that I prefer concise replies.",
            "timestamp": "2026-06-05T10:00:00+08:00",
            "sender_id": "user-1",
        },
        {
            "role": "assistant",
            "content": "Understood.",
            "timestamp": "2026-06-05T10:00:02+08:00",
        },
    ]

    result = await pipeline.process_turn(
        session=session,
        channel="websocket",
        chat_id="cursor-chat",
        actor_id="user-1",
        turn_id="turn-cursor",
    )

    assert result.status == "ok"
    assert result.cursor_before == 0
    assert result.cursor_after == 2
    assert (tmp_path / "memory" / "nearline" / ".cursor").read_text(encoding="utf-8").strip() == "2"
    session_cursors = json.loads(
        (tmp_path / "memory" / "nearline" / "session_cursors.json").read_text(encoding="utf-8")
    )
    assert session_cursors == {"websocket:cursor-chat": 2}
    events = _load_jsonl(tmp_path / "memory" / "nearline" / "events.jsonl")
    event_names = {row["event_name"] for row in events}
    assert {"memcell_created", "episode_extracted", "profile_refresh_requested"} <= event_names


@pytest.mark.asyncio
async def test_nearline_pipeline_generates_agent_case_for_execution_turn(tmp_path) -> None:
    pipeline = NearlineMemoryPipeline(
        tmp_path,
        config=NearlineMemoryConfig(enabled=True, pipeline_enabled=True),
    )
    session = Session(key="cli:task")
    session.messages = [
        {
            "role": "user",
            "content": "Create a reminder for next Monday.",
            "timestamp": "2026-06-05T11:00:00+08:00",
            "sender_id": "user-2",
        },
        {
            "role": "assistant",
            "content": "",
            "timestamp": "2026-06-05T11:00:01+08:00",
            "tool_calls": [{"id": "call_1", "function": {"name": "create_reminder"}}],
        },
        {
            "role": "tool",
            "content": "Reminder scheduled.",
            "timestamp": "2026-06-05T11:00:02+08:00",
            "name": "create_reminder",
            "tool_call_id": "call_1",
        },
        {
            "role": "assistant",
            "content": "Done, I scheduled it.",
            "timestamp": "2026-06-05T11:00:03+08:00",
        },
    ]

    result = await pipeline.process_turn(
        session=session,
        channel="cli",
        chat_id="task",
        actor_id="origin-agent",
        turn_id="turn-2",
    )

    assert result.status == "ok"
    assert result.agent_cases_written == 1
    records = _load_jsonl(tmp_path / "memory" / "nearline" / "agent_cases.jsonl")
    assert len(records) == 1
    assert records[0]["approach"] == "create_reminder"
    assert "scheduled" in records[0]["outcome_summary"].lower()


@pytest.mark.asyncio
async def test_nearline_pipeline_generates_foresight_only_for_future_intent(tmp_path) -> None:
    pipeline = NearlineMemoryPipeline(
        tmp_path,
        config=NearlineMemoryConfig(enabled=True, pipeline_enabled=True),
    )
    session = Session(key="websocket:future")
    session.messages = [
        {
            "role": "user",
            "content": "I will send the draft tomorrow morning.",
            "timestamp": "2026-06-05T12:00:00+08:00",
            "sender_id": "user-3",
        },
        {
            "role": "assistant",
            "content": "I'll keep that in mind.",
            "timestamp": "2026-06-05T12:00:01+08:00",
        },
    ]

    result = await pipeline.process_turn(
        session=session,
        channel="websocket",
        chat_id="future",
        actor_id="user-3",
        turn_id="turn-3",
    )

    assert result.status == "ok"
    assert result.foresights_written == 1
    records = _load_jsonl(tmp_path / "memory" / "nearline" / "foresights.jsonl")
    assert len(records) == 1
    assert "tomorrow" in records[0]["content"].lower()
    assert records[0]["start_at"]


@pytest.mark.asyncio
async def test_nearline_pipeline_is_incremental_per_session(tmp_path) -> None:
    pipeline = NearlineMemoryPipeline(
        tmp_path,
        config=NearlineMemoryConfig(enabled=True, pipeline_enabled=True),
    )
    session = Session(key="websocket:chat-inc")
    session.messages = [
        {"role": "user", "content": "First", "timestamp": "2026-06-05T10:00:00+08:00"},
        {"role": "assistant", "content": "Ack", "timestamp": "2026-06-05T10:00:01+08:00"},
    ]

    first = await pipeline.process_turn(
        session=session,
        channel="websocket",
        chat_id="chat-inc",
        actor_id="user",
        turn_id="turn-1",
    )
    second = await pipeline.process_turn(
        session=session,
        channel="websocket",
        chat_id="chat-inc",
        actor_id="user",
        turn_id="turn-2",
    )

    assert first.status == "ok"
    assert second.status == "skipped"
    assert second.reason == "no_new_messages"


@pytest.mark.asyncio
async def test_nearline_pipeline_disabled_has_no_side_effects(tmp_path) -> None:
    pipeline = NearlineMemoryPipeline(
        tmp_path,
        config=NearlineMemoryConfig(
            enabled=False,
            pipeline_enabled=True,
            profile_shadow_write_enabled=True,
        ),
    )
    user_file = tmp_path / "USER.md"
    original_user = "# User Profile\n\nManual notes stay put.\n"
    user_file.write_text(original_user, encoding="utf-8")
    session = Session(key="websocket:disabled")
    session.messages = [
        {
            "role": "user",
            "content": "I will send the draft tomorrow morning.",
            "timestamp": "2026-06-05T12:00:00+08:00",
            "sender_id": "user-3",
        },
        {
            "role": "assistant",
            "content": "I'll keep that in mind.",
            "timestamp": "2026-06-05T12:00:01+08:00",
        },
    ]

    result = await pipeline.process_turn(
        session=session,
        channel="websocket",
        chat_id="disabled",
        actor_id="user-3",
        turn_id="turn-disabled",
    )

    assert result.status == "skipped"
    assert result.reason == "disabled"
    assert user_file.read_text(encoding="utf-8") == original_user
    assert not (tmp_path / "memory" / "nearline").exists()


@pytest.mark.asyncio
async def test_nearline_pipeline_updates_profile_sidecar_and_managed_user_region(tmp_path) -> None:
    from OriginAgent.memory.profile import NearlineProfileService

    pipeline = NearlineMemoryPipeline(
        tmp_path,
        config=NearlineMemoryConfig(
            enabled=True,
            pipeline_enabled=True,
            profile_shadow_write_enabled=True,
        ),
    )
    user_file = tmp_path / "USER.md"
    user_file.write_text(
        "# User Profile\n\nManual notes:\n- Keep this line.\n",
        encoding="utf-8",
    )
    session = Session(key="cli:profile")
    session.messages = [
        {
            "role": "user",
            "content": "I prefer concise release updates and I will send the draft tomorrow morning.",
            "timestamp": "2026-06-05T12:00:00+08:00",
            "sender_id": "user-5",
        },
        {
            "role": "assistant",
            "content": "Noted.",
            "timestamp": "2026-06-05T12:00:01+08:00",
        },
    ]

    result = await pipeline.process_turn(
        session=session,
        channel="cli",
        chat_id="profile",
        actor_id="user-5",
        turn_id="turn-profile",
    )

    assert result.status == "ok"
    assert result.profiles_written == 1
    profiles = _load_jsonl(tmp_path / "memory" / "nearline" / "profiles.jsonl")
    assert len(profiles) == 1
    assert "concise release updates" in profiles[0]["summary"]

    user_text = user_file.read_text(encoding="utf-8")
    start, end = NearlineProfileService.managed_markers()
    assert "Manual notes:\n- Keep this line." in user_text
    assert start in user_text
    assert end in user_text
    assert "Managed Profile Snapshot" in user_text


@pytest.mark.asyncio
async def test_nearline_pipeline_writes_profile_snapshot_without_user_shadow_when_shadow_disabled(tmp_path) -> None:
    pipeline = NearlineMemoryPipeline(
        tmp_path,
        config=NearlineMemoryConfig(
            enabled=True,
            pipeline_enabled=True,
            profile_shadow_write_enabled=False,
        ),
    )
    user_file = tmp_path / "USER.md"
    original_user = "# User Profile\n\nManual notes stay put.\n"
    user_file.write_text(original_user, encoding="utf-8")
    session = Session(key="cli:profile-no-shadow")
    session.messages = [
        {
            "role": "user",
            "content": "I prefer concise release updates and I will send the draft tomorrow morning.",
            "timestamp": "2026-06-05T12:00:00+08:00",
            "sender_id": "user-5",
        },
        {
            "role": "assistant",
            "content": "Noted.",
            "timestamp": "2026-06-05T12:00:01+08:00",
        },
    ]

    result = await pipeline.process_turn(
        session=session,
        channel="cli",
        chat_id="profile-no-shadow",
        actor_id="user-5",
        turn_id="turn-profile-no-shadow",
    )

    assert result.status == "ok"
    assert result.profiles_written == 1
    profiles = _load_jsonl(tmp_path / "memory" / "nearline" / "profiles.jsonl")
    assert len(profiles) == 1
    assert user_file.read_text(encoding="utf-8") == original_user


@pytest.mark.asyncio
async def test_nearline_pipeline_retry_after_partial_failure_is_idempotent(tmp_path) -> None:
    pipeline = NearlineMemoryPipeline(
        tmp_path,
        config=NearlineMemoryConfig(enabled=True, pipeline_enabled=True),
    )
    session = Session(key="cli:retry")
    session.messages = [
        {
            "role": "user",
            "content": "I will send the draft tomorrow morning.",
            "timestamp": "2026-06-05T12:00:00+08:00",
            "sender_id": "user-3",
        },
        {
            "role": "assistant",
            "content": "I'll keep that in mind.",
            "timestamp": "2026-06-05T12:00:01+08:00",
        },
    ]

    original_append_events = pipeline.store.append_events
    call_count = {"value": 0}

    def flaky_append_events(events):
        call_count["value"] += 1
        if call_count["value"] == 1:
            raise RuntimeError("boom after artifacts")
        return original_append_events(events)

    pipeline.store.append_events = flaky_append_events  # type: ignore[method-assign]

    first = await pipeline.process_turn(
        session=session,
        channel="cli",
        chat_id="retry",
        actor_id="user-3",
        turn_id="turn-retry",
    )
    assert first.status == "error"

    pipeline.store.append_events = original_append_events  # type: ignore[method-assign]
    second = await pipeline.process_turn(
        session=session,
        channel="cli",
        chat_id="retry",
        actor_id="user-3",
        turn_id="turn-retry",
    )

    assert second.status == "ok"
    assert len(_load_jsonl(tmp_path / "memory" / "nearline" / "memcells.jsonl")) == 1
    assert len(_load_jsonl(tmp_path / "memory" / "nearline" / "episodes.jsonl")) == 1
    assert len(_load_jsonl(tmp_path / "memory" / "nearline" / "foresights.jsonl")) == 1
    assert len(_load_jsonl(tmp_path / "memory" / "nearline" / "profiles.jsonl")) == 1
    assert len(_load_jsonl(tmp_path / "memory" / "nearline" / "events.jsonl")) == 4
    assert (tmp_path / "memory" / "nearline" / ".cursor").read_text(encoding="utf-8").strip() == "2"
