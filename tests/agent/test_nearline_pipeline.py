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
