from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
import json

import pytest

from OriginAgent.config.schema import ContextConfig
from OriginAgent.agent.context import ContextBuilder
from OriginAgent.agent.identity import ActorResolver
from OriginAgent.agent.loop import AgentLoop
from OriginAgent.agent.reminders import ReminderRecord, ReminderStore
from OriginAgent.agent.scope import ScopeResolver
from OriginAgent.agent.working_memory import WORKING_MEMORY_METADATA_KEY, WorkingMemoryManager
from OriginAgent.agent.world_state import WorldStateManager
from OriginAgent.bus.events import InboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.providers.base import LLMResponse
from OriginAgent.session.manager import SessionManager


def _provider():
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    return provider


def test_runtime_context_has_phase1_identity_fields():
    context = ActorResolver().resolve_runtime_context(
        channel="chat",
        chat_id="room_1",
        sender_id="user_123",
        metadata={"device_id": "device-a"},
        session_key="chat:room_1",
    )

    assert context.actor_id == "user_123"
    assert context.user_id == "user_123"
    assert context.session_id == "chat:room_1"
    assert context.device_id == "device-a"
    assert context.default_scope == "session"


def test_runtime_context_subagent_defaults_to_task_scope():
    context = ActorResolver().resolve_runtime_context(
        channel="system",
        chat_id="cli:home",
        sender_id="subagent",
        metadata={},
        session_key="cli:home",
    )

    assert context.default_scope == "task"


def test_working_memory_hydrates_goal_and_persists(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    session.metadata["goal_state"] = {
        "status": "active",
        "objective": "Finish phase 1 implementation",
        "ui_summary": "P1",
    }
    manager = WorkingMemoryManager(sessions)
    snapshot = manager.load(session)

    assert snapshot.current_goal == "Finish phase 1 implementation"
    assert "goal_summary: P1" in snapshot.priority_facts

    manager.upsert(session, current_plan=["Step 1", "Step 2"])
    assert WORKING_MEMORY_METADATA_KEY in session.metadata
    assert session.metadata[WORKING_MEMORY_METADATA_KEY]["current_plan"] == ["Step 1", "Step 2"]


def test_scope_resolver_enforces_basic_visibility_rules():
    resolver = ScopeResolver()

    assert resolver.is_visible(scope="session", current_scope="task") is True
    assert resolver.is_visible(
        scope="device",
        current_scope="device",
        device_id="device-a",
        current_device_id="device-a",
    ) is True
    assert resolver.is_visible(
        scope="device",
        current_scope="device",
        device_id="device-a",
        current_device_id="device-b",
    ) is False
    assert resolver.is_visible(scope="task", current_scope="session") is False
    assert resolver.is_visible(
        scope="user",
        current_scope="session",
        owner_id="user-1",
        current_owner_id="user-1",
    ) is True
    assert resolver.is_visible(
        scope="user",
        current_scope="session",
        owner_id="user-1",
        current_owner_id="user-2",
    ) is False


def test_working_memory_hydrates_due_reminders(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    reminders = ReminderStore(tmp_path)
    reminders.upsert(
        ReminderRecord.create(
            session_key="cli:test",
            channel="cli",
            chat_id="test",
            content="Follow up on the current plan",
            due_at="2026-06-01T00:00:00+00:00",
        )
    )
    manager = WorkingMemoryManager(sessions, reminder_store=reminders)

    snapshot = manager.load(session)

    assert "Follow up on the current plan" in snapshot.attention_items


def test_context_builder_injects_continuity_and_working_memory_blocks(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionManager(workspace)
    session = sessions.get_or_create("cli:direct")
    session.metadata["goal_state"] = {
        "status": "active",
        "objective": "Keep current task state",
        "ui_summary": "task",
    }
    builder = ContextBuilder(workspace=workspace, timezone="UTC", sessions=sessions)
    runtime_context = ActorResolver().resolve_runtime_context(
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        metadata={},
        session_key="cli:direct",
    )

    messages = builder.build_messages(
        history=[],
        current_message="Continue the work",
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        session_metadata=session.metadata,
        runtime_context=runtime_context,
        session_key="cli:direct",
    )

    user_content = messages[-1]["content"]
    kinds = [block.get("_meta", {}).get("kind") for block in user_content if isinstance(block, dict)]
    text = "\n".join(block.get("text", "") for block in user_content if isinstance(block, dict))

    assert ContextBuilder.RUNTIME_CONTEXT_KIND in kinds
    assert ContextBuilder.CONTINUITY_CONTEXT_KIND in kinds
    assert ContextBuilder.WORKING_MEMORY_CONTEXT_KIND in kinds
    assert ContextBuilder.WORLD_STATE_CONTEXT_KIND in kinds
    assert '"user_id": "user-1"' in text
    assert "Keep current task state" in text


def test_context_builder_uses_real_world_state_snapshot(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionManager(workspace)
    session = sessions.get_or_create("cli:direct")
    builder = ContextBuilder(workspace=workspace, timezone="UTC", sessions=sessions)
    builder.world_state = WorldStateManager(workspace, sessions)
    runtime_context = ActorResolver().resolve_runtime_context(
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        metadata={"device_id": "device-a"},
        session_key="cli:direct",
    )
    image = workspace / "desk.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (workspace / "desk.png.json").write_text(
        '{"summary":"Desk shows a notebook and a lamp.","objects":["notebook","lamp"],"confidence":0.84}',
        encoding="utf-8",
    )
    builder.world_state.ingest_media(
        session,
        runtime_context=runtime_context,
        media_paths=[str(image)],
    )

    messages = builder.build_messages(
        history=[],
        current_message="Continue the work",
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        session_metadata=session.metadata,
        runtime_context=runtime_context,
        session_key="cli:direct",
    )

    user_content = messages[-1]["content"]
    world_block = next(
        block
        for block in user_content
        if isinstance(block, dict)
        and block.get("_meta", {}).get("kind") == ContextBuilder.WORLD_STATE_CONTEXT_KIND
    )

    assert '"status": "active"' in world_block["text"]
    assert '"version": "phase2"' in world_block["text"]
    assert "Desk shows a notebook and a lamp." in world_block["text"]
    assert '"snapshots"' not in world_block["text"]
    assert '"media_path"' not in world_block["text"]


def test_context_builder_can_disable_phase1_continuity_blocks(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionManager(workspace)
    session = sessions.get_or_create("cli:direct")
    builder = ContextBuilder(
        workspace=workspace,
        timezone="UTC",
        sessions=sessions,
        context_config=ContextConfig(enable_phase1_continuity=False),
    )
    runtime_context = ActorResolver().resolve_runtime_context(
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        metadata={},
        session_key="cli:direct",
    )

    messages = builder.build_messages(
        history=[],
        current_message="Continue the work",
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        session_metadata=session.metadata,
        runtime_context=runtime_context,
        session_key="cli:direct",
    )

    user_content = messages[-1]["content"]
    kinds = [block.get("_meta", {}).get("kind") for block in user_content if isinstance(block, dict)]

    assert ContextBuilder.RUNTIME_CONTEXT_KIND in kinds
    assert ContextBuilder.CONTINUITY_CONTEXT_KIND not in kinds
    assert ContextBuilder.WORKING_MEMORY_CONTEXT_KIND not in kinds
    assert ContextBuilder.WORLD_STATE_CONTEXT_KIND not in kinds


def test_loop_introspection_exposes_continuity_summary(tmp_path: Path):
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )
    msg = InboundMessage(
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        content="How should we proceed next?",
        metadata={},
    )
    session = loop.sessions.get_or_create("cli:direct")
    runtime_context = loop._resolve_runtime_context(msg, session_key="cli:direct")
    loop._last_runtime_context = runtime_context
    loop._last_continuity_session_key = "cli:direct"
    loop._update_working_memory_from_turn(
        session,
        runtime_context=runtime_context,
        current_message=msg.content,
    )

    continuity = loop.introspection.continuity_summary()

    assert continuity["enabled"] is True
    assert continuity["current_session_key"] == "cli:direct"
    assert continuity["runtime_context"]["user_id"] == "user-1"
    assert continuity["working_memory"]["pending_questions"] == ["How should we proceed next?"]
    assert continuity["world_state"]["status"] == "placeholder"
    assert continuity["last_context_assembly"] == {}


def test_loop_introspection_exposes_world_state_after_media_ingest(tmp_path: Path):
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )
    image = tmp_path / "scene.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (tmp_path / "scene.png.json").write_text(
        '{"summary":"Kitchen counter has a kettle.","objects":["kettle"],"confidence":0.76}',
        encoding="utf-8",
    )
    msg = InboundMessage(
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        content="What do you see here?",
        media=[str(image)],
        metadata={"device_id": "device-a"},
    )
    session = loop.sessions.get_or_create("cli:direct")
    runtime_context = loop._resolve_runtime_context(msg, session_key="cli:direct")
    loop._last_runtime_context = runtime_context
    loop._last_continuity_session_key = "cli:direct"
    loop._update_working_memory_from_turn(
        session,
        runtime_context=runtime_context,
        current_message=msg.content,
        media_paths=msg.media,
    )

    continuity = loop.introspection.continuity_summary()

    assert continuity["world_state"]["status"] == "active"
    assert continuity["world_state"]["version"] == "phase2"
    assert continuity["world_state"]["world_summary"]["focus"] == ["Kitchen counter has a kettle."]
    assert "world_attention: Kitchen counter has a kettle." in continuity["working_memory"]["attention_items"]


def test_context_builder_retrieval_fusion_injects_multi_source_blocks_and_audit(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionManager(workspace)
    session = sessions.get_or_create("cli:direct")
    builder = ContextBuilder(
        workspace=workspace,
        timezone="UTC",
        sessions=sessions,
        memory_feature_flags={"semantic_retrieval_enabled": True},
    )
    builder.memory.upsert_fact_and_rebuild_memory(
        "User prefers dark mode",
        category="preference",
        scope="user.interface.theme",
        owner="user",
        source_cursors=[1],
        source_excerpt="please use dark mode",
    )
    nearline = workspace / "memory" / "nearline"
    nearline.mkdir(parents=True, exist_ok=True)
    sessions_dir = workspace / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "cli_direct.jsonl").write_text(
        "\n".join([
            json.dumps({"_type": "metadata", "key": "cli:direct"}, ensure_ascii=False),
            json.dumps(
                {
                    "role": "user",
                    "content": "Need a deployment checklist for Friday release.",
                    "timestamp": "2026-06-05T10:00:00+08:00",
                },
                ensure_ascii=False,
            ),
        ]) + "\n",
        encoding="utf-8",
    )
    (nearline / "episodes.jsonl").write_text(
        (
            '{"episode_id":"ep_1","memcell_id":"mem_1","session_key":"cli:direct",'
            '"owner_id":"user-1","summary":"Prepare deployment checklist.",'
            '"content":"Prepare deployment checklist before release.",'
            '"timestamp":"2026-06-05T10:00:00+08:00","source_message_ids":["msg_1"],"metadata":{}}'
        )
        + "\n",
        encoding="utf-8",
    )
    builder.memory.append_history("Need a deployment checklist for Friday release.")
    runtime_context = ActorResolver().resolve_runtime_context(
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        metadata={},
        session_key="cli:direct",
    )

    messages = builder.build_messages(
        history=[],
        current_message="Need a deployment checklist for Friday release.",
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        session_metadata=session.metadata,
        runtime_context=runtime_context,
        session_key="cli:direct",
    )

    user_content = messages[-1]["content"]
    sources = [
        block.get("_meta", {}).get("source")
        for block in user_content
        if isinstance(block, dict) and block.get("_meta", {}).get("kind") == ContextBuilder.REFERENCE_CONTEXT_KIND
    ]

    assert "memory_retrieval" in sources
    assert "layered_memory" in sources
    assert "retrieval_session_search" in sources
    assert "recent_history" in sources
    assert builder._last_retrieval_fusion["source_counts"]["fact_store"] >= 1
    assert builder._last_retrieval_fusion["source_counts"]["nearline_retrieval"] >= 1
    assert builder._last_retrieval_fusion["source_counts"]["session_search"] >= 1


def test_context_builder_retrieval_fusion_respects_user_scope_prefix(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionManager(workspace)
    builder = ContextBuilder(
        workspace=workspace,
        timezone="UTC",
        sessions=sessions,
        memory_feature_flags={"semantic_retrieval_enabled": True},
    )
    builder.memory.upsert_fact_and_rebuild_memory(
        "User one prefers dark mode",
        category="preference",
        scope="user.preference.theme",
        owner="user",
        source_cursors=[1],
        source_excerpt="user one preference",
    )
    builder.memory.upsert_fact_and_rebuild_memory(
        "Workspace prefers concise replies",
        category="preference",
        scope="workspace.preference.style",
        owner="user",
        source_cursors=[2],
        source_excerpt="user two preference",
    )
    runtime_context = ActorResolver().resolve_runtime_context(
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        metadata={"scope": "user"},
        session_key="cli:direct",
    )

    blocks = builder.build_reference_context_blocks(
        session_key="cli:direct",
        runtime_context=runtime_context,
        current_message="What do I prefer?",
    )
    text = "\n".join(block.get("text", "") for block in blocks if isinstance(block, dict))

    assert "User one prefers dark mode" in text
    assert "Workspace prefers concise replies" not in text


def test_context_builder_retrieval_fusion_records_trimming(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionManager(workspace)
    builder = ContextBuilder(
        workspace=workspace,
        timezone="UTC",
        sessions=sessions,
        context_config=ContextConfig(
            max_retrieval_blocks=1,
            max_retrieval_chars=400,
            max_retrieval_hits_per_source=1,
        ),
        memory_feature_flags={"semantic_retrieval_enabled": True},
    )
    builder.memory.upsert_fact_and_rebuild_memory(
        "User prefers dark mode",
        category="preference",
        scope="user.interface.theme",
        owner="user",
        source_cursors=[1],
        source_excerpt="please use dark mode",
    )
    nearline = workspace / "memory" / "nearline"
    nearline.mkdir(parents=True, exist_ok=True)
    (nearline / "episodes.jsonl").write_text(
        (
            '{"episode_id":"ep_1","memcell_id":"mem_1","session_key":"cli:direct",'
            '"owner_id":"user-1","summary":"Prepare deployment checklist.",'
            '"content":"Prepare deployment checklist before release.",'
            '"timestamp":"2026-06-05T10:00:00+08:00","source_message_ids":["msg_1"],"metadata":{}}'
        )
        + "\n",
        encoding="utf-8",
    )
    builder.memory.append_history("Need a deployment checklist for Friday release.")
    runtime_context = ActorResolver().resolve_runtime_context(
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        metadata={},
        session_key="cli:direct",
    )

    blocks = builder.build_reference_context_blocks(
        session_key="cli:direct",
        runtime_context=runtime_context,
        current_message="Need a deployment checklist for Friday release.",
    )

    retrieval_blocks = [
        block for block in blocks
        if isinstance(block, dict) and block.get("_meta", {}).get("source") in {
            "memory_retrieval",
            "layered_memory",
            "retrieval_session_search",
        }
    ]

    assert len(retrieval_blocks) == 1
    assert builder._last_retrieval_fusion["trimmed_count"] >= 1


def test_world_state_filters_device_scoped_snapshot_with_mismatched_device(tmp_path: Path):
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )
    image = tmp_path / "entry.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (tmp_path / "entry.png.json").write_text(
        (
            '{"summary":"Entry camera sees a package.","scope":"device",'
            '"device_id":"device-a","objects":["package"],"confidence":0.81}'
        ),
        encoding="utf-8",
    )
    session = loop.sessions.get_or_create("cli:direct")
    ingest_context = ActorResolver().resolve_runtime_context(
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        metadata={"device_id": "device-a"},
        session_key="cli:direct",
    )
    loop.world_state.ingest_media(
        session,
        runtime_context=ingest_context,
        media_paths=[str(image)],
    )
    read_context = ActorResolver().resolve_runtime_context(
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        metadata={"device_id": "device-b", "scope": "device"},
        session_key="cli:direct",
    )

    filtered = loop.world_state.filtered_candidates(
        session,
        runtime_context=read_context,
        current_message="What is at the door now?",
    )

    assert filtered["included_summary"] == {}
    assert filtered["filtered_candidates"][0]["reasons"] == ["scope_hidden"]


@pytest.mark.asyncio
async def test_loop_records_last_context_assembly_after_real_turn(tmp_path: Path):
    async def chat_with_retry(**kwargs):
        return LLMResponse(content="Done.", usage={})

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )

    result = await loop._process_message(
        InboundMessage(
            channel="cli",
            chat_id="direct",
            sender_id="user-1",
            content="How should we proceed next?",
            metadata={},
        )
    )

    assert result is not None
    continuity = loop.introspection.continuity_summary()
    assert continuity["last_context_assembly"]["enabled"] is True
    assert "runtime_context" in continuity["last_context_assembly"]["block_kinds"]
