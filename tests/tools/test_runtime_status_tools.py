from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from OpenHome.agent.confirmation import ConfirmationRequest, PendingConfirmationStore
from OpenHome.agent.domain_packs import DomainPackManager
from OpenHome.agent.tools.filesystem import ReadFileTool
from OpenHome.agent.tools.runtime_status import (
    ConfirmationSummaryTool,
    CronSummaryTool,
    RuntimeStatusTool,
    ToolAuditSummaryTool,
)
from OpenHome.config.schema import DomainPacksConfig
from OpenHome.cron.service import CronService
from OpenHome.cron.types import CronSchedule

RAW_COMMAND = "echo super-secret-command"
RAW_PATH = "C:/secret/path/file.txt"
RAW_URL = "https://example.com/private?token=secret"
RAW_ACTOR_HASH = "actor_hash_should_not_show"
RAW_TARGET_HASH = "target_hash_should_not_show"
RAW_EVENT_HASH = "event_hash_should_not_show"
RAW_MESSAGE = "message with secret device_id lamp_123"
RAW_PROMPT = "please unlock the private thing"
RAW_PAYLOAD = "payload_secret"


def _serialized(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@pytest.mark.asyncio
async def test_generic_file_tool_still_cannot_read_raw_protected_audit(tmp_path) -> None:
    protected = tmp_path / "memory" / "audit" / "tool_calls.jsonl"
    protected.parent.mkdir(parents=True)
    protected.write_text("{}", encoding="utf-8")
    tool = ReadFileTool(workspace=tmp_path)

    result = await tool.execute(path="memory/audit/tool_calls.jsonl")

    assert "protected runtime state" in result


@pytest.mark.asyncio
async def test_tool_audit_summary_aggregates_without_raw_or_hash_values(tmp_path) -> None:
    audit_path = tmp_path / "memory" / "audit" / "tool_calls.jsonl"
    audit_path.parent.mkdir(parents=True)
    rows = [
        {
            "tool_name": "exec",
            "status": "policy_denied",
            "policy_rule": "capability_snapshot_required",
            "created_at": "2026-05-17T01:00:00+00:00",
            "actor_id_hash": RAW_ACTOR_HASH,
            "target_hash": RAW_TARGET_HASH,
            "event_hash": RAW_EVENT_HASH,
            "event_id": "tool_audit_raw_id",
            "raw_hint": RAW_COMMAND,
        },
        {
            "tool_name": "web_fetch",
            "status": "error",
            "policy_rule": "ssrf_denied",
            "created_at": "2026-05-17T02:00:00+00:00",
            "session_key_hash": "session_hash_should_not_show",
            "target_hash": "url_hash_should_not_show",
            "raw_hint": RAW_URL,
        },
        {
            "tool_name": "read_file",
            "status": "success",
            "created_at": "2026-05-17T00:00:00+00:00",
            "raw_hint": RAW_PATH,
        },
    ]
    audit_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )

    result = await ToolAuditSummaryTool(workspace=tmp_path, audit_mode="security").execute()

    assert result == {
        "audit_mode": "security",
        "enabled": True,
        "total": 3,
        "status_counts": {"policy_denied": 1, "error": 1, "success": 1},
        "policy_rule_counts": {
            "capability_snapshot_required": 1,
            "ssrf_denied": 1,
        },
        "top_failed_tools": {"exec": 1, "web_fetch": 1},
        "latest_created_at": "2026-05-17T02:00:00+00:00",
    }
    serialized = _serialized(result)
    for forbidden in (
        RAW_COMMAND,
        RAW_PATH,
        RAW_URL,
        RAW_ACTOR_HASH,
        RAW_TARGET_HASH,
        RAW_EVENT_HASH,
        "tool_audit_raw_id",
        "session_hash_should_not_show",
        "url_hash_should_not_show",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_tool_audit_summary_reports_disabled_when_audit_mode_off(tmp_path) -> None:
    result = await ToolAuditSummaryTool(workspace=tmp_path, audit_mode="off").execute()

    assert result["audit_mode"] == "off"
    assert result["enabled"] is False
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_cron_summary_does_not_expose_message_or_routing(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    service.add_job(
        name="secret job",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message=RAW_MESSAGE,
        deliver=True,
        channel="private-channel",
        to="private-chat",
        capability_snapshot={
            "source": "cron",
            "trigger": "scheduled",
            "can_exec": False,
            "can_read_files": False,
        },
    )

    result = await CronSummaryTool(cron_service=service).execute()

    assert result["available"] is True
    assert result["job_count"] == 1
    assert result["enabled_count"] == 1
    assert result["schedule_kind_counts"] == {"every": 1}
    assert result["has_next_run_count"] == 1
    assert result["capability_summary_counts"] == {"cron:scheduled:enabled_flags=0": 1}
    serialized = _serialized(result)
    for forbidden in (RAW_MESSAGE, "secret job", "private-channel", "private-chat"):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_cron_summary_accepts_object_capability_snapshot(tmp_path) -> None:
    @dataclass
    class SnapshotObject:
        source: str = "cron"
        trigger: str = "scheduled"
        can_exec: bool = True
        can_read_files: bool = False

    service = SimpleNamespace(
        list_jobs=lambda include_disabled=False: [
            SimpleNamespace(
                enabled=True,
                schedule=SimpleNamespace(kind="every"),
                state=SimpleNamespace(next_run_at_ms=1),
                payload=SimpleNamespace(capability_snapshot=SnapshotObject()),
            )
        ]
    )

    result = await CronSummaryTool(cron_service=service).execute()  # type: ignore[arg-type]

    assert result["capability_summary_counts"] == {"cron:scheduled:enabled_flags=1": 1}


@pytest.mark.asyncio
async def test_confirmation_summary_does_not_expose_prompt_reason_payload_or_scope(tmp_path) -> None:
    store = PendingConfirmationStore(tmp_path)
    now = datetime.now(timezone.utc)
    store.write_all([
        ConfirmationRequest(
            confirmation_id="confirmation_secret",
            kind="action_confirmation",
            status="pending",
            prompt=RAW_PROMPT,
            action="set_light_power",
            scope="home.living_room.lighting.private_device",
            trigger="user_initiated",
            risk="high",
            requested_by="actor_secret",
            decision_reason="because user asked for private payload",
            presence_status="unknown",
            related_fact_ids=[],
            created_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=2)).isoformat(),
            action_payload={"secret": RAW_PAYLOAD},
        ),
        ConfirmationRequest(
            confirmation_id="confirmation_expired",
            kind="notify_only",
            status="expired",
            prompt="expired prompt",
            action=None,
            scope=None,
            trigger="system",
            risk=None,
            requested_by=None,
            decision_reason="expired reason",
            presence_status="unknown",
            related_fact_ids=[],
            created_at=now.isoformat(),
            expires_at=(now - timedelta(minutes=1)).isoformat(),
        ),
    ])

    result = await ConfirmationSummaryTool(workspace=tmp_path, confirmation_store=store).execute()

    assert result == {
        "confirmation_count": 2,
        "kind_counts": {"action_confirmation": 1, "notify_only": 1},
        "status_counts": {"pending": 1, "expired": 1},
        "risk_counts": {"high": 1, "unknown": 1},
        "pending_count": 1,
        "expired_count": 1,
    }
    serialized = _serialized(result)
    for forbidden in (
        RAW_PROMPT,
        RAW_PAYLOAD,
        "private_device",
        "actor_secret",
        "because user asked",
        "expired reason",
        "confirmation_secret",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_runtime_status_uses_workspace_basename_not_absolute_path(tmp_path) -> None:
    class Registry:
        tool_names = ["a", "b"]

    result = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=Registry(),
        sessions=object(),
        pending_queues={"session": object()},
        audit_mode="minimal",
    ).execute()

    assert result["workspace_present"] is True
    assert result["workspace_name"] == tmp_path.name
    assert result["registered_tools_count"] == 2
    assert result["pending_queue_count"] == 1
    assert str(tmp_path) not in _serialized(result)


@pytest.mark.asyncio
async def test_runtime_status_reports_domain_pack_counts(tmp_path) -> None:
    pack = tmp_path / "domain_packs" / "research"
    pack.mkdir(parents=True)
    (pack / "domain_pack.yaml").write_text(
        "id: research\nname: Research\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    (pack / "CAPABILITIES.md").write_text("# Research\n", encoding="utf-8")
    manager = DomainPackManager(
        tmp_path,
        config=DomainPacksConfig(active=["research"]),
        builtin_dir=tmp_path / "empty",
    )
    manager.record_domain_tool_runtime("research", "research_search", "registered")
    manager.record_domain_tool_runtime("research", "research_bad", "skipped", "bad")

    result = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=["a"]),
        sessions=object(),
        pending_queues={},
        domain_pack_manager=manager,
    ).execute()

    assert result["domain_packs_count"] == 1
    assert result["active_domain_pack_ids"] == ["research"]
    assert result["registered_domain_tools_count"] == 1
    assert result["skipped_domain_tools_count"] == 1


@pytest.mark.asyncio
async def test_runtime_status_reports_background_review_counts(tmp_path) -> None:
    class ReviewService:
        def runtime_status(self):
            return {
                "background_review_enabled": True,
                "background_review_running_count": 1,
                "background_review_proposal_count": 3,
                "background_review_pending_count": 2,
                "background_review_last_created_at": "2026-05-19T10:00:00+00:00",
                "background_review_last_result": {"status": "ok", "proposals_written": 1},
            }

    result = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=["a"]),
        sessions=object(),
        pending_queues={},
        background_review_service=ReviewService(),
    ).execute()

    assert result["background_review_enabled"] is True
    assert result["background_review_running_count"] == 1
    assert result["background_review_proposal_count"] == 3
    assert result["background_review_pending_count"] == 2
