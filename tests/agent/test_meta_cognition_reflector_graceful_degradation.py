from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.meta_cognition_audit import JsonlMetaCognitionAuditLedger
from OriginAgent.agent.meta_cognition_models import MetaTrigger, ThoughtJournalEntry
from OriginAgent.agent.meta_cognition_reflector import MetaCognitionReflector


def _config(**overrides):
    base = {
        "enabled": True,
        "trigger_collection_enabled": True,
        "max_accepted_triggers_per_turn": 2,
        "session_cooldown_seconds": 0,
        "trigger_type_cooldown_seconds": 0,
        "queue_max_items": 10,
        "capture_user_correction_explicit_only": True,
        "capture_task_completion_from_complete_goal_only": True,
        "structured_reflection_enabled": False,
        "working_memory_bridge_enabled": False,
        "memory_candidate_bridge_enabled": False,
        "memory_candidate_min_confidence": 0.85,
        "pattern_consolidation_enabled": False,
        "evolution_bridge_enabled": False,
        "pattern_window_days": 14,
        "pattern_window_max_reflections": 200,
        "pattern_min_frequency": 3,
        "pattern_min_distinct_turns": 2,
        "pattern_max_example_refs": 5,
        "signal_max_evidence_refs": 6,
        "max_signal_upserts_per_turn": 1,
        "allowed_evolution_target_types": ("workflow_candidate", "skill_candidate"),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _reflector(
    tmp_path: Path,
    *,
    config: SimpleNamespace | None = None,
    router: Any | None = None,
    working_memory: Any | None = None,
):
    session = SimpleNamespace(
        key="cli:direct",
        metadata={"goal_state": {"status": "completed", "objective": "finish task"}},
    )
    sessions = SimpleNamespace(get_or_create=lambda key: session)
    wm = working_memory or SimpleNamespace(
        inspect=lambda session, identity=None: {
            "attention_items": [],
            "pending_questions": [],
        },
        load=lambda session, identity=None: SimpleNamespace(attention_items=[], pending_questions=[]),
        append_attention_item=MagicMock(),
        append_pending_question=MagicMock(),
    )
    return MetaCognitionReflector(
        workspace=tmp_path,
        config=config or _config(),
        audit=JsonlMetaCognitionAuditLedger(tmp_path),
        auxiliary_router=router,
        provider=MagicMock(),
        model="test-model",
        sessions=sessions,
        working_memory=wm,
        context_config=SimpleNamespace(world_attention_max_items=3),
    )


def _minimal_journal() -> ThoughtJournalEntry:
    return ThoughtJournalEntry(
        entry_id="j1",
        session_key="cli:direct",
        trigger_type="tool_failure",
        task_reference="turn-1",
    )


def _minimal_trigger() -> MetaTrigger:
    return MetaTrigger(
        trigger_id="t1",
        session_key="cli:direct",
        trigger_type="tool_failure",
        source_type="tool_execution_observer",
        source_reference="grep:1",
        evidence_refs=["tool:grep"],
        payload={"status": "error"},
    )


def test_parse_response_empty_string_returns_none_tuple(tmp_path: Path) -> None:
    """空字符串应优雅降级返回 (None, None, None)，而非抛出 ValueError。"""
    reflector = _reflector(tmp_path)

    result = reflector._parse_response(
        "",
        session_key="cli:direct",
        journals=[_minimal_journal()],
        triggers=[_minimal_trigger()],
        owner_id="user-1",
    )

    assert result == (None, None, None)


def test_parse_response_invalid_json_returns_none_tuple(tmp_path: Path) -> None:
    """非法 JSON 应优雅降级返回 (None, None, None)，而非抛出 ValueError。"""
    reflector = _reflector(tmp_path)

    result = reflector._parse_response(
        "not a json",
        session_key="cli:direct",
        journals=[_minimal_journal()],
        triggers=[_minimal_trigger()],
        owner_id="user-1",
    )

    assert result == (None, None, None)


@pytest.mark.asyncio
async def test_reflect_turn_reasoning_content_fallback(tmp_path: Path) -> None:
    """content 为空但 reasoning_content 含有效 JSON 时，应正常解析并产出 enriched journal。"""
    router = MagicMock()
    router.call_llm = AsyncMock(
        return_value=SimpleNamespace(
            finish_reason="stop",
            content="",
            reasoning_content='{"journal_enrichment": {"summary": "from reasoning", "strategy_summary": "reasoning fallback"}}',
        )
    )
    reflector = _reflector(
        tmp_path,
        config=_config(structured_reflection_enabled=True),
        router=router,
    )
    trigger = _minimal_trigger()

    result = await reflector.reflect_turn(
        session_key="cli:direct",
        turn_id="turn-reasoning",
        turn_snapshot={"user_message": "test", "assistant_final_content": "test"},
        accepted_triggers=[trigger],
        runtime_context=SimpleNamespace(identity=None, user_id="user-1"),
    )

    # reasoning_content 被成功解析，enriched 非 None，走正常 ok 路径
    assert result.status == "ok"
    assert result.reason == "ok"
    journals = reflector.audit.recent_journals(limit=10)
    # 末条应为 enriched journal，summary 来自 reasoning_content
    assert journals[-1]["summary"] == "from reasoning"
    assert journals[-1]["strategy_summary"] == "reasoning fallback"
