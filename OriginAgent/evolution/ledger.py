"""Append-only local evolution event ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock

from OriginAgent.evolution.events import EvolutionEvent

HashChainStatus = Literal["ok", "broken"]


@dataclass(frozen=True)
class LedgerVerificationResult:
    chain_integrity: HashChainStatus
    event_count: int
    terminal_event_hash: str | None = None
    broken_at_index: int | None = None
    broken_line: int | None = None
    expected_hash: str | None = None
    actual_hash: str | None = None
    error: str = ""

    @property
    def valid(self) -> bool:
        return self.chain_integrity == "ok"

    @property
    def ok(self) -> bool:
        return self.valid


class EvolutionLedger:
    """Append-only JSONL ledger with deterministic event hashing."""

    def __init__(
        self,
        workspace: Path,
        event_path: Path | None = None,
        lock_path: Path | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        memory_dir = self.workspace / "memory"
        self.event_path = Path(event_path) if event_path is not None else memory_dir / "evolution_events.jsonl"
        self._lock_path = Path(lock_path) if lock_path is not None else memory_dir / ".evolution_ledger.lock"

    def append(self, event: EvolutionEvent) -> EvolutionEvent:
        """Append an event and return the immutable event with ledger hashes populated."""

        with self._locked():
            previous_hash = self._last_event_hash_unlocked()
            event_with_previous = replace(
                event,
                previous_event_hash=previous_hash,
                event_hash="",
                signature="",
            )
            event_hash = compute_event_hash(event_with_previous.to_dict())
            event_to_write = replace(event_with_previous, event_hash=event_hash)
            self.event_path.parent.mkdir(parents=True, exist_ok=True)
            with self.event_path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_dump(event_to_write.to_dict()).decode("utf-8") + "\n")
            return event_to_write

    def verify_chain(self) -> LedgerVerificationResult:
        """Verify the full hash chain.

        This is O(n) over all events. If event count exceeds 10,000, add
        checkpointing or segmented verification before enabling startup-wide checks.
        """

        with self._locked():
            previous_hash: str | None = None
            terminal_hash: str | None = None
            count = 0
            if not self.event_path.exists():
                return LedgerVerificationResult(chain_integrity="ok", event_count=0)
            try:
                with self.event_path.open("r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if not line.strip():
                            continue
                        count += 1
                        event = json.loads(line)
                        if event.get("previous_event_hash") != previous_hash:
                            return LedgerVerificationResult(
                                chain_integrity="broken",
                                event_count=count,
                                terminal_event_hash=terminal_hash,
                                broken_at_index=count - 1,
                                broken_line=line_number,
                                expected_hash=previous_hash,
                                actual_hash=event.get("previous_event_hash"),
                                error="previous_event_hash mismatch",
                            )
                        expected_hash = compute_event_hash(event)
                        if event.get("event_hash") != expected_hash:
                            return LedgerVerificationResult(
                                chain_integrity="broken",
                                event_count=count,
                                terminal_event_hash=terminal_hash,
                                broken_at_index=count - 1,
                                broken_line=line_number,
                                expected_hash=expected_hash,
                                actual_hash=event.get("event_hash"),
                                error="event_hash mismatch",
                            )
                        previous_hash = str(event["event_hash"])
                        terminal_hash = previous_hash
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                return LedgerVerificationResult(
                    chain_integrity="broken",
                    event_count=count,
                    terminal_event_hash=terminal_hash,
                    broken_at_index=count,
                    broken_line=count + 1,
                    error=str(exc),
                )
            return LedgerVerificationResult(
                chain_integrity="ok",
                event_count=count,
                terminal_event_hash=terminal_hash,
            )

    def _locked(self) -> FileLock:
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self._lock_path))

    def _last_event_hash_unlocked(self) -> str | None:
        if not self.event_path.exists():
            return None
        last_line = ""
        with self.event_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last_line = line
        if not last_line:
            return None
        event = json.loads(last_line)
        event_hash = event.get("event_hash")
        if not isinstance(event_hash, str) or not event_hash:
            raise ValueError("last ledger event is missing event_hash")
        return event_hash


def canonical_dump(event: dict[str, Any]) -> bytes:
    serialized = json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return serialized.encode("utf-8")


def compute_event_hash(event: dict[str, Any]) -> str:
    payload = dict(event)
    payload.pop("event_hash", None)
    payload.pop("signature", None)
    return hashlib.sha256(canonical_dump(payload)).hexdigest()
