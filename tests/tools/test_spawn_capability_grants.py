from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.subagent import SubagentManager, SubagentStatus
from OriginAgent.agent.tools.shell import ExecTool
from OriginAgent.agent.tools.spawn import SpawnTool
from OriginAgent.agent.tools.registry import ToolRegistry
from OriginAgent.bus.queue import MessageBus
from OriginAgent.config.schema import ExecToolConfig
from OriginAgent.security.capabilities import CapabilitySnapshot
from OriginAgent.security.grants import CapabilityGrant, CapabilityGrantStore
from OriginAgent.security.policy import PolicyDeniedError


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return provider


def _grant(grant_id: str, **kwargs) -> CapabilityGrant:
    return CapabilityGrant(
        grant_id=grant_id,
        created_by="admin",
        created_at=datetime.now(timezone.utc).isoformat(),
        **kwargs,
    )


def _manager(tmp_path: Path, *, grant_store=None, exec_config=None) -> SubagentManager:
    return SubagentManager(
        provider=_provider(),
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=1000,
        grant_store=grant_store,
        exec_config=exec_config,
    )


def test_spawn_without_grant_uses_parent_derived_snapshot(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    parent = CapabilitySnapshot.user_turn()

    effective = manager._snapshot_for_spawn(parent_snapshot=parent)

    assert effective == parent.derive_subagent()


@pytest.mark.asyncio
async def test_spawn_legacy_capability_snapshot_parameter_means_parent_snapshot(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    captured: dict[str, CapabilitySnapshot] = {}

    async def fake_run(spec):
        captured["snapshot"] = spec.tools._capability_snapshot
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
        )

    manager.runner.run = fake_run
    manager._announce_result = AsyncMock()
    parent = CapabilitySnapshot.user_turn()

    await manager.spawn(task="do work", capability_snapshot=parent)
    await asyncio.gather(*manager._running_tasks.values(), return_exceptions=True)

    assert captured["snapshot"] == parent.derive_subagent()


def test_spawn_grant_cannot_expand_parent_derived_snapshot(tmp_path: Path) -> None:
    store = CapabilityGrantStore(tmp_path)
    store.put(_grant("grant-secret-1", can_exec=True, can_write_files=True))
    manager = _manager(tmp_path, grant_store=store)
    parent = CapabilitySnapshot.user_turn()

    effective = manager._snapshot_for_spawn(
        parent_snapshot=parent,
        grant_id="grant-secret-1",
    )

    assert effective.source == "subagent"
    assert effective.trigger == "subagent"
    assert effective.can_exec is False
    assert effective.can_write_files is False


def test_spawn_grant_preserves_capabilities_within_parent_scope(tmp_path: Path) -> None:
    store = CapabilityGrantStore(tmp_path)
    store.put(
        _grant(
            "grant-secret-1",
            can_read_files=True,
            allowed_mcp_scopes=("read", "write"),
            allowed_device_domains=("lighting",),
        )
    )
    manager = _manager(tmp_path, grant_store=store)
    parent = replace(
        CapabilitySnapshot.user_turn(),
        allowed_device_domains=("lighting", "climate"),
        allowed_mcp_scopes=("read",),
    )

    effective = manager._snapshot_for_spawn(
        parent_snapshot=parent,
        grant_id="grant-secret-1",
    )

    assert effective.can_read_files is True
    assert effective.allowed_mcp_scopes == ("read",)
    assert effective.allowed_device_domains == ()


@pytest.mark.parametrize(
    ("grant_id", "grant", "policy_rule"),
    [
        ("missing-secret", None, "capability_grant_missing"),
        (
            "expired-secret",
            _grant(
                "expired-secret",
                expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            ),
            "capability_grant_expired",
        ),
        (
            "revoked-secret",
            _grant("revoked-secret", revoked_at=datetime.now(timezone.utc).isoformat()),
            "capability_grant_revoked",
        ),
    ],
)
def test_invalid_spawn_grant_fails_closed_without_raw_grant_id(
    tmp_path: Path,
    grant_id: str,
    grant: CapabilityGrant | None,
    policy_rule: str,
) -> None:
    store = CapabilityGrantStore(tmp_path)
    if grant is not None:
        store.put(grant)
    manager = _manager(tmp_path, grant_store=store)

    with pytest.raises(PolicyDeniedError) as exc:
        manager._snapshot_for_spawn(
            parent_snapshot=CapabilitySnapshot.user_turn(),
            grant_id=grant_id,
        )

    assert exc.value.policy_rule == policy_rule
    assert str(exc.value) == "Capability grant is missing, expired, or revoked."
    assert grant_id not in str(exc.value)


def test_spawn_grant_id_without_store_fails_as_missing_without_raw_grant_id(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path, grant_store=None)
    raw_grant_id = "grant-secret-1"

    with pytest.raises(PolicyDeniedError) as exc:
        manager._snapshot_for_spawn(
            parent_snapshot=CapabilitySnapshot.user_turn(),
            grant_id=raw_grant_id,
        )

    assert exc.value.policy_rule == "capability_grant_missing"
    assert str(exc.value) == "Capability grant is missing, expired, or revoked."
    assert raw_grant_id not in str(exc.value)


def test_spawn_tool_schema_excludes_grants_and_capability_flags(tmp_path: Path) -> None:
    tool = SpawnTool(_manager(tmp_path))
    properties = tool.parameters["properties"]

    for forbidden in (
        "grant_id",
        "grantId",
        "can_exec",
        "can_write_files",
        "can_spawn",
        "allowed_mcp_scopes",
        "allowed_device_domains",
    ):
        assert forbidden not in properties


@pytest.mark.asyncio
async def test_spawn_tool_passes_parent_snapshot_without_double_deriving(tmp_path: Path) -> None:
    seen: dict[str, CapabilitySnapshot | None] = {}

    class _Manager:
        max_concurrent_subagents = 1

        def get_running_count(self) -> int:
            return 0

        async def spawn(self, **kwargs):
            seen["parent"] = kwargs.get("parent_capability_snapshot")
            seen["legacy"] = kwargs.get("capability_snapshot")
            seen["grant_id"] = kwargs.get("grant_id")
            return "ok"

    parent = CapabilitySnapshot.user_turn()
    tool = SpawnTool(_Manager())
    tool.set_capability_snapshot(parent)

    assert await tool.execute(task="do work") == "ok"
    assert seen["parent"] == parent
    assert seen["legacy"] is None
    assert seen["grant_id"] is None


@pytest.mark.asyncio
async def test_invalid_grant_fails_before_background_task_is_created(tmp_path: Path) -> None:
    manager = _manager(tmp_path, grant_store=CapabilityGrantStore(tmp_path))

    with pytest.raises(PolicyDeniedError):
        await manager.spawn(
            task="do work",
            parent_capability_snapshot=CapabilitySnapshot.user_turn(),
            grant_id="missing-secret",
        )

    assert manager._running_tasks == {}
    assert manager._task_statuses == {}


async def _run_subagent_and_capture_tools(
    manager: SubagentManager,
    snapshot: CapabilitySnapshot,
) -> list[str]:
    captured: dict[str, list[str]] = {}

    async def fake_run(spec):
        captured["tool_names"] = spec.tools.tool_names
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
        )

    manager.runner.run = fake_run
    manager._announce_result = AsyncMock()
    status = SubagentStatus(
        task_id="sub-1",
        label="label",
        task_description="do work",
        started_at=time.monotonic(),
    )
    await manager._run_subagent(
        "sub-1",
        "do work",
        "label",
        {"channel": "cli", "chat_id": "direct"},
        status,
        capability_snapshot=snapshot,
    )
    await asyncio.sleep(0)
    return captured["tool_names"]


@pytest.mark.asyncio
async def test_exec_registration_follows_effective_snapshot_and_profile(tmp_path: Path) -> None:
    disabled = _manager(
        tmp_path,
        exec_config=ExecToolConfig(profile="disabled"),
    )
    allowed_snapshot = replace(
        CapabilitySnapshot.user_turn(),
        source="subagent",
        trigger="subagent",
        can_exec=True,
    )

    disabled_tools = await _run_subagent_and_capture_tools(disabled, allowed_snapshot)
    assert "exec" not in disabled_tools

    local_dev = _manager(
        tmp_path,
        exec_config=ExecToolConfig(profile="local_dev", allow_unsafe_exec=True, sandbox=""),
    )
    local_tools = await _run_subagent_and_capture_tools(local_dev, allowed_snapshot)
    assert "exec" in local_tools

    denied_snapshot = replace(allowed_snapshot, can_exec=False)
    denied_tools = await _run_subagent_and_capture_tools(local_dev, denied_snapshot)
    assert "exec" in denied_tools

    registry = ToolRegistry(capability_snapshot=denied_snapshot)
    registry.register(
        ExecTool(
            working_dir=str(tmp_path),
            security_profile="local_dev",
            allow_unsafe_exec=True,
            sandbox="none",
        )
    )
    result = await registry.execute("exec", {"command": "echo ok"})
    assert "not allowed by the current capability snapshot" in result


@pytest.mark.asyncio
async def test_subagent_default_policy_uses_current_normal_toolset(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    snapshot = CapabilitySnapshot.user_turn().derive_subagent()

    tool_names = await _run_subagent_and_capture_tools(manager, snapshot)

    assert {"read_file", "list_dir", "glob", "grep"} <= set(tool_names)
    assert {"ask_user", "long_task", "complete_goal", "session_search", "notebook_edit"} <= set(tool_names)
    assert {"write_file", "edit_file", "web_search", "web_fetch", "exec"} <= set(tool_names)
    assert "message" not in tool_names
    assert "spawn" not in tool_names
    assert "cron" not in tool_names
