from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from OpenHome.agent.loop import AgentLoop
from OpenHome.agent.tools.content_read import ContentReadTool
from OpenHome.bus.queue import MessageBus
from OpenHome.config.schema import Config, ContentReadToolConfig
from OpenHome.integrations.content_read.reader import ContentReader
from OpenHome.integrations.content_read.types import ContentReadResult


def test_content_reader_detects_mvp_providers() -> None:
    reader = ContentReader()

    assert reader.detect_provider("https://github.com/iBigQiang/feedgrab") == "github"
    assert reader.detect_provider("https://news.ycombinator.com/item?id=1") == "hackernews"
    assert reader.detect_provider("https://example.com/feed.xml") == "rss"
    assert reader.detect_provider("https://example.com/article") == "generic"


@pytest.mark.asyncio
async def test_content_read_truncates_structured_payload(monkeypatch) -> None:
    async def fake_read(self, url: str, provider: str = "auto") -> ContentReadResult:
        return ContentReadResult(
            source_type="web",
            title="Example",
            url=url,
            content="abcdef",
            metadata={"extractor": "fake"},
        )

    monkeypatch.setattr(ContentReader, "read", fake_read)
    tool = ContentReadTool(config=ContentReadToolConfig(use_jina_reader=False))

    result = await tool.execute("https://example.com/page", max_chars=3)
    data = json.loads(result)

    assert data["source_type"] == "web"
    assert data["content"] == "abc"
    assert data["truncated"] is True
    assert data["metadata"] == {"extractor": "fake"}
    assert data["untrusted"] is True


@pytest.mark.asyncio
async def test_content_read_rejects_non_http_url() -> None:
    tool = ContentReadTool()

    result = await tool.execute("ftp://example.com/file")
    data = json.loads(result)

    assert "error" in data
    assert "URL validation failed" in data["error"]


@pytest.mark.asyncio
async def test_content_read_reports_disabled_provider() -> None:
    tool = ContentReadTool(config=ContentReadToolConfig(providers=["rss"]))

    result = await tool.execute("https://github.com/iBigQiang/feedgrab")
    data = json.loads(result)

    assert data["error"] == "content_read provider 'github' is disabled"


def test_agent_loop_registers_content_read_by_default(tmp_path) -> None:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=provider)

    assert "content_read" in loop.tools.tool_names
