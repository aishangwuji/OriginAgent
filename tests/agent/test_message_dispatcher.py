from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.message_dispatcher import MessageDispatcher, MessageDispatcherDeps
from OriginAgent.bus.events import InboundMessage


@pytest.mark.asyncio
async def test_message_dispatcher_run_uses_loop_dispatch() -> None:
    loop = SimpleNamespace(
        _running=True,
        bus=SimpleNamespace(
            consume_inbound=AsyncMock(side_effect=[InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hello"), asyncio.CancelledError()]),
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

    loop._dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_message_dispatcher_cleans_pending_queue() -> None:
    queue = asyncio.Queue()
    queue.put_nowait(InboundMessage(channel="cli", sender_id="u", chat_id="c", content="leftover"))
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
        sessions=SimpleNamespace(get_or_create=MagicMock(return_value=SimpleNamespace(metadata={}, messages=[])), save=MagicMock()),
        bus=SimpleNamespace(publish_outbound=AsyncMock(), publish_inbound=AsyncMock()),
        provider=MagicMock(),
        model="test-model",
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    await dispatcher.dispatch_message(InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hello"))

    assert "cli:c" not in loop._pending_queues
