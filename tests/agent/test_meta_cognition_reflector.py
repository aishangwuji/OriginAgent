"""Tests for MetaCognitionReflector._extract_active_goal."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.meta_cognition_audit import JsonlMetaCognitionAuditLedger
from OriginAgent.agent.meta_cognition_reflector import MetaCognitionReflector
from OriginAgent.agent.working_memory import WorkingMemoryManager, WorkingMemorySnapshot


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


def _make_reflector(
    tmp_path: Path,
    *,
    session: SimpleNamespace,
    working_memory: Any,
    sessions: Any | None = None,
) -> MetaCognitionReflector:
    sessions = sessions or SimpleNamespace(get_or_create=lambda key: session)
    return MetaCognitionReflector(
        workspace=tmp_path,
        config=_config(),
        audit=JsonlMetaCognitionAuditLedger(tmp_path),
        auxiliary_router=None,
        provider=MagicMock(),
        model="test-model",
        sessions=sessions,
        working_memory=working_memory,
        context_config=SimpleNamespace(world_attention_max_items=3),
    )


def test_extract_active_goal_returns_current_goal(tmp_path: Path) -> None:
    """working_memory 有 current_goal 时,_extract_active_goal 返回该 goal。"""
    session = SimpleNamespace(key="cli:direct", metadata={})
    snapshot = WorkingMemorySnapshot(session_key="cli:direct", current_goal="finish the report")
    working_memory = SimpleNamespace(load=lambda s, identity=None: snapshot)
    reflector = _make_reflector(tmp_path, session=session, working_memory=working_memory)

    assert reflector._extract_active_goal("cli:direct") == "finish the report"


def test_extract_active_goal_empty_returns_empty_string(tmp_path: Path) -> None:
    """working_memory 的 current_goal 为空时,返回空字符串。"""
    session = SimpleNamespace(key="cli:direct", metadata={})
    snapshot = WorkingMemorySnapshot(session_key="cli:direct", current_goal="")
    working_memory = SimpleNamespace(load=lambda s, identity=None: snapshot)
    reflector = _make_reflector(tmp_path, session=session, working_memory=working_memory)

    assert reflector._extract_active_goal("cli:direct") == ""


def test_extract_active_goal_expired_goal_returns_empty_string(tmp_path: Path) -> None:
    """goal_state 已过期(超过30分钟)时,working_memory 不注入 current_goal,返回空字符串。"""
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    session = SimpleNamespace(
        key="cli:direct",
        metadata={
            "goal_state": {
                "status": "active",
                "objective": "expired goal that should not surface",
                "started_at": expired_at,
            }
        },
    )
    sessions = SimpleNamespace(get_or_create=lambda key: session)
    # 使用真实的 WorkingMemoryManager,验证其内置的 30 分钟过期检查生效
    working_memory = WorkingMemoryManager(sessions)
    reflector = _make_reflector(
        tmp_path,
        session=session,
        working_memory=working_memory,
        sessions=sessions,
    )

    assert reflector._extract_active_goal("cli:direct") == ""
