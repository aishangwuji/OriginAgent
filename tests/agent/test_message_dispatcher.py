from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.message_dispatcher import MessageDispatcher, MessageDispatcherDeps
from OriginAgent.bus.events import InboundMessage, OutboundMessage


def _message(*, content: str = "hello", channel: str = "cli", metadata: dict | None = None) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id="u",
        chat_id="c",
        content=content,
        metadata=metadata or {},
    )


def _transient_cancel() -> asyncio.CancelledError:
    return asyncio.CancelledError()


@pytest.mark.asyncio
async def test_message_dispatcher_run_uses_loop_dispatch() -> None:
    msg = _message()
    state = {"count": 0}

    async def consume_inbound():
        state["count"] += 1
        if state["count"] == 1:
            loop._running = False
            return msg
        raise AssertionError("run_forever should have stopped after _running=False")

    loop = SimpleNamespace(
        _running=True,
        bus=SimpleNamespace(
            consume_inbound=AsyncMock(side_effect=consume_inbound),
            publish_outbound=AsyncMock(),
            publish_inbound=AsyncMock(),
        ),
        auto_compact=SimpleNamespace(check_expired=MagicMock()),
        _schedule_background=MagicMock(),
        _pending_queues={},
        commands=SimpleNamespace(
            is_priority=MagicMock(return_value=False),
            is_dispatchable_command=MagicMock(return_value=False),
        ),
        _effective_session_key=MagicMock(return_value="cli:c"),
        _dispatch=AsyncMock(),
        _active_tasks={},
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    await dispatcher.run_forever()
    await asyncio.sleep(0)

    loop._dispatch.assert_awaited_once_with(msg)


@pytest.mark.asyncio
async def test_message_dispatcher_run_timeout_triggers_auto_compact() -> None:
    calls = {"count": 0}

    async def consume_inbound():
        calls["count"] += 1
        if calls["count"] == 1:
            raise asyncio.TimeoutError()
        loop._running = False
        return _message()

    loop = SimpleNamespace(
        _running=True,
        bus=SimpleNamespace(
            consume_inbound=AsyncMock(side_effect=consume_inbound),
            publish_outbound=AsyncMock(),
            publish_inbound=AsyncMock(),
        ),
        auto_compact=SimpleNamespace(check_expired=MagicMock()),
        _schedule_background=MagicMock(),
        _pending_queues={},
        commands=SimpleNamespace(
            is_priority=MagicMock(return_value=False),
            is_dispatchable_command=MagicMock(return_value=False),
        ),
        _effective_session_key=MagicMock(return_value="cli:c"),
        _dispatch=AsyncMock(),
        _active_tasks={},
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    await dispatcher.run_forever()
    await asyncio.sleep(0)

    loop.auto_compact.check_expired.assert_called_once_with(
        loop._schedule_background,
        active_session_keys=loop._pending_queues.keys(),
    )


@pytest.mark.asyncio
async def test_message_dispatcher_run_priority_command_uses_dispatch_priority() -> None:
    msg = _message(content="/restart")

    async def consume_inbound():
        loop._running = False
        return msg

    loop = SimpleNamespace(
        _running=True,
        bus=SimpleNamespace(
            consume_inbound=AsyncMock(side_effect=consume_inbound),
            publish_outbound=AsyncMock(),
            publish_inbound=AsyncMock(),
        ),
        auto_compact=SimpleNamespace(check_expired=MagicMock()),
        _schedule_background=MagicMock(),
        _pending_queues={},
        commands=SimpleNamespace(
            is_priority=MagicMock(return_value=True),
            is_dispatchable_command=MagicMock(return_value=False),
            dispatch_priority=AsyncMock(),
            dispatch=AsyncMock(),
        ),
        _effective_session_key=MagicMock(return_value="cli:c"),
        _dispatch=AsyncMock(),
        _dispatch_command_inline=AsyncMock(),
        _active_tasks={},
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    await dispatcher.run_forever()

    loop._dispatch_command_inline.assert_awaited_once_with(
        msg,
        "cli:c",
        "/restart",
        loop.commands.dispatch_priority,
    )
    loop._dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_dispatcher_run_dispatchable_command_uses_inline_dispatch() -> None:
    msg = _message(content="/status")
    pending = asyncio.Queue(maxsize=MessageDispatcher.PENDING_QUEUE_MAXSIZE)

    async def consume_inbound():
        loop._running = False
        return msg

    loop = SimpleNamespace(
        _running=True,
        bus=SimpleNamespace(
            consume_inbound=AsyncMock(side_effect=consume_inbound),
            publish_outbound=AsyncMock(),
            publish_inbound=AsyncMock(),
        ),
        auto_compact=SimpleNamespace(check_expired=MagicMock()),
        _schedule_background=MagicMock(),
        _pending_queues={"cli:c": pending},
        commands=SimpleNamespace(
            is_priority=MagicMock(return_value=False),
            is_dispatchable_command=MagicMock(return_value=True),
            dispatch_priority=AsyncMock(),
            dispatch=AsyncMock(),
        ),
        _effective_session_key=MagicMock(return_value="cli:c"),
        _dispatch=AsyncMock(),
        _dispatch_command_inline=AsyncMock(),
        _active_tasks={},
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    await dispatcher.run_forever()

    loop._dispatch_command_inline.assert_awaited_once_with(
        msg,
        "cli:c",
        "/status",
        loop.commands.dispatch,
    )
    loop._dispatch.assert_not_awaited()
    assert pending.empty()


@pytest.mark.asyncio
async def test_message_dispatcher_run_followup_is_routed_to_pending_queue() -> None:
    msg = _message(content="follow-up")
    pending = asyncio.Queue(maxsize=MessageDispatcher.PENDING_QUEUE_MAXSIZE)

    async def consume_inbound():
        loop._running = False
        return msg

    loop = SimpleNamespace(
        _running=True,
        bus=SimpleNamespace(
            consume_inbound=AsyncMock(side_effect=consume_inbound),
            publish_outbound=AsyncMock(),
            publish_inbound=AsyncMock(),
        ),
        auto_compact=SimpleNamespace(check_expired=MagicMock()),
        _schedule_background=MagicMock(),
        _pending_queues={"cli:c": pending},
        commands=SimpleNamespace(
            is_priority=MagicMock(return_value=False),
            is_dispatchable_command=MagicMock(return_value=False),
            dispatch_priority=AsyncMock(),
            dispatch=AsyncMock(),
        ),
        _effective_session_key=MagicMock(return_value="cli:c"),
        _dispatch=AsyncMock(),
        _dispatch_command_inline=AsyncMock(),
        _active_tasks={},
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    await dispatcher.run_forever()

    assert pending.qsize() == 1
    queued = pending.get_nowait()
    assert queued.content == "follow-up"
    loop._dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_dispatcher_run_queuefull_falls_back_to_new_dispatch_task() -> None:
    msg = _message(content="follow-up")
    pending = asyncio.Queue(maxsize=1)
    pending.put_nowait(_message(content="already queued"))

    async def consume_inbound():
        loop._running = False
        return msg

    loop = SimpleNamespace(
        _running=True,
        bus=SimpleNamespace(
            consume_inbound=AsyncMock(side_effect=consume_inbound),
            publish_outbound=AsyncMock(),
            publish_inbound=AsyncMock(),
        ),
        auto_compact=SimpleNamespace(check_expired=MagicMock()),
        _schedule_background=MagicMock(),
        _pending_queues={"cli:c": pending},
        commands=SimpleNamespace(
            is_priority=MagicMock(return_value=False),
            is_dispatchable_command=MagicMock(return_value=False),
            dispatch_priority=AsyncMock(),
            dispatch=AsyncMock(),
        ),
        _effective_session_key=MagicMock(return_value="cli:c"),
        _dispatch=AsyncMock(),
        _dispatch_command_inline=AsyncMock(),
        _active_tasks={},
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    await dispatcher.run_forever()
    await asyncio.sleep(0)

    loop._dispatch.assert_awaited_once_with(msg)
    assert pending.qsize() == 1


@pytest.mark.asyncio
async def test_message_dispatcher_run_raises_after_repeated_transient_cancels() -> None:
    loop = SimpleNamespace(
        _running=True,
        bus=SimpleNamespace(
            consume_inbound=AsyncMock(side_effect=[_transient_cancel() for _ in range(MessageDispatcher._MAX_TRANSIENT_CANCELS)]),
            publish_outbound=AsyncMock(),
            publish_inbound=AsyncMock(),
        ),
        auto_compact=SimpleNamespace(check_expired=MagicMock()),
        _schedule_background=MagicMock(),
        _pending_queues={},
        commands=SimpleNamespace(
            is_priority=MagicMock(return_value=False),
            is_dispatchable_command=MagicMock(return_value=False),
        ),
        _effective_session_key=MagicMock(return_value="cli:c"),
        _dispatch=AsyncMock(),
        _active_tasks={},
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.run_forever()

    assert loop.bus.consume_inbound.await_count == MessageDispatcher._MAX_TRANSIENT_CANCELS


@pytest.mark.asyncio
async def test_message_dispatcher_cleans_pending_queue() -> None:
    queue = asyncio.Queue()
    queue.put_nowait(_message(content="leftover"))
    loop = SimpleNamespace(
        _effective_session_key=MagicMock(return_value="cli:c"),
        _session_locks={},
        _concurrency_gate=None,
        _pending_queues={"cli:c": queue},
        _process_message=AsyncMock(return_value=None),
        _dispatch_command_inline=AsyncMock(),
        _schedule_background=MagicMock(),
        _restore_runtime_checkpoint=MagicMock(return_value=False),
        _clear_pending_user_turn=MagicMock(),
        sessions=SimpleNamespace(
            get_or_create=MagicMock(return_value=SimpleNamespace(metadata={}, messages=[])),
            save=MagicMock(),
        ),
        bus=SimpleNamespace(publish_outbound=AsyncMock(), publish_inbound=AsyncMock()),
        provider=MagicMock(),
        model="test-model",
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    await dispatcher.dispatch_message(_message())

    assert "cli:c" not in loop._pending_queues


@pytest.mark.asyncio
async def test_message_dispatcher_republishes_leftover_messages() -> None:
    leftover = _message(content="leftover")

    async def process_message(_msg, **kwargs):
        pending_queue = kwargs["pending_queue"]
        pending_queue.put_nowait(leftover)
        return None

    loop = SimpleNamespace(
        _effective_session_key=MagicMock(return_value="cli:c"),
        _session_locks={},
        _concurrency_gate=None,
        _pending_queues={},
        _process_message=AsyncMock(side_effect=process_message),
        _dispatch_command_inline=AsyncMock(),
        _schedule_background=MagicMock(),
        _restore_runtime_checkpoint=MagicMock(return_value=False),
        _clear_pending_user_turn=MagicMock(),
        sessions=SimpleNamespace(
            get_or_create=MagicMock(return_value=SimpleNamespace(metadata={}, messages=[])),
            save=MagicMock(),
        ),
        bus=SimpleNamespace(publish_outbound=AsyncMock(), publish_inbound=AsyncMock()),
        provider=MagicMock(),
        model="test-model",
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    await dispatcher.dispatch_message(_message())

    loop.bus.publish_inbound.assert_awaited_once_with(leftover)
    assert "cli:c" not in loop._pending_queues


@pytest.mark.asyncio
async def test_message_dispatcher_cancelled_dispatch_restores_checkpoint() -> None:
    session = SimpleNamespace(metadata={}, messages=[])
    release = asyncio.Event()

    async def process_message(_msg, **kwargs):
        await release.wait()
        return None

    loop = SimpleNamespace(
        _effective_session_key=MagicMock(return_value="cli:c"),
        _session_locks={},
        _concurrency_gate=None,
        _pending_queues={},
        _process_message=AsyncMock(side_effect=process_message),
        _dispatch_command_inline=AsyncMock(),
        _schedule_background=MagicMock(),
        _restore_runtime_checkpoint=MagicMock(return_value=True),
        _clear_pending_user_turn=MagicMock(),
        sessions=SimpleNamespace(
            get_or_create=MagicMock(return_value=session),
            save=MagicMock(),
        ),
        bus=SimpleNamespace(publish_outbound=AsyncMock(), publish_inbound=AsyncMock()),
        provider=MagicMock(),
        model="test-model",
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    task = asyncio.create_task(dispatcher.dispatch_message(_message()))
    await asyncio.sleep(0)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    loop._restore_runtime_checkpoint.assert_called_once_with(session)
    loop._clear_pending_user_turn.assert_called_once_with(session)
    loop.sessions.save.assert_called_once_with(session)
    assert "cli:c" not in loop._pending_queues


@pytest.mark.asyncio
async def test_message_dispatcher_error_publishes_fallback() -> None:
    loop = SimpleNamespace(
        _effective_session_key=MagicMock(return_value="cli:c"),
        _session_locks={},
        _concurrency_gate=None,
        _pending_queues={},
        _process_message=AsyncMock(side_effect=RuntimeError("boom")),
        _dispatch_command_inline=AsyncMock(),
        _schedule_background=MagicMock(),
        _restore_runtime_checkpoint=MagicMock(return_value=False),
        _clear_pending_user_turn=MagicMock(),
        sessions=SimpleNamespace(
            get_or_create=MagicMock(return_value=SimpleNamespace(metadata={}, messages=[])),
            save=MagicMock(),
        ),
        bus=SimpleNamespace(publish_outbound=AsyncMock(), publish_inbound=AsyncMock()),
        provider=MagicMock(),
        model="test-model",
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    await dispatcher.dispatch_message(_message())

    published = loop.bus.publish_outbound.await_args.args[0]
    assert isinstance(published, OutboundMessage)
    assert published.content == "Sorry, I encountered an error."


@pytest.mark.asyncio
async def test_message_dispatcher_websocket_turn_end_and_title_background() -> None:
    session = SimpleNamespace(metadata={"goal": "active"}, messages=[])
    scheduled: list[object] = []

    def capture_background(coro) -> None:
        scheduled.append(coro)
        coro.close()

    loop = SimpleNamespace(
        _effective_session_key=MagicMock(return_value="websocket:c"),
        _session_locks={},
        _concurrency_gate=None,
        _pending_queues={},
        _process_message=AsyncMock(
            return_value=OutboundMessage(channel="websocket", chat_id="c", content="done")
        ),
        _dispatch_command_inline=AsyncMock(),
        _schedule_background=MagicMock(side_effect=capture_background),
        _restore_runtime_checkpoint=MagicMock(return_value=False),
        _clear_pending_user_turn=MagicMock(),
        sessions=SimpleNamespace(
            get_or_create=MagicMock(return_value=session),
            save=MagicMock(),
        ),
        bus=SimpleNamespace(publish_outbound=AsyncMock(), publish_inbound=AsyncMock()),
        provider=MagicMock(),
        model="test-model",
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    await dispatcher.dispatch_message(
        _message(
            channel="websocket",
            metadata={"webui": True, "webui_turn_latency_ms": 42},
        )
    )

    assert loop.bus.publish_outbound.await_count == 2
    turn_end = loop.bus.publish_outbound.await_args_list[1].args[0]
    assert turn_end.metadata["_turn_end"] is True
    assert turn_end.metadata["latency_ms"] == 42
    loop._schedule_background.assert_called_once()
    assert len(scheduled) == 1
