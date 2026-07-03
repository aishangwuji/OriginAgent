"""Shared cross-tenant space: facts, devices, calendar.

Every tenant's BDI _gather_beliefs() pulls from their own facts
AND from shared facts.  Devices (lights, AC, robot) are shared
by default — they belong to the house, not to a person.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SharedSpace:
    """Household-level shared state visible to all tenants.

    Attributes:
        workspace: Root workspace path under which shared data is stored.
    """

    workspace: Path

    @property
    def facts_dir(self) -> Path:
        """Directory holding shared fact files."""
        return self.workspace / "shared" / "facts"

    @property
    def device_domains(self) -> list[str]:
        """Device domains that are shared by default (extensible per config)."""
        return ["smart_home"]

    @property
    def calendar_path(self) -> Path:
        """Path to the shared calendar JSONL file."""
        return self.workspace / "shared" / "calendar.jsonl"

    def shared_facts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Read shared facts visible to all tenants.

        Args:
            limit: Maximum number of facts to return (most recent).

        Returns:
            List of fact dicts, ordered oldest-first, capped to *limit*.
        """
        facts: list[dict[str, Any]] = []
        facts_file = self.facts_dir / "shared_facts.jsonl"
        if facts_file.exists():
            import json

            with open(facts_file, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    facts.append(json.loads(stripped))
        return facts[-limit:]

    def add_shared_fact(self, fact: dict[str, Any], *, added_by: str = "") -> None:
        """Add a fact visible to all tenants.

        Args:
            fact: The fact dict to persist.  Must be JSON-serialisable.
            added_by: Optional identifier of who added the fact.
        """
        from datetime import datetime, timezone

        self.facts_dir.mkdir(parents=True, exist_ok=True)
        import json

        fact["added_by"] = added_by
        fact["added_at"] = datetime.now(timezone.utc).isoformat()
        facts_file = self.facts_dir / "shared_facts.jsonl"
        with open(facts_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(fact, ensure_ascii=False) + "\n")
