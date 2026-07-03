"""Factory for creating the configured evolution ledger backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from OriginAgent.evolution.identity import EvolutionIdentityStore
from OriginAgent.evolution.ledger import EvolutionLedger
from OriginAgent.evolution.ledger_sqlite import SqliteEvolutionLedger


def create_ledger(
    workspace: Path,
    *,
    config: Any | None = None,
    identity_store: EvolutionIdentityStore | None = None,
    sign_events: bool = False,
) -> EvolutionLedger | SqliteEvolutionLedger:
    """Create the configured evolution ledger backend.

    Reads ``ledger_backend`` from config (default ``"sqlite"``).
    Falls back to JSONL if config is unavailable or explicitly set to ``"jsonl"``.
    """
    backend = "sqlite"
    if config is not None:
        backend = getattr(config, "ledger_backend", "sqlite") or "sqlite"

    if backend == "jsonl":
        return EvolutionLedger(
            workspace=workspace,
            identity_store=identity_store,
            sign_events=sign_events,
        )

    return SqliteEvolutionLedger(
        workspace=workspace,
        identity_store=identity_store,
        sign_events=sign_events,
    )


def migrate_if_needed(
    workspace: Path,
    *,
    config: Any | None = None,
    identity_store: EvolutionIdentityStore | None = None,
) -> SqliteEvolutionLedger | None:
    """Migrate from JSONL to SQLite if the JSONL file exists and SQLite doesn't.

    Returns the SQLite ledger if migration occurred, None if already on SQLite.
    Raises ValueError if the JSONL chain is broken.
    """
    backend = "sqlite"
    if config is not None:
        backend = getattr(config, "ledger_backend", "sqlite") or "sqlite"

    if backend != "sqlite":
        return None

    jsonl_path = workspace / "memory" / "evolution_events.jsonl"
    db_path = workspace / "memory" / "evolution_ledger.sqlite3"

    if db_path.exists() or not jsonl_path.exists():
        return None

    return SqliteEvolutionLedger.migrate_from_jsonl(
        workspace=workspace,
        jsonl_path=jsonl_path,
        db_path=db_path,
        identity_store=identity_store,
    )
