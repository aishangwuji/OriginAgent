"""Tests for ForesightRecord → Desire auto-sync."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from OriginAgent.bdi.models import Desire, DesireStatus, DesirePriority, now_iso
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.deliberation import DeliberationEngine


class MockForesightRecord:
    def __init__(self, foresight_id, owner_id, session_key, content, end_at=None):
        self.foresight_id = foresight_id
        self.owner_id = owner_id
        self.session_key = session_key
        self.content = content
        self.end_at = end_at


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as td:
        yield DesireStore(Path(td))


class TestForesightSync:
    @pytest.mark.asyncio
    async def test_creates_desire_from_unlinked_foresight(self, store):
        """When a ForesightRecord exists without a corresponding Desire, create one."""
        mock_ms = MagicMock()
        mock_ms.list_foresights = MagicMock(return_value=[
            MockForesightRecord(
                foresight_id="fs_001",
                owner_id="user:test",
                session_key="sess:test",
                content="I'll review the PR by tomorrow",
                end_at="2026-06-27T18:00:00",
            )
        ])

        mock_provider = MagicMock()

        engine = DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=mock_provider,
            model="test",
            auto_create_from_foresight=True,
            enabled=True,
        )

        with patch("OriginAgent.memory.store.NearlineMemoryStore", return_value=mock_ms):
            await engine._sync_foresights([])

        found = store.find_by_source_foresight("fs_001")
        assert len(found) == 1
        assert found[0].content == "I'll review the PR by tomorrow"
        assert found[0].deadline_at == "2026-06-27T18:00:00"
        assert found[0].status == DesireStatus.PENDING

    @pytest.mark.asyncio
    async def test_skip_already_linked_foresight(self, store):
        """Don't create duplicate Desires for already-linked ForesightRecords."""
        store.add(Desire(
            desire_id="existing",
            owner_id="user:test",
            session_key="sess:test",
            content="Already tracked",
            source_foresight_id="fs_001",
            status=DesireStatus.ACTIVE,
        ))

        mock_ms = MagicMock()
        mock_ms.list_foresights = MagicMock(return_value=[
            MockForesightRecord(
                foresight_id="fs_001",
                owner_id="user:test",
                session_key="sess:test",
                content="I'll review the PR by tomorrow",
            )
        ])

        mock_provider = MagicMock()
        engine = DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=mock_provider,
            model="test",
            auto_create_from_foresight=True,
            enabled=True,
        )

        with patch("OriginAgent.memory.store.NearlineMemoryStore", return_value=mock_ms):
            await engine._sync_foresights([Desire(
                desire_id="existing",
                owner_id="user:test",
                session_key="sess:test",
                content="Already tracked",
                source_foresight_id="fs_001",
            )])

        found = store.find_by_source_foresight("fs_001")
        assert len(found) == 1

    @pytest.mark.asyncio
    async def test_sync_guards_against_flag(self, store):
        """When auto_create_from_foresight is False, _sync_foresights is not called by run_cycle."""
        mock_ms = MagicMock()
        mock_ms.list_foresights = MagicMock(return_value=[
            MockForesightRecord("fs_001", "u", "s", "Test")
        ])

        mock_provider = MagicMock()
        engine = DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=mock_provider,
            model="test",
            auto_create_from_foresight=False,
            enabled=True,
        )

        with patch("OriginAgent.memory.store.NearlineMemoryStore", return_value=mock_ms):
            result = await engine.run_cycle()

        # With no active desires and auto_create off, no desires should exist
        found = store.find_by_source_foresight("fs_001")
        assert len(found) == 0
