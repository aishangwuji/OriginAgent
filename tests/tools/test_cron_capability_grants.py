from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from OpenHome.agent.tools.audit import InMemoryToolAuditSink, ToolAuditConfig
from OpenHome.agent.tools.cron import CronTool
from OpenHome.agent.tools.filesystem import ReadFileTool, WriteFileTool
from OpenHome.agent.tools.registry import ToolRegistry
from OpenHome.cron.service import CronService
from OpenHome.cron.types import CronPayload, CronSchedule
from OpenHome.security.capabilities import CapabilitySnapshot
from OpenHome.security.grants import (
    CapabilityGrant,
    CapabilityGrantStore,
    snapshot_for_cron_payload,
)
from OpenHome.security.policy import PolicyDeniedError


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


def _past() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


def _grant(grant_id: str, **kwargs) -> CapabilityGrant:
    return CapabilityGrant(
        grant_id=grant_id,
        created_by="admin",
        created_at=datetime.now(timezone.utc).isoformat(),
        **kwargs,
    )


def test_cron_tool_schema_excludes_grants_and_capability_flags(tmp_path: Path) -> None:
    tool = CronTool(CronService(tmp_path / "cron" / "jobs.json"))

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


def test_cron_tool_rejects_grant_and_capability_flag_params(tmp_path: Path) -> None:
    registry = ToolRegistry(capability_snapshot=CapabilitySnapshot.user_turn())
    tool = CronTool(CronService(tmp_path / "cron" / "jobs.json"))
    tool.set_context("chat", "home")
    tool.set_capability_snapshot(CapabilitySnapshot.user_turn())
    registry.register(tool)

    _, _, error = registry.prepare_call(
        "cron",
        {
            "action": "add",
            "message": "hello",
            "every_seconds": 60,
            "grant_id": "grant-secret-1",
            "can_exec": True,
        },
    )

    assert error is not None
    assert "unexpected property grant_id" in error.lower()
    assert "unexpected property can_exec" in error.lower()


def test_cron_service_persists_and_reloads_grant_id(tmp_path: Path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    job = service.add_job(
        name="granted",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        grant_id="grant-secret-1",
    )

    service._load_store()
    service._save_store()

    raw = json.loads((tmp_path / "cron" / "jobs.json").read_text(encoding="utf-8"))
    assert raw["jobs"][0]["payload"]["grantId"] == "grant-secret-1"

    loaded = CronService(tmp_path / "cron" / "jobs.json").get_job(job.id)
    assert loaded is not None
    assert loaded.payload.grant_id == "grant-secret-1"


def test_old_cron_job_without_grant_id_loads_and_uses_scheduled_default(tmp_path: Path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "old",
                        "name": "old",
                        "enabled": True,
                        "schedule": {"kind": "every", "everyMs": 60000},
                        "payload": {
                            "kind": "agent_turn",
                            "message": "hello",
                            "capabilitySnapshot": {"can_exec": True},
                        },
                        "state": {},
                        "createdAtMs": 1,
                        "updatedAtMs": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    job = CronService(store_path).get_job("old")
    assert job is not None
    assert job.payload.grant_id is None
    assert job.payload.capability_snapshot == {"can_exec": True}
    assert snapshot_for_cron_payload(
        job.payload,
        CapabilityGrantStore(tmp_path),
    ) == CapabilitySnapshot.scheduled_default()


def test_valid_grant_restores_read_and_exec_snapshot(tmp_path: Path) -> None:
    store = CapabilityGrantStore(tmp_path)
    store.put(
        _grant(
            "grant-secret-1",
            expires_at=_future(),
            can_read_files=True,
            can_exec=True,
        )
    )

    snapshot = snapshot_for_cron_payload(CronPayload(grant_id="grant-secret-1"), store)

    assert snapshot.source == "cron"
    assert snapshot.trigger == "scheduled"
    assert snapshot.can_read_files is True
    assert snapshot.can_exec is True
    assert snapshot.can_write_files is False


@pytest.mark.parametrize(
    ("grant_id", "grant", "policy_rule"),
    [
        ("missing-secret", None, "capability_grant_missing"),
        (
            "expired-secret",
            _grant("expired-secret", expires_at=_past()),
            "capability_grant_expired",
        ),
        (
            "revoked-secret",
            _grant("revoked-secret", revoked_at=datetime.now(timezone.utc).isoformat()),
            "capability_grant_revoked",
        ),
    ],
)
def test_invalid_grant_fails_closed_without_raw_grant_id(
    tmp_path: Path,
    grant_id: str,
    grant: CapabilityGrant | None,
    policy_rule: str,
) -> None:
    store = CapabilityGrantStore(tmp_path)
    if grant is not None:
        store.put(grant)

    with pytest.raises(PolicyDeniedError) as exc:
        snapshot_for_cron_payload(CronPayload(grant_id=grant_id), store)

    assert exc.value.policy_rule == policy_rule
    assert str(exc.value) == "Capability grant is missing, expired, or revoked."
    assert grant_id not in str(exc.value)


@pytest.mark.parametrize("mode", ["off", "minimal", "security"])
def test_audit_mode_does_not_change_grant_enforcement(tmp_path: Path, mode: str) -> None:
    store = CapabilityGrantStore(tmp_path)
    registry = ToolRegistry(
        audit_sink=InMemoryToolAuditSink(),
        audit_config=ToolAuditConfig(mode=mode),  # type: ignore[arg-type]
    )
    assert registry is not None

    with pytest.raises(PolicyDeniedError) as exc:
        snapshot_for_cron_payload(CronPayload(grant_id="missing-secret"), store)

    assert exc.value.policy_rule == "capability_grant_missing"


@pytest.mark.asyncio
async def test_raw_grant_store_is_protected_from_generic_file_tools(tmp_path: Path) -> None:
    store = CapabilityGrantStore(tmp_path)
    store.put(_grant("grant-secret-1"))
    path = tmp_path / "memory" / "security" / "capability_grants.json"

    read = await ReadFileTool(workspace=tmp_path).execute(str(path))
    write = await WriteFileTool(workspace=tmp_path).execute(str(path), "[]")

    assert "protected runtime state" in read
    assert "protected runtime state" in write


@pytest.mark.asyncio
async def test_cron_job_with_missing_grant_fails_before_on_job_body(tmp_path: Path) -> None:
    called = False
    store = CapabilityGrantStore(tmp_path)

    async def guarded_on_job(job):
        nonlocal called
        snapshot_for_cron_payload(job.payload, store)
        called = True

    service = CronService(tmp_path / "cron" / "jobs.json", on_job=guarded_on_job)
    job = service.add_job(
        name="missing-grant",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
        grant_id="missing-secret",
    )

    await service.run_job(job.id, force=True)
    loaded = service.get_job(job.id)

    assert called is False
    assert loaded is not None
    assert loaded.state.last_status == "error"
    assert loaded.state.last_error == "Capability grant is missing, expired, or revoked."
