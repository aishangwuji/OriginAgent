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


# ---------------------------------------------------------------------------
# P1-a: Background review invalid JSON — reasoning_content fallback + diagnostics
# ---------------------------------------------------------------------------


def _capture_loguru_warnings() -> tuple[list[str], int]:
    """Add a loguru sink that collects WARNING-level messages.

    Returns (messages, handler_id) so the caller can remove the sink.
    """
    import loguru

    messages: list[str] = []

    def sink(message) -> None:
        record = message.record
        if record["level"].name == "WARNING":
            messages.append(record["message"])

    handler_id = loguru.logger.add(sink, format="{message}", level="WARNING")
    return messages, handler_id


def test_load_json_payload_empty_string_returns_none_no_warning() -> None:
    """Empty content must not emit a warning — the model may legitimately
    return empty when there's nothing to propose.

    Regression: ``Background review returned invalid JSON`` warning fired
    repeatedly even on empty responses, flooding the logs.
    """
    import loguru
    from OriginAgent.agent.background_review import _load_json_payload

    messages, handler_id = _capture_loguru_warnings()
    try:
        result = _load_json_payload("")
    finally:
        loguru.logger.remove(handler_id)
    assert result is None
    assert not any("invalid JSON" in m for m in messages)


def test_load_json_payload_whitespace_only_returns_none_no_warning() -> None:
    """Whitespace-only content is equivalent to empty — no warning."""
    import loguru
    from OriginAgent.agent.background_review import _load_json_payload

    messages, handler_id = _capture_loguru_warnings()
    try:
        result = _load_json_payload("   \n  \t  ")
    finally:
        loguru.logger.remove(handler_id)
    assert result is None
    assert not any("invalid JSON" in m for m in messages)


def test_load_json_payload_invalid_json_warns_with_diagnostics() -> None:
    """Invalid JSON must warn with char count + preview for diagnosis.

    Regression: original warning was just ``"Background review returned
    invalid JSON"`` with no context, making it impossible to diagnose
    what the model actually returned.
    """
    import loguru
    from OriginAgent.agent.background_review import _load_json_payload

    bad_text = "This is not JSON at all, just plain text from the model."
    messages, handler_id = _capture_loguru_warnings()
    try:
        result = _load_json_payload(bad_text)
    finally:
        loguru.logger.remove(handler_id)
    assert result is None
    warnings = [m for m in messages if "invalid JSON" in m]
    assert len(warnings) == 1, "exactly one warning expected"
    msg = warnings[0]
    # Diagnostic info: char count
    assert str(len(bad_text)) in msg
    # Diagnostic info: preview of the content
    assert "This is not JSON" in msg


def test_load_json_payload_valid_json_returns_parsed() -> None:
    """Sanity check: valid JSON still parses correctly."""
    from OriginAgent.agent.background_review import _load_json_payload

    payload = _load_json_payload('{"proposals": []}')
    assert payload == {"proposals": []}


def test_load_json_payload_fenced_json_returns_parsed() -> None:
    """Sanity check: ```json fenced content still parses correctly."""
    from OriginAgent.agent.background_review import _load_json_payload

    payload = _load_json_payload('```json\n{"proposals": []}\n```')
    assert payload == {"proposals": []}


@pytest.mark.asyncio
async def test_review_turn_falls_back_to_reasoning_content(tmp_path: Path) -> None:
    """When ``response.content`` is empty, use ``reasoning_content``.

    Regression: DeepSeek-R1 and similar reasoning models sometimes put
    the JSON payload in ``reasoning_content`` and leave ``content``
    empty. The original code only checked ``content``, causing every
    such response to log ``invalid JSON`` and drop all proposals.

    Mirrors the fallback in ``meta_cognition_reflector.py:397``.
    """
    proposal_json = json.dumps({
        "proposals": [
            {
                "type": "memory",
                "domain_id": "core",
                "title": "From reasoning content",
                "content": "User prefers concise answers.",
                "rationale": "Stated explicitly.",
                "confidence": 0.8,
            }
        ]
    })
    provider = FakeProvider(LLMResponse(
        content="",  # empty content
        reasoning_content=proposal_json,  # JSON in reasoning_content
        finish_reason="stop",
    ))
    service = BackgroundReviewService(
        workspace=tmp_path,
        provider=provider,
        model="fake-model",
        config=BackgroundReviewConfig(enabled=True),
    )

    result = await service.review_turn(
        session_key="websocket:chat1",
        turn_id="turn-1",
        channel="websocket",
        chat_id="chat1",
        message_id="m1",
        messages=[{"role": "user", "content": "remember this"}],
    )

    assert result.status == "ok"
    assert result.proposals_written == 1
    # Verify the proposal was actually written
    records = service.store.iter_all()
    assert len(records) == 1
    assert records[0]["title"] == "From reasoning content"


@pytest.mark.asyncio
async def test_review_turn_prefers_content_over_reasoning_content(
    tmp_path: Path,
) -> None:
    """When both fields are present, ``content`` takes precedence.

    Ensures the fallback doesn't accidentally override the primary
    content path.
    """
    content_json = json.dumps({
        "proposals": [
            {
                "type": "memory",
                "domain_id": "core",
                "title": "From content",
                "content": "Primary path.",
            }
        ]
    })
    reasoning_json = json.dumps({
        "proposals": [
            {
                "type": "memory",
                "domain_id": "core",
                "title": "From reasoning",
                "content": "Should not be used.",
            }
        ]
    })
    provider = FakeProvider(LLMResponse(
        content=content_json,
        reasoning_content=reasoning_json,
        finish_reason="stop",
    ))
    service = BackgroundReviewService(
        workspace=tmp_path,
        provider=provider,
        model="fake-model",
        config=BackgroundReviewConfig(enabled=True),
    )

    result = await service.review_turn(
        session_key="websocket:chat1",
        turn_id="turn-1",
        channel="websocket",
        chat_id="chat1",
        message_id="m1",
        messages=[{"role": "user", "content": "remember this"}],
    )

    assert result.status == "ok"
    records = service.store.iter_all()
    assert len(records) == 1
    assert records[0]["title"] == "From content"


@pytest.mark.asyncio
async def test_review_turn_falls_back_to_reasoning_when_content_not_json(
    tmp_path: Path,
) -> None:
    """When ``content`` is non-empty but NOT valid JSON, fall back to
    ``reasoning_content``.

    Regression observed in production (2026-07-17 22:26): DeepSeek-R1
    put its reasoning process into ``content`` (9405 chars of "We are
    given a conversation...") and the actual JSON proposals into
    ``reasoning_content``. The original ``content or reasoning_content``
    fallback only triggered when content was EMPTY — when content is
    non-JSON prose, the fallback never ran and proposals were dropped.

    Fix: try ``content`` first; if it doesn't parse as JSON, try
    ``reasoning_content`` as a secondary fallback.
    """
    reasoning_json = json.dumps({
        "proposals": [
            {
                "type": "memory",
                "domain_id": "core",
                "title": "From reasoning fallback",
                "content": "User prefers concise answers.",
                "rationale": "Stated explicitly.",
                "confidence": 0.8,
            }
        ]
    })
    # content is non-empty prose (not JSON) — simulates reasoning model
    # putting its thought process in content
    prose_content = (
        "We are given a conversation from a cron channel. "
        "The user message is a scheduled reminder. "
        "Let's analyze the evidence before producing proposals."
    )
    provider = FakeProvider(LLMResponse(
        content=prose_content,
        reasoning_content=reasoning_json,
        finish_reason="stop",
    ))
    service = BackgroundReviewService(
        workspace=tmp_path,
        provider=provider,
        model="fake-model",
        config=BackgroundReviewConfig(enabled=True),
    )

    result = await service.review_turn(
        session_key="websocket:chat1",
        turn_id="turn-1",
        channel="websocket",
        chat_id="chat1",
        message_id="m1",
        messages=[{"role": "user", "content": "remember this"}],
    )

    assert result.status == "ok"
    assert result.proposals_written == 1
    records = service.store.iter_all()
    assert len(records) == 1
    assert records[0]["title"] == "From reasoning fallback"


@pytest.mark.asyncio
async def test_review_turn_warns_once_when_both_content_and_reasoning_invalid(
    tmp_path: Path,
) -> None:
    """When BOTH ``content`` and ``reasoning_content`` fail to parse as
    JSON, warn exactly once (not twice) and return zero proposals.

    Ensures the secondary fallback doesn't double-warn when both fields
    contain invalid data.
    """
    import loguru
    provider = FakeProvider(LLMResponse(
        content="This is prose, not JSON.",
        reasoning_content="Also prose, also not JSON.",
        finish_reason="stop",
    ))
    service = BackgroundReviewService(
        workspace=tmp_path,
        provider=provider,
        model="fake-model",
        config=BackgroundReviewConfig(enabled=True),
    )

    # Capture warnings
    warnings: list[str] = []

    def sink(message) -> None:
        record = message.record
        if record["level"].name == "WARNING":
            warnings.append(record["message"])

    handler_id = loguru.logger.add(sink, format="{message}", level="WARNING")
    try:
        result = await service.review_turn(
            session_key="websocket:chat1",
            turn_id="turn-1",
            channel="websocket",
            chat_id="chat1",
            message_id="m1",
            messages=[{"role": "user", "content": "remember this"}],
        )
    finally:
        loguru.logger.remove(handler_id)

    # Should complete without crash, zero proposals
    assert result.status == "ok"
    assert result.proposals_written == 0
    # Exactly one "invalid JSON" warning (from the final fallback attempt)
    invalid_json_warnings = [w for w in warnings if "invalid JSON" in w]
    assert len(invalid_json_warnings) == 1, (
        f"expected exactly 1 invalid JSON warning, got {len(invalid_json_warnings)}: "
        f"{invalid_json_warnings}"
    )


@pytest.mark.asyncio
async def test_review_turn_uses_configured_max_tokens(tmp_path: Path) -> None:
    """``max_tokens`` must be read from ``BackgroundReviewConfig`` and passed
    to the LLM call, not hardcoded.

    Regression (production logs 2026-07-17 22:44-22:50): deepseek-v4-flash
    is a reasoning model. With ``max_tokens=2048`` (the old hardcoded value),
    reasoning_content consumed the entire completion budget (8505-9377 chars
    ≈ 2000-3500 tokens) and ``content`` was left empty — the model never had
    room to output the final JSON. Raising to 8192 gives reasoning and
    content separate headroom. See rule 17 (no hardcoded magic values).
    """
    provider = FakeProvider(_proposal_response())
    service = BackgroundReviewService(
        workspace=tmp_path,
        provider=provider,
        model="fake-model",
        config=BackgroundReviewConfig(enabled=True, max_tokens=8192),
    )

    await service.review_turn(
        session_key="websocket:chat1",
        turn_id="turn-1",
        channel="websocket",
        chat_id="chat1",
        message_id="m1",
        messages=[{"role": "user", "content": "remember this"}],
    )

    assert len(provider.calls) == 1
    assert provider.calls[0]["max_tokens"] == 8192, (
        f"expected max_tokens=8192 from config, got {provider.calls[0].get('max_tokens')}"
    )


@pytest.mark.asyncio
async def test_review_turn_default_max_tokens_is_16384(tmp_path: Path) -> None:
    """Default ``max_tokens`` must be 16384 (raised from 8192 on 2026-07-18).

    The default must be large enough for reasoning models to output both
    reasoning_content AND the final JSON in content. Production logs
    (2026-07-17 22:44-22:50) showed deepseek-v4-flash consuming ~8000+
    tokens in reasoning_content alone under the 8192 budget, leaving the
    JSON in ``content`` truncated. Raising to 16384 gives reasoning and
    content separate headroom.
    """
    provider = FakeProvider(_proposal_response())
    service = BackgroundReviewService(
        workspace=tmp_path,
        provider=provider,
        model="fake-model",
        config=BackgroundReviewConfig(enabled=True),
    )

    await service.review_turn(
        session_key="websocket:chat1",
        turn_id="turn-1",
        channel="websocket",
        chat_id="chat1",
        message_id="m1",
        messages=[{"role": "user", "content": "remember this"}],
    )

    assert len(provider.calls) == 1
    assert provider.calls[0]["max_tokens"] == 16384, (
        f"expected default max_tokens=16384, got {provider.calls[0].get('max_tokens')}"
    )


def test_background_review_prompt_guides_reasoning_model_to_output_json(
    tmp_path: Path,
) -> None:
    """The system prompt must explicitly guide reasoning models to put their
    final JSON in the ``content`` field.

    Reasoning models (DeepSeek-R1, Kimi, MiMo, deepseek-v4-flash) may put
    their entire thinking process in ``reasoning_content`` and leave
    ``content`` empty if not explicitly told otherwise. The prompt must
    contain an instruction that makes the output location unambiguous.
    """
    from OriginAgent.utils.prompt_templates import render_template

    rendered = render_template(
        "agent/background_review.md",
        strip=True,
        allowed_types="memory, fact, skill",
        max_proposals=8,
    )
    # The prompt must explicitly mention "content" as the output location
    # to prevent reasoning models from leaving it empty.
    assert "content" in rendered.lower(), (
        "prompt must mention 'content' to guide reasoning models where to put JSON"
    )
    # Must explicitly tell reasoning models not to leave content empty.
    guidance_keywords = ["do not leave", "must appear", "in the content", "content field"]
    assert any(kw in rendered.lower() for kw in guidance_keywords), (
        f"prompt must contain explicit guidance for reasoning models "
        f"(looked for {guidance_keywords})"
    )
