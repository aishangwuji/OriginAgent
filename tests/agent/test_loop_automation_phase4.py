from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.loop import AgentLoop, TurnContext, TurnState
from OriginAgent.agent.agent_turn_pipeline import AgentTurnPipeline
from OriginAgent.bus.events import InboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.config.schema import DeviceToolsConfig, DomainPacksConfig, ToolsConfig
from OriginAgent.agent.action_continuity import ActionContinuityInputs, ActionWorldView
from OriginAgent.agent.action_runtime import ActionIntent
from OriginAgent.agent.identity import RuntimeContext
from OriginAgent.domain_packs.robot.runtime.robot_actions import RobotActionPlanner
from OriginAgent.domain_packs.smart_home.runtime.devices import DeviceRecord, DeviceRegistry
from OriginAgent.domain_packs.smart_home.runtime.action_automation import ActionAutomationCoordinator
from OriginAgent.domain_packs.robot.runtime.robot_executor import (
    RobotActionExecutor,
    RobotActionWritebackAdapter,
)
from OriginAgent.domain_packs.robot.runtime.robot_actions import TypedRobotAction
from OriginAgent.providers.base import LLMResponse


def _provider():
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="Done", tool_calls=[]))
    return provider


def _loop(tmp_path: Path) -> AgentLoop:
    return AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
        domain_packs_config=DomainPacksConfig(active=["smart_home"]),
        tools_config=ToolsConfig(
            device=DeviceToolsConfig(
                enabled=True,
                lighting_enabled=True,
                mode="dry_run",
                backend="fake",
                automation_enabled=True,
                automation_allowed_domains=["lighting"],
                automation_max_actions_per_pass=1,
                automation_dry_run_only=True,
            )
        ),
        device_registry=DeviceRegistry(
            [
                DeviceRecord(
                    device_id="ceiling_light",
                    domain="lighting",
                    room="living_room",
                    device_ref="ceiling_light",
                )
            ]
        ),
    )


def _continuity_inputs() -> ActionContinuityInputs:
    runtime_context = RuntimeContext(
        actor_id="alice",
        user_id="alice",
        session_id="cli:home",
        device_id="device-a",
        trigger="automation",
        channel="cli",
        chat_id="home",
        session_key="cli:home",
        source="automation",
        default_scope="session",
    )
    return ActionContinuityInputs(
        runtime_context=runtime_context,
        working_memory={"priority_facts": ["lights should help visibility"], "attention_items": [], "tool_residue": []},
        world_view=ActionWorldView(
            included_summary={"summary_id": "world_1", "focus": ["living room looks dark"]},
            contested_summary={"contested": False, "items": []},
            freshness={"is_fresh": True, "generated_at": "2026-06-09T00:00:00+00:00", "fresh_until": "2099-06-09T00:05:00+00:00"},
            selection_reasons=["fresh_visible_fallback"],
        ),
        governance_summary={},
        retrieval_hints={},
        pending_confirmations=[],
    )


@pytest.mark.asyncio
async def test_automation_state_skips_when_disabled(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    loop.tools_config.device.automation_enabled = False
    session = loop.sessions.get_or_create("cli:home")
    runtime_context = loop.actor_resolver.resolve_runtime_context(
        channel="cli",
        chat_id="home",
        sender_id="alice",
        metadata={},
        session_key="cli:home",
    )
    ctx = TurnContext(
        msg=InboundMessage(channel="cli", sender_id="alice", chat_id="home", content="hello"),
        session_key="cli:home",
        state=TurnState.AUTOMATION,
        turn_id="turn-1",
        session=session,
        runtime_context=runtime_context,
    )

    event = await loop._state_automation(ctx)

    assert event == "skip"
    assert loop._last_action_continuity_audit["reason"] == "automation_disabled"


@pytest.mark.asyncio
async def test_automation_state_appends_confirmation_appendix(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    session = loop.sessions.get_or_create("cli:home")
    loop.context.build_action_continuity_inputs = lambda session_key, runtime_context: _continuity_inputs()  # type: ignore[method-assign]
    runtime_context = loop.actor_resolver.resolve_runtime_context(
        channel="cli",
        chat_id="home",
        sender_id="alice",
        metadata={},
        session_key="cli:home",
    )
    fake_executor = SimpleNamespace(
        submit_automation=lambda action, continuity_inputs, proposal=None: (
            SimpleNamespace(
                status="pending_confirmation",
                action_id="action_auto_1",
                reason="needs confirmation",
                confirmation_id="confirmation_auto_1",
                backend_called=False,
                permission_status=None,
                is_real_execution=False,
                backend_kind="dry_run",
                physical_target_domain="lighting",
            ),
            SimpleNamespace(outcome="pending_confirmation", reason="needs confirmation", audit={}),
        )
    )
    smart_home_contribution = loop._domain_runtime_contributions[0]
    loop._domain_runtime_contributions[:] = [
        SimpleNamespace(
            action_continuity_provider=smart_home_contribution.action_continuity_provider,
            action_continuity_writeback_adapter=smart_home_contribution.action_continuity_writeback_adapter,
            tool_context={"device_action_executor": fake_executor},
        )
    ]
    ctx = TurnContext(
        msg=InboundMessage(channel="cli", sender_id="alice", chat_id="home", content="hello"),
        session_key="cli:home",
        state=TurnState.AUTOMATION,
        turn_id="turn-2",
        session=session,
        runtime_context=runtime_context,
        final_content="Primary response",
    )

    event = await loop._state_automation(ctx)
    respond = await loop._state_respond(ctx)

    assert event == "ok"
    assert respond == "ok"
    assert ctx.outbound is not None
    assert "Automation" in ctx.outbound.content
    assert loop._last_action_continuity_audit["execution_result"]["status"] == "pending_confirmation"
    assert loop._last_action_continuity_audit["planner_result"]["proposals"]
    assert loop._last_action_continuity_audit["selected_proposal_digest"] is not None


@pytest.mark.asyncio
async def test_automation_state_skips_when_only_preview_proposals_exist(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    loop._domain_runtime_contributions[:] = [
        SimpleNamespace(
            action_continuity_provider=RobotActionPlanner(),
            action_continuity_writeback_adapter=RobotActionWritebackAdapter(),
            tool_context={
                "device_action_executor": RobotActionExecutor(
                    confirmation_manager=loop._confirmation_manager,
                ),
            },
        )
    ]
    session = loop.sessions.get_or_create("cli:home")
    loop.context.build_action_continuity_inputs = lambda session_key, runtime_context: ActionContinuityInputs(  # type: ignore[method-assign]
        runtime_context=runtime_context,
        working_memory={"priority_facts": ["robot preview requested"], "attention_items": [], "tool_residue": []},
        world_view=ActionWorldView(
            included_summary={"summary_id": "world_robot", "focus": ["robot arm near the desk"]},
            contested_summary={"contested": False, "items": []},
            freshness={"is_fresh": True},
            selection_reasons=["robot_focus"],
        ),
        governance_summary={},
        retrieval_hints={},
        pending_confirmations=[],
    )
    runtime_context = loop.actor_resolver.resolve_runtime_context(
        channel="cli",
        chat_id="home",
        sender_id="alice",
        metadata={},
        session_key="cli:home",
    )
    ctx = TurnContext(
        msg=InboundMessage(channel="cli", sender_id="alice", chat_id="home", content="hello"),
        session_key="cli:home",
        state=TurnState.AUTOMATION,
        turn_id="turn-3",
        session=session,
        runtime_context=runtime_context,
    )

    event = await loop._state_automation(ctx)

    assert event == "skip"
    assert loop._last_action_continuity_audit["reason"] == "no_executable_proposal"
    assert loop._last_action_continuity_audit["planner_result"]["proposals"][0]["preview_only"] is True


@pytest.mark.asyncio
async def test_inspect_context_reports_action_view(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    loop._record_action_continuity_audit({
        "planning_inputs": {"session_key": "cli:home"},
        "planning_evidence": {"planning_reason": "rule based"},
        "automation_origin": "smart_home",
        "planner_result": {"proposals": [{"automation_origin": "smart_home"}]},
        "selected_proposal_digest": "digest-1",
        "skipped_reasons": ["RobotActionPlanner: no_proposal"],
        "preconditions": {"outcome": "allow"},
        "execution_result": {"status": "dry_run"},
        "continuity_writeback": {"result_status": "dry_run"},
    })

    result = await loop.tools.execute("originagent_inspect_context", {})

    assert result["views"]["action"]["automation_origin"] == "smart_home"
    assert result["views"]["action"]["execution_result"]["status"] == "dry_run"
    assert result["views"]["action"]["planner_result"]["proposals"][0]["automation_origin"] == "smart_home"
    assert result["views"]["action"]["selected_proposal_digest"] == "digest-1"
    assert result["views"]["action"]["cache_timestamp"] == loop._cached_action_summary["cache_timestamp"]


def test_action_summary_cache_refreshes_to_latest_audit(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    loop._record_action_continuity_audit({
        "status": "ok",
        "planner_result": {"proposals": [{"proposal_digest": "digest-1"}]},
        "selected_proposal_digest": "digest-1",
    })

    loop._record_action_continuity_audit({
        "status": "ok",
        "planner_result": {"proposals": [{"proposal_digest": "digest-2"}]},
        "selected_proposal_digest": "digest-2",
    })

    assert loop._last_action_continuity_audit["selected_proposal_digest"] == "digest-2"
    assert loop._cached_action_summary["selected_proposal_digest"] == "digest-2"
    assert loop._cached_action_summary["planner_result"]["proposals"][0]["proposal_digest"] == "digest-2"
    assert loop.introspection._action_summary()["selected_proposal_digest"] == "digest-2"


def test_action_automation_coordinator_uses_registry_single_lighting_target() -> None:
    coordinator = ActionAutomationCoordinator(
        device_registry=DeviceRegistry(
            [
                DeviceRecord(
                    device_id="lamp_a",
                    domain="lighting",
                    room="study",
                    device_ref="lamp_a",
                )
            ]
        )
    )

    proposal = coordinator.run_once(
        session_key="cli:home",
        continuity_inputs=ActionContinuityInputs(
            runtime_context=_continuity_inputs().runtime_context,
            working_memory=_continuity_inputs().working_memory,
            world_view=ActionWorldView(
                included_summary={"summary_id": "world_1", "focus": ["study looks dark"]},
                contested_summary={"contested": False, "items": []},
                freshness={"is_fresh": True},
                selection_reasons=["fresh_visible_fallback"],
            ),
            governance_summary={},
            retrieval_hints={},
            pending_confirmations=[],
        ),
    )

    assert proposal is not None
    assert proposal.typed_action.device_id == "lamp_a"
    assert proposal.typed_action.room == "study"


def test_action_automation_coordinator_skips_ambiguous_multi_device_target() -> None:
    coordinator = ActionAutomationCoordinator(
        device_registry=DeviceRegistry(
            [
                DeviceRecord(device_id="lamp_a", domain="lighting", room="study", device_ref="lamp_a"),
                DeviceRecord(device_id="lamp_b", domain="lighting", room="living_room", device_ref="lamp_b"),
            ]
        )
    )

    proposal = coordinator.run_once(
        session_key="cli:home",
        continuity_inputs=ActionContinuityInputs(
            runtime_context=_continuity_inputs().runtime_context,
            working_memory=_continuity_inputs().working_memory,
            world_view=ActionWorldView(
                included_summary={"summary_id": "world_1", "focus": ["home looks dark"]},
                contested_summary={"contested": False, "items": []},
                freshness={"is_fresh": True},
                selection_reasons=["fresh_visible_fallback"],
            ),
            governance_summary={},
            retrieval_hints={},
            pending_confirmations=[],
        ),
    )

    assert proposal is None


def test_resume_precheck_denies_automation_confirmation_when_world_turns_contested(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    session = loop.sessions.get_or_create("cli:home")
    loop._write_continuity_runtime_identity(
        session,
        RuntimeContext(
            actor_id="alice",
            user_id="alice",
            session_id="cli:home",
            device_id="device-a",
            trigger="user_initiated",
            channel="cli",
            chat_id="home",
            session_key="cli:home",
            source="user_turn",
            default_scope="session",
        ),
    )
    loop.context.build_action_continuity_inputs = lambda session_key, runtime_context: ActionContinuityInputs(  # type: ignore[method-assign]
        runtime_context=runtime_context,
        working_memory={"priority_facts": [], "attention_items": [], "tool_residue": []},
        world_view=ActionWorldView(
            included_summary={"summary_id": "world_2", "focus": ["living room looks dark"]},
            contested_summary={"contested": True, "items": ["inspection disagreement"]},
            freshness={
                "is_fresh": True,
                "generated_at": "2026-06-09T00:00:00+00:00",
                "fresh_until": "2099-06-09T00:05:00+00:00",
            },
            selection_reasons=["fresh_visible_fallback"],
        ),
        governance_summary={},
        retrieval_hints={},
        pending_confirmations=[],
    )

    decision = loop._resume_action_confirmation_precheck(
        ActionIntent(
            action="set_light_power",
            scope="home.living_room.lighting.ceiling_light",
            trigger="automation",
            risk="low",
            requested_by="alice",
            payload={"device_id": "ceiling_light", "domain": "lighting", "action_type": "set_light_power", "power": "on"},
            continuity_session_ref="cli:home",
            continuity_origin="smart_home",
            continuity_proposal_digest="digest-1",
        ),
        SimpleNamespace(metadata={"arc_session": "cli:home"}),
        datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
    )

    assert decision is not None
    assert decision.decision == "deny"
    assert "contested" in decision.reason


def test_robot_preview_planner_emits_preview_only_proposal() -> None:
    proposal = RobotActionPlanner().run_once(
        session_key="cli:robot",
        continuity_inputs=ActionContinuityInputs(
            runtime_context=_continuity_inputs().runtime_context,
            working_memory=_continuity_inputs().working_memory,
            world_view=ActionWorldView(
                included_summary={"summary_id": "world_robot", "focus": ["robot arm near the desk"]},
                contested_summary={"contested": False, "items": []},
                freshness={"is_fresh": True},
                selection_reasons=["robot_focus"],
            ),
            governance_summary={},
            retrieval_hints={},
            pending_confirmations=[],
        ),
    )

    assert proposal is not None
    assert proposal.preview_only is True
    assert proposal.automation_origin == "robot"


@pytest.mark.asyncio
async def test_robot_simulator_proposal_runs_through_automation_state(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    loop._domain_runtime_contributions[:] = [
        SimpleNamespace(
            action_continuity_provider=RobotActionPlanner(),
            action_continuity_writeback_adapter=RobotActionWritebackAdapter(),
            tool_context={
                "device_action_executor": RobotActionExecutor(
                    confirmation_manager=loop._confirmation_manager,
                ),
            },
        )
    ]
    loop.context.build_action_continuity_inputs = lambda session_key, runtime_context: ActionContinuityInputs(  # type: ignore[method-assign]
        runtime_context=runtime_context,
        user_goal_domain="robot",
        working_memory={"priority_facts": ["robot simulator requested"], "attention_items": [], "tool_residue": []},
        world_view=ActionWorldView(
            included_summary={"summary_id": "world_robot", "focus": ["robot simulator request"]},
            contested_summary={"contested": False, "items": []},
            freshness={"is_fresh": True},
            selection_reasons=["robot_focus"],
        ),
        governance_summary={},
        retrieval_hints={},
        pending_confirmations=[],
    )
    session = loop.sessions.get_or_create("cli:home")
    runtime_context = loop.actor_resolver.resolve_runtime_context(
        channel="cli",
        chat_id="home",
        sender_id="alice",
        metadata={},
        session_key="cli:home",
    )
    ctx = TurnContext(
        msg=InboundMessage(channel="cli", sender_id="alice", chat_id="home", content="hello"),
        session_key="cli:home",
        state=TurnState.AUTOMATION,
        turn_id="turn-robot",
        session=session,
        runtime_context=runtime_context,
        final_content="Primary response",
    )

    event = await loop._state_automation(ctx)
    respond = await loop._state_respond(ctx)

    assert event == "ok"
    assert respond == "ok"
    assert loop._last_action_continuity_audit["automation_origin"] == "robot"
    assert loop._last_action_continuity_audit["execution_result"]["backend_kind"] == "robot_simulator"
    assert loop._last_action_continuity_audit["continuity_writeback"]["result_status"] == "dry_run"
    assert loop._cached_action_summary["selected_proposal_digest"] == loop._last_action_continuity_audit["selected_proposal_digest"]


def test_select_automation_proposal_prefers_requested_origin() -> None:
    smart = SimpleNamespace(automation_origin="smart_home", preview_only=False)
    robot = SimpleNamespace(automation_origin="robot", preview_only=False)

    selected = AgentTurnPipeline._select_automation_proposal(
        [robot, smart],
        preferred_origin="robot",
    )

    assert selected is robot


def test_robot_executor_allows_simulator_and_marks_dry_run(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )
    executor = RobotActionExecutor(
        confirmation_manager=loop._confirmation_manager,
    )
    result, decision = executor.submit_automation(
        TypedRobotAction(
            action_type="simulate_robot_motion",
            target="unitree_g1",
            parameters={"mode": "simulator", "focus": "robot simulator"},
            requested_by="alice",
        ),
        continuity_inputs=_continuity_inputs(),
    )

    assert decision.outcome == "allow"
    assert result.status == "dry_run"
    assert result.backend_kind == "robot_simulator"
    assert result.is_real_execution is False


def test_robot_resume_precheck_allows_handoff_when_confirmation_is_satisfied(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    loop._domain_runtime_contributions[:] = [
        SimpleNamespace(
            action_continuity_provider=RobotActionPlanner(),
            action_continuity_writeback_adapter=RobotActionWritebackAdapter(),
            tool_context={
                "device_action_executor": RobotActionExecutor(
                    confirmation_manager=loop._confirmation_manager,
                ),
            },
        )
    ]
    session = loop.sessions.get_or_create("cli:home")
    loop._write_continuity_runtime_identity(
        session,
        RuntimeContext(
            actor_id="alice",
            user_id="alice",
            session_id="cli:home",
            device_id="device-a",
            trigger="automation",
            channel="cli",
            chat_id="home",
            session_key="cli:home",
            source="automation",
            default_scope="session",
        ),
    )
    continuity_inputs = ActionContinuityInputs(
        runtime_context=_continuity_inputs().runtime_context,
        working_memory={"priority_facts": [], "attention_items": [], "tool_residue": []},
        world_view=ActionWorldView(
            included_summary={"summary_id": "world_robot", "focus": ["robot handoff operator assist"]},
            contested_summary={"contested": False, "items": []},
            freshness={"is_fresh": True},
            selection_reasons=["robot_focus"],
        ),
        governance_summary={},
        retrieval_hints={},
        pending_confirmations=[],
    )
    loop.context.build_action_continuity_inputs = lambda session_key, runtime_context: continuity_inputs  # type: ignore[method-assign]
    decision = loop._resume_action_confirmation_precheck(
        SimpleNamespace(
            action="handoff_robot_motion",
            scope="robot.unitree_g1",
            payload={"target": "unitree_g1", "mode": "handoff"},
            requested_by="alice",
            idempotency_key="robot-handoff-1",
            continuity_session_ref="cli:home",
            continuity_origin="robot",
            continuity_proposal_digest="digest-robot-1",
        ),
        SimpleNamespace(metadata={"arc_session": "cli:home"}),
        datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
    )

    assert decision is None or decision.decision == "allow"
