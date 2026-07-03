"""Reusable JSONL -> SQLite migration patterns."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from OriginAgent.storage.sqlite_helpers import connect, ensure_schema


@dataclass
class MigrationResult:
    """Outcome of a single JSONL -> SQLite migration run."""

    ok: bool
    records_imported: int
    records_skipped: int
    error: str = ""


class AppendOnlyMigrator(ABC):
    """Migrate an append-only JSONL store to SQLite.

    Subclasses define: table DDL, row serializer, and validation logic.
    """

    def __init__(self, workspace: Path, db_path: Path, jsonl_path: Path) -> None:
        self.workspace = Path(workspace)
        self.db_path = db_path
        self.jsonl_path = jsonl_path

    @abstractmethod
    def table_ddl(self) -> str:
        """Return the CREATE TABLE/INDEX DDL for the target store."""
        ...

    @abstractmethod
    def validate_line(self, line: dict[str, Any]) -> bool:
        """Return True if *line* passes schema validation."""
        ...

    @abstractmethod
    def insert_row(self, conn: Any, line: dict[str, Any]) -> None:
        """Insert one row parsed from a JSONL line into the open connection."""
        ...

    def migrate(self) -> MigrationResult:
        """Run the migration. Idempotent -- safe to call repeatedly."""
        if not self.jsonl_path.exists():
            return MigrationResult(ok=True, records_imported=0, records_skipped=0)

        conn = connect(self.db_path)
        try:
            ensure_schema(conn, self.table_ddl())
            imported = 0
            skipped = 0
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                with open(self.jsonl_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            skipped += 1
                            continue
                        if not self.validate_line(data):
                            skipped += 1
                            continue
                        self.insert_row(conn, data)
                        imported += 1
            return MigrationResult(ok=True, records_imported=imported, records_skipped=skipped)
        finally:
            conn.close()


class ReadModifyWriteMigrator(ABC):
    """Migrate a read-modify-write JSONL store to SQLite.

    Loads all records from JSONL, validates each, then upserts into SQLite
    in a single transaction.  Subclasses define: table DDL, row validator,
    and upsert logic.
    """

    def __init__(self, workspace: Path, db_path: Path, jsonl_path: Path) -> None:
        self.workspace = Path(workspace)
        self.db_path = db_path
        self.jsonl_path = jsonl_path

    @abstractmethod
    def table_ddl(self) -> str:
        """Return the CREATE TABLE/INDEX DDL for the target store."""
        ...

    @abstractmethod
    def validate_line(self, line: dict[str, Any]) -> bool:
        """Return True if *line* passes schema validation."""
        ...

    @abstractmethod
    def upsert_row(self, conn: Any, line: dict[str, Any]) -> None:
        """Upsert one row parsed from a JSONL line into the open connection."""
        ...

    def migrate(self) -> MigrationResult:
        """Run the migration. Idempotent -- safe to call repeatedly."""
        if not self.jsonl_path.exists():
            return MigrationResult(ok=True, records_imported=0, records_skipped=0)

        conn = connect(self.db_path)
        try:
            ensure_schema(conn, self.table_ddl())
            imported = 0
            skipped = 0
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                with open(self.jsonl_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            skipped += 1
                            continue
                        if not self.validate_line(data):
                            skipped += 1
                            continue
                        self.upsert_row(conn, data)
                        imported += 1
            return MigrationResult(ok=True, records_imported=imported, records_skipped=skipped)
        finally:
            conn.close()
