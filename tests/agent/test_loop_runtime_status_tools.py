from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.loop import AgentLoop
from OriginAgent.agent.subagent_records import SubagentTaskRecord
from OriginAgent.agent.agent_runtime_context import set_tool_context
from OriginAgent.agent.evolution import (
    SIGNAL_KIND_WORKFLOW,
    OpportunitySignalCandidate,
    OpportunitySignalStore,
)
from OriginAgent.agent.evolution_control_plane import CONTROL_EVENT_DENIED, CONTROL_EVENT_EXECUTED
from OriginAgent.agent.evolution_outcomes import EvolutionOutcomeStore
from OriginAgent.bus.queue import MessageBus
from OriginAgent.config.schema import (
    Config,
    DomainPacksConfig,
    EvolutionConfig,
    NearlineMemoryConfig,
    ToolAuditConfig,
)
from OriginAgent.cron.types import CronSchedule

RUNTIME_TOOL_NAMES = {
    "originagent_runtime_status",
    "originagent_tool_audit_summary",
    "originagent_cron_summary",
    "originagent_confirmation_summary",
}


def _provider():
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    return provider


def _config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    return cfg


def test_agent_loop_registers_runtime_explain_tools_by_default(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )

    assert RUNTIME_TOOL_NAMES.issubset(set(loop.tools.tool_names))
    assert not any(name in loop.tools._audit_config.security_tools for name in RUNTIME_TOOL_NAMES)


@pytest.mark.asyncio
async def test_agent_loop_registers_active_domain_tools_and_reports_runtime_status(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "domain_packs" / "research"
    tools_dir = pack / "tools"
    tools_dir.mkdir(parents=True)
    (pack / "CAPABILITIES.md").write_text("# Research\n", encoding="utf-8")
    (pack / "domain_pack.yaml").write_text(
        "id: research\n"
        "name: Research\n"
        "version: 0.1.0\n"
        "tools:\n"
        "  - id: research_search\n"
        "    module: tools.search\n"
        "    class: ResearchSearchTool\n"
        "    permissions: []\n",
        encoding="utf-8",
    )
    (tools_dir / "search.py").write_text(
        "from OriginAgent.agent.tools.base import Tool\n\n"
        "class ResearchSearchTool(Tool):\n"
        "    name = 'research_search'\n"
        "    @property\n"
        "    def description(self):\n"
        "        return 'search'\n"
        "    @property\n"
        "    def parameters(self):\n"
        "        return {'type': 'object', 'properties': {}, 'additionalProperties': False}\n"
        "    @property\n"
        "    def read_only(self):\n"
        "        return True\n"
        "    async def execute(self, **kwargs):\n"
        "        return 'ok'\n",
        encoding="utf-8",
    )

    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
        domain_packs_config=DomainPacksConfig(active=["research"]),
    )

    assert loop.tools.has("research_search")
    counts = loop.domain_packs.domain_tool_runtime_counts()
    assert counts["registered"] == 1
    assert counts["skipped"] == 0
    runtime_status = await loop.tools.execute("originagent_runtime_status", {})
    assert runtime_status["registered_domain_tools_count"] == 1
    assert runtime_status["active_domain_pack_ids"] == ["research"]
    assert runtime_status["self_model"]["domains"]["stats"]["active_domain_pack_count"] == 1


@pytest.mark.asyncio
async def test_runtime_status_reports_confirmation_store_available(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )

    result = await loop.tools.execute("originagent_runtime_status", {})

    assert result["confirmation_available"] is True
    assert result["self_model"]["runtime"]["confirmation_available"] is True


@pytest.mark.asyncio
async def test_runtime_status_reports_subagent_summary(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )
    loop.subagents.records.append_task(SubagentTaskRecord(
        subagent_id="sub-1",
        root_subagent_id="sub-1",
        parent_subagent_id=None,
        subagent_depth=1,
        parent_session_key="cli:direct",
        origin_channel="cli",
        origin_chat_id="direct",
        origin_message_id=None,
        task_label="inspect",
        task_summary="inspect repository",
        delegated_profile_summary="read=on, write=off",
        allowed_tools_summary=["read_file", "grep"],
        provider_summary="inherit:test-model",
        isolation_mode="shared_process",
        terminal_status="completed",
        stop_reason="done",
        started_at="2026-05-28T00:00:00+00:00",
        ended_at="2026-05-28T00:00:01+00:00",
    ))

    result = await loop.tools.execute("originagent_runtime_status", {})

    assert result["subagent_task_total"] == 1
    assert result["subagent_recent_task_count"] == 1
    assert result["subagent_terminal_status_counts"]["completed"] == 1
    assert result["subagent_recent_tasks"][0]["subagent_id"] == "sub-1"


@pytest.mark.asyncio
async def test_runtime_status_reports_cron_backed_reminder_summary(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )
    assert loop.cron_service is None
    last_run_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cron = SimpleNamespace(
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
    loop.cron_service = cron
    loop.introspection._cron_service = cron

    result = await loop.tools.execute("originagent_runtime_status", {})

    assert result["reminder_total"] == 1
    assert result["reminder_status_counts"]["fired"] == 1
    assert result["reminder_last_fired_at"] is not None


@pytest.mark.parametrize("mode", ["off", "minimal", "security"])
@pytest.mark.asyncio
async def test_runtime_explain_tools_do_not_require_capability_snapshot(
    tmp_path: Path,
    mode: str,
) -> None:
    cfg = _config(tmp_path)
    cfg.tools.audit = ToolAuditConfig(mode=mode)  # type: ignore[arg-type]
    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())

    for name in RUNTIME_TOOL_NAMES:
        result = await loop.tools.execute(name, {})
        assert not isinstance(result, str) or not result.startswith("Error"), name


@pytest.mark.asyncio
async def test_runtime_status_reflects_direct_constructor_audit_mode(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
        tool_audit_config=ToolAuditConfig(mode="security"),
    )

    result = await loop.tools.execute("originagent_runtime_status", {})

    assert result["audit_mode"] == "security"
    assert result["workspace_name"] == tmp_path.name
    assert result["self_model"]["identity"]["audit_mode"] == "security"


@pytest.mark.asyncio
async def test_runtime_status_reflects_from_config_audit_mode(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.tools.audit = ToolAuditConfig(mode="off")
    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())

    runtime_status = await loop.tools.execute("originagent_runtime_status", {})
    audit_summary = await loop.tools.execute("originagent_tool_audit_summary", {})

    assert runtime_status["audit_mode"] == "off"
    assert audit_summary["audit_mode"] == "off"
    assert audit_summary["enabled"] is False
    assert runtime_status["self_model"]["identity"]["audit_mode"] == "off"


@pytest.mark.asyncio
async def test_runtime_status_uses_from_config_nearline_runtime_config(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.agents.defaults.nearline_memory = NearlineMemoryConfig(
        enabled=True,
        pipeline_enabled=False,
        profile_shadow_write_enabled=True,
    )
    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())

    result = await loop.tools.execute("originagent_runtime_status", {})

    assert loop.nearline_memory.config.enabled is True
    assert loop.nearline_memory.config.pipeline_enabled is False
    assert result["background_tasks"]["tasks"]["nearline_memory"]["nearline_enabled"] is True
    assert result["background_tasks"]["tasks"]["nearline_memory"]["nearline_pipeline_enabled"] is False
    assert result["self_model"]["memory"]["nearline"]["status"] == "idle"
    assert result["self_model"]["memory"]["nearline"]["profile_shadow_write_enabled"] is True


@pytest.mark.asyncio
async def test_evolution_control_tool_is_registered_and_preview_is_read_only(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )

    actions = await loop.tools.execute("originagent_evolution_control", {"operation": "list_actions"})
    preview = await loop.tools.execute(
        "originagent_evolution_control",
        {
            "operation": "preview_action",
            "action_kind": "suppress_signal",
            "target_id": "missing_signal",
            "reason": "preview only",
        },
    )
    outcome_stats = EvolutionOutcomeStore(tmp_path).stats()

    assert loop.tools.has("originagent_evolution_control")
    assert actions["ok"] is True
    assert any(item["action_kind"] == "suppress_signal" for item in actions["actions"])
    assert preview["will_write"] is False
    assert not any(name.startswith("control_action_") for name in outcome_stats["outcome_type_counts"])


@pytest.mark.asyncio
async def test_evolution_control_tool_execute_respects_manual_override(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )

    result = await loop.tools.execute(
        "originagent_evolution_control",
        {
            "operation": "execute_action",
            "action_kind": "suppress_signal",
            "target_id": "missing_signal",
            "reason": "not allowed",
        },
    )

    outcome_stats = EvolutionOutcomeStore(tmp_path).stats()
    assert result["ok"] is False
    assert result["error"] == "manual_override_disabled"
    assert result["will_write"] is False
    assert outcome_stats["outcome_type_counts"][CONTROL_EVENT_DENIED] == 1


@pytest.mark.asyncio
async def test_evolution_control_tool_execute_records_context_actor(tmp_path: Path) -> None:
    signal = OpportunitySignalStore(tmp_path).upsert_candidates([
        OpportunitySignalCandidate(
            kind=SIGNAL_KIND_WORKFLOW,
            target_key="weekly report",
            title="Weekly report workflow",
            summary="Repeated weekly report steps.",
            evidence_sources=[{"cursor": 1, "timestamp": "2026-05-20T10:00:00+00:00"}],
        )
    ])[0]
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
        evolution_config=EvolutionConfig(allow_manual_override=True),
    )
    set_tool_context(
        loop.tools,
        channel="web",
        chat_id="chat-1",
        session_key="session-1",
        actor_id="operator-1",
        trigger="user",
    )

    result = await loop.tools.execute(
        "originagent_evolution_control",
        {
            "operation": "execute_action",
            "action_kind": "suppress_signal",
            "target_id": signal.opportunity_id,
            "reason": "accepted suggestion",
        },
    )

    events = EvolutionOutcomeStore(tmp_path).read_all()
    control_event = next(event for event in events if event["type"] == CONTROL_EVENT_EXECUTED)
    assert result["ok"] is True
    assert control_event["metadata"]["actor"] == "operator-1"
    assert control_event["metadata"]["source"] == "originagent_evolution_control:user"
