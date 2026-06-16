from pathlib import Path
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.domain_packs import DomainPackManager
from OriginAgent.agent.action_runtime import ActionExecutionResult
from OriginAgent.agent.identity import ActorResolver, RuntimeContext
from OriginAgent.agent.loop import AgentLoop
from OriginAgent.agent.tools.audit import InMemoryToolAuditSink, ToolAuditConfig
from OriginAgent.agent.tools.base import Tool, tool_parameters
from OriginAgent.agent.tools.registry import _safe_hash
from OriginAgent.agent.tools.schema import StringSchema, tool_parameters_schema
from OriginAgent.bus.events import InboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.config.schema import Config, DomainPacksConfig, ToolsConfig, DeviceToolsConfig
from OriginAgent.security.capabilities import CapabilitySnapshot


class RecordingResolver(ActorResolver):
    def __init__(self, actor_id: str = "resolved_actor") -> None:
        self.actor_id = actor_id
        self.calls = []

    def resolve_runtime_context(
        self,
        *,
        channel: str,
        chat_id: str,
        sender_id: str,
        metadata: dict,
        session_key: str | None = None,
        routing_channel: str | None = None,
        routing_chat_id: str | None = None,
    ) -> RuntimeContext:
        self.calls.append({
            "channel": channel,
            "chat_id": chat_id,
            "sender_id": sender_id,
            "metadata": metadata,
            "session_key": session_key,
            "routing_channel": routing_channel,
            "routing_chat_id": routing_chat_id,
        })
        base = super().resolve_runtime_context(
            channel=channel,
            chat_id=chat_id,
            sender_id=sender_id,
            metadata=metadata,
            session_key=session_key,
            routing_channel=routing_channel,
            routing_chat_id=routing_chat_id,
        )
        return RuntimeContext(
            actor_id=self.actor_id,
            user_id=base.user_id,
            session_id=base.session_id,
            device_id=base.device_id,
            trigger=base.trigger,
            channel=base.channel,
            chat_id=base.chat_id,
            session_key=base.session_key,
            source=base.source,
            default_scope=base.default_scope,
        )


class FakeExecutor:
    def __init__(self) -> None:
        self.actions = []

    def submit_typed(self, action):
        self.actions.append(action)
        return ActionExecutionResult(
            status="dry_run",
            action_id="action_1",
            reason="ok",
            backend_called=True,
            permission_status="allow",
        )


@tool_parameters(
    tool_parameters_schema(
        value=StringSchema("Value"),
        required=["value"],
        additional_properties=False,
    )
)
class FakeAuditedTool(Tool):
    name = "fake_audited"

    @property
    def description(self) -> str:
        return "Fake audited tool."

    async def execute(self, value: str):
        return {"value": value}


def _provider():
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return provider


def _loop(tmp_path: Path, *, resolver: ActorResolver | None = None, executor=None) -> AgentLoop:
    kwargs = {}
    if executor is not None:
        kwargs["domain_pack_manager"] = DomainPackManager(
            tmp_path,
            config=DomainPacksConfig(active=["smart_home"]),
        )
        kwargs["tools_config"] = ToolsConfig(
            device=DeviceToolsConfig(enabled=True, lighting_enabled=True, mode="dry_run", backend="fake")
        )
    return AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
        actor_resolver=resolver,
        device_action_executor=executor,
        **kwargs,
    )


def test_loop_resolves_runtime_context_from_inbound_message_once(tmp_path: Path):
    resolver = RecordingResolver()
    loop = _loop(tmp_path, resolver=resolver)
    msg = InboundMessage(
        channel="chat",
        chat_id="room_1",
        sender_id="raw_sender",
        content="hi",
        metadata={"actor_id": "admin_user", "message_id": "m1"},
    )

    context = loop._resolve_runtime_context(msg, session_key="chat:room_1")

    assert context.actor_id == "resolved_actor"
    assert context.trigger == "user_initiated"
    assert resolver.calls == [{
        "channel": "chat",
        "chat_id": "room_1",
        "sender_id": "raw_sender",
        "metadata": {"actor_id": "admin_user", "message_id": "m1"},
        "session_key": "chat:room_1",
        "routing_channel": None,
        "routing_chat_id": None,
    }]


@pytest.mark.asyncio
async def test_loop_runtime_context_feeds_device_actor_and_trigger(tmp_path: Path):
    resolver = RecordingResolver()
    executor = FakeExecutor()
    loop = _loop(tmp_path, resolver=resolver, executor=executor)
    msg = InboundMessage(
        channel="chat",
        chat_id="room_1",
        sender_id="raw_sender",
        content="hi",
        metadata={"actor_id": "admin_user"},
    )
    context = loop._resolve_runtime_context(msg, session_key="chat:room_1")

    loop._set_tool_context(
        msg.channel,
        msg.chat_id,
        msg.metadata.get("message_id"),
        msg.metadata,
        capability_snapshot=CapabilitySnapshot.user_turn(),
        runtime_context=context,
    )
    tool = loop.tools.get("originagent_device_lighting_set_power")
    await tool.execute(device_id="lamp", power="on")

    assert executor.actions[0].requested_by == "resolved_actor"
    assert executor.actions[0].trigger == "user_initiated"


@pytest.mark.asyncio
async def test_loop_audit_context_uses_resolved_actor_not_spoofed_metadata(tmp_path: Path):
    resolver = RecordingResolver(actor_id="user_123")
    loop = _loop(tmp_path, resolver=resolver)
    sink = InMemoryToolAuditSink()
    loop.tools._audit_sink = sink
    loop.tools._audit_config = ToolAuditConfig(mode="security")
    msg = InboundMessage(
        channel="chat",
        chat_id="room_1",
        sender_id="user_123",
        content="hi",
        metadata={"actor_id": "admin_user"},
    )
    context = loop._resolve_runtime_context(msg, session_key="chat:room_1")
    loop.tools.register(FakeAuditedTool())

    loop._set_tool_context(
        msg.channel,
        msg.chat_id,
        metadata=msg.metadata,
        capability_snapshot=CapabilitySnapshot.user_turn(),
        runtime_context=context,
    )
    await loop.tools.execute("fake_audited", {"value": "ok"})

    assert sink.events
    event = sink.events[0]
    assert event.actor_id_hash
    assert event.actor_id_hash != _safe_hash("admin_user")
    assert event.actor_id_hash == _safe_hash("user_123")


def test_loop_routing_override_does_not_spoof_actor(tmp_path: Path):
    loop = _loop(tmp_path)
    msg = InboundMessage(
        channel="system",
        chat_id="cli:home",
        sender_id="subagent",
        content="done",
        metadata={"actor_id": "admin_user"},
    )

    context = loop._resolve_runtime_context(
        msg,
        channel="cli",
        chat_id="home",
        session_key="cli:home",
    )

    assert context.channel == "cli"
    assert context.chat_id == "home"
    assert context.session_key == "cli:home"
    assert context.actor_id == "subagent"
    assert context.trigger == "subagent"
    assert context.source == "subagent"


def test_loop_routing_override_preserves_original_system_trigger(tmp_path: Path):
    loop = _loop(tmp_path)
    msg = InboundMessage(
        channel="system",
        chat_id="cli:home",
        sender_id="system_runner",
        content="tick",
        metadata={"actor_id": "admin_user"},
    )

    context = loop._resolve_runtime_context(
        msg,
        channel="cli",
        chat_id="home",
        session_key="cli:home",
    )

    assert context.channel == "cli"
    assert context.chat_id == "home"
    assert context.actor_id == "system_runner"
    assert context.trigger == "system"
    assert context.source == "system"


def test_loop_from_config_initializes_transcription_provider_when_enabled(tmp_path: Path):
    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    config.providers.groq.api_key = "groq-key"
    config.tools.local_awareness.enabled = True
    config.tools.local_awareness.audio.transcription_enabled = True

    loop = AgentLoop.from_config(config, provider=_provider())

    assert loop._transcription_provider is not None
    assert loop._transcription_provider.__class__.__name__ == "GroqTranscriptionProvider"


def test_loop_runtime_context_triggers_do_not_degrade_to_user_initiated(tmp_path: Path):
    loop = _loop(tmp_path)

    cron = loop._resolve_runtime_context(
        InboundMessage(channel="cron", chat_id="home", sender_id="cron", content="tick")
    )
    system = loop._resolve_runtime_context(
        InboundMessage(channel="system", chat_id="cli:home", sender_id="system", content="tick")
    )
    subagent = loop._resolve_runtime_context(
        InboundMessage(channel="system", chat_id="cli:home", sender_id="subagent", content="done")
    )

    assert cron.trigger == "scheduled"
    assert system.trigger == "system"
    assert subagent.trigger == "subagent"
