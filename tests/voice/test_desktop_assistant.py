"""Tests for the desktop voice assistant client.

These tests mock WebSocket and audio capture so they run without a live
gateway, microphone, or Volcengine credentials.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from OriginAgent.voice.desktop_assistant import DesktopVoiceAssistant


class TestDesktopVoiceAssistantInit:
    def test_init_stores_ws_url_and_chat_id(self):
        assistant = DesktopVoiceAssistant(
            ws_url="ws://example.com:1234", chat_id="mychat"
        )
        assert assistant._ws_url == "ws://example.com:1234"
        assert assistant._chat_id == "mychat"

    def test_init_default_ws_url_derived_from_gateway_config(self):
        # The default ws_url must be derived from GatewayConfig (host=127.0.0.1,
        # port=18790) rather than the legacy hardcoded 8765.
        assistant = DesktopVoiceAssistant()
        assert assistant._ws_url == "ws://127.0.0.1:18790"
        # chat_id defaults to None and is assigned on connect
        assert assistant._chat_id is None

    def test_init_does_not_use_legacy_port(self):
        assistant = DesktopVoiceAssistant()
        assert ":8765" not in assistant._ws_url


class TestPlayTts:
    @pytest.fixture
    def assistant(self):
        return DesktopVoiceAssistant(ws_url="ws://x:1", chat_id="c")

    @pytest.mark.asyncio
    async def test_downloads_and_plays_http_url(self, assistant, monkeypatch):
        """_play_tts_url downloads an HTTP audio URL and plays it via AudioPlayback."""
        played: list[Path] = []
        monkeypatch.setattr(
            assistant._playback, "play", lambda p: played.append(p) or True
        )

        class FakeResp:
            content = b"audio-bytes"

            def raise_for_status(self) -> None:
                pass

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                return FakeResp()

        monkeypatch.setattr(httpx, "AsyncClient", lambda: FakeClient())

        await assistant._play_tts_url("http://example.com/a.wav")

        assert len(played) == 1, "playback must be invoked once"
        # The temp file is cleaned up after playback, so only assert the call happened.
        assert all(isinstance(p, Path) for p in played)

    @pytest.mark.asyncio
    async def test_plays_base64_data_url_without_httpx(self, assistant, monkeypatch):
        """A base64 data URL is decoded locally (no network) and played."""
        played: list[Path] = []
        monkeypatch.setattr(
            assistant._playback, "play", lambda p: played.append(p) or True
        )

        # Track that httpx is NOT used for data URLs.
        client_calls: list[str] = []

        class SpyClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                client_calls.append(url)
                raise AssertionError("httpx must not be used for data URLs")

        monkeypatch.setattr(httpx, "AsyncClient", lambda: SpyClient())

        url = (
            "data:audio/wav;base64,"
            "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEA+AAACABAAZGF0YQAAAAA="
        )
        await assistant._play_tts_url(url)

        assert len(played) == 1
        assert client_calls == []

    @pytest.mark.asyncio
    async def test_handles_download_failure(self, assistant, monkeypatch):
        """When httpx raises, _play_tts_url logs a warning and does not raise."""
        played: list[Path] = []
        monkeypatch.setattr(
            assistant._playback, "play", lambda p: played.append(p)
        )

        class FailingClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                raise httpx.ConnectError("boom")

        monkeypatch.setattr(httpx, "AsyncClient", lambda: FailingClient())

        # Must not raise.
        await assistant._play_tts_url("http://example.com/a.wav")

        assert played == [], "no playback should be attempted on download failure"

    @pytest.mark.asyncio
    async def test_unsupported_url_format_does_not_raise(self, assistant, monkeypatch):
        played: list[Path] = []
        monkeypatch.setattr(
            assistant._playback, "play", lambda p: played.append(p)
        )

        # Neither a data URL nor an http(s) URL.
        await assistant._play_tts_url("ftp://example.com/a.wav")

        assert played == []


class TestMainFunction:
    def test_main_accepts_ws_url_and_chat_id(self, monkeypatch):
        """main() parses --ws-url and --chat-id and forwards them to the assistant."""
        captured: dict = {}

        class FakeAssistant:
            def __init__(self, *, ws_url=None, chat_id=None):
                captured["ws_url"] = ws_url
                captured["chat_id"] = chat_id

            async def run(self):
                captured["ran"] = True

        # Patch the symbols used inside main()'s module.
        import OriginAgent.voice.desktop_assistant as mod

        monkeypatch.setattr(mod, "DesktopVoiceAssistant", FakeAssistant)

        def fake_run(coro):
            # Close the coroutine without actually running the event loop.
            coro.close()
            captured["scheduled"] = True

        monkeypatch.setattr(mod.asyncio, "run", fake_run)
        monkeypatch.setattr(
            sys, "argv", ["desktop_assistant", "--ws-url", "ws://h:4321", "--chat-id", "chatX"]
        )

        mod.main()

        assert captured["ws_url"] == "ws://h:4321"
        assert captured["chat_id"] == "chatX"
        assert captured.get("scheduled") is True

    def test_main_defaults_when_no_args(self, monkeypatch):
        captured: dict = {}

        class FakeAssistant:
            def __init__(self, *, ws_url=None, chat_id=None):
                captured["ws_url"] = ws_url
                captured["chat_id"] = chat_id

            async def run(self):
                captured["ran"] = True

        import OriginAgent.voice.desktop_assistant as mod

        monkeypatch.setattr(mod, "DesktopVoiceAssistant", FakeAssistant)

        def fake_run(coro):
            coro.close()

        monkeypatch.setattr(mod.asyncio, "run", fake_run)
        monkeypatch.setattr(sys, "argv", ["desktop_assistant"])

        mod.main()

        # Default fallback (hardcoded per task spec).
        assert captured["ws_url"] == "ws://127.0.0.1:18790"
        assert captured["chat_id"] is None
