"""SQLite-backed evolution ledger with atomic hash chain integrity.

Replaces the JSONL+FileLock pattern with SQLite transactions.
Hash chain computation stays in Python (identity_store.sign() is a Python
service) -- SQLite provides only atomicity and durability guarantees.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from OriginAgent.evolution.events import EvolutionEvent
from OriginAgent.evolution.identity import EvolutionIdentityStore
from OriginAgent.evolution.ledger import (
    LEDGER_ROTATION_EVENT_THRESHOLD,
    EvolutionLedger,
    LedgerStatus,
    LedgerVerificationResult,
    canonical_dump,
    compute_event_hash,
)


class SqliteEvolutionLedger:
    """SQLite-backed append-only evolution event ledger.

    Maintains the same hash-chain integrity guarantees as the JSONL ledger:
    - Every event's hash depends on the previous event's hash
    - Signatures are verified via EvolutionIdentityStore
    - Atomic writes via SQLite BEGIN/COMMIT transactions
    """

    def __init__(
        self,
        workspace: Path,
        db_path: Path | None = None,
        identity_store: EvolutionIdentityStore | None = None,
        sign_events: bool = False,
        db_busy_timeout: int = 5000,
    ) -> None:
        self.workspace = Path(workspace)
        memory_dir = self.workspace / "memory"
        self.db_path = (
            Path(db_path) if db_path is not None
            else memory_dir / "evolution_ledger.sqlite3"
        )
        self._sign_events = sign_events
        self._identity_store = identity_store
        self._db_busy_timeout = db_busy_timeout  # SQLite busy timeout in ms (spec 3.4, rule 17)
        self._conn: sqlite3.Connection | None = None

    # -- Connection management -----------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=self._db_busy_timeout / 1000)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        self._ensure_schema(conn)
        self._conn = conn
        return self._conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(f"PRAGMA busy_timeout={self._db_busy_timeout}")  # spec 3.4, rule 17
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS evolution_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                actor_public_key TEXT NOT NULL DEFAULT '',
                artifact_digest TEXT NOT NULL DEFAULT '',
                previous_event_hash TEXT,
                event_hash TEXT NOT NULL DEFAULT '',
                signature TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                CHECK(length(event_hash) > 0)
            ) STRICT;
            CREATE INDEX IF NOT EXISTS idx_events_type
                ON evolution_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_digest
                ON evolution_events(artifact_digest);
        """)
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- Core API (same signatures as EvolutionLedger) ------------------------------

    def append(self, event: EvolutionEvent) -> EvolutionEvent:
        """Append an event with hash chain integrity in a single transaction."""
        from dataclasses import replace

        conn = self._get_conn()
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            # Get previous hash
            row = conn.execute(
                "SELECT event_hash FROM evolution_events "
                "ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            previous_hash = row["event_hash"] if row is not None else None

            # Compute event hash
            identity_store = self._identity_store
            actor_public_key = event.actor_public_key
            if self._sign_events:
                identity_store = identity_store or EvolutionIdentityStore()
                actor_public_key = identity_store.public_key_b64

            event_with_previous = replace(
                event,
                actor_public_key=actor_public_key,
                previous_event_hash=previous_hash,
                event_hash="",
                signature="",
            )
            event_hash = compute_event_hash(event_with_previous.to_dict())
            event_to_write = replace(event_with_previous, event_hash=event_hash)

            signature = ""
            if self._sign_events and identity_store is not None:
                signature = identity_store.sign(event_hash.encode("utf-8"))
                event_to_write = replace(event_to_write, signature=signature)

            # Atomic INSERT
            conn.execute(
                """INSERT INTO evolution_events
                   (event_id, event_type, actor_public_key, artifact_digest,
                    previous_event_hash, event_hash, signature, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_to_write.event_id,
                    event_to_write.event_type.value
                    if hasattr(event_to_write.event_type, 'value')
                    else str(event_to_write.event_type),
                    event_to_write.actor_public_key,
                    event_to_write.artifact_digest,
                    event_to_write.previous_event_hash,
                    event_to_write.event_hash,
                    event_to_write.signature or "",
                    canonical_dump(event_to_write.to_dict()).decode("utf-8"),
                ),
            )

            return event_to_write

    def verify_chain(
        self, verify_signatures: bool = False
    ) -> LedgerVerificationResult:
        """Verify the full hash chain from SQLite.

        Same O(n) algorithm as JSONL ledger -- reads all rows, validates
        each hash matches previous+payload.
        """
        conn = self._get_conn()
        previous_hash: str | None = None
        terminal_hash: str | None = None
        count = 0
        unsigned_count = 0
        invalid_signature_count = 0

        rows = conn.execute(
            "SELECT * FROM evolution_events ORDER BY rowid"
        ).fetchall()

        for row in rows:
            count += 1
            event = dict(row)

            # Normalize: treat None and "" as equivalent for previous_event_hash
            row_prev = event.get("previous_event_hash") or ""
            expected_prev = previous_hash or ""
            if row_prev != expected_prev:
                return LedgerVerificationResult(
                    chain_integrity="broken",
                    event_count=count,
                    terminal_event_hash=terminal_hash,
                    broken_at_index=count - 1,
                    broken_line=count,
                    expected_hash=expected_prev,
                    actual_hash=row_prev,
                    error="previous_event_hash mismatch",
                    unsigned_event_count=unsigned_count,
                    invalid_signature_count=invalid_signature_count,
                )

            payload = json.loads(event.get("payload_json", "{}"))
            expected_hash = compute_event_hash(payload)
            if event.get("event_hash") != expected_hash:
                return LedgerVerificationResult(
                    chain_integrity="broken",
                    event_count=count,
                    terminal_event_hash=terminal_hash,
                    broken_at_index=count - 1,
                    broken_line=count,
                    expected_hash=expected_hash,
                    actual_hash=event.get("event_hash"),
                    error="event_hash mismatch",
                    unsigned_event_count=unsigned_count,
                    invalid_signature_count=invalid_signature_count,
                )

            if verify_signatures and event.get("signature"):
                if not self._verify_row_signature(event):
                    invalid_signature_count += 1
            elif not event.get("signature"):
                unsigned_count += 1

            previous_hash = event["event_hash"]
            terminal_hash = previous_hash

        return LedgerVerificationResult(
            chain_integrity="ok",
            event_count=count,
            terminal_event_hash=terminal_hash,
            unsigned_event_count=unsigned_count,
            invalid_signature_count=invalid_signature_count,
        )

    def _verify_row_signature(self, event: dict) -> bool:
        from OriginAgent.evolution.identity import verify_event_signature
        return verify_event_signature(event)

    def status(self, verify_signatures: bool = False) -> LedgerStatus:
        verification = self.verify_chain(verify_signatures=verify_signatures)
        return LedgerStatus(
            chain_integrity=verification.chain_integrity,
            event_count=verification.event_count,
            terminal_event_hash=verification.terminal_event_hash,
            event_path=str(self.db_path),
            rotation_recommended=(
                verification.event_count > LEDGER_ROTATION_EVENT_THRESHOLD
            ),
            unsigned_event_count=verification.unsigned_event_count,
            invalid_signature_count=verification.invalid_signature_count,
            error=verification.error,
        )

    # -- Migration ----------------------------------------------------------------

    @classmethod
    def migrate_from_jsonl(
        cls,
        workspace: Path,
        jsonl_path: Path | None = None,
        db_path: Path | None = None,
        *,
        identity_store: EvolutionIdentityStore | None = None,
        sign_events: bool = False,
    ) -> SqliteEvolutionLedger:
        """Create a SQLite ledger and import all events from a JSONL ledger.

        Verifies the JSONL hash chain before migration and aborts if broken.
        """
        from OriginAgent.evolution.events import EvolutionEvent

        jsonl_ledger = EvolutionLedger(
            workspace=workspace,
            event_path=jsonl_path,
            identity_store=identity_store,
            sign_events=sign_events,
        )
        sqlite_ledger = cls(
            workspace=workspace,
            db_path=db_path,
            identity_store=identity_store,
            sign_events=False,  # Don't re-sign during migration
        )

        # Verify source chain
        verification = jsonl_ledger.verify_chain(verify_signatures=True)
        if not verification.ok:
            raise ValueError(
                f"Source JSONL ledger chain is broken: {verification.error}"
            )

        # Import events
        if jsonl_ledger.event_path.exists():
            with jsonl_ledger.event_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    event = EvolutionEvent(**data)
                    sqlite_ledger.append(event)

        return sqlite_ledger
