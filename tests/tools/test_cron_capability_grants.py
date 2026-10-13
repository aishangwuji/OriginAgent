from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from OriginAgent.agent.tools.audit import InMemoryToolAuditSink, ToolAuditConfig
from OriginAgent.agent.tools.cron import CronTool
from OriginAgent.agent.tools.filesystem import ReadFileTool, WriteFileTool
from OriginAgent.agent.tools.registry import ToolRegistry
from OriginAgent.cron.service import CronService
from OriginAgent.cron.types import CronPayload, CronSchedule
from OriginAgent.security.capabilities import CapabilitySnapshot
from OriginAgent.security.grants import (
    CapabilityGrant,
    CapabilityGrantStore,
    snapshot_for_cron_payload,
)
from OriginAgent.security.policy import PolicyDeniedError


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


@pytest.mark.asyncio
async def test_add_job_inherits_user_capability_snapshot(tmp_path: Path) -> None:
    """D1 修复回归守卫：``_add_job`` 必须把用户当前 ``capability_snapshot``
    持久化到 ``job.payload.capability_snapshot``，让 ``on_cron_job``
    (cli/commands.py:911-923) 能通过 ``snapshot_for_trigger`` 重建出真实权限。

    修复前根因：cron.py:_add_job 硬编码 ``scheduled_default().to_dict()``
    (全 False)，触发端读到全 False → 所有 tool call 被 policy 拦死 →
    生成 confirmation 永远无法在 cron 会话中通过审批 → 死锁。

    Architecture constraint (rule 12 + rule 18):
    - rule 12 幂等：同一 snapshot 重复创建 job 得到相同 payload（已由
      turn-scoped 幂等键覆盖，此处只验证单次创建内容正确）
    - rule 18 安全边界：snapshot 字段必须是受信任来源（CronTool
      在 user-initiated turn 中收到的 snapshot），不能由 LLM 注入
    """
    service = CronService(tmp_path / "cron" / "jobs.json")
    tool = CronTool(service)
    tool.set_context("telegram", "chat-1", session_key="tenant:owner")
    # 模拟用户发起的 turn（完整权限，与实际用户场景一致）
    tool.set_capability_snapshot(CapabilitySnapshot.user_turn())

    result = await tool.execute(
        action="add",
        message="Check mail and report",
        every_seconds=3600,
    )

    assert result.startswith("Created job"), f"Expected success, got: {result}"
    jobs = service.list_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    # payload.capability_snapshot 必须反映 user_turn，而非 scheduled_default()
    snap_dict = job.payload.capability_snapshot
    assert snap_dict, "capability_snapshot must not be empty"
    assert snap_dict.get("can_exec") is True, (
        f"Expected can_exec=True (inherited from user_turn), "
        f"got can_exec={snap_dict.get('can_exec')} — cron job will deadlock "
        f"on tool_approval like D1 described"
    )
    assert snap_dict.get("can_read_files") is True
    assert snap_dict.get("can_create_cron") is True
    # source 应为 user_turn，证明继承自用户当前 snapshot 而非硬编码 cron
    assert snap_dict.get("source") == "user_turn", (
        f"Expected source='user_turn', got source={snap_dict.get('source')!r}"
    )


class _FakeCronBridge:
    """Test double for CronDesireBridge — captures on_cron_job_created calls."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def on_cron_job_created(self, job, session_key: str = "", owner_id: str = "") -> None:
        self.calls.append({
            "job_id": job.id,
            "session_key": session_key,
            "owner_id": owner_id,
        })


@pytest.mark.asyncio
async def test_add_job_passes_grant_id_and_real_owner_id(tmp_path: Path) -> None:
    """D2 + D3 联合回归守卫：

    D2: ``_add_job`` 必须从 ``CapabilityGrantStore`` 查找当前 session 的
    active grant，把 ``grant_id`` 透传给 ``CronService.add_job``，让 cron
    触发时通过 ``snapshot_for_cron_payload`` 重新加载最新授权（而非创建
    时刻的静态 snapshot 快照）。

    D3: ``_add_job`` 必须把真实 ``actor_id``（通过 ``set_context`` 注入）
    传给 ``cron_bridge.on_cron_job_created`` 作为 ``owner_id``，让 BDI
    Desire 关联到正确的用户身份（而非硬编码 ``"user"``）。

    Architecture constraint (rule 12 + rule 18):
    - rule 12 幂等：grant 查询基于 session_key + tool_name，多次创建同
      一 cron job 会复用同一 grant（latest_active_for_session 已排序）
    - rule 18 安全边界：grant_store 是受信任来源（admin_config 或
      user_confirmation），CronTool 只读取不写入
    """
    grant_store = CapabilityGrantStore(tmp_path)
    grant = _grant(
        "grant-d2-d3-test",
        expires_at=_future(),
        can_read_files=True,
        can_exec=True,
        can_create_cron=True,
        session_key="tenant:owner",
        tool_name="cron",
    )
    grant_store.put(grant)

    bridge = _FakeCronBridge()
    service = CronService(tmp_path / "cron" / "jobs.json")
    tool = CronTool(service, cron_bridge=bridge, grant_store=grant_store)
    tool.set_context(
        "telegram", "chat-1",
        metadata={},
        session_key="tenant:owner",
        actor_id="tenant:owner",
    )
    tool.set_capability_snapshot(CapabilitySnapshot.user_turn())

    result = await tool.execute(
        action="add",
        message="Check mail",
        every_seconds=3600,
    )

    assert result.startswith("Created job"), f"Expected success, got: {result}"
    jobs = service.list_jobs()
    assert len(jobs) == 1
    job = jobs[0]

    # D2: payload.grant_id 必须等于 grant_store 中的 active grant_id
    assert job.payload.grant_id == "grant-d2-d3-test", (
        f"Expected payload.grant_id='grant-d2-d3-test' (from grant_store), "
        f"got grant_id={job.payload.grant_id!r} — cron job will not pick up "
        f"future grant updates from grant_store"
    )

    # D3: cron_bridge.on_cron_job_created 收到的 owner_id 必须是真实 actor_id
    assert len(bridge.calls) == 1, f"Expected 1 bridge call, got {len(bridge.calls)}"
    call = bridge.calls[0]
    assert call["owner_id"] == "tenant:owner", (
        f"Expected owner_id='tenant:owner' (real actor_id), "
        f"got owner_id={call['owner_id']!r} — BDI Desire will be attributed "
        f"to wrong user (was hardcoded 'user' before D3 fix)"
    )
    assert call["session_key"] == "tenant:owner"


@pytest.mark.asyncio
async def test_add_job_without_grant_store_falls_back_to_no_grant_id(tmp_path: Path) -> None:
    """D2 向后兼容守卫：未注入 ``grant_store`` 时（旧调用方/测试场景），
    ``_add_job`` 不得崩溃，``payload.grant_id`` 应为 ``None``——
    触发端 fallback 到 ``scheduled_default()`` (现有行为)。
    """
    service = CronService(tmp_path / "cron" / "jobs.json")
    # 不传 grant_store —— 模拟旧调用方
    tool = CronTool(service)
    tool.set_context("telegram", "chat-1", session_key="tenant:owner")
    tool.set_capability_snapshot(CapabilitySnapshot.user_turn())

    result = await tool.execute(
        action="add",
        message="Check mail",
        every_seconds=3600,
    )

    assert result.startswith("Created job"), f"Expected success, got: {result}"
    jobs = service.list_jobs()
    assert len(jobs) == 1
    # 未注入 grant_store 时 grant_id 必须为 None（向后兼容）
    assert jobs[0].payload.grant_id is None


@pytest.mark.asyncio
async def test_add_job_without_actor_id_falls_back_to_user(tmp_path: Path) -> None:
    """D3 向后兼容守卫：``set_context`` 未传 ``actor_id`` 时（旧调用方），
    ``on_cron_job_created`` 收到的 ``owner_id`` 应回退到 ``"user"``
    （旧行为，不破坏 BDI Desire 的 owner 字段）。
    """
    bridge = _FakeCronBridge()
    service = CronService(tmp_path / "cron" / "jobs.json")
    tool = CronTool(service, cron_bridge=bridge)
    # 不传 actor_id —— 模拟旧调用方
    tool.set_context("telegram", "chat-1", session_key="tenant:guest")
    tool.set_capability_snapshot(CapabilitySnapshot.user_turn())

    await tool.execute(
        action="add",
        message="Check mail",
        every_seconds=3600,
    )

    assert len(bridge.calls) == 1
    # owner_id 应回退到 "user"（旧行为）
    assert bridge.calls[0]["owner_id"] == "user", (
        f"Expected owner_id='user' (fallback for backward compat), "
        f"got owner_id={bridge.calls[0]['owner_id']!r}"
    )
