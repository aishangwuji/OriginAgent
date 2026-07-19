"""Tests for cache-stability-ordered block assembly in ContextAssemblerV2.

Verifies that ``assemble()`` orders blocks by a stability gradient — stable
reference blocks first, volatile blocks and ``runtime_context`` last — to
maximize DeepSeek prompt cache prefix hits (rule 22: resource governance).

Background: ``runtime_context`` carries a per-minute ``current_time`` field.
Placing it first (the previous behavior) busts the entire prompt cache prefix
on every turn. The stability gradient moves it to the tail so the stable
prefix (user profile / archived summaries / continuity) survives across
turns.
"""

from __future__ import annotations

import datetime as _datetime_module
from datetime import datetime as _real_datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from OriginAgent.agent.context import ContextBuilder as _RealContextBuilder
from OriginAgent.agent.context_assembler import ContextAssemblerV2


def _build_full_block_builder() -> MagicMock:
    """Construct a mock builder whose blocks intentionally arrive in the
    *wrong* order, so the test proves ``assemble()`` reorders them into the
    stability gradient.

    Block kinds match the real ``ContextBuilder`` KIND constants (see
    ``context.py`` lines 73-84) so the reorder logic's rank lookups exercise
    the same keys used in production.
    """
    builder = MagicMock()
    builder.timezone = "UTC"
    # Match real ContextBuilder KIND constants (context.py:73-84).
    builder.RUNTIME_CONTEXT_KIND = "runtime_context"
    builder.REFERENCE_CONTEXT_KIND = "reference_context"
    builder.INTERNAL_EVENT_KIND = "internal_event"
    builder.CONTINUITY_CONTEXT_KIND = "continuity_context"
    builder.WORKING_MEMORY_CONTEXT_KIND = "working_memory_context"
    builder.WORLD_STATE_CONTEXT_KIND = "world_state_context"
    builder.RECOVERED_CONTINUITY_CONTEXT_KIND = "recovered_continuity_context"
    builder.TASK_STATE_CONTEXT_KIND = "task_state_context"
    builder.CLOSED_EPISODE_SUMMARIES_KIND = "closed_episode_summaries"
    builder.world_state = None
    builder.session_store = None

    builder.build_user_content.return_value = [
        {"type": "text", "text": "user msg", "_meta": {"kind": "user_text"}},
    ]
    builder.build_runtime_context_block.return_value = {
        "type": "text",
        "text": "runtime",
        "_meta": {"kind": "runtime_context"},
    }
    builder.build_internal_event_block.return_value = {
        "type": "text",
        "text": "internal event",
        "_meta": {"kind": "internal_event"},
    }
    builder.prepare_prewarm_bundle.return_value = None
    # Continuity blocks intentionally shuffled: continuity first, then
    # working_memory, then world_state — the opposite of the target order.
    # assemble() must reorder to working_memory → world_state → continuity.
    builder.build_phase1_continuity_blocks.return_value = [
        {"type": "text", "text": "continuity", "_meta": {"kind": "continuity_context"}},
        {"type": "text", "text": "working memory", "_meta": {"kind": "working_memory_context"}},
        {"type": "text", "text": "world state", "_meta": {"kind": "world_state_context"}},
    ]
    # Reference blocks intentionally shuffled so stable / session-stable /
    # volatile groups are interleaved on input. assemble() must regroup them
    # into stable → session-stable → volatile order.
    builder.build_reference_context_blocks.return_value = [
        {"type": "text", "text": "memory retrieval",
         "_meta": {"kind": "reference_context", "source": "memory_retrieval"}},
        {"type": "text", "text": "user profile",
         "_meta": {"kind": "reference_context", "source": "user_profile"}},
        {"type": "text", "text": "prewarm seed",
         "_meta": {"kind": "reference_context", "source": "retrieval_prewarm_seed"}},
        {"type": "text", "text": "session search",
         "_meta": {"kind": "reference_context", "source": "retrieval_session_search"}},
        {"type": "text", "text": "archived summary",
         "_meta": {"kind": "reference_context", "source": "archived_session_summary"}},
        {"type": "text", "text": "recent history",
         "_meta": {"kind": "reference_context", "source": "recent_history"}},
        {"type": "text", "text": "closed episodes",
         "_meta": {"kind": "reference_context", "source": "closed_episode_summaries"}},
    ]

    builder.collect_assembly_audit.return_value = {
        "media": {},
        "retrieval_fusion": {
            "enabled": True,
            "sources_used": [],
            "source_counts": {},
            "deduped_count": 0,
            "trimmed_count": 0,
            "hits": {},
        },
        "governance": {},
        "prewarm": {"prewarm_empty": True},
        "governance_enabled": False,
        "prewarm_enabled": False,
    }
    return builder


def _block_label(block: dict[str, Any]) -> str:
    """Compact ``kind[:source]`` label for readable assertion failures."""
    meta = block.get("_meta", {}) if isinstance(block, dict) else {}
    kind = meta.get("kind", "?")
    source = meta.get("source")
    return f"{kind}:{source}" if source else kind


def test_block_order_stable_first() -> None:
    """assemble() must order blocks by the cache-stability gradient:

    stable reference → session-stable reference → continuity (working_memory,
    world_state, continuity) → recovered_continuity → volatile reference →
    runtime_context → internal_event → user_text.

    This maximizes the DeepSeek prompt cache stable prefix (rule 22).
    """
    builder = _build_full_block_builder()
    assembler = ContextAssemblerV2(builder)

    recovered = {
        "type": "text",
        "text": "recovered continuity",
        "_meta": {"kind": "recovered_continuity_context"},
    }

    with patch("OriginAgent.agent.context_assembler.log_event"):
        result = assembler.assemble(
            current_message="hello",
            media=None,
            channel="cli",
            chat_id="chat-1",
            sender_id="user-1",
            session_summary=None,
            session_metadata=None,
            internal_event=("cron", "fired"),
            runtime_context=None,
            session_key="cli:test-session",
            recovered_continuity_block=recovered,
            include_current_message=True,
        )

    labels = [_block_label(b) for b in result.blocks]

    # 1. Stable reference group first, in stability order.
    stable_expected = [
        "reference_context:user_profile",
        "reference_context:archived_session_summary",
        "reference_context:closed_episode_summaries",
    ]
    stable_actual = [lab for lab in labels if lab in stable_expected]
    assert stable_actual == stable_expected, (
        f"stable reference blocks not in expected order: {stable_actual}; full: {labels}"
    )

    # 2. Session-stable reference (prewarm seed) follows the stable group.
    prewarm_idx = labels.index("reference_context:retrieval_prewarm_seed")
    last_stable_idx = max(labels.index(k) for k in stable_expected)
    assert prewarm_idx > last_stable_idx, (
        f"retrieval_prewarm_seed ({prewarm_idx}) must follow stable group "
        f"(ends at {last_stable_idx}): {labels}"
    )

    # 3. Continuity group: working_memory → world_state → continuity.
    wm_idx = labels.index("working_memory_context")
    ws_idx = labels.index("world_state_context")
    ct_idx = labels.index("continuity_context")
    assert wm_idx < ws_idx < ct_idx, (
        f"continuity blocks must be working_memory → world_state → "
        f"continuity; got working_memory={wm_idx}, world_state={ws_idx}, "
        f"continuity={ct_idx}: {labels}"
    )

    # 4. Recovered continuity follows the continuity group.
    rec_idx = labels.index("recovered_continuity_context")
    assert rec_idx > ct_idx, (
        f"recovered_continuity ({rec_idx}) must follow continuity_context "
        f"({ct_idx}): {labels}"
    )

    # 5. Volatile reference group follows recovered continuity.
    volatile_expected = [
        "reference_context:recent_history",
        "reference_context:memory_retrieval",
        "reference_context:retrieval_session_search",
    ]
    for v in volatile_expected:
        assert labels.index(v) > rec_idx, (
            f"volatile reference {v} must follow recovered_continuity: {labels}"
        )
    volatile_actual = [lab for lab in labels if lab in volatile_expected]
    assert volatile_actual == volatile_expected, (
        f"volatile reference blocks not in expected order: {volatile_actual}; "
        f"full: {labels}"
    )

    # 6. runtime_context follows all reference blocks (per-minute clock → tail).
    rt_idx = labels.index("runtime_context")
    for v in volatile_expected:
        assert rt_idx > labels.index(v), (
            f"runtime_context ({rt_idx}) must follow volatile reference "
            f"{v}: {labels}"
        )

    # 7. internal_event follows runtime_context.
    ie_idx = labels.index("internal_event")
    assert ie_idx > rt_idx, (
        f"internal_event ({ie_idx}) must follow runtime_context ({rt_idx}): "
        f"{labels}"
    )

    # 8. user_text is always last (changes every turn by definition).
    assert labels[-1] == "user_text", (
        f"user_text must be last: {labels}"
    )


def test_runtime_context_not_first() -> None:
    """runtime_context must NOT be the first block.

    It carries the per-minute ``current_time`` and would bust the entire
    DeepSeek prompt cache prefix if placed at the head. This is the core
    regression guard for the cache-stability gradient.
    """
    builder = _build_full_block_builder()
    assembler = ContextAssemblerV2(builder)

    with patch("OriginAgent.agent.context_assembler.log_event"):
        result = assembler.assemble(
            current_message="hello",
            media=None,
            channel="cli",
            chat_id="chat-1",
            sender_id="user-1",
            session_summary=None,
            session_metadata=None,
            internal_event=None,
            runtime_context=None,
            session_key="cli:test-session",
            recovered_continuity_block=None,
            include_current_message=True,
        )

    labels = [_block_label(b) for b in result.blocks]
    assert labels[0] != "runtime_context", (
        f"runtime_context must not be first (cache-stability gradient): {labels}"
    )
    # runtime_context still present in the user message (constraint: must
    # remain readable by the LLM, just ordered last among metadata blocks).
    assert "runtime_context" in labels, (
        f"runtime_context must still be present in the user message: {labels}"
    )
    # And user_text remains the very last block.
    assert labels[-1] == "user_text", (
        f"user_text must be last: {labels}"
    )


# ---------------------------------------------------------------------------
# End-to-end prompt-cache stability tests (Tasks 1.1 / 1.2 / 1.3).
#
# These tests verify the *combined* effect of the three Task-1 sub-fixes on
# the actual assembled user_content:
#   * Task 1.1 — ``current_time`` is emitted at HH:MM precision via
#     ``current_time_str_minutes`` so it doesn't change within a minute.
#   * Task 1.2 — block order follows a stability gradient; runtime_context
#     (volatile) is moved to the tail so the stable prefix survives.
#   * Task 1.3 — DeepSeek ``supports_prompt_caching=True``; the
#     ``llm.response`` log emits ``cached_tokens`` and a WARN on cache miss.
#
# Mock strategy:
#   * Tests 1 & 2 use a mock ContextBuilder whose stable blocks are
#     deterministic, but whose ``build_runtime_context_block`` delegates to
#     the *real* ``ContextBuilder.build_runtime_context_text``. This makes
#     the time-dependent behavior of the runtime_context block authentic
#     (exercising Task 1.1's ``current_time_str_minutes``) while keeping the
#     stable prefix byte-deterministic for assertion.
#   * ``datetime.now()`` is mocked via ``monkeypatch.setattr`` on the
#     ``datetime`` module so the ``from datetime import datetime`` lookup
#     inside ``current_time_str_minutes`` resolves to the fake class.
#   * Test 3 mocks the LLM provider to return a ``LLMResponse`` with
#     ``cached_tokens=1200`` and asserts the ``llm.response`` log event
#     carries the field — verifying Task 1.3's observability path.
# ---------------------------------------------------------------------------


class _FakeDatetime(_real_datetime):
    """Test double for ``datetime.datetime`` with a controllable ``current``.

    ``current_time_str_minutes`` does ``from datetime import datetime`` which
    resolves through the ``datetime`` module's attribute lookup, so patching
    ``datetime_module.datetime`` to this class makes ``datetime.now()`` inside
    the helper return our controlled value.
    """

    current = _real_datetime(2026, 7, 19, 14, 30, 15)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.current


def _build_cache_e2e_builder() -> MagicMock:
    """Construct a mock builder with deterministic stable blocks but a *real*
    time-dependent runtime_context block.

    The stable blocks (user_profile, prewarm_seed, working_memory,
    recent_history) are fixed strings so the prefix is byte-deterministic.
    The runtime_context block delegates to
    ``ContextBuilder.build_runtime_context_text`` so it genuinely reflects
    ``current_time_str_minutes(timezone)`` — exercising Task 1.1's minute-
    precision behavior end-to-end.
    """
    builder = MagicMock()
    builder.timezone = "UTC"
    # Match real ContextBuilder KIND constants (context.py:93-104).
    builder.RUNTIME_CONTEXT_KIND = "runtime_context"
    builder.REFERENCE_CONTEXT_KIND = "reference_context"
    builder.INTERNAL_EVENT_KIND = "internal_event"
    builder.CONTINUITY_CONTEXT_KIND = "continuity_context"
    builder.WORKING_MEMORY_CONTEXT_KIND = "working_memory_context"
    builder.WORLD_STATE_CONTEXT_KIND = "world_state_context"
    builder.RECOVERED_CONTINUITY_CONTEXT_KIND = "recovered_continuity_context"
    builder.TASK_STATE_CONTEXT_KIND = "task_state_context"
    builder.CLOSED_EPISODE_SUMMARIES_KIND = "closed_episode_summaries"
    builder.world_state = None
    builder.session_store = None

    # Deterministic stable / session-stable / volatile reference blocks.
    # The stable prefix (user_profile + prewarm_seed + working_memory +
    # recent_history) must exceed 500 bytes so the cache-stability assertion
    # is meaningful (task requirement: "至少前 500 字节稳定即可").
    builder.build_reference_context_blocks.return_value = [
        {"type": "text",
         "text": (
             "<reference_context source='user_profile'>\n"
             "Test User\n"
             "Lang: en\n"
             "Timezone: UTC\n"
             "Preferences: terse answers, dark mode, weekly digest on Mondays\n"
             "Active projects: OriginAgent prompt-cache fix, BDI deliberation refactoring\n"
             "Communication style: direct, no preamble, code-first explanations\n"
             "</reference_context>"
         ),
         "_meta": {"kind": "reference_context", "source": "user_profile"}},
        {"type": "text",
         "text": (
             "<reference_context source='retrieval_prewarm_seed'>\n"
             "seed-payload-v1\n"
             "Topics: prompt cache, deepseek, prefix stability\n"
             "</reference_context>"
         ),
         "_meta": {"kind": "reference_context", "source": "retrieval_prewarm_seed"}},
        {"type": "text",
         "text": (
             "<reference_context source='recent_history'>\n"
             "last user msg here\n"
             "</reference_context>"
         ),
         "_meta": {"kind": "reference_context", "source": "recent_history"}},
    ]
    # Deterministic continuity block (working_memory).
    builder.build_phase1_continuity_blocks.return_value = [
        {"type": "text",
         "text": (
             "<working_memory_context>\n"
             "active task: ship prompt-cache fix\n"
             "open loops: verify HH:MM precision, verify block order gradient\n"
             "</working_memory_context>"
         ),
         "_meta": {"kind": "working_memory_context"}},
    ]
    builder.build_user_content.return_value = [
        {"type": "text", "text": "user msg", "_meta": {"kind": "user_text"}},
    ]

    # Real time-dependent runtime_context block — delegates to the actual
    # ContextBuilder.build_runtime_context_block so current_time_str_minutes
    # is exercised authentically. This produces a real runtime_context block
    # whose ``current_time`` field reflects the mocked datetime.
    builder.build_runtime_context_block.side_effect = (
        _RealContextBuilder.build_runtime_context_block
    )
    builder.build_internal_event_block.return_value = {
        "type": "text", "text": "internal event", "_meta": {"kind": "internal_event"},
    }
    builder.prepare_prewarm_bundle.return_value = None
    builder.collect_assembly_audit.return_value = {
        "media": {},
        "retrieval_fusion": {
            "enabled": True, "sources_used": [], "source_counts": {},
            "deduped_count": 0, "trimmed_count": 0, "hits": {},
        },
        "governance": {},
        "prewarm": {"prewarm_empty": True},
        "governance_enabled": False,
        "prewarm_enabled": False,
    }
    return builder


def _joined_block_text(blocks) -> str:
    """Concatenate the ``text`` field of all text blocks in order."""
    return "\n".join(
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _prefix_before_runtime(result) -> str:
    """Return the joined text of all blocks strictly before runtime_context."""
    prefix_blocks: list[dict] = []
    for block in result.blocks:
        if not isinstance(block, dict):
            continue
        if block.get("_meta", {}).get("kind") == "runtime_context":
            break
        prefix_blocks.append(block)
    return _joined_block_text(prefix_blocks)


def test_same_minute_produces_identical_prefix(monkeypatch) -> None:
    """Within the same minute, the assembled prompt must be byte-identical.

    DeepSeek's automatic prefix caching requires the prompt prefix to be
    byte-stable across calls. ``current_time`` is emitted at HH:MM precision
    (Task 1.1), so within the same minute the entire assembled user_content
    (including the runtime_context block) must be byte-identical — fulfilling
    the prefix-cache hit prerequisite.

    Mock strategy: control ``datetime.now()`` to return two different seconds
    (14:30:15 and 14:30:59) within the same minute; the rest of the assembly
    pipeline runs through the real ``ContextAssemblerV2.assemble`` and the
    real ``current_time_str_minutes``.
    """
    monkeypatch.setattr(_datetime_module, "datetime", _FakeDatetime)

    builder = _build_cache_e2e_builder()
    assembler = ContextAssemblerV2(builder)

    # First call at 14:30:15.
    _FakeDatetime.current = _real_datetime(2026, 7, 19, 14, 30, 15)
    with patch("OriginAgent.agent.context_assembler.log_event"):
        result1 = assembler.assemble(
            current_message="hello",
            media=None,
            channel="cli",
            chat_id="chat-1",
            sender_id="user-1",
            session_summary=None,
            session_metadata=None,
            internal_event=None,
            runtime_context=None,
            session_key="cli:test-session",
            recovered_continuity_block=None,
            include_current_message=True,
        )

    # Second call at 14:30:59 — same minute, different second.
    _FakeDatetime.current = _real_datetime(2026, 7, 19, 14, 30, 59)
    with patch("OriginAgent.agent.context_assembler.log_event"):
        result2 = assembler.assemble(
            current_message="hello",
            media=None,
            channel="cli",
            chat_id="chat-1",
            sender_id="user-1",
            session_summary=None,
            session_metadata=None,
            internal_event=None,
            runtime_context=None,
            session_key="cli:test-session",
            recovered_continuity_block=None,
            include_current_message=True,
        )

    text1 = _joined_block_text(result1.blocks)
    text2 = _joined_block_text(result2.blocks)

    # Within the same minute, HH:MM doesn't change → entire prompt must be
    # byte-identical (the DeepSeek prefix-cache hit prerequisite).
    assert text1 == text2, (
        "Same-minute calls must produce byte-identical prompts; "
        "current_time_str_minutes should return the same HH:MM value. "
        f"First divergence:\ntext1[:500]={text1[:500]!r}\n"
        f"text2[:500]={text2[:500]!r}"
    )

    # Sanity: the stable prefix is at least 500 bytes (so the assertion is
    # meaningful, not vacuously true on a near-empty prompt).
    prefix1 = _prefix_before_runtime(result1)
    assert len(prefix1) >= 500, (
        f"Stable prefix too short ({len(prefix1)} bytes) for a meaningful "
        f"cache-stability assertion; check test fixture."
    )


def test_different_minute_only_runtime_context_changes(monkeypatch) -> None:
    """Across minute boundaries, only runtime_context's current_time changes.

    The stable prefix (user_profile, prewarm_seed, working_memory,
    recent_history) must be byte-identical across minute boundaries — this is
    what DeepSeek's prefix cache can still hit on after the per-minute clock
    tick. Only the runtime_context block (which carries ``current_time`` at
    HH:MM precision) should differ.

    This is the cross-minute partial-hit guarantee: the cache-miss portion is
    limited to the volatile tail (runtime_context + user_text), not the
    entire prompt.
    """
    monkeypatch.setattr(_datetime_module, "datetime", _FakeDatetime)

    builder = _build_cache_e2e_builder()
    assembler = ContextAssemblerV2(builder)

    # First call at 14:30:00.
    _FakeDatetime.current = _real_datetime(2026, 7, 19, 14, 30, 0)
    with patch("OriginAgent.agent.context_assembler.log_event"):
        result1 = assembler.assemble(
            current_message="hello",
            media=None,
            channel="cli",
            chat_id="chat-1",
            sender_id="user-1",
            session_summary=None,
            session_metadata=None,
            internal_event=None,
            runtime_context=None,
            session_key="cli:test-session",
            recovered_continuity_block=None,
            include_current_message=True,
        )

    # Second call at 14:31:00 — different minute.
    _FakeDatetime.current = _real_datetime(2026, 7, 19, 14, 31, 0)
    with patch("OriginAgent.agent.context_assembler.log_event"):
        result2 = assembler.assemble(
            current_message="hello",
            media=None,
            channel="cli",
            chat_id="chat-1",
            sender_id="user-1",
            session_summary=None,
            session_metadata=None,
            internal_event=None,
            runtime_context=None,
            session_key="cli:test-session",
            recovered_continuity_block=None,
            include_current_message=True,
        )

    # 1. Stable prefix (everything before runtime_context) must be byte-
    #    identical across minute boundaries — this is the partial-hit segment.
    prefix1 = _prefix_before_runtime(result1)
    prefix2 = _prefix_before_runtime(result2)
    assert prefix1 == prefix2, (
        "Stable prefix must be byte-identical across minute boundaries; "
        "only runtime_context should change. "
        f"prefix1={prefix1!r}\nprefix2={prefix2!r}"
    )

    # 2. Locate the runtime_context block in each result and verify its
    #    current_time field actually differs across minutes.
    def _find_runtime_block(result):
        for block in result.blocks:
            if isinstance(block, dict) and block.get("_meta", {}).get("kind") == "runtime_context":
                return block
        return None

    rt1 = _find_runtime_block(result1)
    rt2 = _find_runtime_block(result2)
    assert rt1 is not None and rt2 is not None, (
        "runtime_context block must be present in assembled output"
    )
    assert "14:30" in rt1["text"], (
        f"First call (14:30) runtime_context must contain '14:30'; got: {rt1['text']!r}"
    )
    assert "14:31" in rt2["text"], (
        f"Second call (14:31) runtime_context must contain '14:31'; got: {rt2['text']!r}"
    )
    # The runtime_context block text itself must differ across minutes.
    assert rt1["text"] != rt2["text"], (
        "runtime_context block text must differ across minute boundaries "
        "(current_time at HH:MM precision should tick)"
    )

    # 3. The full prompt differs (because runtime_context differs).
    full1 = _joined_block_text(result1.blocks)
    full2 = _joined_block_text(result2.blocks)
    assert full1 != full2, (
        "Full prompt must differ across minute boundaries (runtime_context "
        "current_time should tick)"
    )


@pytest.mark.asyncio
async def test_cached_tokens_in_llm_response_log() -> None:
    """``event.llm.response`` log must carry the ``cached_tokens`` field.

    Verifies Task 1.3's observability path end-to-end: provider returns a
    response whose ``usage`` contains ``cached_tokens=1200``;
    ``AgentRunner._request_model``'s ``_log_llm_response`` helper must emit
    ``cached_tokens=1200`` in the ``llm.response`` log event.

    The test uses the real DeepSeek ProviderSpec (which Task 1.3 set to
    ``supports_prompt_caching=True``) so the cache-miss WARN branch is also
    exercised — but with ``cached_tokens=1200 > 0``, no WARN should fire.
    """
    from OriginAgent.agent.hook import AgentHook, AgentHookContext
    from OriginAgent.agent.runner import (
        AgentRunner,
        AgentRunSpec,
        _cache_miss_warn_last,
    )
    from OriginAgent.config.schema import AgentDefaults
    from OriginAgent.providers.base import LLMResponse
    from OriginAgent.providers.registry import find_by_name

    # Use the real DeepSeek spec — verifies Task 1.3's
    # supports_prompt_caching=True setting end-to-end.
    deepseek_spec = find_by_name("deepseek")
    assert deepseek_spec is not None, "DeepSeek ProviderSpec not found in registry"
    assert deepseek_spec.supports_prompt_caching is True, (
        "Task 1.3: DeepSeek spec must declare supports_prompt_caching=True"
    )

    provider = MagicMock()
    provider.supports_progress_deltas = False
    provider._spec = deepseek_spec
    fake_response = LLMResponse(
        content="done",
        tool_calls=[],
        finish_reason="stop",
        usage={
            "prompt_tokens": 1500,
            "completion_tokens": 200,
            "total_tokens": 1700,
            "cached_tokens": 1200,
        },
    )
    provider.chat_with_retry = AsyncMock(return_value=fake_response)

    # Clear the cache-miss WARN dedup map so the test is isolated from any
    # prior test that may have populated the (deepseek, deepseek-chat) key.
    _cache_miss_warn_last.clear()

    runner = AgentRunner(provider)
    tools = MagicMock()
    tools.get_definitions.return_value = []

    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        model="deepseek-chat",
        max_iterations=1,
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        session_key="test-session",
        llm_timeout_s=0,  # disable outer timeout; go through return-response path
    )
    hook = AgentHook()
    context = AgentHookContext(iteration=0, messages=spec.initial_messages)

    with patch("OriginAgent.agent.runner.log_event") as mock_log_event:
        await runner._request_model(provider, spec, spec.initial_messages, hook, context)

    # Find the llm.response event call.
    resp_call = None
    for call in mock_log_event.call_args_list:
        if call.args and call.args[0] == "llm.response":
            resp_call = call
            break
    assert resp_call is not None, "llm.response event was not logged"
    kwargs = resp_call.kwargs
    assert "cached_tokens" in kwargs, (
        "llm.response log must include cached_tokens field for observability "
        "(Task 1.3)"
    )
    assert kwargs["cached_tokens"] == 1200, (
        f"Expected cached_tokens=1200 in llm.response log; got "
        f"{kwargs['cached_tokens']}"
    )
