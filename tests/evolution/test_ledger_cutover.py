"""Regression tests for production ledger cutover."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from OriginAgent.evolution.ledger_factory import create_ledger, migrate_if_needed
from OriginAgent.evolution.ledger import EvolutionLedger
from OriginAgent.evolution.ledger_sqlite import SqliteEvolutionLedger


class TestLedgerFactory:
    def test_default_creates_sqlite(self, tmp_path: Path) -> None:
        ledger = create_ledger(workspace=tmp_path)
        assert isinstance(ledger, SqliteEvolutionLedger)

    def test_jsonl_backend_creates_jsonl(self, tmp_path: Path) -> None:
        config = MagicMock(ledger_backend="jsonl")
        ledger = create_ledger(workspace=tmp_path, config=config)
        assert isinstance(ledger, EvolutionLedger)

    def test_missing_config_defaults_to_sqlite(self, tmp_path: Path) -> None:
        ledger = create_ledger(workspace=tmp_path, config=None)
        assert isinstance(ledger, SqliteEvolutionLedger)


class TestAutoMigration:
    def test_no_migration_when_db_exists(self, tmp_path: Path) -> None:
        # Create SQLite ledger first
        SqliteEvolutionLedger(workspace=tmp_path).close()
        result = migrate_if_needed(workspace=tmp_path)
        assert result is None

    def test_no_migration_when_no_jsonl(self, tmp_path: Path) -> None:
        result = migrate_if_needed(workspace=tmp_path)
        assert result is None

    def test_migration_from_existing_jsonl(self, tmp_path: Path) -> None:
        from OriginAgent.evolution.events import EventType, EvolutionEvent

        # Create JSONL ledger with events
        jsonl = EvolutionLedger(workspace=tmp_path)
        jsonl.append(EvolutionEvent.new(
            EventType.MODULE_VERIFIED,
            actor_public_key="test-key",
            artifact_digest="digest-1",
            result={"test": True},
        ))
        jsonl.append(EvolutionEvent.new(
            EventType.MODULE_VERIFIED,
            actor_public_key="test-key",
            artifact_digest="digest-2",
            result={"test": True},
        ))

        result = migrate_if_needed(workspace=tmp_path)
        assert result is not None
        assert isinstance(result, SqliteEvolutionLedger)
        verification = result.verify_chain()
        assert verification.chain_integrity == "ok"
        assert verification.event_count == 2
