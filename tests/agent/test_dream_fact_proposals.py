import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.facts import (
    FactDeprecationProposal,
    FactProposal,
    parse_fact_proposal_response,
    validate_deprecation_proposal,
    validate_fact_proposal,
)
from OriginAgent.agent.memory import Dream, MemoryStore
from OriginAgent.agent.runner import AgentRunResult


def _json(upserts=None, deprecations=None, hints=None):
    return json.dumps({
        "facts_to_upsert": upserts or [],
        "facts_to_deprecate": deprecations or [],
        "memory_render_hints": hints or [],
    })


def _proposal(**overrides):
    data = {
        "content": "User prefers warm lights after 22:00",
        "category": "preference",
        "scope": "home.living_room.lighting",
        "owner": "user",
        "source_cursors": [1],
        "source_excerpt": "after 22:00 please make the living room lights warm",
        "confidence": 0.86,
        "expires_at": None,
        "supersedes_fact_id": None,
        "requires_confirmation": None,
        "status": None,
        "reason": "User explicitly stated a lighting preference.",
    }
    data.update(overrides)
    return data


def _history(cursor=1, content="after 22:00 please make the living room lights warm"):
    return [{"cursor": cursor, "timestamp": "2026-05-15 12:00", "content": content}]


def _run_result(stop_reason="completed"):
    return AgentRunResult(
        final_content=stop_reason,
        stop_reason=stop_reason,
        messages=[],
        tools_used=[],
        usage={},
        tool_events=[],
    )


def _store(tmp_path):
    store = MemoryStore(tmp_path)
    store.write_soul("# Soul\n")
    store.write_user("# User\n")
    store.write_memory("# Memory\n- original\n")
    return store


def test_parse_fact_proposal_json_and_fenced_json():
    batch = parse_fact_proposal_response(_json([_proposal()]))
    fenced = parse_fact_proposal_response(f"```json\n{_json([_proposal()])}\n```")

    assert len(batch.facts_to_upsert) == 1
    assert len(fenced.facts_to_upsert) == 1
    assert batch.facts_to_upsert[0].source_cursors == [1]


def test_parse_defaults_missing_lists_to_empty():
    batch = parse_fact_proposal_response("{}")

    assert batch.facts_to_upsert == []
    assert batch.facts_to_deprecate == []
    assert batch.memory_render_hints == []


def test_parse_invalid_top_level_json_fails_batch():
    with pytest.raises(ValueError):
        parse_fact_proposal_response("{not json")


def test_parse_bad_single_proposal_is_rejected_but_good_continues():
    batch = parse_fact_proposal_response(_json([
        {"content": "missing required fields"},
        _proposal(content="User prefers quiet notifications", scope="user.notifications"),
    ]))

    assert len(batch.facts_to_upsert) == 1
    assert len(batch.parse_rejected) == 1
    assert batch.facts_to_upsert[0].content == "User prefers quiet notifications"


def test_validate_rejects_missing_source_cursors():
    proposal = FactProposal(**_proposal(source_cursors=[]))

    result = validate_fact_proposal(
        proposal,
        existing_facts=[],
        history_entries=_history(),
        batch_cursor_min=1,
        batch_cursor_max=1,
    )

    assert result.decision == "reject"
    assert any(issue.code == "missing_source_cursors" for issue in result.issues)


def test_validate_rejects_source_cursor_outside_batch():
    proposal = FactProposal(**_proposal(source_cursors=[99]))

    result = validate_fact_proposal(
        proposal,
        existing_facts=[],
        history_entries=_history(),
        batch_cursor_min=1,
        batch_cursor_max=1,
    )

    assert result.decision == "reject"
    assert any(issue.code == "source_cursor_out_of_batch" for issue in result.issues)


def test_validate_rejects_missing_source_excerpt():
    proposal = FactProposal(**_proposal(source_excerpt=""))

    result = validate_fact_proposal(
        proposal,
        existing_facts=[],
        history_entries=_history(),
        batch_cursor_min=1,
        batch_cursor_max=1,
    )

    assert result.decision == "reject"
    assert any(issue.code == "missing_source_excerpt" for issue in result.issues)


def test_validate_rejects_source_excerpt_not_found_in_cited_cursor():
    proposal = FactProposal(**_proposal(
        source_excerpt="User explicitly said lock the door at 22:00",
    ))

    result = validate_fact_proposal(
        proposal,
        existing_facts=[],
        history_entries=_history(content="User asked about warm lighting only"),
        batch_cursor_min=1,
        batch_cursor_max=1,
    )

    assert result.decision == "reject"
    assert any(issue.code == "source_excerpt_not_found" for issue in result.issues)


@pytest.mark.parametrize(
    "proposal",
    [
        _proposal(category="policy", scope="home.entry.lock", content="Guests cannot unlock the door"),
        _proposal(category="safety", scope="home.kitchen.gas", content="Alert if gas is detected"),
        _proposal(category="preference", scope="home.entry", content="Unlock the front door for guests"),
    ],
)
def test_validate_policy_safety_and_high_risk_go_pending(proposal):
    result = validate_fact_proposal(
        FactProposal(**proposal),
        existing_facts=[],
        history_entries=_history(content=proposal["source_excerpt"]),
        batch_cursor_min=1,
        batch_cursor_max=1,
    )

    assert result.decision == "pending_confirmation"
    assert any(issue.code == "high_risk_memory" for issue in result.issues)


def test_validate_temporary_without_expires_goes_pending():
    proposal = FactProposal(**_proposal(
        category="temporary",
        scope="household.guests",
        content="Parents are visiting this week",
        source_excerpt="Parents are visiting this week",
        expires_at=None,
    ))

    result = validate_fact_proposal(
        proposal,
        existing_facts=[],
        history_entries=_history(content=proposal.source_excerpt),
        batch_cursor_min=1,
        batch_cursor_max=1,
    )

    assert result.decision == "pending_confirmation"
    assert any(issue.code == "temporary_missing_expires_at" for issue in result.issues)


def test_validate_temporary_language_in_preference_goes_pending():
    proposal = FactProposal(**_proposal(
        content="Use bright lights this week",
        source_excerpt="Use bright lights this week",
    ))

    result = validate_fact_proposal(
        proposal,
        existing_facts=[],
        history_entries=_history(content=proposal.source_excerpt),
        batch_cursor_min=1,
        batch_cursor_max=1,
    )

    assert result.decision == "pending_confirmation"
    assert any(issue.code == "temporary_language_non_temporary" for issue in result.issues)


def test_validate_uncertain_language_caps_confidence():
    proposal = FactProposal(**_proposal(
        content="User usually prefers warm lights",
        source_excerpt="I usually prefer warm lights",
        confidence=0.95,
    ))

    result = validate_fact_proposal(
        proposal,
        existing_facts=[],
        history_entries=_history(content=proposal.source_excerpt),
        batch_cursor_min=1,
        batch_cursor_max=1,
    )

    assert result.decision == "active"
    assert result.confidence == 0.7
    assert any(issue.code == "uncertain_language" for issue in result.issues)


def test_validate_conflict_same_category_scope_goes_pending(tmp_path):
    store = MemoryStore(tmp_path)
    existing = store.fact_store.upsert_fact(
        "User prefers cool lights after 22:00",
        category="preference",
        scope="home.living_room.lighting",
        owner="user",
        source_cursors=[1],
        source_excerpt="cool lights",
    )
    proposal = FactProposal(**_proposal())

    result = validate_fact_proposal(
        proposal,
        existing_facts=[existing],
        history_entries=_history(),
        batch_cursor_min=1,
        batch_cursor_max=1,
    )

    assert result.decision == "pending_confirmation"
    assert any(issue.code == "possible_conflict" for issue in result.issues)


def test_deprecation_validator_rejects_unknown_or_unsubstantiated(tmp_path):
    store = MemoryStore(tmp_path)
    existing = store.fact_store.upsert_fact("Old note")

    unknown = validate_deprecation_proposal(
        FactDeprecationProposal(
            fact_id="fact_missing",
            reason="cursor 1 contradicts it",
            source_cursors=[1],
        ),
        existing_facts=[existing],
        history_entries=_history(),
        batch_cursor_min=1,
        batch_cursor_max=1,
    )
    no_source = validate_deprecation_proposal(
        FactDeprecationProposal(fact_id=existing.fact_id, reason="contradicted"),
        existing_facts=[existing],
        history_entries=_history(),
        batch_cursor_min=1,
        batch_cursor_max=1,
    )

    assert unknown.decision == "reject"
    assert any(issue.code == "unknown_fact_id" for issue in unknown.issues)
    assert no_source.decision == "reject"
    assert any(issue.code == "missing_current_source_for_deprecation" for issue in no_source.issues)


def test_deprecation_reason_cursor_text_does_not_replace_source_cursors(tmp_path):
    store = MemoryStore(tmp_path)
    existing = store.fact_store.upsert_fact("Old note")

    result = validate_deprecation_proposal(
        FactDeprecationProposal(fact_id=existing.fact_id, reason="cursor 1 contradicts it"),
        existing_facts=[existing],
        history_entries=_history(),
        batch_cursor_min=1,
        batch_cursor_max=1,
    )

    assert result.decision == "reject"
    assert any(issue.code == "missing_current_source_for_deprecation" for issue in result.issues)


def test_deprecation_validator_rejects_active_high_risk_fact(tmp_path):
    store = MemoryStore(tmp_path)
    policy = store.fact_store.upsert_fact(
        "Trusted family can unlock the side door",
        category="policy",
        scope="home.entry.side_door",
        requires_confirmation=False,
        status="active",
    )

    result = validate_deprecation_proposal(
        FactDeprecationProposal(
            fact_id=policy.fact_id,
            reason="cursor 1 contradicts it",
            source_cursors=[1],
        ),
        existing_facts=[policy],
        history_entries=_history(),
        batch_cursor_min=1,
        batch_cursor_max=1,
    )

    assert result.decision == "reject"
    assert any(issue.code == "active_high_risk_deprecation" for issue in result.issues)


def test_apply_active_pending_rejected_and_rebuilds_memory(tmp_path):
    store = MemoryStore(tmp_path)
    batch = parse_fact_proposal_response(_json([
        _proposal(
            content="User prefers warm lights",
            source_excerpt="living room lights warm",
        ),
        _proposal(
            category="policy",
            scope="home.entry.lock",
            content="Guests cannot unlock the front door",
            source_excerpt="guests cannot unlock the front door",
        ),
        _proposal(content="Missing source", source_cursors=[], source_excerpt="source"),
    ]))

    result = store.apply_fact_proposals_and_rebuild_memory(
        batch,
        history_entries=_history(
            content=(
                "after 22:00 please make the living room lights warm; "
                "guests cannot unlock the front door"
            ),
        ),
    )

    assert len(result.accepted) == 1
    assert len(result.pending) == 1
    assert len(result.rejected) == 1
    assert len(store.fact_store.read_all()) == 2
    memory = store.read_memory()
    assert "User prefers warm lights" in memory
    assert "## Pending Confirmation" in memory
    assert "Guests cannot unlock the front door" in memory
    assert "Missing source" not in store.facts_file.read_text(encoding="utf-8")


def test_apply_active_auto_limit_excess_goes_pending(tmp_path):
    store = MemoryStore(tmp_path)
    upserts = [
        _proposal(
            content=f"Low risk preference {idx}",
            scope=f"user.preference.{idx}",
            source_excerpt=f"Low risk preference {idx}",
        )
        for idx in range(6)
    ]
    batch = parse_fact_proposal_response(_json(upserts))

    result = store.apply_fact_proposals_and_rebuild_memory(
        batch,
        history_entries=_history(
            content="\n".join(f"Low risk preference {idx}" for idx in range(6)),
        ),
    )

    assert len(result.accepted) == 5
    assert len(result.pending) == 1
    assert result.pending[0].status == "pending_confirmation"


def test_apply_deprecation_validates_and_limits(tmp_path):
    store = MemoryStore(tmp_path)
    facts = [store.fact_store.upsert_fact(f"Old note {idx}") for idx in range(4)]
    batch = parse_fact_proposal_response(_json(
        deprecations=[
            {"fact_id": fact.fact_id, "reason": "cursor 1 contradicts it", "source_cursors": [1]}
            for fact in facts
        ] + [
            {"fact_id": "fact_missing", "reason": "cursor 1 contradicts it", "source_cursors": [1]},
        ],
    ))

    result = store.apply_fact_proposals_and_rebuild_memory(
        batch,
        history_entries=_history(),
    )

    assert len(result.deprecated) == 3
    assert len(result.rejected) == 2
    assert [fact.status for fact in store.fact_store.read_all()].count("deprecated") == 3


@pytest.mark.asyncio
async def test_dream_valid_low_risk_proposal_writes_fact_and_advances_cursor(tmp_path):
    store = _store(tmp_path)
    cursor = store.append_history("after 22:00 please make the living room lights warm")
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=MagicMock(content=_json([
        _proposal(source_cursors=[cursor]),
    ])))
    dream = Dream(store=store, provider=provider, model="test-model")
    dream._runner.run = AsyncMock(return_value=_run_result())  # type: ignore[method-assign]

    result = await dream.run()

    assert result is True
    assert store.get_last_dream_cursor() == cursor
    facts = store.fact_store.read_all()
    assert len(facts) == 1
    assert facts[0].status == "active"
    assert "User prefers warm lights after 22:00" in store.read_memory()
    dream._runner.run.assert_called_once()


@pytest.mark.asyncio
async def test_dream_invalid_json_does_not_advance_or_run_phase2(tmp_path):
    store = _store(tmp_path)
    store.append_history("remember this")
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=MagicMock(content="{bad json"))
    dream = Dream(store=store, provider=provider, model="test-model")
    dream._runner.run = AsyncMock(return_value=_run_result())  # type: ignore[method-assign]

    result = await dream.run()

    assert result is False
    assert store.get_last_dream_cursor() == 0
    assert store.fact_store.read_all() == []
    dream._runner.run.assert_not_called()


@pytest.mark.asyncio
async def test_dream_all_rejected_still_advances_after_processing(tmp_path):
    store = _store(tmp_path)
    cursor = store.append_history("remember this")
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=MagicMock(content=_json([
        _proposal(source_cursors=[], source_excerpt="source"),
    ])))
    dream = Dream(store=store, provider=provider, model="test-model")
    dream._runner.run = AsyncMock(return_value=_run_result())  # type: ignore[method-assign]

    result = await dream.run()

    assert result is True
    assert store.get_last_dream_cursor() == cursor
    assert store.fact_store.read_all() == []


@pytest.mark.asyncio
async def test_dream_apply_failure_restores_and_does_not_advance(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.append_history("remember this")
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=MagicMock(content=_json([_proposal()])))
    dream = Dream(store=store, provider=provider, model="test-model")
    dream._runner.run = AsyncMock(return_value=_run_result())  # type: ignore[method-assign]

    def fail_apply(*args, **kwargs):
        store.write_memory("# Memory\n- dirty\n")
        raise RuntimeError("apply failed")

    monkeypatch.setattr(store, "apply_fact_proposals_and_rebuild_memory", fail_apply)

    result = await dream.run()

    assert result is False
    assert store.get_last_dream_cursor() == 0
    assert store.read_memory() == "# Memory\n- original\n"
    dream._runner.run.assert_not_called()


@pytest.mark.asyncio
async def test_dream_phase2_memory_change_restores_and_does_not_advance(tmp_path):
    store = _store(tmp_path)
    store.append_history("remember this")
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=MagicMock(content=_json([])))
    dream = Dream(store=store, provider=provider, model="test-model")

    async def dirty_phase2(_spec):
        store.write_memory("# Memory\n- dirty\n")
        return _run_result()

    dream._runner.run = dirty_phase2  # type: ignore[method-assign]

    result = await dream.run()

    assert result is False
    assert store.get_last_dream_cursor() == 0
    assert store.read_memory() == "# Memory\n- original\n"


@pytest.mark.asyncio
async def test_dream_phase2_facts_change_restores_and_does_not_advance(tmp_path):
    store = _store(tmp_path)
    store.append_history("remember this")
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=MagicMock(content=_json([])))
    dream = Dream(store=store, provider=provider, model="test-model")

    async def dirty_phase2(_spec):
        store.fact_store.upsert_fact("dirty fact")
        return _run_result()

    dream._runner.run = dirty_phase2  # type: ignore[method-assign]

    result = await dream.run()

    assert result is False
    assert store.get_last_dream_cursor() == 0
    assert store.fact_store.read_all() == []
