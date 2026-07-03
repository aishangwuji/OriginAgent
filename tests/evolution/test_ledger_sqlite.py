"""Tests for SQLite-backed evolution ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from OriginAgent.evolution.events import EventType, EvolutionEvent
from OriginAgent.evolution.ledger import EvolutionLedger
from OriginAgent.evolution.ledger_sqlite import SqliteEvolutionLedger


def _make_event(event_id: str, **overrides) -> EvolutionEvent:
    return EvolutionEvent(
        event_id=event_id,
        event_type=overrides.pop("event_type", EventType.MODULE_VERIFIED),
        actor_public_key=overrides.pop("actor_public_key", "test-key"),
        artifact_digest=overrides.pop("artifact_digest", f"digest-{event_id}"),
        created_at=overrides.pop("created_at", "2026-01-01T00:00:00+00:00"),
        **overrides,
    )


class TestSqliteLedgerAppendAndVerify:
    def test_empty_ledger_has_ok_chain(self, tmp_path: Path) -> None:
        ledger = SqliteEvolutionLedger(workspace=tmp_path)
        result = ledger.verify_chain()
        assert result.chain_integrity == "ok"
        assert result.event_count == 0

    def test_single_event_has_valid_chain(self, tmp_path: Path) -> None:
        ledger = SqliteEvolutionLedger(workspace=tmp_path)
        ledger.append(_make_event("evt-1"))
        result = ledger.verify_chain()
        assert result.chain_integrity == "ok"
        assert result.event_count == 1

    def test_three_events_form_valid_chain(self, tmp_path: Path) -> None:
        ledger = SqliteEvolutionLedger(workspace=tmp_path)
        ledger.append(_make_event("evt-1"))
        ledger.append(_make_event("evt-2"))
        ledger.append(_make_event("evt-3"))
        result = ledger.verify_chain()
        assert result.chain_integrity == "ok"
        assert result.event_count == 3

    def test_hash_chain_is_sequential(self, tmp_path: Path) -> None:
        ledger = SqliteEvolutionLedger(workspace=tmp_path)
        e1 = ledger.append(_make_event("evt-1"))
        e2 = ledger.append(_make_event("evt-2"))
        assert e2.previous_event_hash == e1.event_hash

    def test_close_and_reopen_preserves_data(self, tmp_path: Path) -> None:
        db_path = tmp_path / "ledger.db"
        ledger = SqliteEvolutionLedger(workspace=tmp_path, db_path=db_path)
        ledger.append(_make_event("evt-1"))
        ledger.append(_make_event("evt-2"))
        ledger.close()

        # Reopen
        ledger2 = SqliteEvolutionLedger(workspace=tmp_path, db_path=db_path)
        result = ledger2.verify_chain()
        assert result.chain_integrity == "ok"
        assert result.event_count == 2


class TestJsonlToSqliteMigration:
    def test_migration_preserves_hash_chain(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        memory = workspace / "memory"
        memory.mkdir(parents=True)
        jsonl_path = memory / "evolution_events.jsonl"

        # Populate JSONL ledger
        jsonl_ledger = EvolutionLedger(workspace=workspace, event_path=jsonl_path)
        jsonl_ledger.append(_make_event("evt-1"))
        jsonl_ledger.append(_make_event("evt-2"))
        jsonl_ledger.append(_make_event("evt-3"))

        # Verify source chain
        src_verification = jsonl_ledger.verify_chain()
        assert src_verification.chain_integrity == "ok"

        # Migrate
        db_path = memory / "evolution_ledger.sqlite3"
        sqlite_ledger = SqliteEvolutionLedger.migrate_from_jsonl(
            workspace=workspace,
            jsonl_path=jsonl_path,
            db_path=db_path,
        )

        # Verify migrated chain
        result = sqlite_ledger.verify_chain()
        assert result.chain_integrity == "ok"
        assert result.event_count == 3
        assert result.terminal_event_hash == src_verification.terminal_event_hash

    def test_migration_refuses_broken_chain(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        memory = workspace / "memory"
        memory.mkdir(parents=True)
        jsonl_path = memory / "evolution_events.jsonl"

        # Write two events with a broken hash chain
        e1 = _make_event("evt-1")
        e2 = _make_event("evt-2", previous_event_hash="wrong-hash")
        from OriginAgent.evolution.ledger import canonical_dump, compute_event_hash

        from dataclasses import replace

        # Event 1: valid
        h1 = compute_event_hash(e1.to_dict())
        e1_final = replace(e1, event_hash=h1)
        jsonl_path.write_text(
            canonical_dump(e1_final.to_dict()).decode("utf-8") + "\n",
            encoding="utf-8",
        )

        # Event 2: broken previous hash
        e2_broken = replace(e2, previous_event_hash="broken-chain-hash")
        h2 = compute_event_hash(e2_broken.to_dict())
        e2_final = replace(e2_broken, event_hash=h2)
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(canonical_dump(e2_final.to_dict()).decode("utf-8") + "\n")

        db_path = memory / "evolution_ledger.sqlite3"
        with pytest.raises(ValueError, match="Source JSONL ledger chain is broken"):
            SqliteEvolutionLedger.migrate_from_jsonl(
                workspace=workspace,
                jsonl_path=jsonl_path,
                db_path=db_path,
            )
