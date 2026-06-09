from OriginAgent.agent.identity import ActorResolver


def test_runtime_context_uses_sender_as_actor():
    context = ActorResolver().resolve_runtime_context(
        channel="chat",
        chat_id="room_1",
        sender_id="user_123",
        metadata={},
    )

    assert context.actor_id == "user_123"
    assert context.trigger == "user_initiated"
    assert context.source == "user_turn"


def test_runtime_context_missing_sender_becomes_unknown():
    context = ActorResolver().resolve_runtime_context(
        channel="chat",
        chat_id="room_1",
        sender_id="",
        metadata={},
    )

    assert context.actor_id == "unknown"


def test_runtime_context_ignores_metadata_actor_id():
    context = ActorResolver().resolve_runtime_context(
        channel="chat",
        chat_id="room_1",
        sender_id="user_123",
        metadata={"actor_id": "admin_user"},
    )

    assert context.actor_id == "user_123"


def test_runtime_context_metadata_actor_id_does_not_create_actor():
    context = ActorResolver().resolve_runtime_context(
        channel="chat",
        chat_id="room_1",
        sender_id="",
        metadata={"actor_id": "admin_user"},
    )

    assert context.actor_id == "unknown"


def test_resolve_actor_only_wraps_runtime_context():
    resolver = ActorResolver()

    assert resolver.resolve(
        channel="chat",
        chat_id="room_1",
        sender_id="user_123",
        metadata={"actor_id": "admin_user"},
    ) == "user_123"


def test_runtime_context_trigger_and_source_for_cron_system_subagent_and_user():
    resolver = ActorResolver()

    cron = resolver.resolve_runtime_context(
        channel="cron",
        chat_id="home",
        sender_id="cron_runner",
        metadata={},
    )
    system = resolver.resolve_runtime_context(
        channel="system",
        chat_id="cli:home",
        sender_id="system_runner",
        metadata={},
    )
    subagent_sender = resolver.resolve_runtime_context(
        channel="system",
        chat_id="cli:home",
        sender_id="subagent",
        metadata={},
    )
    subagent_event = resolver.resolve_runtime_context(
        channel="cli",
        chat_id="home",
        sender_id="worker",
        metadata={"injected_event": "subagent_result"},
    )
    user = resolver.resolve_runtime_context(
        channel="cli",
        chat_id="home",
        sender_id="user",
        metadata={},
    )

    assert (cron.trigger, cron.source) == ("scheduled", "cron")
    assert (system.trigger, system.source) == ("system", "system")
    assert (subagent_sender.trigger, subagent_sender.source) == ("subagent", "subagent")
    assert (subagent_event.trigger, subagent_event.source) == ("subagent", "subagent")
    assert (user.trigger, user.source) == ("user_initiated", "user_turn")


def test_snapshot_for_automation_returns_automation_capability() -> None:
    from OriginAgent.agent.agent_runtime_context import snapshot_for_trigger

    snapshot = snapshot_for_trigger("automation")

    assert snapshot.trigger == "automation"
    assert snapshot.source == "automation"
    assert snapshot.can_exec is False
    assert snapshot.allowed_device_domains == ("lighting",)
