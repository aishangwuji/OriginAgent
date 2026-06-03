"""Tests for web_fetch SSRF protection and untrusted content marking."""

from __future__ import annotations

import json
import socket
from unittest.mock import patch

import pytest

from OriginAgent.agent.tools.web import WebFetchTool
from OriginAgent.config.schema import ContentReadToolConfig, WebFetchConfig
from OriginAgent.integrations.content_read.reader import ContentReader
from OriginAgent.integrations.content_read.types import ContentReadResult


def _fake_resolve_private(hostname, port, family=0, type_=0, proto=0, flags=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))]


def _fake_resolve_public(hostname, port, family=0, type_=0, proto=0, flags=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


@pytest.mark.asyncio
async def test_web_fetch_auto_routes_known_platform_to_structured_provider(monkeypatch):
    tool = WebFetchTool(config=WebFetchConfig(use_jina_reader=False))
    seen: dict[str, str] = {}

    async def fake_read(self, url: str, provider: str = "auto") -> ContentReadResult:
        seen["url"] = url
        seen["provider"] = provider
        return ContentReadResult(
            source_type="github",
            title="owner/repo",
            url=url,
            content="abcdef",
            metadata={"stars": 1},
        )

    monkeypatch.setattr(ContentReader, "read", fake_read)

    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(
            url="https://github.com/owner/repo",
            provider="auto",
            max_chars=3,
        )

    data = json.loads(result)
    assert seen == {"url": "https://github.com/owner/repo", "provider": "github"}
    assert data["extractor"] == "content_read:github"
    assert data["source_type"] == "github"
    assert data["content"] == "abc"
    assert data["truncated"] is True
    assert data["metadata"] == {"stars": 1}
    assert data["untrusted"] is True
    assert "[External content" in data["text"]


@pytest.mark.asyncio
async def test_web_fetch_provider_generic_forces_original_fetch_path(monkeypatch):
    tool = WebFetchTool(config=WebFetchConfig(use_jina_reader=False))

    async def fail_structured(*args, **kwargs):
        raise AssertionError("provider=generic must not call content_read providers")

    class FakeStreamResponse:
        headers = {"content-type": "text/html"}
        url = "https://github.com/owner/repo"
        status_code = 200
        encoding = "utf-8"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"<html><head><title>Repo</title></head><body><p>Hello</p></body></html>"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            return FakeStreamResponse()

    monkeypatch.setattr(ContentReader, "read", fail_structured)
    monkeypatch.setattr("OriginAgent.agent.tools.web.httpx.AsyncClient", FakeClient)

    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(
            url="https://github.com/owner/repo",
            provider="generic",
        )

    data = json.loads(result)
    assert data["extractor"] == "readability"
    assert data["untrusted"] is True


@pytest.mark.asyncio
async def test_web_fetch_auto_keeps_structured_providers_when_legacy_tool_disabled(monkeypatch):
    tool = WebFetchTool(
        config=WebFetchConfig(use_jina_reader=False),
        content_read_config=ContentReadToolConfig(enabled=False),
    )
    seen: dict[str, str] = {}

    async def fake_read(self, url: str, provider: str = "auto") -> ContentReadResult:
        seen["url"] = url
        seen["provider"] = provider
        return ContentReadResult(
            source_type="github",
            title="owner/repo",
            url=url,
            content="structured",
            metadata={},
        )

    monkeypatch.setattr(ContentReader, "read", fake_read)

    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url="https://github.com/owner/repo")

    data = json.loads(result)
    assert seen == {"url": "https://github.com/owner/repo", "provider": "github"}
    assert data["extractor"] == "content_read:github"
    assert data["content"] == "structured"
    assert data["untrusted"] is True


@pytest.mark.asyncio
async def test_web_fetch_unknown_provider_returns_clear_error():
    tool = WebFetchTool()

    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url="https://example.com/page", provider="unknown")

    data = json.loads(result)
    assert "Unsupported web_fetch provider" in data["error"]


@pytest.mark.asyncio
async def test_web_fetch_unknown_mode_returns_clear_error():
    tool = WebFetchTool()

    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url="https://example.com/page", mode="unknown")

    data = json.loads(result)
    assert "Unsupported web_fetch mode" in data["error"]


@pytest.mark.asyncio
async def test_web_fetch_structured_mode_uses_generic_content_reader(monkeypatch):
    tool = WebFetchTool(config=WebFetchConfig(use_jina_reader=False))
    seen: dict[str, str] = {}

    async def fake_read(self, url: str, provider: str = "auto") -> ContentReadResult:
        seen["url"] = url
        seen["provider"] = provider
        return ContentReadResult(
            source_type="web",
            title="Example",
            url=url,
            content="generic content",
            metadata={"extractor": "fake"},
        )

    monkeypatch.setattr(ContentReader, "read", fake_read)

    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(
            url="https://example.com/page",
            mode="structured",
            provider="generic",
        )

    data = json.loads(result)
    assert seen == {"url": "https://example.com/page", "provider": "generic"}
    assert data["extractor"] == "content_read:web"
    assert data["source_type"] == "web"
    assert data["content"] == "generic content"
    assert data["untrusted"] is True


@pytest.mark.asyncio
async def test_web_fetch_web_mode_skips_structured_provider_on_known_platform(monkeypatch):
    tool = WebFetchTool(config=WebFetchConfig(use_jina_reader=False))

    async def fail_structured(*args, **kwargs):
        raise AssertionError("mode=web must not call content_read providers")

    class FakeStreamResponse:
        headers = {"content-type": "text/html"}
        url = "https://github.com/owner/repo"
        status_code = 200
        encoding = "utf-8"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"<html><head><title>Repo</title></head><body><p>Hello</p></body></html>"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            return FakeStreamResponse()

    monkeypatch.setattr(ContentReader, "read", fail_structured)
    monkeypatch.setattr("OriginAgent.agent.tools.web.httpx.AsyncClient", FakeClient)

    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url="https://github.com/owner/repo", mode="web")

    data = json.loads(result)
    assert data["extractor"] == "readability"
    assert data["untrusted"] is True


@pytest.mark.asyncio
async def test_web_fetch_blocks_private_ip():
    tool = WebFetchTool()
    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_private):
        result = await tool.execute(url="http://169.254.169.254/computeMetadata/v1/")
    data = json.loads(result)
    assert "error" in data
    assert "private" in data["error"].lower() or "blocked" in data["error"].lower()


@pytest.mark.asyncio
async def test_web_fetch_blocks_localhost():
    tool = WebFetchTool()
    def _resolve_localhost(hostname, port, family=0, type_=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]
    with patch("OriginAgent.security.network.socket.getaddrinfo", _resolve_localhost):
        result = await tool.execute(url="http://localhost/admin")
    data = json.loads(result)
    assert "error" in data


@pytest.mark.asyncio
async def test_web_fetch_result_contains_untrusted_flag():
    """When fetch succeeds, result JSON must include untrusted=True and the banner."""
    tool = WebFetchTool()

    fake_html = "<html><head><title>Test</title></head><body><p>Hello world</p></body></html>"


    class FakeStreamResponse:
        status_code = 200
        url = "https://example.com/page"
        headers = {"content-type": "text/html"}
        encoding = "utf-8"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self): pass

        async def aiter_bytes(self):
            yield fake_html.encode("utf-8")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            return FakeStreamResponse()

    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_public), \
         patch("OriginAgent.agent.tools.web.httpx.AsyncClient", FakeClient):
        result = await tool.execute(url="https://example.com/page")

    data = json.loads(result)
    assert data.get("untrusted") is True
    assert "[External content" in data.get("text", "")


@pytest.mark.asyncio
async def test_web_fetch_can_skip_jina_and_use_custom_user_agent(monkeypatch):
    tool = WebFetchTool(
        config=WebFetchConfig(use_jina_reader=False),
        user_agent="OriginAgent-test-agent",
    )
    seen_headers: list[dict] = []

    async def _fail_jina(*args, **kwargs):
        raise AssertionError("Jina Reader should be skipped when disabled")

    class FakeStreamResponse:
        headers = {"content-type": "text/html"}
        url = "https://example.com/page"
        status_code = 200
        encoding = "utf-8"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"<html><head><title>Test</title></head><body><p>Hello world</p></body></html>"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            seen_headers.append(headers or {})
            return FakeStreamResponse()

    monkeypatch.setattr(tool, "_fetch_jina", _fail_jina)
    monkeypatch.setattr("OriginAgent.agent.tools.web.httpx.AsyncClient", FakeClient)

    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url="https://example.com/page")

    data = json.loads(result)
    assert data["extractor"] == "readability"
    assert [headers["User-Agent"] for headers in seen_headers] == [
        "OriginAgent-test-agent",
        "OriginAgent-test-agent",
    ]


@pytest.mark.asyncio
async def test_web_fetch_blocks_private_redirect_before_returning_image(monkeypatch):
    tool = WebFetchTool()

    class FakeStreamResponse:
        headers = {"content-type": "image/png"}
        url = "http://127.0.0.1/secret.png"
        status_code = 200
        content = b"\x89PNG\r\n\x1a\n"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aread(self):
            return self.content

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield self.content

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            return FakeStreamResponse()

    monkeypatch.setattr("OriginAgent.agent.tools.web.httpx.AsyncClient", FakeClient)

    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url="https://example.com/image.png")

    data = json.loads(result)
    assert "error" in data
    assert "redirect blocked" in data["error"].lower()


@pytest.mark.asyncio
async def test_web_fetch_readability_stream_rejects_oversize_binary(monkeypatch):
    from OriginAgent.agent.tools.limits import ToolLimits
    from OriginAgent.security.policy import PolicyDeniedError

    tool = WebFetchTool(
        config=WebFetchConfig(use_jina_reader=False),
        limits=ToolLimits(web_fetch_max_bytes=4),
    )

    class FakeStreamResponse:
        headers = {"content-type": "application/octet-stream"}
        url = "https://example.com/blob"
        status_code = 200
        encoding = "utf-8"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"123"
            yield b"45"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            return FakeStreamResponse()

    monkeypatch.setattr("OriginAgent.agent.tools.web.httpx.AsyncClient", FakeClient)

    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_public):
        with pytest.raises(PolicyDeniedError) as exc:
            await tool.execute(url="https://example.com/blob")

    assert exc.value.policy_rule == "web_fetch_binary_max_bytes"


@pytest.mark.asyncio
async def test_web_fetch_stream_rejects_oversize_image(monkeypatch):
    from OriginAgent.agent.tools.limits import ToolLimits
    from OriginAgent.security.policy import PolicyDeniedError

    tool = WebFetchTool(
        config=WebFetchConfig(use_jina_reader=False),
        limits=ToolLimits(web_fetch_max_bytes=4),
    )
    chunks_read = 0

    class FakeStreamResponse:
        headers = {"content-type": "image/png"}
        url = "https://example.com/image.png"
        status_code = 200
        encoding = "utf-8"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            nonlocal chunks_read
            for chunk in (b"123", b"45", b"SHOULD_NOT_READ"):
                chunks_read += 1
                yield chunk

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            return FakeStreamResponse()

    monkeypatch.setattr("OriginAgent.agent.tools.web.httpx.AsyncClient", FakeClient)

    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_public):
        with pytest.raises(PolicyDeniedError) as exc:
            await tool.execute(url="https://example.com/image.png")

    assert exc.value.policy_rule == "web_fetch_binary_max_bytes"
    assert chunks_read == 2


@pytest.mark.asyncio
async def test_web_fetch_stream_rejects_oversize_html_without_text_property(monkeypatch):
    from OriginAgent.agent.tools.limits import ToolLimits
    from OriginAgent.security.policy import PolicyDeniedError

    tool = WebFetchTool(
        config=WebFetchConfig(use_jina_reader=False),
        limits=ToolLimits(web_fetch_max_bytes=8),
    )

    class FakeStreamResponse:
        headers = {"content-type": "text/html"}
        url = "https://example.com/huge"
        status_code = 200
        encoding = "utf-8"

        @property
        def text(self):
            raise AssertionError("web_fetch must not use response.text for streamed bodies")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"<html>"
            yield b"<body>too large</body>"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            return FakeStreamResponse()

    monkeypatch.setattr("OriginAgent.agent.tools.web.httpx.AsyncClient", FakeClient)

    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve_public):
        with pytest.raises(PolicyDeniedError) as exc:
            await tool.execute(url="https://example.com/huge")

    assert exc.value.policy_rule == "web_fetch_binary_max_bytes"
