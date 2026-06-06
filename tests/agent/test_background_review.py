from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from OriginAgent.agent.background_review import BackgroundReviewService, ReviewProposalStore
from OriginAgent.agent.loop import AgentLoop, TurnContext, TurnState
from OriginAgent.bus.events import InboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.command.builtin import cmd_reviews
from OriginAgent.command.router import CommandContext
from OriginAgent.config.schema import BackgroundReviewConfig, CuratorConfig
from OriginAgent.providers.base import LLMProvider, LLMResponse
from OriginAgent.session.manager import Session


class FakeProvider(LLMProvider):
    def __init__(self, response: LLMResponse):
        super().__init__()
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat(self, **kwargs: Any) -> LLMResponse:
        return await self.chat_with_retry(**kwargs)

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return self.response


def _proposal_response() -> LLMResponse:
    return LLMResponse(
        content=json.dumps({
            "proposals": [
                {
                    "type": "memory",
                    "domain_id": "core",
                    "title": "Remember preferred style",
                    "content": (
                        "User prefers direct answers. "
                        "api_key=sk-proj-secretsecretsecretsecret"
                    ),
                    "rationale": "The user explicitly requested a direct style.",
                    "confidence": 0.9,
                    "evidence": ["Please be direct."],
                },
                {
                    "type": "skill",
                    "domain_id": "inactive_domain",
                    "title": "Should be filtered",
                    "content": "Filtered because domain is not active.",
                },
            ]
        }),
        finish_reason="stop",
    )


@pytest.mark.asyncio
async def test_background_review_disabled_does_not_call_provider(tmp_path: Path) -> None:
    provider = FakeProvider(_proposal_response())
    service = BackgroundReviewService(
        workspace=tmp_path,
        provider=provider,
        model="fake-model",
        config=BackgroundReviewConfig(enabled=False),
    )

    result = await service.review_turn(
        session_key="websocket:chat1",
        turn_id="turn-1",
        channel="websocket",
        chat_id="chat1",
        message_id="m1",
        messages=[{"role": "user", "content": "remember this"}],
    )

    assert result.status == "skipped"
    assert provider.calls == []
    assert not (tmp_path / "memory" / "review_proposals.jsonl").exists()


@pytest.mark.asyncio
async def test_background_review_writes_redacted_pending_proposals(tmp_path: Path) -> None:
    provider = FakeProvider(_proposal_response())
    service = BackgroundReviewService(
        workspace=tmp_path,
        provider=provider,
        model="fake-model",
        config=BackgroundReviewConfig(enabled=True, allowed_proposal_types=["memory", "skill"]),
    )

    result = await service.review_turn(
        session_key="websocket:chat1",
        turn_id="turn-1",
        channel="websocket",
        chat_id="chat1",
        message_id="m1",
        messages=[
            {"role": "user", "content": "Please be direct.", "timestamp": "2026-05-19T10:00:00"},
            {"role": "assistant", "content": "Understood.", "timestamp": "2026-05-19T10:00:01"},
        ],
    )

    assert result.status == "ok"
    assert result.proposals_written == 1
    records = ReviewProposalStore(tmp_path).recent()
    assert len(records) == 1
    assert records[0]["proposal_type"] == "memory"
    assert records[0]["domain_id"] == "core"
    assert records[0]["status"] == "pending"
    assert "[REDACTED_SECRET]" in records[0]["content"]
    assert "sk-proj" not in records[0]["content"]
    status = service.runtime_status()
    assert status["last_status"] == "ok"
    assert status["last_reason"] == "ok"
    assert status["consecutive_failures"] == 0


def test_background_review_prompt_filters_derived_nearline_messages(tmp_path: Path) -> None:
    provider = FakeProvider(_proposal_response())
    service = BackgroundReviewService(
        workspace=tmp_path,
        provider=provider,
        model="fake-model",
        config=BackgroundReviewConfig(enabled=True),
    )

    prompt = service._build_prompt(
        session_key="websocket:chat1",
        turn_id="turn-1",
        channel="websocket",
        chat_id="chat1",
        message_id="m1",
        messages=[
            {
                "role": "user",
                "content": "Please remember I prefer direct answers.",
                "timestamp": "2026-05-19T10:00:00",
            },
            {
                "role": "assistant",
                "content": "Foresight follow-up...",
                "timestamp": "2026-05-19T10:01:00",
                "_from_active": True,
                "injected_event": "active_intent",
            },
            {
                "role": "tool",
                "content": "source_path=memory/nearline/episodes.jsonl",
                "timestamp": "2026-05-19T10:02:00",
            },
        ],
    )

    assert "Please remember I prefer direct answers." in prompt
    assert "Foresight follow-up" not in prompt
    assert "memory/nearline/episodes.jsonl" not in prompt


@pytest.mark.asyncio
async def test_background_review_writes_proposals_off_event_loop_thread(tmp_path: Path) -> None:
    class ThreadRecordingStore:
        def __init__(self) -> None:
            self.thread_id: int | None = None

        def append_many(self, proposals):
            self.thread_id = threading.get_ident()
            return len(proposals)

    store = ThreadRecordingStore()
    loop_thread_id = threading.get_ident()
    service = BackgroundReviewService(
        workspace=tmp_path,
        provider=FakeProvider(_proposal_response()),
        model="fake-model",
        config=BackgroundReviewConfig(enabled=True, allowed_proposal_types=["memory"]),
        store=store,
    )

    result = await service.review_turn(
        session_key="websocket:chat1",
        turn_id="turn-1",
        channel="websocket",
        chat_id="chat1",
        message_id="m1",
        messages=[{"role": "user", "content": "Please be direct."}],
    )

    assert result.status == "ok"
    assert result.proposals_written == 1
    assert store.thread_id is not None
    assert store.thread_id != loop_thread_id


@pytest.mark.asyncio
async def test_background_review_failure_sets_structured_runtime_report(tmp_path: Path) -> None:
    service = BackgroundReviewService(
        workspace=tmp_path,
        provider=FakeProvider(_proposal_response()),
        model="fake-model",
        config=BackgroundReviewConfig(enabled=True),
    )
    service._build_prompt = lambda **_: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[assignment]

    result = await service.review_turn(
        session_key="websocket:chat1",
        turn_id="turn-1",
        channel="websocket",
        chat_id="chat1",
        message_id="m1",
        messages=[{"role": "user", "content": "remember this"}],
    )

    status = service.runtime_status()
    assert result.status == "error"
    assert status["last_status"] == "error"
    assert status["last_reason"] == "boom"
    assert status["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_agent_loop_schedules_review_only_for_successful_user_turn(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=FakeProvider(LLMResponse(content='{"proposals":[]}', finish_reason="stop")),
        workspace=tmp_path,
        model="fake-model",
        learning_config=BackgroundReviewConfig(enabled=True),
    )
    loop.background_review.review_turn = AsyncMock(return_value=None)
    session = Session(key="websocket:chat1")
    session.messages.extend([
        {"role": "user", "content": "remember this"},
        {"role": "assistant", "content": "done"},
    ])
    ctx = TurnContext(
        msg=InboundMessage(
            channel="websocket",
            sender_id="webui",
            chat_id="chat1",
            content="remember this",
            metadata={"message_id": "m1"},
        ),
        session_key="websocket:chat1",
        state=TurnState.SAVE,
        turn_id="turn-1",
        session=session,
        final_content="done",
        stop_reason="stop",
    )

    loop._schedule_background_review(ctx)
    await asyncio.gather(*loop._background_tasks)

    loop.background_review.review_turn.assert_awaited_once()

    loop.background_review.review_turn.reset_mock()
    ctx.stop_reason = "ask_user"
    loop._schedule_background_review(ctx)
    assert loop.background_review.review_turn.await_count == 0


@pytest.mark.asyncio
async def test_agent_loop_schedules_curator_only_for_successful_user_turn(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=FakeProvider(LLMResponse(content='{"proposals":[]}', finish_reason="stop")),
        workspace=tmp_path,
        model="fake-model",
        curator_config=CuratorConfig(enabled=True),
    )
    loop.curator.review_workspace = AsyncMock(return_value=None)
    session = Session(key="websocket:chat1")
    session.messages.extend([
        {"role": "user", "content": "remember this"},
        {"role": "assistant", "content": "done"},
    ])
    ctx = TurnContext(
        msg=InboundMessage(
            channel="websocket",
            sender_id="webui",
            chat_id="chat1",
            content="remember this",
            metadata={"message_id": "m1"},
        ),
        session_key="websocket:chat1",
        state=TurnState.SAVE,
        turn_id="turn-1",
        session=session,
        final_content="done",
        stop_reason="stop",
    )

    loop._schedule_curator_review(ctx)
    await asyncio.gather(*loop._background_tasks)

    loop.curator.review_workspace.assert_awaited_once()

    loop.curator.review_workspace.reset_mock()
    ctx.stop_reason = "ask_user"
    loop._schedule_curator_review(ctx)
    assert loop.curator.review_workspace.await_count == 0


@pytest.mark.asyncio
async def test_schedule_background_discards_completed_task(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=FakeProvider(LLMResponse(content="ok", finish_reason="stop")),
        workspace=tmp_path,
        model="fake-model",
    )

    release = asyncio.Event()

    async def _work() -> None:
        await release.wait()

    loop._schedule_background(_work())
    assert len(loop._background_tasks) == 1

    release.set()
    await asyncio.gather(*loop._background_tasks, return_exceptions=True)
    await asyncio.sleep(0)

    assert len(loop._background_tasks) == 0


@pytest.mark.asyncio
async def test_reviews_command_lists_pending_proposals(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    service = BackgroundReviewService(
        workspace=tmp_path,
        provider=FakeProvider(_proposal_response()),
        model="fake-model",
        config=BackgroundReviewConfig(enabled=True),
        store=store,
    )
    await service.review_turn(
        session_key="websocket:chat1",
        turn_id="turn-1",
        channel="websocket",
        chat_id="chat1",
        message_id="m1",
        messages=[{"role": "user", "content": "Please be direct."}],
    )

    ctx = CommandContext(
        msg=InboundMessage(
            channel="websocket",
            sender_id="webui",
            chat_id="chat1",
            content="/reviews",
            metadata={},
        ),
        session=None,
        key="websocket:chat1",
        raw="/reviews",
        loop=SimpleNamespace(background_review=service),
    )

    out = await cmd_reviews(ctx)

    assert "Background Review Proposals" in out.content
    assert "Remember preferred style" in out.content
    assert "review_" in out.content
