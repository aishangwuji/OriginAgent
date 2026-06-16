from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from OriginAgent.agent.identity import ActorResolver
from OriginAgent.agent.snapshot_inspection import SnapshotInspectionService
from OriginAgent.agent.world_state import WorldStateManager
from OriginAgent.providers.base import LLMResponse
from OriginAgent.session.manager import SessionManager


_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class _Provider:
    supports_vision = True

    def __init__(self) -> None:
        self.messages = []

    async def chat_with_retry(self, *, messages, model=None):
        self.messages = messages
        return LLMResponse(
            content='{"confirmed":["image present"],"new_details":["a tiny png"],"confidence":0.8,"status":"completed"}'
        )


class _TextProvider(_Provider):
    supports_vision = False


def _runtime_context():
    return ActorResolver().resolve_runtime_context(
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        metadata={},
        session_key="cli:direct",
    )


@pytest.mark.asyncio
async def test_snapshot_inspection_sends_image_content_for_vision_provider(tmp_path: Path) -> None:
    media_dir = tmp_path / "uploads" / "perception"
    media_dir.mkdir(parents=True)
    (media_dir / "frame.png").write_bytes(_PNG_BYTES)
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:direct")
    runtime_context = _runtime_context()
    world = WorldStateManager(tmp_path, sessions)
    snapshot = world.ingest_media(
        session,
        runtime_context=runtime_context,
        media_paths=["uploads/perception/frame.png"],
    )
    provider = _Provider()
    service = SnapshotInspectionService(world_state=world, provider=provider, workspace=tmp_path)

    result = await service.inspect(
        session,
        runtime_context=runtime_context,
        snapshot_id=snapshot.snapshots[-1].snapshot_id,
        requested_by="test",
    )

    content = provider.messages[0]["content"]
    assert isinstance(content, list)
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert result["inspection"]["inspection_mode"] == "vision"
    assert result["inspection"]["source_mime_type"] == "image/png"


@pytest.mark.asyncio
async def test_snapshot_inspection_marks_text_fallback_for_text_only_provider(tmp_path: Path) -> None:
    media_dir = tmp_path / "uploads" / "perception"
    media_dir.mkdir(parents=True)
    (media_dir / "frame.png").write_bytes(_PNG_BYTES)
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:direct")
    runtime_context = _runtime_context()
    world = WorldStateManager(tmp_path, sessions)
    snapshot = world.ingest_media(
        session,
        runtime_context=runtime_context,
        media_paths=["uploads/perception/frame.png"],
    )
    provider = _TextProvider()
    service = SnapshotInspectionService(world_state=world, provider=provider, workspace=tmp_path)

    result = await service.inspect(
        session,
        runtime_context=runtime_context,
        snapshot_id=snapshot.snapshots[-1].snapshot_id,
        requested_by="test",
    )

    assert isinstance(provider.messages[0]["content"], str)
    assert result["inspection"]["inspection_mode"] == "text_fallback"
    assert result["inspection"]["failure_reason"] == "provider_vision_unsupported"
