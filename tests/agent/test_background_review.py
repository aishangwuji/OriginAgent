from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from OpenHome.agent.background_review import BackgroundReviewService, ReviewProposalStore
from OpenHome.agent.loop import AgentLoop, TurnContext, TurnState
from OpenHome.bus.events import InboundMessage
from OpenHome.bus.queue import MessageBus
from OpenHome.command.builtin import cmd_reviews
from OpenHome.command.router import CommandContext
from OpenHome.config.schema import BackgroundReviewConfig
from OpenHome.providers.base import LLMProvider, LLMResponse
from OpenHome.session.manager import Session


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
