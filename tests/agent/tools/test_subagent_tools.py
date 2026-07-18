"""Tests for subagent tool registration and wiring."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from OriginAgent.config.schema import AgentDefaults
from OriginAgent.security.capabilities import CapabilitySnapshot
from OriginAgent.security.grants import CapabilityGrantStore
from OriginAgent.security.policy import PolicyDeniedError

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


@pytest.mark.asyncio
async def test_subagent_exec_tool_receives_allowed_env_keys(tmp_path):
    """allowed_env_keys from ExecToolConfig must be forwarded to the subagent's ExecTool."""
    from OriginAgent.agent.subagent import SubagentManager, SubagentStatus
    from OriginAgent.agent.subagent_policy import SubagentPolicy
    from OriginAgent.bus.queue import MessageBus
    from OriginAgent.config.schema import ExecToolConfig

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        exec_config=ExecToolConfig(allowed_env_keys=["GOPATH", "JAVA_HOME"]),
    )
    mgr._announce_result = AsyncMock()

    async def fake_run(spec):
        exec_tool = spec.tools.get("exec")
        assert exec_tool is not None
        assert exec_tool.allowed_env_keys == ["GOPATH", "JAVA_HOME"]
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    status = SubagentStatus(
        task_id="sub-1", label="label", task_description="do task", started_at=time.monotonic()
    )
    permissive = SubagentPolicy(
        capability_snapshot=CapabilitySnapshot.user_turn(),
        allowed_tool_names=frozenset({"exec"}),
        allow_web=False,
    )
    await mgr._run_subagent(
        "sub-1",
        "do task",
        "label",
        {"channel": "test", "chat_id": "c1"},
        status,
        delegated_policy=permissive,
    )

    mgr.runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_subagent_uses_configured_max_iterations(tmp_path):
    """Subagents should honor the configured tool-iteration limit."""
    from OriginAgent.agent.subagent import SubagentManager, SubagentStatus
    from OriginAgent.agent.subagent_policy import SubagentPolicy
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        max_iterations=37,
    )
    mgr._announce_result = AsyncMock()

    async def fake_run(spec):
        assert spec.max_iterations == 37
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    status = SubagentStatus(
        task_id="sub-1", label="label", task_description="do task", started_at=time.monotonic()
    )
    permissive = SubagentPolicy(
        capability_snapshot=CapabilitySnapshot.user_turn(),
        allowed_tool_names=frozenset({"read_file", "list_dir", "glob", "grep"}),
        allow_web=False,
    )
    await mgr._run_subagent(
        "sub-1",
        "do task",
        "label",
        {"channel": "test", "chat_id": "c1"},
        status,
        delegated_policy=permissive,
    )

    mgr.runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_spawn_persists_subagent_task_record(tmp_path):
    from OriginAgent.agent.subagent import SubagentManager
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    release = asyncio.Event()

    async def fake_run_subagent(*args, **kwargs):
        await release.wait()

    mgr._run_subagent = AsyncMock(side_effect=fake_run_subagent)

    result = await mgr.spawn(
        task="inspect the repository state",
        label="inspect",
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
        capability_snapshot=CapabilitySnapshot.user_turn(),
    )

    assert "started" in result
    records_path = tmp_path / "memory" / "subagents" / "tasks.jsonl"
    rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[-1]["subagent_id"]
    assert rows[-1]["parent_session_key"] == "cli:direct"
    assert rows[-1]["terminal_status"] == "spawned"
    assert rows[-1]["task_label"] == "inspect"
    live_path = tmp_path / "memory" / "subagents" / "live" / f"{rows[-1]['subagent_id']}.json"
    assert live_path.exists()

    release.set()
    await asyncio.gather(*mgr._running_tasks.values(), return_exceptions=True)


def test_subagent_manager_reconciles_stale_live_files(tmp_path):
    from OriginAgent.agent.subagent import SubagentManager
    from OriginAgent.bus.queue import MessageBus

    live_dir = tmp_path / "memory" / "subagents" / "live"
    live_dir.mkdir(parents=True, exist_ok=True)
    (live_dir / "stale-1.json").write_text(
        json.dumps({
            "subagent_id": "stale-1",
            "root_subagent_id": "stale-1",
            "parent_subagent_id": None,
            "parent_session_key": "cli:direct",
            "task_label": "stale task",
            "phase": "awaiting_tools",
            "iteration": 1,
            "started_at": "2026-06-18T00:00:00+00:00",
            "last_heartbeat_at": "2026-06-18T00:00:01+00:00",
            "current_tool_name": "read_file",
            "grant_ref": "",
            "isolation_mode": "shared_process",
            "subagent_depth": 1,
        }),
        encoding="utf-8",
    )
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    status = mgr.runtime_status()
    assert status["subagent_lost_since_restart_count"] == 1
    assert status["subagent_stale_entries"][0]["subagent_id"] == "stale-1"
    assert not (live_dir / "stale-1.json").exists()


@pytest.mark.asyncio
async def test_manager_spawn_rejects_when_at_concurrency_limit(tmp_path):
    from OriginAgent.agent.subagent import SubagentManager
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    release = asyncio.Event()

    async def fake_run_subagent(*args, **kwargs):
        await release.wait()

    mgr._run_subagent = AsyncMock(side_effect=fake_run_subagent)

    first = await mgr.spawn(
        task="first task",
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
        capability_snapshot=CapabilitySnapshot.user_turn(),
    )
    second = await mgr.spawn(
        task="second task",
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
        capability_snapshot=CapabilitySnapshot.user_turn(),
    )

    assert "started" in first
    assert "concurrency limit reached" in second
    assert len(mgr._running_tasks) == 1

    release.set()
    await asyncio.gather(*mgr._running_tasks.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_subagent_persists_tool_records(tmp_path):
    from OriginAgent.agent.subagent import SubagentManager, SubagentStatus
    from OriginAgent.agent.subagent_policy import SubagentPolicy
    from OriginAgent.bus.queue import MessageBus
    from OriginAgent.providers.base import LLMResponse, ToolCallRequest
    from OriginAgent.security.capabilities import CapabilitySnapshot

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock(side_effect=[
        LLMResponse(
            content="thinking",
            tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
        ),
        LLMResponse(content="done", tool_calls=[]),
    ])
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    mgr._announce_result = AsyncMock()

    status = SubagentStatus(
        task_id="sub-1", label="label", task_description="do task", started_at=time.monotonic()
    )
    await mgr._run_subagent(
        "sub-1",
        "do task",
        "label",
        {"channel": "test", "chat_id": "c1", "session_key": "test:c1"},
        status,
        delegated_policy=SubagentPolicy(
            capability_snapshot=CapabilitySnapshot.user_turn(),
            allowed_tool_names=frozenset({"list_dir"}),
            allow_web=False,
        ),
    )

    tools_path = tmp_path / "memory" / "subagents" / "tools.jsonl"
    rows = [json.loads(line) for line in tools_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    assert rows[-1]["subagent_id"] == "sub-1"
    assert rows[-1]["tool_name"] == "list_dir"
    assert rows[-1]["status"] in {"success", "failed"}
    assert "path" in rows[-1]["argument_summary"]
    assert rows[-1]["root_subagent_id"] in {None, "sub-1"}
    assert rows[-1]["subagent_depth"] == 1


@pytest.mark.asyncio
async def test_cancel_by_session_persists_cancelled_record(tmp_path):
    from OriginAgent.agent.subagent import SubagentManager
    from OriginAgent.agent.subagent import SubagentStatus
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    async def cancelled_run(_spec):
        raise asyncio.CancelledError

    mgr._announce_result = AsyncMock()
    status = SubagentStatus(
        task_id="sub-1",
        label="label",
        task_description="do task",
        started_at=time.monotonic(),
    )
    with patch.object(mgr.runner, "run", new=AsyncMock(side_effect=cancelled_run)):
        with pytest.raises(asyncio.CancelledError):
            await mgr._run_subagent(
                "sub-1",
                "do task",
                "label",
                {"channel": "test", "chat_id": "c1", "session_key": "test:c1"},
                status,
            )

    tasks_path = tmp_path / "memory" / "subagents" / "tasks.jsonl"
    rows = [json.loads(line) for line in tasks_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(row["terminal_status"] == "cancelled" for row in rows)


@pytest.mark.asyncio
async def test_subagent_default_policy_does_not_register_spawn(tmp_path):
    from OriginAgent.agent.subagent import SubagentManager, SubagentStatus
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    mgr._announce_result = AsyncMock()
    captured: dict[str, list[str]] = {}

    async def fake_run(spec):
        captured["tool_names"] = spec.tools.tool_names
        return SimpleNamespace(stop_reason="done", final_content="done", error=None, tool_events=[])

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    status = SubagentStatus(
        task_id="sub-1",
        label="label",
        task_description="do task",
        started_at=time.monotonic(),
    )
    await mgr._run_subagent(
        "sub-1",
        "do task",
        "label",
        {"channel": "test", "chat_id": "c1", "session_key": "test:c1"},
        status,
    )

    assert "spawn" not in captured["tool_names"]


@pytest.mark.asyncio
async def test_subagent_nested_policy_registers_spawn(tmp_path):
    from OriginAgent.agent.subagent import SubagentManager, SubagentStatus
    from OriginAgent.agent.subagent_policy import SubagentPolicy
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    mgr._announce_result = AsyncMock()
    captured: dict[str, list[str]] = {}

    async def fake_run(spec):
        captured["tool_names"] = spec.tools.tool_names
        return SimpleNamespace(stop_reason="done", final_content="done", error=None, tool_events=[])

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    policy = SubagentPolicy(
        capability_snapshot=CapabilitySnapshot.user_turn().derive_subagent(),
        allowed_tool_names=frozenset({"read_file", "list_dir", "glob", "grep", "spawn"}),
        allow_web=False,
        allow_nested_spawn=True,
        max_subagent_depth=2,
        max_children_per_subagent=2,
        child_allowed_tool_names=frozenset({"read_file", "grep"}),
    )
    status = SubagentStatus(
        task_id="sub-1",
        label="label",
        task_description="do task",
        started_at=time.monotonic(),
        root_subagent_id="sub-1",
        subagent_depth=1,
    )
    await mgr._run_subagent(
        "sub-1",
        "do task",
        "label",
        {
            "channel": "test",
            "chat_id": "c1",
            "session_key": "test:c1",
            "root_subagent_id": "sub-1",
            "parent_subagent_id": None,
            "subagent_depth": "1",
        },
        status,
        delegated_policy=policy,
    )

    assert "spawn" in captured["tool_names"]


@pytest.mark.asyncio
async def test_manager_nested_spawn_rejects_depth_limit(tmp_path):
    from OriginAgent.agent.subagent import SubagentManager
    from OriginAgent.agent.subagent_policy import SubagentPolicy
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    policy = SubagentPolicy(
        capability_snapshot=CapabilitySnapshot.user_turn().derive_subagent(),
        allowed_tool_names=frozenset({"read_file", "spawn"}),
        allow_nested_spawn=True,
        max_subagent_depth=1,
        max_children_per_subagent=1,
    )

    result = await mgr.spawn(
        task="nested",
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
        delegated_policy=policy,
        parent_subagent_id="parent-1",
        root_subagent_id="root-1",
        subagent_depth=2,
    )

    assert "maximum delegated depth reached" in result


@pytest.mark.asyncio
async def test_manager_nested_spawn_rejects_child_limit(tmp_path):
    from OriginAgent.agent.subagent import SubagentManager
    from OriginAgent.agent.subagent_policy import SubagentPolicy
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    mgr._child_counts["parent-1"] = 1
    policy = SubagentPolicy(
        capability_snapshot=CapabilitySnapshot.user_turn().derive_subagent(),
        allowed_tool_names=frozenset({"read_file", "spawn"}),
        allow_nested_spawn=True,
        max_subagent_depth=2,
        max_children_per_subagent=1,
    )

    result = await mgr.spawn(
        task="nested",
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
        delegated_policy=policy,
        parent_subagent_id="parent-1",
        root_subagent_id="root-1",
        subagent_depth=1,
    )

    assert "child delegation limit reached" in result


@pytest.mark.asyncio
async def test_spawn_tool_rejects_when_at_concurrency_limit(tmp_path):
    """SpawnTool should return an error string when the concurrency limit is reached."""
    from OriginAgent.agent.subagent import SubagentManager
    from OriginAgent.agent.tools.spawn import SpawnTool
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    mgr._announce_result = AsyncMock()

    # Block the first subagent so it stays "running"
    release = asyncio.Event()

    async def fake_run(spec):
        await release.wait()
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    tool = SpawnTool(mgr)
    tool.set_context("test", "c1", "test:c1")
    tool.set_capability_snapshot(CapabilitySnapshot.user_turn())

    # First spawn succeeds
    result = await tool.execute(task="first task")
    assert "started" in result

    # Second spawn should be rejected (default limit is 1)
    result = await tool.execute(task="second task")
    assert "Cannot spawn subagent" in result
    assert "concurrency limit reached" in result

    # Release the first subagent
    release.set()
    # Allow cleanup
    await asyncio.gather(*mgr._running_tasks.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_spawn_tool_missing_snapshot_fails_closed(tmp_path):
    from OriginAgent.agent.subagent import SubagentManager
    from OriginAgent.agent.tools.spawn import SpawnTool
    from OriginAgent.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    tool = SpawnTool(mgr)

    with pytest.raises(PolicyDeniedError) as exc:
        await tool.execute(task="do work")

    assert exc.value.policy_rule == "capability_snapshot_required"


def test_subagent_snapshot_downgrades_write_cron_device() -> None:
    snapshot = CapabilitySnapshot.user_turn().derive_subagent()

    assert snapshot.can_read_files is True
    assert snapshot.can_write_files is False
    assert snapshot.can_create_cron is False
    assert snapshot.can_exec is False
    assert snapshot.allowed_device_domains == ()


def test_subagent_default_max_concurrent_matches_agent_defaults(tmp_path):
    """Direct SubagentManager construction should use the agent default concurrency limit."""
    from OriginAgent.agent.subagent import SubagentManager
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    assert mgr.max_concurrent_subagents == AgentDefaults().max_concurrent_subagents


def test_subagent_default_max_iterations_matches_agent_defaults(tmp_path):
    """Direct SubagentManager construction should use the agent default limit."""
    from OriginAgent.agent.subagent import SubagentManager
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    assert mgr.max_iterations == AgentDefaults().max_tool_iterations


def test_agent_loop_passes_max_iterations_to_subagents(tmp_path):
    """AgentLoop's configured limit should be shared with spawned subagents."""
    from OriginAgent.agent.loop import AgentLoop
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        max_iterations=42,
    )

    assert loop.subagents.max_iterations == 42


def test_agent_loop_passes_grant_store_to_subagents(tmp_path):
    """AgentLoop should provide the workspace-backed grant store to subagents."""
    from OriginAgent.agent.loop import AgentLoop
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")

    assert isinstance(loop.subagents.grant_store, CapabilityGrantStore)
    assert loop.subagents.grant_store.path == tmp_path / "memory" / "security" / "capability_grants.json"


@pytest.mark.asyncio
async def test_agent_loop_syncs_updated_max_iterations_before_run(tmp_path):
    """Runtime max_iterations changes should be reflected before tool execution."""
    from OriginAgent.agent.loop import AgentLoop
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        max_iterations=42,
    )
    loop.tools.get_definitions = MagicMock(return_value=[])

    async def fake_run(spec):
        assert spec.max_iterations == 55
        assert loop.subagents.max_iterations == 55
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
            messages=[],
            usage={},
            had_injections=False,
            tools_used=[],
        )

    loop.runner.run = AsyncMock(side_effect=fake_run)
    loop.max_iterations = 55

    await loop._run_agent_loop([])

    loop.runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_drain_pending_blocks_while_subagents_running(tmp_path):
    """_drain_pending should block when no messages are available but sub-agents are still running."""
    from OriginAgent.agent.loop import AgentLoop
    from OriginAgent.bus.events import InboundMessage
    from OriginAgent.bus.queue import MessageBus
    from OriginAgent.session.manager import Session

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")

    pending_queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
    session = Session(key="test:drain-block")
    injection_callback = None

    # Capture the injection_callback that _run_agent_loop creates
    async def fake_runner_run(spec):
        nonlocal injection_callback
        injection_callback = spec.injection_callback

        # Simulate: first call to injection_callback should block because
        # sub-agents are running and no messages are in the queue yet.
        # We'll resolve this from a concurrent task.
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
            messages=[],
            usage={},
            had_injections=False,
            tools_used=[],
            last_sent_messages=None,
        )

    loop.runner.run = AsyncMock(side_effect=fake_runner_run)

    # Register a running sub-agent in the SubagentManager for this session
    async def _hang_forever():
        await asyncio.Event().wait()

    hang_task = asyncio.create_task(_hang_forever())
    loop.subagents._session_tasks.setdefault(session.key, set()).add("sub-drain-1")
    loop.subagents._running_tasks["sub-drain-1"] = hang_task

    # Run _run_agent_loop — this defines the _drain_pending closure
    await loop._run_agent_loop(
        [{"role": "user", "content": "test"}],
        session=session,
        channel="test",
        chat_id="c1",
        pending_queue=pending_queue,
    )

    assert injection_callback is not None

    # Now test the callback directly
    # With sub-agents running and an empty queue, it should block
    drain_task = asyncio.create_task(injection_callback())

    # Give it a moment to enter the blocking wait
    await asyncio.sleep(0.05)

    # Should still be running (blocked on pending_queue.get())
    assert not drain_task.done(), "drain should block while sub-agents are running"

    # Now put a message in the queue (simulating sub-agent completion)
    await pending_queue.put(InboundMessage(
        sender_id="subagent",
        channel="test",
        chat_id="c1",
        content="Sub-agent result",
        media=None,
        metadata={},
    ))

    # Should unblock and return results.
    # sender_id="subagent" → _is_internal_event → role=SYSTEM (rule 18:
    # internal events must not be disguised as user role to avoid LLM
    # misinterpretation).
    results = await asyncio.wait_for(drain_task, timeout=2.0)
    assert len(results) >= 1
    assert results[0]["role"] == "system"
    assert "Sub-agent result" in str(results[0]["content"])

    # Cleanup
    hang_task.cancel()
    try:
        await hang_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_drain_pending_no_block_when_no_subagents(tmp_path):
    """_drain_pending should not block when no sub-agents are running."""
    from OriginAgent.agent.loop import AgentLoop
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")

    pending_queue: asyncio.Queue = asyncio.Queue()
    injection_callback = None

    async def fake_runner_run(spec):
        nonlocal injection_callback
        injection_callback = spec.injection_callback
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
            messages=[],
            usage={},
            had_injections=False,
            tools_used=[],
        )

    loop.runner.run = AsyncMock(side_effect=fake_runner_run)

    await loop._run_agent_loop(
        [{"role": "user", "content": "test"}],
        session=None,
        channel="test",
        chat_id="c1",
        pending_queue=pending_queue,
    )

    assert injection_callback is not None

    # With no sub-agents and empty queue, should return immediately
    results = await asyncio.wait_for(injection_callback(), timeout=1.0)
    assert results == []


@pytest.mark.asyncio
async def test_drain_pending_timeout(tmp_path):
    """_drain_pending should return empty after timeout when sub-agents hang."""
    from OriginAgent.agent.loop import AgentLoop
    from OriginAgent.bus.queue import MessageBus
    from OriginAgent.session.manager import Session

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")

    pending_queue: asyncio.Queue = asyncio.Queue()
    session = Session(key="test:drain-timeout")
    injection_callback = None

    async def fake_runner_run(spec):
        nonlocal injection_callback
        injection_callback = spec.injection_callback
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
            messages=[],
            usage={},
            had_injections=False,
            tools_used=[],
            last_sent_messages=None,
        )

    loop.runner.run = AsyncMock(side_effect=fake_runner_run)

    # Register a "running" sub-agent that will never complete
    async def _hang_forever():
        await asyncio.Event().wait()

    hang_task = asyncio.create_task(_hang_forever())
    loop.subagents._session_tasks.setdefault(session.key, set()).add("sub-timeout-1")
    loop.subagents._running_tasks["sub-timeout-1"] = hang_task

    await loop._run_agent_loop(
        [{"role": "user", "content": "test"}],
        session=session,
        channel="test",
        chat_id="c1",
        pending_queue=pending_queue,
    )

    assert injection_callback is not None

    # Patch the timeout to be very short for testing
    with patch("OriginAgent.agent.loop.asyncio.wait_for") as mock_wait:
        mock_wait.side_effect = asyncio.TimeoutError
        results = await injection_callback()
        assert results == []

    # Cleanup
    hang_task.cancel()
    try:
        await hang_task
    except asyncio.CancelledError:
        pass
