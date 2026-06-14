"""Unit and lightweight integration tests for the WebSocket channel."""

import asyncio
import functools
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import httpx
import pytest
import websockets
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

from OriginAgent.agent.background_review import ReviewProposal, ReviewProposalStore
from OriginAgent.bus.events import OUTBOUND_META_AGENT_UI, OutboundMessage
from OriginAgent.channels.websocket import (
    WebSocketChannel,
    WebSocketConfig,
    _is_valid_chat_id,
    _issue_route_secret_matches,
    _normalize_config_path,
    _normalize_http_path,
    _parse_envelope,
    _parse_inbound_payload,
    _parse_query,
    _parse_request_path,
)
from OriginAgent.config.loader import load_config, save_config
from OriginAgent.config.schema import Config, MCPServerConfig

# -- Shared helpers (aligned with test_websocket_integration.py) ---------------

_PORT = 29876


def _ch(bus: Any, **kw: Any) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": _PORT,
        "path": "/ws",
        "websocketRequiresToken": False,
    }
    cfg.update(kw)
    return WebSocketChannel(cfg, bus)


@pytest.fixture()
def bus() -> MagicMock:
    b = MagicMock()
    b.publish_inbound = AsyncMock()
    return b


async def _http_get(url: str, headers: dict[str, str] | None = None) -> httpx.Response:
    """Run GET in a thread to avoid blocking the asyncio loop shared with websockets."""
    return await asyncio.to_thread(
        functools.partial(httpx.get, url, headers=headers or {}, timeout=5.0)
    )


def test_normalize_http_path_strips_trailing_slash_except_root() -> None:
    assert _normalize_http_path("/chat/") == "/chat"
    assert _normalize_http_path("/chat?x=1") == "/chat"
    assert _normalize_http_path("/") == "/"


def test_parse_request_path_matches_normalize_and_query() -> None:
    path, query = _parse_request_path("/ws/?token=secret&client_id=u1")
    assert path == _normalize_http_path("/ws/?token=secret&client_id=u1")
    assert query == _parse_query("/ws/?token=secret&client_id=u1")


def test_normalize_config_path_matches_request() -> None:
    assert _normalize_config_path("/ws/") == "/ws"
    assert _normalize_config_path("/") == "/"


def test_parse_query_extracts_token_and_client_id() -> None:
    query = _parse_query("/?token=secret&client_id=u1")
    assert query.get("token") == ["secret"]
    assert query.get("client_id") == ["u1"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ('{"content": "hi"}', "hi"),
        ('{"text": "there"}', "there"),
        ('{"message": "x"}', "x"),
        ("  ", None),
        ("{}", None),
    ],
)
def test_parse_inbound_payload(raw: str, expected: str | None) -> None:
    assert _parse_inbound_payload(raw) == expected


def test_parse_inbound_invalid_json_falls_back_to_raw_string() -> None:
    assert _parse_inbound_payload("{not json") == "{not json"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"content": ""}', None),           # empty string content
        ('{"content": 123}', None),          # non-string content
        ('{"content": "  "}', None),         # whitespace-only content
        ('["hello"]', '["hello"]'),           # JSON array: not a dict, treated as plain text
        ('{"unknown_key": "val"}', None),    # unrecognized key
        ('{"content": null}', None),         # null content
    ],
)
def test_parse_inbound_payload_edge_cases(raw: str, expected: str | None) -> None:
    assert _parse_inbound_payload(raw) == expected


def test_web_socket_config_path_must_start_with_slash() -> None:
    with pytest.raises(ValueError, match='path must start with "/"'):
        WebSocketConfig(path="bad")


def test_ssl_context_requires_both_cert_and_key_files() -> None:
    bus = MagicMock()
    channel = WebSocketChannel(
        {"enabled": True, "allowFrom": ["*"], "sslCertfile": "/tmp/c.pem", "sslKeyfile": ""},
        bus,
    )
    with pytest.raises(ValueError, match="ssl_certfile and ssl_keyfile"):
        channel._build_ssl_context()


def test_default_config_includes_safe_bind_and_streaming() -> None:
    defaults = WebSocketChannel.default_config()
    assert defaults["enabled"] is False
    assert defaults["host"] == "127.0.0.1"
    assert defaults["streaming"] is True
    assert defaults["allowFrom"] == ["*"]
    assert defaults.get("tokenIssuePath", "") == ""


def test_token_issue_path_must_differ_from_websocket_path() -> None:
    with pytest.raises(ValueError, match="token_issue_path must differ"):
        WebSocketConfig(path="/ws", token_issue_path="/ws")


def test_issue_route_secret_matches_bearer_and_header() -> None:
    from websockets.datastructures import Headers

    secret = "my-secret"
    bearer_headers = Headers([("Authorization", "Bearer my-secret")])
    assert _issue_route_secret_matches(bearer_headers, secret) is True
    x_headers = Headers([("X-OriginAgent-Auth", "my-secret")])
    assert _issue_route_secret_matches(x_headers, secret) is True
    wrong = Headers([("Authorization", "Bearer other")])
    assert _issue_route_secret_matches(wrong, secret) is False


def test_issue_route_secret_matches_empty_secret() -> None:
    from websockets.datastructures import Headers

    # Empty secret always returns True regardless of headers
    assert _issue_route_secret_matches(Headers([]), "") is True
    assert _issue_route_secret_matches(Headers([("Authorization", "Bearer anything")]), "") is True


@pytest.mark.asyncio
async def test_webui_message_envelope_marks_inbound_metadata(bus: MagicMock) -> None:
    channel = _ch(bus)
    conn = MagicMock()
    conn.remote_address = ("127.0.0.1", 50123)

    await channel._dispatch_envelope(
        conn,
        "webui-client",
        {"type": "message", "chat_id": "chat-1", "content": "hello", "webui": True},
    )

    msg = bus.publish_inbound.await_args.args[0]
    assert msg.channel == "websocket"
    assert msg.chat_id == "chat-1"
    assert msg.metadata["webui"] is True
    assert msg.metadata["_wants_stream"] is True


@pytest.mark.asyncio
async def test_plain_websocket_message_does_not_mark_webui(bus: MagicMock) -> None:
    channel = _ch(bus)
    conn = MagicMock()

    await channel._dispatch_envelope(
        conn,
        "custom-client",
        {"type": "message", "chat_id": "chat-1", "content": "hello"},
    )

    msg = bus.publish_inbound.await_args.args[0]
    assert "webui" not in msg.metadata


@pytest.mark.asyncio
async def test_send_delivers_json_message_with_media_and_reply() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "chat-1")

    msg = OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content="hello",
        reply_to="m1",
        media=["/tmp/a.png"],
        buttons=[["Yes", "No"]],
    )
    await channel.send(msg)

    mock_ws.send.assert_awaited_once()
    payload = json.loads(mock_ws.send.call_args[0][0])
    assert payload["event"] == "message"
    assert payload["chat_id"] == "chat-1"
    assert payload["text"] == "hello\n\n1. Yes\n2. No"
    assert payload["button_prompt"] == "hello"
    assert payload["reply_to"] == "m1"
    assert payload["media"] == ["/tmp/a.png"]
    assert payload["buttons"] == [["Yes", "No"]]


@pytest.mark.asyncio
async def test_send_stages_external_media_as_signed_url(monkeypatch, tmp_path) -> None:
    bus = MagicMock()
    media_root = tmp_path / "media"
    ws_media = media_root / "websocket"
    ws_media.mkdir(parents=True)
    external = tmp_path / "clip.mp4"
    external.write_bytes(b"video")

    def fake_media_dir(channel: str | None = None):
        return ws_media if channel == "websocket" else media_root

    monkeypatch.setattr("OriginAgent.channels.websocket.get_media_dir", fake_media_dir)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "chat-1")

    await channel.send(
        OutboundMessage(
            channel="websocket",
            chat_id="chat-1",
            content="video",
            media=[str(external)],
        )
    )

    payload = json.loads(mock_ws.send.call_args[0][0])
    assert payload["media"] == [str(external)]
    assert payload["media_urls"][0]["name"] == "clip.mp4"
    assert payload["media_urls"][0]["url"].startswith("/api/media/")
    assert any(p.name.endswith("-clip.mp4") for p in ws_media.iterdir())


@pytest.mark.asyncio
async def test_send_missing_connection_is_noop_without_error() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    msg = OutboundMessage(channel="websocket", chat_id="missing", content="x")
    await channel.send(msg)


@pytest.mark.asyncio
async def test_send_removes_connection_on_connection_closed() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    mock_ws.send.side_effect = ConnectionClosed(Close(1006, ""), Close(1006, ""), True)
    channel._attach(mock_ws, "chat-1")

    msg = OutboundMessage(channel="websocket", chat_id="chat-1", content="hello")
    await channel.send(msg)

    assert "chat-1" not in channel._subs
    assert mock_ws not in channel._conn_chats


@pytest.mark.asyncio
async def test_send_delta_removes_connection_on_connection_closed() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"], "streaming": True}, bus)
    mock_ws = AsyncMock()
    mock_ws.send.side_effect = ConnectionClosed(Close(1006, ""), Close(1006, ""), True)
    channel._attach(mock_ws, "chat-1")

    await channel.send_delta("chat-1", "chunk", {"_stream_delta": True, "_stream_id": "s1"})

    assert "chat-1" not in channel._subs
    assert mock_ws not in channel._conn_chats


@pytest.mark.asyncio
async def test_send_delta_emits_delta_and_stream_end() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"], "streaming": True}, bus)
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "chat-1")

    await channel.send_delta("chat-1", "part", {"_stream_delta": True, "_stream_id": "sid"})
    await channel.send_delta("chat-1", "", {"_stream_end": True, "_stream_id": "sid"})

    assert mock_ws.send.await_count == 2
    first = json.loads(mock_ws.send.call_args_list[0][0][0])
    second = json.loads(mock_ws.send.call_args_list[1][0][0])
    assert first["event"] == "delta"
    assert first["chat_id"] == "chat-1"
    assert first["text"] == "part"
    assert first["stream_id"] == "sid"
    assert second["event"] == "stream_end"
    assert second["chat_id"] == "chat-1"
    assert second["stream_id"] == "sid"


@pytest.mark.asyncio
async def test_send_progress_includes_agent_ui_blob() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "chat-1")

    blob = {"kind": "panel", "data": {"version": 1, "event": "tick", "id": "r1"}}
    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content="progress panel",
        metadata={"_progress": True, OUTBOUND_META_AGENT_UI: blob},
    ))

    payload = json.loads(mock_ws.send.await_args.args[0])
    assert payload["event"] == "message"
    assert payload["kind"] == "progress"
    assert payload["agent_ui"] == blob


@pytest.mark.asyncio
async def test_send_reasoning_delta_emits_streaming_frame() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "chat-1")

    await channel.send_reasoning_delta(
        "chat-1",
        "step-by-step thinking",
        {"_reasoning_delta": True, "_stream_id": "r1"},
    )

    payload = json.loads(mock_ws.send.await_args.args[0])
    assert payload["event"] == "reasoning_delta"
    assert payload["chat_id"] == "chat-1"
    assert payload["text"] == "step-by-step thinking"
    assert payload["stream_id"] == "r1"


@pytest.mark.asyncio
async def test_send_reasoning_end_emits_close_frame() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "chat-1")

    await channel.send_reasoning_end("chat-1", {"_reasoning_end": True, "_stream_id": "r1"})

    payload = json.loads(mock_ws.send.await_args.args[0])
    assert payload == {"event": "reasoning_end", "chat_id": "chat-1", "stream_id": "r1"}


@pytest.mark.asyncio
async def test_send_reasoning_one_shot_expands_to_delta_plus_end() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "chat-1")

    await channel.send_reasoning(OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content="thinking",
        metadata={"_reasoning": True},
    ))

    assert mock_ws.send.await_count == 2
    first = json.loads(mock_ws.send.call_args_list[0][0][0])
    second = json.loads(mock_ws.send.call_args_list[1][0][0])
    assert first["event"] == "reasoning_delta"
    assert first["text"] == "thinking"
    assert second["event"] == "reasoning_end"


@pytest.mark.asyncio
async def test_send_reasoning_delta_drops_empty_chunks() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "chat-1")

    await channel.send_reasoning_delta("chat-1", "", {"_reasoning_delta": True})

    mock_ws.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_reasoning_without_subscribers_is_noop() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)

    await channel.send_reasoning_delta("unattached", "thinking", None)
    await channel.send_reasoning_end("unattached", None)


@pytest.mark.asyncio
async def test_send_turn_end_emits_turn_end_event() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "chat-1")

    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content="",
        metadata={"_turn_end": True},
    ))

    mock_ws.send.assert_awaited_once()
    body = json.loads(mock_ws.send.await_args.args[0])
    assert body == {"event": "turn_end", "chat_id": "chat-1"}


@pytest.mark.asyncio
async def test_send_session_updated_emits_session_updated_event() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "chat-1")

    await channel.send(OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content="",
        metadata={"_session_updated": True},
    ))

    mock_ws.send.assert_awaited_once()
    body = json.loads(mock_ws.send.await_args.args[0])
    assert body == {"event": "session_updated", "chat_id": "chat-1"}


@pytest.mark.asyncio
async def test_send_non_connection_closed_exception_is_raised() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    mock_ws.send.side_effect = RuntimeError("unexpected")
    channel._attach(mock_ws, "chat-1")

    msg = OutboundMessage(channel="websocket", chat_id="chat-1", content="hello")
    with pytest.raises(RuntimeError, match="unexpected"):
        await channel.send(msg)


@pytest.mark.asyncio
async def test_send_delta_missing_connection_is_noop() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"], "streaming": True}, bus)
    # No exception, no error — just a no-op
    await channel.send_delta("nonexistent", "chunk", {"_stream_delta": True, "_stream_id": "s1"})


@pytest.mark.asyncio
async def test_stop_is_idempotent() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    # stop() before start() should not raise
    await channel.stop()
    await channel.stop()


@pytest.mark.asyncio
async def test_end_to_end_client_receives_ready_and_agent_sees_inbound(bus: MagicMock) -> None:
    port = 29876
    channel = _ch(bus, port=port)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=tester") as client:
            ready_raw = await client.recv()
            ready = json.loads(ready_raw)
            assert ready["event"] == "ready"
            assert ready["client_id"] == "tester"
            chat_id = ready["chat_id"]

            await client.send(json.dumps({"content": "ping from client"}))
            await asyncio.sleep(0.08)

            bus.publish_inbound.assert_awaited()
            inbound = bus.publish_inbound.call_args[0][0]
            assert inbound.channel == "websocket"
            assert inbound.sender_id == "tester"
            assert inbound.chat_id == chat_id
            assert inbound.content == "ping from client"

            await client.send("plain text frame")
            await asyncio.sleep(0.08)
            assert bus.publish_inbound.await_count >= 2
            second = [c[0][0] for c in bus.publish_inbound.call_args_list][-1]
            assert second.content == "plain text frame"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_token_rejects_handshake_when_mismatch(bus: MagicMock) -> None:
    port = 29877
    channel = _ch(bus, port=port, path="/", token="secret")

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as excinfo:
            async with websockets.connect(f"ws://127.0.0.1:{port}/?token=wrong"):
                pass
        assert excinfo.value.response.status_code == 401
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_wrong_path_returns_404(bus: MagicMock) -> None:
    port = 29878
    channel = _ch(bus, port=port)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as excinfo:
            async with websockets.connect(f"ws://127.0.0.1:{port}/other"):
                pass
        assert excinfo.value.response.status_code == 404
    finally:
        await channel.stop()
        await server_task


def test_registry_discovers_websocket_channel() -> None:
    from OriginAgent.channels.registry import load_channel_class

    cls = load_channel_class("websocket")
    assert cls.name == "websocket"


@pytest.mark.asyncio
async def test_http_route_issues_token_then_websocket_requires_it(bus: MagicMock) -> None:
    port = 29879
    channel = _ch(
        bus, port=port,
        tokenIssuePath="/auth/token",
        tokenIssueSecret="route-secret",
        websocketRequiresToken=True,
    )

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        deny = await _http_get(f"http://127.0.0.1:{port}/auth/token")
        assert deny.status_code == 401

        issue = await _http_get(
            f"http://127.0.0.1:{port}/auth/token",
            headers={"Authorization": "Bearer route-secret"},
        )
        assert issue.status_code == 200
        token = issue.json()["token"]
        assert token.startswith("nbwt_")

        with pytest.raises(websockets.exceptions.InvalidStatus) as missing_token:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=x"):
                pass
        assert missing_token.value.response.status_code == 401

        uri = f"ws://127.0.0.1:{port}/ws?token={token}&client_id=caller"
        async with websockets.connect(uri) as client:
            ready = json.loads(await client.recv())
            assert ready["event"] == "ready"
            assert ready["client_id"] == "caller"

        with pytest.raises(websockets.exceptions.InvalidStatus) as reuse:
            async with websockets.connect(uri):
                pass
        assert reuse.value.response.status_code == 401
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_settings_api_returns_safe_subset_and_updates_whitelist(
    bus: MagicMock,
    monkeypatch,
    tmp_path,
) -> None:
    port = 29891
    config_path = tmp_path / "config.json"
    config = Config()
    config.agents.defaults.model = "openai/gpt-4o"
    config.providers.openai.api_key = "secret-key"
    config.tools.web.search.provider = "brave"
    config.tools.web.search.api_key = "brave-secret"
    config.tools.mcp_servers["github"] = MCPServerConfig(
        type="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"GITHUB_TOKEN": "ghp_secret"},
        tool_timeout=45,
        enabled_tools=["search"],
    )
    save_config(config, config_path)
    monkeypatch.setattr("OriginAgent.config.loader._current_config_path", config_path)

    channel = _ch(bus, port=port)
    channel._api_tokens["tok"] = time.monotonic() + 300

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        settings = await _http_get(
            f"http://127.0.0.1:{port}/api/settings",
            headers={"Authorization": "Bearer tok"},
        )
        assert settings.status_code == 200
        body = settings.json()
        assert body["agent"]["model"] == "openai/gpt-4o"
        assert body["agent"]["provider"] == "openai"
        providers = {provider["name"]: provider for provider in body["providers"]}
        assert providers["openai"]["configured"] is True
        assert providers["openai"]["api_key_hint"] == "secr••••-key"
        assert providers["openai"]["model_catalog_kind"] == "official"
        assert providers["openrouter"]["configured"] is False
        assert providers["openrouter"]["model_catalog_kind"] == "catalog"
        assert providers["custom"]["model_catalog_kind"] == "custom"
        assert body["agent"]["has_api_key"] is True
        assert body["web_search"]["provider"] == "brave"
        assert body["web_search"]["api_key_hint"] == "brav••••cret"
        assert body["learning"]["background_review"]["enabled"] is False
        assert body["runtime_controls"]["channels"]["show_reasoning"] is True
        assert body["runtime_controls"]["search"]["web_enabled"] is True
        assert body["runtime_controls"]["execution"]["exec_profile"] == "local_dev"
        assert body["runtime_controls"]["subagent"]["mode"] == "normal"
        assert body["mcp"]["servers"] == [
            {
                "name": "github",
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "••••"},
                "url": "",
                "headers": {},
                "tool_timeout": 45,
                "enabled_tools": ["search"],
            }
        ]
        search_providers = {provider["name"]: provider for provider in body["web_search"]["providers"]}
        assert search_providers["duckduckgo"]["credential"] == "none"
        assert search_providers["searxng"]["credential"] == "base_url"
        assert "secret-key" not in settings.text
        assert "brave-secret" not in settings.text
        assert "ghp_secret" not in settings.text

        provider_updated = await _http_get(
            "http://127.0.0.1:"
            f"{port}/api/settings/provider/update?provider=openrouter"
            "&api_key=sk-or-test&api_base=https%3A%2F%2Fopenrouter.ai%2Fapi%2Fv1",
            headers={"Authorization": "Bearer tok"},
        )
        assert provider_updated.status_code == 200
        provider_body = provider_updated.json()
        assert provider_body["requires_restart"] is False
        provider_rows = {provider["name"]: provider for provider in provider_body["providers"]}
        assert provider_rows["openrouter"]["configured"] is True
        assert provider_rows["openrouter"]["model_catalog_kind"] == "catalog"
        assert "sk-or-test" not in provider_updated.text

        updated = await _http_get(
            "http://127.0.0.1:"
            f"{port}/api/settings/update?model=openrouter/test"
            "&provider=openrouter",
            headers={"Authorization": "Bearer tok"},
        )
        assert updated.status_code == 200
        assert updated.json()["requires_restart"] is False

        search_updated = await _http_get(
            "http://127.0.0.1:"
            f"{port}/api/settings/web-search/update?provider=searxng"
            "&base_url=https%3A%2F%2Fsearch.example.com",
            headers={"Authorization": "Bearer tok"},
        )
        assert search_updated.status_code == 200
        search_body = search_updated.json()
        assert search_body["requires_restart"] is False
        assert search_body["web_search"]["provider"] == "searxng"
        assert search_body["web_search"]["api_key_hint"] is None
        assert search_body["web_search"]["base_url"] == "https://search.example.com"

        learning_updated = await _http_get(
            "http://127.0.0.1:"
            f"{port}/api/settings/learning/background-review/update?enabled=true",
            headers={"Authorization": "Bearer tok"},
        )
        assert learning_updated.status_code == 200
        assert learning_updated.json()["requires_restart"] is False
        assert learning_updated.json()["learning"]["background_review"]["enabled"] is True

        runtime_updated = await _http_get(
            "http://127.0.0.1:"
            f"{port}/api/settings/runtime/update?config="
            + quote(json.dumps({
                "channels": {"show_reasoning": False},
                "agent": {"allow_agent_initiated_messages": True},
                "search": {"web_enabled": False},
                "execution": {"exec_profile": "disabled"},
                "subagent": {"mode": "restricted"},
                "audit": {"audit_mode": "security"},
                "runtime": {"profile": "safe"},
            })),
            headers={"Authorization": "Bearer tok"},
        )
        assert runtime_updated.status_code == 200
        runtime_body = runtime_updated.json()
        assert runtime_body["requires_restart"] is True
        assert runtime_body["runtime_controls"]["channels"]["show_reasoning"] is False
        assert runtime_body["runtime_controls"]["agent"]["allow_agent_initiated_messages"] is True
        assert runtime_body["runtime_controls"]["search"]["web_enabled"] is False
        assert runtime_body["runtime_controls"]["execution"]["exec_profile"] == "disabled"
        assert runtime_body["runtime_controls"]["subagent"]["mode"] == "restricted"
        assert runtime_body["runtime_controls"]["audit"]["audit_mode"] == "security"
        assert runtime_body["runtime_controls"]["runtime"]["profile"] == "safe"

        saved = load_config(config_path)
        assert saved.agents.defaults.model == "openrouter/test"
        assert saved.agents.defaults.provider == "openrouter"
        assert saved.agents.defaults.learning.background_review.enabled is True
        assert saved.agents.defaults.allow_agent_initiated_messages is True
        assert saved.runtime.profile == "safe"
        assert saved.channels.show_reasoning is False
        assert saved.providers.openrouter.api_key == "sk-or-test"
        assert saved.providers.openrouter.api_base == "https://openrouter.ai/api/v1"
        assert saved.tools.web.search.provider == "searxng"
        assert saved.tools.web.search.api_key == ""
        assert saved.tools.web.search.base_url == "https://search.example.com"
        assert saved.tools.web.enable is False
        assert saved.tools.exec.profile == "disabled"
        assert saved.agents.defaults.subagent_policy.mode == "restricted"
        assert saved.tools.audit.mode == "security"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_settings_mcp_routes_manage_servers(
    bus: MagicMock,
    monkeypatch,
    tmp_path,
) -> None:
    port = 29893
    config_path = tmp_path / "config.json"
    config = Config()
    save_config(config, config_path)
    monkeypatch.setattr("OriginAgent.config.loader._current_config_path", config_path)

    channel = _ch(bus, port=port)
    channel._api_tokens["tok"] = time.monotonic() + 300

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        auth = {"Authorization": "Bearer tok"}
        stdio_config = {
            "name": "github",
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "ghp_secret"},
            "tool_timeout": 45,
            "enabled_tools": ["search"],
        }
        created = await _http_get(
            "http://127.0.0.1:"
            f"{port}/api/settings/mcp/upsert?config="
            + quote(json.dumps(stdio_config)),
            headers=auth,
        )
        assert created.status_code == 200
        assert created.json()["requires_restart"] is True
        assert "ghp_secret" not in created.text
        saved = load_config(config_path)
        assert saved.tools.mcp_servers["github"].command == "npx"
        assert saved.tools.mcp_servers["github"].env["GITHUB_TOKEN"] == "ghp_secret"

        update_config = {
            "name": "github",
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": ""},
            "tool_timeout": 30,
            "enabled_tools": ["*"],
        }
        updated = await _http_get(
            "http://127.0.0.1:"
            f"{port}/api/settings/mcp/upsert?config="
            + quote(json.dumps(update_config)),
            headers=auth,
        )
        assert updated.status_code == 200
        saved = load_config(config_path)
        assert saved.tools.mcp_servers["github"].env["GITHUB_TOKEN"] == "ghp_secret"
        assert saved.tools.mcp_servers["github"].enabled_tools == ["*"]

        http_config = {
            "name": "remote",
            "type": "streamableHttp",
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer secret"},
            "tool_timeout": 20,
            "enabled_tools": ["*"],
        }
        http_created = await _http_get(
            "http://127.0.0.1:"
            f"{port}/api/settings/mcp/upsert?config="
            + quote(json.dumps(http_config)),
            headers=auth,
        )
        assert http_created.status_code == 200
        assert "Bearer secret" not in http_created.text
        saved = load_config(config_path)
        assert saved.tools.mcp_servers["remote"].url == "https://mcp.example.com/mcp"
        assert saved.tools.mcp_servers["remote"].headers["Authorization"] == "Bearer secret"

        ha_created = await _http_get(
            "http://127.0.0.1:"
            f"{port}/api/settings/mcp/home-assistant/upsert?"
            "name=home_assistant&address=http%3A%2F%2Flocalhost%3A8123%2Fhome%2F0"
            "&token=ha_token",
            headers=auth,
        )
        assert ha_created.status_code == 200
        assert ha_created.json()["requires_restart"] is True
        assert "ha_token" not in ha_created.text
        saved = load_config(config_path)
        ha = saved.tools.mcp_servers["home_assistant"]
        assert ha.type == "streamableHttp"
        assert ha.url == "http://localhost:8123/api/mcp"
        assert ha.headers["Authorization"] == "Bearer ha_token"
        assert ha.enabled_tools == ["*"]
        assert "127.0.0.1/32" in saved.tools.ssrf_whitelist
        assert "::1/128" in saved.tools.ssrf_whitelist

        ha_updated = await _http_get(
            "http://127.0.0.1:"
            f"{port}/api/settings/mcp/home-assistant/upsert?"
            "name=home_assistant&address=localhost%3A8123",
            headers=auth,
        )
        assert ha_updated.status_code == 200
        saved = load_config(config_path)
        assert saved.tools.mcp_servers["home_assistant"].url == "http://localhost:8123/api/mcp"
        assert saved.tools.mcp_servers["home_assistant"].headers["Authorization"] == "Bearer ha_token"

        deleted = await _http_get(
            f"http://127.0.0.1:{port}/api/settings/mcp/delete?name=github",
            headers=auth,
        )
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert "github" not in load_config(config_path).tools.mcp_servers
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_settings_mcp_routes_validate_input(
    bus: MagicMock,
    monkeypatch,
    tmp_path,
) -> None:
    port = 29894
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("OriginAgent.config.loader._current_config_path", config_path)

    channel = _ch(bus, port=port)
    channel._api_tokens["tok"] = time.monotonic() + 300

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        auth = {"Authorization": "Bearer tok"}
        cases = [
            {"name": "bad name", "type": "stdio", "command": "npx"},
            {"name": "missing_command", "type": "stdio", "command": ""},
            {"name": "missing_url", "type": "sse", "url": ""},
            {"name": "bad_type", "type": "websocket", "url": "https://example.com"},
        ]
        for payload in cases:
            resp = await _http_get(
                "http://127.0.0.1:"
                f"{port}/api/settings/mcp/upsert?config="
                + quote(json.dumps(payload)),
                headers=auth,
            )
            assert resp.status_code == 400

        bad_ha_name = await _http_get(
            f"http://127.0.0.1:{port}/api/settings/mcp/home-assistant/upsert?"
            "name=bad%20name&address=http%3A%2F%2Flocalhost%3A8123&token=ha",
            headers=auth,
        )
        assert bad_ha_name.status_code == 400

        bad_ha_url = await _http_get(
            f"http://127.0.0.1:{port}/api/settings/mcp/home-assistant/upsert?"
            "name=home_assistant&address=ftp%3A%2F%2Flocalhost%3A8123&token=ha",
            headers=auth,
        )
        assert bad_ha_url.status_code == 400

        missing_ha_token = await _http_get(
            f"http://127.0.0.1:{port}/api/settings/mcp/home-assistant/upsert?"
            "name=home_assistant&address=http%3A%2F%2Flocalhost%3A8123",
            headers=auth,
        )
        assert missing_ha_token.status_code == 400
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_settings_provider_models_contract_route_validates_scope(
    bus: MagicMock,
    monkeypatch,
    tmp_path,
) -> None:
    port = 29896
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.openrouter.api_key = "saved-openrouter-key"
    config.providers.openrouter.api_base = "https://openrouter.ai/api/v1"
    save_config(config, config_path)
    monkeypatch.setattr("OriginAgent.config.loader._current_config_path", config_path)

    channel = _ch(bus, port=port)
    channel._api_tokens["tok"] = time.monotonic() + 300

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        auth = {"Authorization": "Bearer tok"}

        denied = await _http_get(
            f"http://127.0.0.1:{port}/api/settings/provider/models?provider=openrouter&api_key=sk-or-test",
        )
        assert denied.status_code == 401

        unsupported = await _http_get(
            f"http://127.0.0.1:{port}/api/settings/provider/models?provider=anthropic&api_key=sk-ant-test",
            headers=auth,
        )
        assert unsupported.status_code == 400
        assert unsupported.json() == {
            "message": "provider does not support automatic model discovery",
            "phase": "contract",
            "reason": "unsupported",
        }

        missing_key = await _http_get(
            f"http://127.0.0.1:{port}/api/settings/provider/models?provider=groq",
            headers=auth,
        )
        assert missing_key.status_code == 400
        assert missing_key.json() == {
            "message": "api_key is required",
            "phase": "contract",
            "reason": "api_key_required",
        }

        with patch(
            "OriginAgent.providers.model_fetch_contract.fetch_provider_models",
            new=AsyncMock(return_value=MagicMock(
                to_json=lambda: {
                    "provider": "openrouter",
                    "status": "available",
                    "catalog_kind": "catalog",
                    "models": [{"id": "gpt-4o-mini", "owned_by": "openai"}],
                    "model_count": 1,
                    "fetched_at": 1_717_171_717.0,
                    "source_url": "https://openrouter.ai/api/v1/models",
                    "cached": False,
                },
            )),
        ) as fetch_mock:
            supported = await _http_get(
                "http://127.0.0.1:"
                f"{port}/api/settings/provider/models?provider=openrouter",
                headers=auth,
            )
        assert supported.status_code == 200
        assert supported.json() == {
            "provider": "openrouter",
            "status": "available",
            "catalog_kind": "catalog",
            "models": [{"id": "gpt-4o-mini", "owned_by": "openai"}],
            "model_count": 1,
            "fetched_at": 1_717_171_717.0,
            "source_url": "https://openrouter.ai/api/v1/models",
            "cached": False,
            "phase": "fetch",
        }
        request_contract = fetch_mock.await_args.args[0]
        assert request_contract.api_key == "saved-openrouter-key"
        assert request_contract.api_base == "https://openrouter.ai/api/v1"
        assert request_contract.force_refresh is False
        assert "saved-openrouter-key" not in supported.text

        with patch(
            "OriginAgent.providers.model_fetch_contract.fetch_provider_models",
            new=AsyncMock(return_value=MagicMock(
                to_json=lambda: {
                    "provider": "openrouter",
                    "status": "available",
                    "catalog_kind": "catalog",
                    "models": [{"id": "gpt-4.1", "owned_by": "openai"}],
                    "model_count": 1,
                    "fetched_at": 1_717_171_718.0,
                    "source_url": "https://openrouter.ai/api/v1/models",
                    "cached": False,
                },
            )),
        ) as refresh_mock:
            refreshed = await _http_get(
                "http://127.0.0.1:"
                f"{port}/api/settings/provider/models?provider=openrouter&force_refresh=true",
                headers=auth,
            )
        assert refreshed.status_code == 200
        refresh_contract = refresh_mock.await_args.args[0]
        assert refresh_contract.force_refresh is True

        from OriginAgent.providers.model_fetch_contract import ProviderModelFetchHttpError

        with patch(
            "OriginAgent.providers.model_fetch_contract.fetch_provider_models",
            new=AsyncMock(
                side_effect=ProviderModelFetchHttpError(
                    "auth_failed",
                    "HTTP 401: authentication failed",
                    status=401,
                ),
            ),
        ):
            upstream_unauthorized = await _http_get(
                "http://127.0.0.1:"
                f"{port}/api/settings/provider/models?provider=openrouter"
                "&api_key=sk-or-test&api_base=https%3A%2F%2Fopenrouter.ai%2Fapi%2Fv1",
                headers=auth,
            )
        assert upstream_unauthorized.status_code == 401
        assert upstream_unauthorized.json() == {
            "message": "HTTP 401: authentication failed",
            "phase": "fetch",
            "reason": "auth_failed",
        }

        with patch(
            "OriginAgent.providers.model_fetch_contract.fetch_provider_models",
            new=AsyncMock(
                side_effect=ProviderModelFetchHttpError(
                    "models_endpoint_missing",
                    "All candidates failed: HTTP 404: missing",
                    status=404,
                ),
            ),
        ):
            upstream_not_found = await _http_get(
                "http://127.0.0.1:"
                f"{port}/api/settings/provider/models?provider=openrouter"
                "&api_key=sk-or-test&api_base=https%3A%2F%2Fopenrouter.ai%2Fapi%2Fv1",
                headers=auth,
            )
        assert upstream_not_found.status_code == 404
        assert upstream_not_found.json() == {
            "message": "All candidates failed: HTTP 404: missing",
            "phase": "fetch",
            "reason": "models_endpoint_missing",
        }
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_unknown_api_routes_return_json_404_not_spa(
    bus: MagicMock,
    tmp_path,
) -> None:
    port = 29895
    channel = _ch(bus, port=port, static_dist_path=tmp_path)
    (tmp_path / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    channel._api_tokens["tok"] = time.monotonic() + 300

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        response = await _http_get(
            f"http://127.0.0.1:{port}/api/settings/mcp/missing",
            headers={"Authorization": "Bearer tok"},
        )
        assert response.status_code == 404
        assert response.text == "not found"
        assert "<html" not in response.text
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_commands_api_returns_slash_command_metadata(bus: MagicMock) -> None:
    port = 29892
    channel = _ch(bus, port=port)
    channel._api_tokens["tok"] = time.monotonic() + 300

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        denied = await _http_get(f"http://127.0.0.1:{port}/api/commands")
        assert denied.status_code == 401

        response = await _http_get(
            f"http://127.0.0.1:{port}/api/commands",
            headers={"Authorization": "Bearer tok"},
        )
        assert response.status_code == 200
        body = response.json()
        commands = {row["command"]: row for row in body["commands"]}
        assert commands["/stop"]["title"] == "Stop current task"
        assert commands["/history"]["arg_hint"] == "[n]"
        assert all("description" in row for row in body["commands"])
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_commands_api_localizes_when_lang_query_is_present(bus: MagicMock) -> None:
    port = 29893
    channel = _ch(bus, port=port)
    channel._api_tokens["tok"] = time.monotonic() + 300

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        response = await _http_get(
            f"http://127.0.0.1:{port}/api/commands?lang=zh-CN",
            headers={"Authorization": "Bearer tok"},
        )
        assert response.status_code == 200
        body = response.json()
        commands = {row["command"]: row for row in body["commands"]}
        assert commands["/stop"]["title"] == "停止当前任务"
        assert commands["/help"]["description"] == "列出可用的斜杠命令。"
    finally:
        await channel.stop()
        await server_task


def test_settings_payload_normalizes_camel_case_provider(
    bus: MagicMock,
    monkeypatch,
    tmp_path,
) -> None:
    config_path = tmp_path / "config.json"
    config = Config()
    config.agents.defaults.provider = "minimaxAnthropic"
    save_config(config, config_path)
    monkeypatch.setattr("OriginAgent.config.loader._current_config_path", config_path)

    body = _ch(bus)._settings_payload()

    assert body["agent"]["provider"] == "minimax_anthropic"


@pytest.mark.asyncio
async def test_end_to_end_server_pushes_streaming_deltas_to_client(bus: MagicMock) -> None:
    port = 29880
    channel = _ch(bus, port=port, streaming=True)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=stream-tester") as client:
            ready_raw = await client.recv()
            ready = json.loads(ready_raw)
            chat_id = ready["chat_id"]

            # Server pushes deltas directly
            await channel.send_delta(
                chat_id, "Hello ", {"_stream_delta": True, "_stream_id": "s1"}
            )
            await channel.send_delta(
                chat_id, "world", {"_stream_delta": True, "_stream_id": "s1"}
            )
            await channel.send_delta(
                chat_id, "", {"_stream_end": True, "_stream_id": "s1"}
            )

            delta1 = json.loads(await client.recv())
            assert delta1["event"] == "delta"
            assert delta1["text"] == "Hello "
            assert delta1["stream_id"] == "s1"

            delta2 = json.loads(await client.recv())
            assert delta2["event"] == "delta"
            assert delta2["text"] == "world"
            assert delta2["stream_id"] == "s1"

            end = json.loads(await client.recv())
            assert end["event"] == "stream_end"
            assert end["stream_id"] == "s1"

            await channel.send(OutboundMessage(
                channel="websocket",
                chat_id=chat_id,
                content="",
                metadata={"_turn_end": True},
            ))

            turn_end = json.loads(await client.recv())
            assert turn_end == {"event": "turn_end", "chat_id": chat_id}
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_token_issue_rejects_when_at_capacity(bus: MagicMock) -> None:
    port = 29881
    channel = _ch(bus, port=port, tokenIssuePath="/auth/token", tokenIssueSecret="s")

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        # Fill issued tokens to capacity
        channel._issued_tokens = {
            f"nbwt_fill_{i}": time.monotonic() + 300 for i in range(channel._MAX_ISSUED_TOKENS)
        }

        resp = await _http_get(
            f"http://127.0.0.1:{port}/auth/token",
            headers={"Authorization": "Bearer s"},
        )
        assert resp.status_code == 429
        data = resp.json()
        assert "error" in data
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_allow_from_rejects_unauthorized_client_id(bus: MagicMock) -> None:
    port = 29882
    channel = _ch(bus, port=port, allowFrom=["alice", "bob"])

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=eve"):
                pass
        assert exc_info.value.response.status_code == 403
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_client_id_truncation(bus: MagicMock) -> None:
    port = 29883
    channel = _ch(bus, port=port)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        long_id = "x" * 200
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id={long_id}") as client:
            ready = json.loads(await client.recv())
            assert ready["client_id"] == "x" * 128
            assert len(ready["client_id"]) == 128
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_non_utf8_binary_frame_ignored(bus: MagicMock) -> None:
    port = 29884
    channel = _ch(bus, port=port)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=bin-test") as client:
            await client.recv()  # consume ready
            # Send non-UTF-8 bytes
            await client.send(b"\xff\xfe\xfd")
            await asyncio.sleep(0.05)
            # publish_inbound should NOT have been called
            bus.publish_inbound.assert_not_awaited()
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_static_token_accepts_issued_token_as_fallback(bus: MagicMock) -> None:
    port = 29885
    channel = _ch(
        bus, port=port,
        token="static-secret",
        tokenIssuePath="/auth/token",
        tokenIssueSecret="route-secret",
    )

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        # Get an issued token
        resp = await _http_get(
            f"http://127.0.0.1:{port}/auth/token",
            headers={"Authorization": "Bearer route-secret"},
        )
        assert resp.status_code == 200
        issued_token = resp.json()["token"]

        # Connect using issued token (not the static one)
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={issued_token}&client_id=caller") as client:
            ready = json.loads(await client.recv())
            assert ready["event"] == "ready"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_allow_from_empty_list_denies_all(bus: MagicMock) -> None:
    port = 29886
    channel = _ch(bus, port=port, allowFrom=[])

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=anyone"):
                pass
        assert exc_info.value.response.status_code == 403
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_websocket_requires_token_without_issue_path(bus: MagicMock) -> None:
    """When websocket_requires_token is True but no token or issue path configured, all connections are rejected."""
    port = 29887
    channel = _ch(bus, port=port, websocketRequiresToken=True)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        # No token at all → 401
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=u"):
                pass
        assert exc_info.value.response.status_code == 401

        # Wrong token → 401
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=u&token=wrong"):
                pass
        assert exc_info.value.response.status_code == 401
    finally:
        await channel.stop()
        await server_task


# -- Multi-chat multiplexing -------------------------------------------------
#
# The multiplex protocol lets one WS connection route N logical chats over
# typed envelopes (`new_chat` / `attach` / `message`). Legacy frames must keep
# working on the connection's default chat_id.


@pytest.mark.asyncio
async def test_multiplex_legacy_still_works(bus: MagicMock) -> None:
    port = 29930
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=legacy") as client:
            ready = json.loads(await client.recv())
            default_chat = ready["chat_id"]

            # Plain text frame routes to default chat_id
            await client.send("hello from legacy")
            await asyncio.sleep(0.1)
            inbound = bus.publish_inbound.call_args[0][0]
            assert inbound.chat_id == default_chat
            assert inbound.content == "hello from legacy"

            # {"content": ...} frame routes to default chat_id
            await client.send(json.dumps({"content": "structured legacy"}))
            await asyncio.sleep(0.1)
            assert bus.publish_inbound.call_args[0][0].chat_id == default_chat
            assert bus.publish_inbound.call_args[0][0].content == "structured legacy"

            # Outbound still reaches the legacy client, with chat_id annotated
            await channel.send(
                OutboundMessage(channel="websocket", chat_id=default_chat, content="reply")
            )
            reply = json.loads(await client.recv())
            assert reply["event"] == "message"
            assert reply["chat_id"] == default_chat
            assert reply["text"] == "reply"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_multiplex_new_chat_roundtrip(bus: MagicMock) -> None:
    port = 29931
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=mp") as client:
            ready = json.loads(await client.recv())
            default_chat = ready["chat_id"]

            await client.send(json.dumps({"type": "new_chat"}))
            attached = json.loads(await client.recv())
            assert attached["event"] == "attached"
            new_chat = attached["chat_id"]
            assert new_chat and new_chat != default_chat

            # Send on the new chat via typed envelope
            await client.send(
                json.dumps({"type": "message", "chat_id": new_chat, "content": "hi on new"})
            )
            await asyncio.sleep(0.1)
            inbound = bus.publish_inbound.call_args[0][0]
            assert inbound.chat_id == new_chat
            assert inbound.content == "hi on new"

            # Server pushes a message back; chat_id must match
            await channel.send(
                OutboundMessage(channel="websocket", chat_id=new_chat, content="ok")
            )
            reply = json.loads(await client.recv())
            assert reply["event"] == "message"
            assert reply["chat_id"] == new_chat
            assert reply["text"] == "ok"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_multiplex_two_chats_isolated(bus: MagicMock) -> None:
    port = 29932
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=two") as client:
            await client.recv()  # ready

            await client.send(json.dumps({"type": "new_chat"}))
            chat_a = json.loads(await client.recv())["chat_id"]
            await client.send(json.dumps({"type": "new_chat"}))
            chat_b = json.loads(await client.recv())["chat_id"]
            assert chat_a != chat_b

            # Push A → client sees A only (FIFO over the single WS).
            await channel.send(
                OutboundMessage(channel="websocket", chat_id=chat_a, content="for-A")
            )
            msg_a = json.loads(await client.recv())
            assert msg_a["chat_id"] == chat_a
            assert msg_a["text"] == "for-A"

            # Push B → client sees B only.
            await channel.send(
                OutboundMessage(channel="websocket", chat_id=chat_b, content="for-B")
            )
            msg_b = json.loads(await client.recv())
            assert msg_b["chat_id"] == chat_b
            assert msg_b["text"] == "for-B"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_multiplex_invalid_frames_return_error(bus: MagicMock) -> None:
    port = 29933
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=bad") as client:
            await client.recv()  # ready

            # attach with bad chat_id
            await client.send(json.dumps({"type": "attach", "chat_id": "has space"}))
            err1 = json.loads(await client.recv())
            assert err1["event"] == "error"

            # message with missing content
            await client.send(json.dumps({"type": "message", "chat_id": "abc", "content": ""}))
            err2 = json.loads(await client.recv())
            assert err2["event"] == "error"

            # unknown type
            await client.send(json.dumps({"type": "nope"}))
            err3 = json.loads(await client.recv())
            assert err3["event"] == "error"

            # Connection survives: legacy frame still works.
            await client.send("still-alive")
            await asyncio.sleep(0.1)
            bus.publish_inbound.assert_awaited()
            assert bus.publish_inbound.call_args[0][0].content == "still-alive"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_multiplex_cleanup_on_disconnect(bus: MagicMock) -> None:
    port = 29934
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=dc") as client:
            ready = json.loads(await client.recv())
            default_chat = ready["chat_id"]
            await client.send(json.dumps({"type": "new_chat"}))
            extra_chat = json.loads(await client.recv())["chat_id"]
            assert default_chat in channel._subs
            assert extra_chat in channel._subs
        # Client gone. Server-side tracking must be empty.
        await asyncio.sleep(0.2)
        assert default_chat not in channel._subs
        assert extra_chat not in channel._subs
        assert not channel._conn_chats
        assert not channel._conn_default
    finally:
        await channel.stop()
        await server_task


def test_parse_envelope_detects_typed_frames() -> None:
    assert _parse_envelope('{"type":"new_chat"}') == {"type": "new_chat"}
    env = _parse_envelope('{"type":"message","chat_id":"abc","content":"hi"}')
    assert env == {"type": "message", "chat_id": "abc", "content": "hi"}


def test_parse_envelope_rejects_legacy_and_garbage() -> None:
    # No `type` field → legacy, caller falls back to _parse_inbound_payload.
    assert _parse_envelope('{"content":"hi"}') is None
    assert _parse_envelope("plain text") is None
    assert _parse_envelope("{broken") is None
    assert _parse_envelope("[1,2,3]") is None
    # Non-string `type` is not a valid envelope.
    assert _parse_envelope('{"type":123}') is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("abc", True),
        ("a1b2_c:d-e", True),
        ("x" * 64, True),
        ("unified:default", True),
        ("", False),
        ("x" * 65, False),
        ("has space", False),
        ("a/b", False),
        ("a.b", False),
        (None, False),
        (123, False),
    ],
)
def test_is_valid_chat_id(value: Any, expected: bool) -> None:
    assert _is_valid_chat_id(value) is expected


def test_handle_webui_thread_get_returns_json(tmp_path, monkeypatch) -> None:
    from urllib.parse import quote

    from websockets.datastructures import Headers
    from websockets.http11 import Request

    from OriginAgent.utils.webui_transcript import append_transcript_object

    monkeypatch.setattr("OriginAgent.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:c1"
    append_transcript_object(key, {"event": "user", "chat_id": "c1", "text": "hi"})
    bus = MagicMock()
    channel = _ch(bus)
    channel._api_tokens["tok"] = time.monotonic() + 300.0
    enc = quote(key, safe="")
    req = Request(f"/api/sessions/{enc}/webui-thread", Headers([("Authorization", "Bearer tok")]))
    resp = channel._handle_webui_thread_get(req, enc)
    assert resp.status_code == 200
    body = json.loads(resp.body.decode())
    assert body["sessionKey"] == key
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == "hi"


def test_review_api_lists_details_and_applies_with_auth(
    tmp_path,
    monkeypatch,
    bus: MagicMock,
) -> None:
    from websockets.datastructures import Headers
    from websockets.http11 import Request

    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)
    monkeypatch.setattr("OriginAgent.config.loader._current_config_path", config_path)

    store = ReviewProposalStore(workspace)
    store.append_many([
        ReviewProposal(
            id="review_memory",
            created_at="2026-05-19T10:00:00+00:00",
            session_key="websocket:chat1",
            turn_id="turn-1",
            proposal_type="memory",
            domain_id="core",
            title="Remember a safe preference",
            content="User prefers concise answers. api_key=sk-proj-secretsecretsecretsecret",
            rationale="User asked for it.",
            confidence=0.9,
            evidence=["Please be concise."],
        )
    ])

    channel = _ch(bus)
    channel._api_tokens["tok"] = time.monotonic() + 300
    authed = Headers([("Authorization", "Bearer tok")])

    denied = channel._handle_reviews_list(Request("/api/reviews", Headers([])))
    assert denied.status_code == 401

    listed = channel._handle_reviews_list(
        Request("/api/reviews?status=pending&type=memory&limit=50", authed)
    )
    assert listed.status_code == 200
    list_body = json.loads(listed.body.decode())
    assert list_body["stats"]["pending_count"] == 1
    assert list_body["proposals"][0]["id"] == "review_memory"
    assert "[REDACTED_SECRET]" in list_body["proposals"][0]["content"]
    assert "sk-proj" not in list_body["proposals"][0]["content"]

    detail = channel._handle_review_detail(
        Request("/api/reviews/review_memory", authed),
        "review_memory",
    )
    assert detail.status_code == 200
    detail_body = json.loads(detail.body.decode())
    assert detail_body["proposal"]["status"] == "pending"

    applied = channel._handle_review_action(
        Request("/api/reviews/review_memory/apply?reason=ok", authed),
        "review_memory",
        "apply",
    )
    assert applied.status_code == 200
    apply_body = json.loads(applied.body.decode())
    assert apply_body["result"]["ok"] is True
    assert apply_body["proposal"]["status"] == "applied"
    assert apply_body["stats"]["pending_count"] == 0

    facts_path = workspace / "memory" / "facts.jsonl"
    assert facts_path.exists()
    facts = [
        json.loads(line)
        for line in facts_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(facts) == 1
    assert facts[0]["scope"] == "review.memory"
    assert "[REDACTED_SECRET]" in facts[0]["content"]

    store.append_many([
        ReviewProposal(
            id="review_workflow",
            created_at="2026-05-19T10:01:00+00:00",
            session_key="websocket:chat1",
            turn_id="turn-2",
            proposal_type="workflow",
            domain_id="core",
            title="Lighting incident response",
            content="Create a manual workflow.",
            rationale="Repeated checklist.",
            confidence=0.8,
            evidence=["Use a manual checklist."],
            payload={
                "workflow_name": "lighting-incident-response",
                "description": "Manual workflow.",
                "body": "Use this as a manual checklist.",
            },
        )
    ])
    workflow_listed = channel._handle_reviews_list(
        Request("/api/reviews?status=pending&type=workflow&limit=50", authed)
    )
    workflow_list_body = json.loads(workflow_listed.body.decode())
    assert workflow_list_body["proposals"][0]["can_apply"] is True

    workflow_applied = channel._handle_review_action(
        Request("/api/reviews/review_workflow/apply?reason=ok", authed),
        "review_workflow",
        "apply",
    )
    workflow_apply_body = json.loads(workflow_applied.body.decode())
    assert workflow_apply_body["result"]["ok"] is True
    assert workflow_apply_body["apply_result"]["artifact"] == {
        "artifact_type": "workflow",
        "workflow_name": "lighting-incident-response",
        "path": "workflows/lighting-incident-response/workflow.yaml",
        "validation": "Workflow artifact is valid.",
    }

    store.append_many([
        ReviewProposal(
            id="review_promote",
            created_at="2026-05-20T10:02:00+00:00",
            session_key="curator:system",
            turn_id="turn-3",
            origin="curator",
            proposal_type="promote_skill",
            domain_id="core",
            title="Promote verified skill",
            content="Curator recommends activating this skill.",
            payload={
                "skill_name": "lighting-troubleshooting",
                "subject_id": "lighting-troubleshooting",
                "subject_type": "skill",
                "subject_path": "skills/lighting-troubleshooting/SKILL.md",
                "curator_key": "promote-skill:lighting-troubleshooting",
                "target_state_hash": "hash-promote-1",
                "suggested_action": "promote_skill",
            },
        ),
        ReviewProposal(
            id="review_merge",
            created_at="2026-05-20T10:03:00+00:00",
            session_key="curator:system",
            turn_id="turn-4",
            origin="curator",
            proposal_type="merge_skill",
            domain_id="core",
            title="Review duplicate skills",
            content="Manual merge review required.",
            payload={
                "subject_id": "alpha,beta",
                "subject_type": "skill_group",
                "subject_path": "skills/alpha/SKILL.md",
                "curator_key": "merge-skill:alpha",
                "target_state_hash": "hash-merge-1",
                "suggested_action": "merge_skill",
            },
        ),
    ])
    promote_skill_dir = workspace / "skills" / "lighting-troubleshooting"
    promote_skill_dir.mkdir(parents=True)
    (promote_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: lighting-troubleshooting\n"
        "description: Lighting help.\n"
        "always: false\n"
        "metadata:\n"
        "  OriginAgent:\n"
        "    proposal_status: proposed\n"
        "    verification_status: verified\n"
        "    lifecycle_status: proposed\n"
        "    review_proposal_id: review_skill\n"
        "    created_by: background_review\n"
        "---\n\n# Lighting\n",
        encoding="utf-8",
    )

    curator_listed = channel._handle_reviews_list(
        Request("/api/reviews?status=pending&origin=curator&limit=50", authed)
    )
    curator_list_body = json.loads(curator_listed.body.decode())
    assert {item["id"] for item in curator_list_body["proposals"]} == {"review_promote", "review_merge"}
    assert curator_list_body["proposals"][0]["origin"] == "curator"

    promote_applied = channel._handle_review_action(
        Request("/api/reviews/review_promote/apply?reason=ok", authed),
        "review_promote",
        "apply",
    )
    promote_apply_body = json.loads(promote_applied.body.decode())
    assert promote_apply_body["result"]["ok"] is True
    assert promote_apply_body["proposal"]["status"] == "applied"

    merge_apply = channel._handle_review_action(
        Request("/api/reviews/review_merge/apply?reason=later", authed),
        "review_merge",
        "apply",
    )
    merge_apply_body = json.loads(merge_apply.body.decode())
    assert merge_apply_body["result"]["ok"] is False
    assert merge_apply_body["result"]["status"] == "pending"
    assert merge_apply_body["proposal"]["status"] == "pending"


def test_webui_self_api_requires_token_and_returns_self_model(
    tmp_path,
    monkeypatch,
    bus: MagicMock,
) -> None:
    from websockets.datastructures import Headers
    from websockets.http11 import Request

    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)
    monkeypatch.setattr("OriginAgent.config.loader._current_config_path", config_path)

    channel = _ch(bus)
    channel._api_tokens["tok"] = time.monotonic() + 300
    authed = Headers([("Authorization", "Bearer tok")])

    denied = channel._handle_self(Request("/api/self", Headers([])))
    assert denied.status_code == 401

    allowed = channel._handle_self(Request("/api/self", authed))
    assert allowed.status_code == 200
    body = json.loads(allowed.body.decode())
    assert body["self_model"]["schema_version"] == 1
    assert body["self_model"]["identity"]["workspace_name"] == "workspace"
    assert body["self_model"]["runtime"]["confirmation_available"] is True


def test_webui_skill_lifecycle_api_requires_token_and_updates_workspace_skill(
    tmp_path,
    monkeypatch,
    bus: MagicMock,
) -> None:
    from websockets.datastructures import Headers
    from websockets.http11 import Request

    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)
    monkeypatch.setattr("OriginAgent.config.loader._current_config_path", config_path)

    skill_dir = workspace / "skills" / "lighting-troubleshooting"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: lighting-troubleshooting\n"
        "description: Lighting help.\n"
        "always: false\n"
        "metadata:\n"
        "  OriginAgent:\n"
        "    proposal_status: proposed\n"
        "    verification_status: unverified\n"
        "    review_proposal_id: review_skill\n"
        "    created_by: background_review\n"
        "---\n\n# Lighting\n\nUse this skill. api_key=sk-proj-secretsecretsecretsecret\n",
        encoding="utf-8",
    )

    channel = _ch(bus)
    channel._api_tokens["tok"] = time.monotonic() + 300
    authed = Headers([("Authorization", "Bearer tok")])

    denied = channel._handle_skills_list(Request("/api/skills", Headers([])))
    assert denied.status_code == 401

    listed = channel._handle_skills_list(
        Request("/api/skills?source=workspace&status=proposed&limit=50", authed)
    )
    assert listed.status_code == 200
    list_body = json.loads(listed.body.decode())
    assert list_body["stats"]["workspace_skills_count"] == 1
    assert list_body["skills"][0]["name"] == "lighting-troubleshooting"
    assert list_body["skills"][0]["verification_status"] == "unverified"
    assert "sk-proj" not in list_body["skills"][0]["body_preview"]

    detail = channel._handle_skill_detail(
        Request("/api/skills/lighting-troubleshooting", authed),
        "lighting-troubleshooting",
    )
    assert detail.status_code == 200
    assert json.loads(detail.body.decode())["skill"]["lifecycle_status"] == "proposed"

    verified = channel._handle_skill_action(
        Request("/api/skills/lighting-troubleshooting/verify?reason=ok", authed),
        "lighting-troubleshooting",
        "verify",
    )
    assert verified.status_code == 200
    verify_body = json.loads(verified.body.decode())
    assert verify_body["result"]["ok"] is True
    assert verify_body["skill"]["verification_status"] == "verified"

    activated = channel._handle_skill_action(
        Request("/api/skills/lighting-troubleshooting/activate", authed),
        "lighting-troubleshooting",
        "activate",
    )
    assert json.loads(activated.body.decode())["skill"]["lifecycle_status"] == "active"

    always = channel._handle_skill_action(
        Request("/api/skills/lighting-troubleshooting/always?enabled=true", authed),
        "lighting-troubleshooting",
        "always",
    )
    always_body = json.loads(always.body.decode())
    assert always_body["skill"]["effective_always"] is True


def test_domains_api_lists_details_and_actions_with_auth(
    tmp_path,
    monkeypatch,
    bus: MagicMock,
) -> None:
    from urllib.parse import quote

    from websockets.datastructures import Headers
    from websockets.http11 import Request

    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)
    monkeypatch.setattr("OriginAgent.config.loader._current_config_path", config_path)

    source = tmp_path / "research-pack"
    source.mkdir(parents=True)
    (source / "domain_pack.yaml").write_text(
        "id: research\n"
        "name: Research\n"
        "version: 0.1.0\n",
        encoding="utf-8",
    )
    (source / "CAPABILITIES.md").write_text("# Research\n", encoding="utf-8")

    channel = _ch(bus)
    channel._api_tokens["tok"] = time.monotonic() + 300
    authed = Headers([("Authorization", "Bearer tok")])

    denied = channel._handle_domains_list(Request("/api/domains", Headers([])))
    assert denied.status_code == 401

    installed = channel._handle_domains_install(
        Request(f"/api/domains/install?source={quote(str(source), safe='')}&reason=seed", authed)
    )
    assert installed.status_code == 200
    install_body = json.loads(installed.body.decode())
    assert install_body["result"]["ok"] is True
    assert install_body["domain"]["id"] == "research"

    listed = channel._handle_domains_list(
        Request("/api/domains?source=workspace&status=available&limit=50", authed)
    )
    assert listed.status_code == 200
    list_body = json.loads(listed.body.decode())
    assert list_body["stats"]["workspace_domain_pack_count"] == 1
    assert list_body["domains"][0]["id"] == "research"

    detail = channel._handle_domain_detail(
        Request("/api/domains/research", authed),
        "research",
    )
    assert detail.status_code == 200
    detail_body = json.loads(detail.body.decode())
    assert detail_body["domain"]["status"] == "available"

    activated = channel._handle_domain_action(
        Request("/api/domains/research/activate?reason=useful", authed),
        "research",
        "activate",
    )
    assert activated.status_code == 200
    activated_body = json.loads(activated.body.decode())
    assert activated_body["result"]["ok"] is True
    assert activated_body["domain"]["active_requested"] is True

    evaluated = channel._handle_domain_action(
        Request("/api/domains/research/eval", authed),
        "research",
        "eval",
    )
    assert evaluated.status_code == 200
    eval_body = json.loads(evaluated.body.decode())
    assert eval_body["result"]["ok"] is True
    assert eval_body["result"]["eval_result"]["status"] == "ok"
