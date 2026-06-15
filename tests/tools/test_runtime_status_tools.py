from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from OriginAgent.agent.confirmation import ConfirmationRequest, PendingConfirmationStore
from OriginAgent.agent.domain_packs import DomainPackManager
from OriginAgent.agent.introspection.service import RuntimeIntrospectionService
from OriginAgent.agent.tools.filesystem import ReadFileTool
from OriginAgent.agent.tools.runtime_status import (
    ConfirmationSummaryTool,
    CronSummaryTool,
    InspectContextTool,
    InspectSnapshotTool,
    RuntimeStatusTool,
    ToolAuditSummaryTool,
)
from OriginAgent.agent.tools.context import RequestContext
from OriginAgent.config.schema import DomainPacksConfig, NearlineMemoryConfig
from OriginAgent.cron.service import CronService
from OriginAgent.cron.types import CronSchedule
from OriginAgent.session.search_index import SessionSearchIndexService

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
async def test_runtime_status_uses_cron_for_reminder_summary(tmp_path) -> None:
    last_run_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    service = SimpleNamespace(
        list_jobs=lambda include_disabled=True: [
            SimpleNamespace(
                enabled=True,
                payload=SimpleNamespace(deliver=True),
                state=SimpleNamespace(
                    next_run_at_ms=last_run_at_ms + 60_000,
                    last_run_at_ms=last_run_at_ms,
                    last_status="success",
                ),
            )
        ]
    )

    result = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=["a"]),
        sessions=object(),
        pending_queues={},
        cron_service=service,
    ).execute()

    assert result["cron_available"] is True
    assert result["reminder_total"] == 1
    assert result["reminder_status_counts"] == {"fired": 1}
    assert result["reminder_last_fired_at"] is not None


@pytest.mark.asyncio
async def test_inspect_context_reports_phase1_views_and_scope_filter(tmp_path) -> None:
    session = SimpleNamespace(
        metadata={
            "working_memory_v1": {
                "scope": "session",
                "owner_id": "user-1",
                "pending_questions": ["What should we do next?"],
            },
            "world_state_v1": {
                "status": "active",
                "version": "phase2",
                "updated_at": "2026-06-09T00:00:00+00:00",
                "scope": "session",
                "owner_id": "user-1",
                "snapshots": [
                    {
                        "snapshot_id": "snap_1",
                        "kind": "image",
                        "source": "media.image",
                        "scope": "session",
                        "owner_id": "user-1",
                        "device_id": "device-a",
                        "captured_at": "2026-06-09T00:00:00+00:00",
                        "media_path": "uploads/desk.png",
                        "summary": "Desk has a notebook.",
                        "objects": ["notebook"],
                        "relationships": [],
                        "confidence": 0.8,
                        "uncertainties": [],
                        "provenance": {"producer": "test"},
                    }
                ],
                "inspections": [],
                "world_summary": {
                    "summary_id": "world_1",
                    "scope": "session",
                    "owner_id": "user-1",
                    "generated_at": "2026-06-09T00:00:00+00:00",
                    "fresh_until": "2026-06-09T00:05:00+00:00",
                    "focus": ["Desk has a notebook."],
                    "relationships": ["notebook is on desk"],
                    "constraints": ["snapshot confidence is high"],
                    "uncertainties": [],
                    "source_snapshot_ids": ["snap_1"],
                    "inspection_ids": [],
                },
            },
        },
        get_history=lambda **kwargs: [
            {"role": "user", "content": "Earlier message"},
            {"role": "assistant", "content": "Earlier answer"},
        ],
    )
    sessions = SimpleNamespace(get_or_create=lambda key: session)
    runtime_context = SimpleNamespace(
        actor_id="user-1",
        user_id="user-1",
        session_id="cli:direct",
        device_id="device-a",
        trigger="user_initiated",
        source="user_turn",
        default_scope="session",
        identity=SimpleNamespace(user_id="user-1"),
    )
    context_builder = SimpleNamespace(
        _context_config=SimpleNamespace(enable_phase1_continuity=True),
        _last_prewarm_audit={
            "prewarm_enabled": True,
            "prewarm_empty": False,
            "prewarm_reason": "prepared",
            "prewarm_sources": ["recent_sessions"],
            "prewarm_seed_counts": {
                "working_memory_seed": 1,
                "world_view_seed": 0,
                "retrieval_seed": 1,
            },
        },
        _last_retrieval_fusion={
            "enabled": True,
            "sources_used": ["fact_store", "prewarm_seed", "nearline_retrieval", "session_search"],
            "source_counts": {
                "fact_store": 1,
                "prewarm_seed": 1,
                "nearline_retrieval": 1,
                "session_search": 1,
            },
            "deduped_count": 1,
            "trimmed_count": 2,
            "scope_filtered": [{"source": "session_search", "reason": "scope_hidden"}],
            "hits": {
                "fact_store": [{"source": "fact_store", "title": "facts"}],
                "prewarm_seed": [{"source": "prewarm_seed", "title": "recent_session"}],
                "nearline_retrieval": [{"source": "nearline_retrieval", "title": "layered_memory"}],
                "session_search": [{"source": "session_search", "title": "sessions"}],
            },
        },
        build_reference_context_blocks=lambda **kwargs: [
            {
                "type": "text",
                "text": "<reference_context source='memory_retrieval'>retrieved fact</reference_context>",
                "_meta": {"kind": "reference_context", "source": "memory_retrieval"},
            },
            {
                "type": "text",
                "text": "<reference_context source='recent_history'>dialogue remainder</reference_context>",
                "_meta": {"kind": "reference_context", "source": "recent_history"},
            },
        ],
    )
    loop = SimpleNamespace(
        _last_runtime_context=runtime_context,
        _last_continuity_session_key="cli:direct",
        _meta_cognition_runtime=SimpleNamespace(
            summary=lambda: {
                "contract_version": "meta_cognition.v1.freeze",
                "enabled": True,
                "trigger_collection_enabled": True,
                "structured_reflection_enabled": True,
                "pattern_consolidation_enabled": True,
                "evolution_bridge_enabled": True,
                "runtime_status": {"accepted_total": 1},
                "recent_triggers": [{"trigger_type": "tool_failure"}],
                "recent_decisions": [{"decision": "accepted"}],
                "recent_journals": [{"entry_id": "journal_1", "summary": "tool failure observed"}],
                "recent_reflections": [{"reflection_id": "reflection_1", "summary": "retry pattern learned"}],
                "recent_confidence_traces": [{"trace_id": "trace_1", "summary": "confidence recorded"}],
                "recent_patterns": [{"pattern_id": "pattern_1", "summary": "repeated retry issue"}],
                "recent_evolution_seeds": [{"seed_id": "seed_1", "summary": "meta skill candidate"}],
                "decision_counts": {"accepted": 1},
                "suppression_reason_counts": {},
                "artifact_status": {
                    "journals_written": 1,
                    "reflections_written": 1,
                    "confidence_traces_written": 1,
                    "patterns_written": 1,
                    "evolution_seeds_written": 1,
                    "working_memory_bridge": {
                        "enabled": True,
                        "last_status": "ok",
                        "decision_counts": {"attention_appended": 1},
                    },
                    "memory_candidate_bridge": {
                        "enabled": True,
                        "last_status": "ok",
                        "decision_counts": {"queued": 1},
                    },
                },
                "working_memory_bridge": {
                    "enabled": True,
                    "last_status": "ok",
                    "decision_counts": {"attention_appended": 1},
                },
                "memory_candidate_bridge": {
                    "enabled": True,
                    "last_status": "ok",
                    "decision_counts": {"queued": 1},
                },
                "bridge_decision_counts": {"attention_appended": 1, "queued": 1},
            }
        ),
        _meta_cognition_reflector=SimpleNamespace(
            recent_artifacts=lambda limit=10: {
                "recent_journals": [{"entry_id": "journal_1", "summary": "tool failure observed"}],
                "recent_reflections": [{"reflection_id": "reflection_1", "summary": "retry pattern learned"}],
                "recent_confidence_traces": [{"trace_id": "trace_1", "summary": "confidence recorded"}],
                "recent_patterns": [{"pattern_id": "pattern_1", "summary": "repeated retry issue"}],
                "recent_evolution_seeds": [{"seed_id": "seed_1", "summary": "meta skill candidate"}],
            },
            runtime_status=lambda: {
                "structured_reflection_enabled": True,
                "artifact_status": {
                    "journals_written": 1,
                    "reflections_written": 1,
                    "confidence_traces_written": 1,
                    "patterns_written": 1,
                    "evolution_seeds_written": 1,
                    "working_memory_bridge": {
                        "enabled": True,
                        "last_status": "ok",
                        "decision_counts": {"attention_appended": 1},
                    },
                    "memory_candidate_bridge": {
                        "enabled": True,
                        "last_status": "ok",
                        "decision_counts": {"queued": 1},
                    },
                },
                "bridge_decision_counts": {"attention_appended": 1, "queued": 1},
            },
        ),
        _last_meta_cognition_summary={},
        _last_meta_artifacts={
            "recent_journals": [{"entry_id": "journal_1", "summary": "tool failure observed"}],
            "recent_reflections": [{"reflection_id": "reflection_1", "summary": "retry pattern learned"}],
            "recent_confidence_traces": [{"trace_id": "trace_1", "summary": "confidence recorded"}],
            "recent_patterns": [{"pattern_id": "pattern_1", "summary": "repeated retry issue"}],
            "recent_evolution_seeds": [{"seed_id": "seed_1", "summary": "meta skill candidate"}],
        },
        _last_meta_trigger_scan=[],
        _last_context_assembly={
            "enabled": True,
            "contract_version": "continuity.v1.freeze",
            "assembler_role": "thin_orchestration_audit",
            "assembly_order": [
                "system_prompt",
                "runtime_state",
                "recovered_continuity_checkpoint",
                "continuity_blocks",
                "reference_blocks",
                "internal_event",
                "current_user_message",
            ],
            "block_kinds": [
                "runtime_context",
                "continuity_context",
                "working_memory_context",
                "world_state_context",
                "reference_context",
            ],
            "continuity_block_kinds": [
                "continuity_context",
                "working_memory_context",
                "world_state_context",
            ],
            "reference_sources": ["memory_retrieval", "recent_history"],
            "current_message_preview": "Please continue the task",
            "prewarm_seed_counts": {"working_memory_seed": 1, "world_view_seed": 0, "retrieval_seed": 1},
        },
        _last_governance_audit={
            "promotion_candidates": [{"candidate_key": "candidate_1", "applied": True}],
            "promotion_conflict_count": 1,
            "forgetting_actions": [{"kind": "empty_turn", "retained": True}],
        },
        _max_messages=120,
        context=context_builder,
        sessions=sessions,
        _replay_token_budget=lambda: 0,
        working_memory=SimpleNamespace(
            inspect=lambda session, identity=None: {
                "scope": "session",
                "owner_id": "user-1",
                "pending_questions": ["What should we do next?"],
            }
        ),
        world_state=SimpleNamespace(
            load=lambda session, identity=None: SimpleNamespace(
                to_json=lambda: session.metadata["world_state_v1"],
                world_summary=SimpleNamespace(
                    to_json=lambda: session.metadata["world_state_v1"]["world_summary"],
                ),
            ),
            filtered_candidates=lambda session, runtime_context=None, current_message=None: {
                "included_summary": session.metadata["world_state_v1"]["world_summary"],
                "filtered_candidates": [
                    {
                        "snapshot_id": "snap_old",
                        "scope": "device",
                        "included": False,
                        "reasons": ["scope_hidden"],
                    }
                ],
                "freshness": {
                    "generated_at": "2026-06-09T00:00:00+00:00",
                    "fresh_until": "2026-06-09T00:05:00+00:00",
                    "is_fresh": True,
                },
                "selection_reasons": ["relevant_to_message"],
                "contested_summary": {"contested": True, "items": ["desk state disputed"]},
            },
            recent_events=lambda session, identity=None, limit=5: {
                "recent_events": [
                    {
                        "event_id": "evt_1",
                        "kind": "contested_world_state",
                        "snapshot_id": "snap_1",
                        "inspection_id": "inspect_1",
                        "summary": "desk state disputed",
                        "confidence": 0.81,
                        "contested": True,
                        "created_at": "2026-06-09T00:01:00+00:00",
                        "source": "camera.image",
                        "scope": "session",
                        "owner_id": "user-1",
                        "provenance": {"producer": "camera", "ingest_method": "workspace_inbox"},
                    }
                ],
                "event_summary": {
                    "total": 1,
                    "by_kind": {"contested_world_state": 1},
                    "latest_created_at": "2026-06-09T00:01:00+00:00",
                },
            },
        ),
        _last_world_attention_write={
            "world_attention_total": 3,
            "world_kept": 2,
            "world_truncated": 1,
            "world_truncated_by_limit": True,
            "attention_merged_items": [
                "world_contested: desk state disputed",
                "world_uncertainty: label unreadable",
            ],
        },
    )

    result = await InspectContextTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=["originagent_inspect_context"]),
        sessions=sessions,
        pending_queues={},
        introspection_service=RuntimeIntrospectionService(
            loop=loop,
            workspace=tmp_path,
            registry=SimpleNamespace(tool_names=["originagent_inspect_context"]),
            sessions=sessions,
            pending_queues={},
        ),
    ).execute()

    assert result["enabled"] is True
    assert result["contract_version"] == "continuity.v1.freeze"
    assert result["views"]["conversation"]["message_count"] == 2
    assert result["views"]["conversation"]["current_message_preview"] == "Please continue the task"
    assert result["views"]["working"]["working_memory"]["pending_questions"] == ["What should we do next?"]
    assert result["views"]["retrieval"]["sources"] == ["memory_retrieval"]
    assert result["views"]["retrieval"]["dialogue_sources"] == ["recent_history"]
    assert result["views"]["retrieval"]["fusion_enabled"] is True
    assert result["views"]["retrieval"]["fusion_source_counts"]["fact_store"] == 1
    assert result["views"]["retrieval"]["fusion_source_counts"]["prewarm_seed"] == 1
    assert result["views"]["retrieval"]["prewarm_hits"][0]["source"] == "prewarm_seed"
    assert result["views"]["retrieval"]["fusion_deduped_count"] == 1
    assert result["views"]["retrieval"]["fusion_trimmed_count"] == 2
    assert result["views"]["retrieval"]["fusion_scope_filtered"][0]["reason"] == "scope_hidden"
    assert result["views"]["world"]["snapshot"]["status"] == "active"
    assert result["views"]["world"]["summary"]["focus"] == ["Desk has a notebook."]
    assert result["views"]["world"]["summary"]["relationships"] == ["notebook is on desk"]
    assert result["views"]["world"]["source_snapshot_ids"] == ["snap_1"]
    assert result["views"]["world"]["filtered_candidates"][0]["reasons"] == ["scope_hidden"]
    assert result["views"]["world"]["freshness"]["is_fresh"] is True
    assert result["views"]["world"]["contested"]["contested"] is True
    assert result["views"]["world"]["selection_reasons"] == ["relevant_to_message"]
    assert result["views"]["world"]["attention_write"]["world_attention_total"] == 3
    assert result["views"]["world"]["attention_write"]["world_kept"] == 2
    assert result["views"]["world"]["recent_events"][0]["kind"] == "contested_world_state"
    assert result["views"]["world"]["event_summary"]["total"] == 1
    assert result["views"]["world"]["event_summary"]["by_kind"]["contested_world_state"] == 1
    assert result["governance"]["consumer"]["dream"]["last_run"]["consumer"] == "dream"
    assert result["governance"]["consumer"]["nearline_profile"]["last_run"]["consumer"] == "nearline_profile"
    assert result["governance"]["consumer"]["dream"]["pending_backlog"]["pending_count"] >= 0
    assert result["governance"]["queue_backlog"]["total"] >= 0
    assert result["governance"]["forgetting_execution"]["executed"] is True
    assert result["views"]["meta_cognition"]["contract_version"] == "meta_cognition.v1.freeze"
    assert result["views"]["meta_cognition"]["recent_triggers"][0]["trigger_type"] == "tool_failure"
    assert result["views"]["meta_cognition"]["structured_reflection_enabled"] is True
    assert result["views"]["meta_cognition"]["recent_journals"][0]["entry_id"] == "journal_1"
    assert result["views"]["meta_cognition"]["recent_reflections"][0]["reflection_id"] == "reflection_1"
    assert result["views"]["meta_cognition"]["recent_confidence_traces"][0]["trace_id"] == "trace_1"
    assert result["views"]["meta_cognition"]["recent_patterns"][0]["pattern_id"] == "pattern_1"
    assert result["views"]["meta_cognition"]["recent_evolution_seeds"][0]["seed_id"] == "seed_1"
    assert result["views"]["meta_cognition"]["working_memory_bridge"]["decision_counts"]["attention_appended"] == 1
    assert result["views"]["meta_cognition"]["memory_candidate_bridge"]["decision_counts"]["queued"] == 1
    assert result["last_context_assembly"]["assembly_order"] == [
        "system_prompt",
        "runtime_state",
        "recovered_continuity_checkpoint",
        "continuity_blocks",
        "reference_blocks",
        "internal_event",
        "current_user_message",
    ]
    assert result["governance"]["promotions"][0]["candidate_key"] == "candidate_1"
    assert result["governance"]["conflicts"] == 1
    assert result["governance"]["forgetting"][0]["kind"] == "empty_turn"
    assert result["scope_filter"]["current_scope"] == "session"
    assert result["scope_filter"]["visibility_matrix"]["device"] is True
    assert result["scope_filter"]["visibility_matrix"]["task"] is False
    assert any(
        candidate["source"] == "working_memory" and candidate["visible"] is True
        for candidate in result["scope_filter"]["candidates"]
    )
    assert any(
        candidate["source"] == "world_view" and candidate["visible"] is True
        for candidate in result["scope_filter"]["candidates"]
    )


@pytest.mark.asyncio
async def test_inspect_snapshot_tool_writes_back_minimal_inspection(tmp_path) -> None:
    session = SimpleNamespace(key="cli:direct")
    sessions = SimpleNamespace(get_or_create=lambda key: session)
    calls: list[dict[str, object]] = []
    inspection_service = SimpleNamespace(
        inspect=AsyncMock(
            side_effect=lambda session, runtime_context=None, snapshot_id=None, requested_by=None: (
                calls.append(
                    {
                        "snapshot_id": snapshot_id,
                        "requested_by": requested_by,
                        "runtime_context": runtime_context,
                    }
                )
                or {
                    "snapshot": {"snapshot_id": snapshot_id},
                    "inspection": {"inspection_id": "inspect_1"},
                    "world_summary": {"focus": ["verified scene"]},
                    "inspection_path": "service",
                }
            )
        )
    )
    world_state = SimpleNamespace()
    loop = SimpleNamespace(world_state=world_state)
    tool = InspectSnapshotTool(
        sessions=sessions,
        introspection_service=RuntimeIntrospectionService(
            loop=loop,
            workspace=tmp_path,
            registry=SimpleNamespace(tool_names=["originagent_inspect_snapshot"]),
            sessions=sessions,
            pending_queues={},
        ),
        inspection_service=inspection_service,
    )
    runtime_context = SimpleNamespace(source="user_turn")
    tool.set_context(
        RequestContext(
            channel="cli",
            chat_id="direct",
            session_key="cli:direct",
            runtime_context=runtime_context,
        )
    )

    result = await tool.execute(snapshot_id="snap_1")

    assert result["inspection"]["inspection_id"] == "inspect_1"
    assert result["inspection_path"] == "service"
    assert calls[0]["snapshot_id"] == "snap_1"
    assert calls[0]["requested_by"] == "user_turn"


@pytest.mark.asyncio
async def test_inspect_snapshot_tool_falls_back_to_legacy_path_when_service_missing(tmp_path) -> None:
    session = SimpleNamespace(key="cli:direct")
    sessions = SimpleNamespace(get_or_create=lambda key: session)
    calls: list[dict[str, object]] = []
    world_state = SimpleNamespace(
        inspect_snapshot=lambda session, runtime_context=None, snapshot_id=None, requested_by=None: (
            calls.append(
                {
                    "snapshot_id": snapshot_id,
                    "requested_by": requested_by,
                    "runtime_context": runtime_context,
                }
            )
            or {
                "snapshot": {"snapshot_id": snapshot_id},
                "inspection": {"inspection_id": "inspect_legacy"},
                "world_summary": {"focus": ["verified scene"]},
            }
        )
    )
    loop = SimpleNamespace(world_state=world_state, provider=None, model=None, auxiliary_router=None)
    tool = InspectSnapshotTool(
        sessions=sessions,
        introspection_service=RuntimeIntrospectionService(
            loop=loop,
            workspace=tmp_path,
            registry=SimpleNamespace(tool_names=["originagent_inspect_snapshot"]),
            sessions=sessions,
            pending_queues={},
        ),
        inspection_service=None,
    )
    runtime_context = SimpleNamespace(source="user_turn")
    tool.set_context(
        RequestContext(
            channel="cli",
            chat_id="direct",
            session_key="cli:direct",
            runtime_context=runtime_context,
        )
    )

    result = await tool.execute(snapshot_id="snap_legacy")

    assert result["inspection"]["inspection_id"] == "inspect_legacy"
    assert result["inspection_path"] == "legacy_fallback"
    assert calls[0]["snapshot_id"] == "snap_legacy"


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
    assert result["self_model"]["identity"]["workspace_name"] == tmp_path.name
    assert result["self_model"]["runtime"]["registered_tools_count"] == 2
    assert str(tmp_path) not in _serialized(result)


@pytest.mark.asyncio
async def test_runtime_status_uses_provided_nearline_runtime_config(tmp_path) -> None:
    class Registry:
        tool_names = ["a"]

    result = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=Registry(),
        sessions=object(),
        pending_queues={},
        nearline_memory_config=NearlineMemoryConfig(
            enabled=True,
            pipeline_enabled=False,
            profile_shadow_write_enabled=True,
        ),
    ).execute()

    assert result["self_model"]["memory"]["nearline"]["status"] == "idle"
    assert result["self_model"]["memory"]["nearline"]["nearline_enabled"] is True
    assert result["self_model"]["memory"]["nearline"]["pipeline_enabled"] is False
    assert result["self_model"]["memory"]["nearline"]["profile_shadow_write_enabled"] is True
    assert result["self_model"]["memory"]["user_profile_file"]["status"] == "missing"
    assert result["self_model"]["memory"]["memory_candidate_queue"]["status"] == "lazy_not_created"


@pytest.mark.asyncio
async def test_runtime_status_exposes_workspace_memory_state(tmp_path) -> None:
    class Registry:
        tool_names = ["a"]

    (tmp_path / "USER.md").write_text("# User Profile\n\n- Name: Ada\n", encoding="utf-8")
    queue_file = tmp_path / "memory" / "memory_candidates.jsonl"
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "candidate_id": "memcand_1",
                        "kind": "preference",
                        "summary": "Prefers concise updates",
                        "source_session_key": "cli:test",
                        "source_refs": ["turn-1"],
                        "source_excerpt": "I prefer concise updates",
                        "confidence": 0.9,
                        "sensitivity": "low",
                        "scope": "user",
                        "owner_id": "user",
                        "created_at": "2026-06-05T10:00:00+00:00",
                        "metadata": {},
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )

    result = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=Registry(),
        sessions=object(),
        pending_queues={},
    ).execute()

    memory = result["self_model"]["memory"]
    assert memory["user_profile_file"]["status"] == "initialized"
    assert memory["user_profile_file"]["exists"] is True
    assert memory["memory_candidate_queue"]["status"] == "active"
    assert memory["memory_candidate_queue"]["pending_count"] == 1
    assert memory["memory_candidate_queue"]["last_candidate_at"] == "2026-06-05T10:00:00+00:00"


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
                "last_status": "ok",
                "last_fault_class": "unknown",
                "last_retryable": False,
                "last_degraded": False,
                "last_reason": "ok",
                "last_started_at": "2026-05-19T09:59:00+00:00",
                "last_finished_at": "2026-05-19T10:00:00+00:00",
                "consecutive_failures": 0,
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
    assert result["self_model"]["runtime"]["background_review_enabled"] is True
    assert result["background_tasks"]["tasks"]["background_review"]["last_status"] == "ok"


@pytest.mark.asyncio
async def test_runtime_status_reports_curator_counts(tmp_path) -> None:
    class CuratorService:
        def runtime_status(self):
            return {
                "curator_enabled": True,
                "curator_running_count": 1,
                "curator_proposal_count": 4,
                "curator_pending_count": 3,
                "curator_last_created_at": "2026-05-20T10:00:00+00:00",
                "curator_last_result": {"status": "ok", "proposals_written": 2},
                "curator_type_counts": {"promote_skill": 1, "deprecate_skill": 3},
                "last_status": "degraded",
                "last_fault_class": "external",
                "last_retryable": False,
                "last_degraded": True,
                "last_reason": "maintenance_failed",
                "last_started_at": "2026-05-20T09:59:00+00:00",
                "last_finished_at": "2026-05-20T10:00:00+00:00",
                "consecutive_failures": 0,
            }

    result = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=["a"]),
        sessions=object(),
        pending_queues={},
        curator_service=CuratorService(),
    ).execute()

    assert result["curator_enabled"] is True
    assert result["curator_running_count"] == 1
    assert result["curator_proposal_count"] == 4
    assert result["curator_pending_count"] == 3
    assert result["curator_type_counts"] == {"promote_skill": 1, "deprecate_skill": 3}
    assert result["background_tasks"]["tasks"]["curator"]["last_status"] == "degraded"


@pytest.mark.asyncio
async def test_runtime_status_reports_skill_lifecycle_counts(tmp_path) -> None:
    skill = tmp_path / "skills" / "candidate"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: candidate\n"
        "description: Candidate skill.\n"
        "always: false\n"
        "metadata:\n"
        "  OriginAgent:\n"
        "    proposal_status: proposed\n"
        "    verification_status: unverified\n"
        "    review_proposal_id: review_candidate\n"
        "    created_by: background_review\n"
        "---\n\n# Candidate\n",
        encoding="utf-8",
    )

    result = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=["a"]),
        sessions=object(),
        pending_queues={},
    ).execute()

    assert result["skills_count"] >= 1
    assert result["workspace_skills_count"] == 1
    assert result["skill_lifecycle_status_counts"]["proposed"] == 1
    assert result["skill_verification_status_counts"]["unverified"] == 1
    assert result["unverified_skill_count"] == 1
    assert result["always_workspace_skill_count"] == 0


@pytest.mark.asyncio
async def test_runtime_status_reports_workflow_artifact_counts(tmp_path) -> None:
    workflow = tmp_path / "workflows" / "manual-check"
    workflow.mkdir(parents=True)
    (workflow / "workflow.yaml").write_text(
        "\n".join([
            "schema_version: 1",
            "name: manual-check",
            "description: Manual check.",
            "kind: manual_guide",
            "execution:",
            "  auto_run: false",
            "  creates_cron: false",
            "  calls_tools: false",
            "body: Review state manually.",
            "steps: []",
            "metadata:",
            "  OriginAgent:",
            "    proposal_status: proposed",
            "    verification_status: unverified",
            "    review_proposal_id: review_manual",
            "    domain_id: core",
            "    created_by: background_review",
            "    source_session: websocket:chat1",
            "    source_turn_id: turn-1",
            "",
        ]),
        encoding="utf-8",
    )
    invalid = tmp_path / "workflows" / "broken"
    invalid.mkdir(parents=True)
    (invalid / "workflow.yaml").write_text("not: valid\n", encoding="utf-8")

    result = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=["a"]),
        sessions=object(),
        pending_queues={},
    ).execute()

    assert result["workflow_artifacts_count"] == 2
    assert result["workflow_artifact_status_counts"] == {"proposed": 1}
    assert result["invalid_workflow_artifacts_count"] == 1


@pytest.mark.asyncio
async def test_runtime_status_reports_session_search_index_counts(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "cli_direct.jsonl").write_text(
        "\n".join([
            json.dumps({"_type": "metadata", "key": "cli:direct"}),
            json.dumps(
                {
                    "role": "user",
                    "content": "智能家居 runtime status search index",
                    "timestamp": "2026-05-20T10:00:00",
                },
                ensure_ascii=False,
            ),
        ])
        + "\n",
        encoding="utf-8",
    )
    index = SessionSearchIndexService(tmp_path, webui_dir=tmp_path / "webui")
    index.refresh_incremental(sources=["sessions"])

    result = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=[]),
        sessions=object(),
        pending_queues={},
        session_search_index_service=index,
    ).execute()

    assert result["session_search_backend"] == "sqlite_fts"
    assert result["session_search_semantic_enabled"] is True
    assert result["session_search_index_available"] is True
    assert result["session_search_indexed_doc_count"] == 1
    assert result["session_search_indexed_source_counts"] == {"sessions": 1}
    assert result["session_search_index_stale"] is False
