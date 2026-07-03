"""SQLite-backed Tier-3 stores — the harder RMW patterns.

FactRelationStoreSqlite, ReviewProposalStoreSqlite, ReviewProposalEventStoreSqlite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.storage.jsonl_migration import AppendOnlyMigrator, ReadModifyWriteMigrator
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect, ensure_schema


# ==========================================================================
# FactRelationStoreSqlite  (RMW — upsert by (source, target, type) composite)
# ==========================================================================

class FactRelationStoreSqlite(ReadModifyWriteMigrator):
    """SQLite-backed fact relation store.

    Replaces FactRelationStore with SQLite upsert semantics.
    """

    DDL = """
        CREATE TABLE IF NOT EXISTS fact_relations (
            relation_id    TEXT NOT NULL,
            source_fact_id TEXT NOT NULL,
            target_fact_id TEXT NOT NULL,
            relation_type  TEXT NOT NULL DEFAULT 'related_to',
            status         TEXT NOT NULL DEFAULT 'active',
            confidence     REAL NOT NULL DEFAULT 0.7,
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
            metadata_json  TEXT NOT NULL DEFAULT '{}',
            payload_json   TEXT NOT NULL DEFAULT '{}',
            UNIQUE(source_fact_id, target_fact_id, relation_type)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_fr_source
            ON fact_relations(source_fact_id, status);
        CREATE INDEX IF NOT EXISTS idx_fr_target
            ON fact_relations(target_fact_id, status);
        CREATE INDEX IF NOT EXISTS idx_fr_type
            ON fact_relations(relation_type);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        d = workspace / "memory"
        super().__init__(workspace=workspace, db_path=db_path or d / "fact_relations.sqlite3",
                         jsonl_path=d / "fact_relations.jsonl")

    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("source_fact_id") and line.get("target_fact_id"))
    def upsert_row(self, conn, line):
        conn.execute(
            """INSERT OR REPLACE INTO fact_relations
               (relation_id, source_fact_id, target_fact_id, relation_type, status,
                confidence, created_at, updated_at, metadata_json, payload_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (line.get("relation_id",""), line.get("source_fact_id",""), line.get("target_fact_id",""),
             line.get("relation_type","related_to"), line.get("status","active"),
             float(line.get("confidence",0.7) or 0.7), line.get("created_at",""), line.get("updated_at",""),
             json.dumps(line.get("metadata",{}), ensure_ascii=False), json.dumps(line, ensure_ascii=False)))

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try: ensure_schema(conn, self.DDL)
        finally: conn.close()

    # Query API matching FactRelationStore

    def read_all(self) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute("SELECT payload_json FROM fact_relations ORDER BY created_at").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally: conn.close()

    def upsert(self, relation: dict[str, Any]) -> dict[str, Any]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO fact_relations
                   (relation_id, source_fact_id, target_fact_id, relation_type, status,
                    confidence, created_at, updated_at, metadata_json, payload_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (relation.get("relation_id",""), relation.get("source_fact_id",""),
                 relation.get("target_fact_id",""), relation.get("relation_type","related_to"),
                 relation.get("status","active"), float(relation.get("confidence",0.7) or 0.7),
                 relation.get("created_at",""), relation.get("updated_at",""),
                 json.dumps(relation.get("metadata",{}), ensure_ascii=False),
                 json.dumps(relation, ensure_ascii=False)))
            conn.commit()
        finally: conn.close()
        return relation

    def reverse_supersedes_index(self) -> dict[str, list[str]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT source_fact_id, target_fact_id FROM fact_relations WHERE relation_type='supersedes' AND status='active'"
            ).fetchall()
            idx: dict[str, list[str]] = {}
            for r in rows:
                idx.setdefault(r["target_fact_id"], []).append(r["source_fact_id"])
            return idx
        finally: conn.close()

    def related_fact_ids(self, fact_id: str, *, relation_types=None, depth=1, bidirectional=False) -> set[str]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT source_fact_id, target_fact_id FROM fact_relations WHERE status='active'"
            ).fetchall()
            adj: dict[str, set[str]] = {}
            for r in rows:
                if relation_types and r["relation_type"] not in relation_types:
                    continue
                adj.setdefault(r["source_fact_id"], set()).add(r["target_fact_id"])
                if bidirectional:
                    adj.setdefault(r["target_fact_id"], set()).add(r["source_fact_id"])
            frontier, visited = {fact_id}, {fact_id}
            for _ in range(max(1, depth)):
                nf: set[str] = set()
                for cur in frontier:
                    for tgt in adj.get(cur, set()):
                        if tgt not in visited:
                            visited.add(tgt); nf.add(tgt)
                frontier = nf
                if not frontier: break
            visited.discard(fact_id)
            return visited
        finally: conn.close()


# ==========================================================================
# ReviewProposalStoreSqlite  (RMW — full-rewrite style converted to SQL)
# ==========================================================================

class ReviewProposalStoreSqlite(ReadModifyWriteMigrator):
    """SQLite-backed review proposal store."""

    DDL = """
        CREATE TABLE IF NOT EXISTS review_proposals (
            id              TEXT PRIMARY KEY,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            session_key     TEXT NOT NULL DEFAULT '',
            turn_id         TEXT NOT NULL DEFAULT '',
            proposal_type   TEXT NOT NULL DEFAULT '',
            domain_id       TEXT NOT NULL DEFAULT '',
            title           TEXT NOT NULL DEFAULT '',
            content         TEXT NOT NULL DEFAULT '',
            origin          TEXT NOT NULL DEFAULT 'background_review',
            rationale       TEXT NOT NULL DEFAULT '',
            confidence      REAL,
            evidence_json   TEXT NOT NULL DEFAULT '[]',
            payload_json    TEXT NOT NULL DEFAULT '{}',
            source_message_id TEXT,
            status          TEXT NOT NULL DEFAULT 'pending',
            full_json       TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_rp_status ON review_proposals(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_rp_origin ON review_proposals(origin, status);
        CREATE INDEX IF NOT EXISTS idx_rp_type ON review_proposals(proposal_type, status);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        d = workspace / "memory"
        super().__init__(workspace=workspace, db_path=db_path or d / "review_proposals.sqlite3",
                         jsonl_path=d / "review_proposals.jsonl")

    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("id"))
    def upsert_row(self, conn, line):
        conn.execute(
            """INSERT OR REPLACE INTO review_proposals
               (id, created_at, session_key, turn_id, proposal_type, domain_id, title,
                content, origin, rationale, confidence, evidence_json, payload_json,
                source_message_id, status, full_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (line.get("id",""), line.get("created_at",""), line.get("session_key",""),
             line.get("turn_id",""), line.get("proposal_type",""), line.get("domain_id",""),
             line.get("title",""), line.get("content",""), line.get("origin","background_review"),
             line.get("rationale",""), line.get("confidence"), json.dumps(line.get("evidence",[]), ensure_ascii=False),
             json.dumps(line.get("payload",{}), ensure_ascii=False), line.get("source_message_id"),
             line.get("status","pending"), json.dumps(line, ensure_ascii=False)))

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try: ensure_schema(conn, self.DDL)
        finally: conn.close()

    def list_records(self, *, origin=None, status=None, proposal_type=None, limit=50) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            where, params = ["1=1"], []
            if origin: where.append("origin=?"); params.append(origin)
            if status: where.append("status=?"); params.append(status)
            if proposal_type: where.append("proposal_type=?"); params.append(proposal_type)
            rows = conn.execute(
                f"SELECT full_json FROM review_proposals WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ?",
                params + [max(1, min(int(limit), 200))]).fetchall()
            return [json.loads(r["full_json"]) for r in rows]
        finally: conn.close()

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute("SELECT full_json FROM review_proposals WHERE id=?", (proposal_id,)).fetchone()
            return json.loads(row["full_json"]) if row else None
        finally: conn.close()

    def update_status(self, proposal_id: str, status: str) -> bool:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            # Also update full_json so get() reflects the change
            row = conn.execute("SELECT full_json FROM review_proposals WHERE id=?", (proposal_id,)).fetchone()
            if row is None:
                return False
            data = json.loads(row["full_json"])
            data["status"] = status
            cur = conn.execute(
                "UPDATE review_proposals SET status=?, full_json=? WHERE id=?",
                (status, json.dumps(data, ensure_ascii=False), proposal_id))
            conn.commit()
            return cur.rowcount > 0
        finally: conn.close()

    def stats(self, *, origin=None) -> dict[str, Any]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            where_clause = "WHERE origin=?" if origin else ""
            where_and = f"{where_clause} AND" if where_clause else "WHERE"
            params = [origin] if origin else []
            total = conn.execute(f"SELECT COUNT(*) as c FROM review_proposals {where_clause}", params).fetchone()["c"]
            pending = conn.execute(f"SELECT COUNT(*) as c FROM review_proposals {where_and} status='pending'", params).fetchone()["c"]
            type_counts = {}
            for r in conn.execute(f"SELECT proposal_type, COUNT(*) as c FROM review_proposals {where_clause} GROUP BY proposal_type", params).fetchall():
                type_counts[r["proposal_type"]] = r["c"]
            return {"proposal_count": total, "pending_count": pending, "type_counts": type_counts}
        finally: conn.close()


# ==========================================================================
# ReviewProposalEventStoreSqlite  (append-only)
# ==========================================================================

class ReviewProposalEventStoreSqlite(AppendOnlyMigrator):
    """SQLite-backed review proposal event log."""

    DDL = """
        CREATE TABLE IF NOT EXISTS review_proposal_events (
            event_id     TEXT PRIMARY KEY,
            proposal_id  TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            reason       TEXT NOT NULL DEFAULT '',
            actor        TEXT NOT NULL DEFAULT 'user',
            review_reason TEXT NOT NULL DEFAULT '',
            fact_id      TEXT,
            skill_name   TEXT,
            skill_path   TEXT,
            workflow_name TEXT,
            workflow_path TEXT,
            error        TEXT NOT NULL DEFAULT '',
            artifact_json TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_rpe_proposal
            ON review_proposal_events(proposal_id, created_at);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        d = workspace / "memory"
        super().__init__(workspace=workspace, db_path=db_path or d / "review_proposal_events.sqlite3",
                         jsonl_path=d / "review_proposal_events.jsonl")

    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("event_id") and line.get("proposal_id"))
    def insert_row(self, conn, line):
        conn.execute(
            """INSERT OR IGNORE INTO review_proposal_events
               (event_id, proposal_id, status, created_at, reason, actor, review_reason,
                fact_id, skill_name, skill_path, workflow_name, workflow_path, error,
                artifact_json, payload_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (line.get("event_id",""), line.get("proposal_id",""), line.get("status",""),
             line.get("created_at",""), line.get("reason",""), line.get("actor","user"),
             line.get("review_reason",""), line.get("fact_id"), line.get("skill_name"),
             line.get("skill_path"), line.get("workflow_name"), line.get("workflow_path"),
             line.get("error",""), json.dumps(line.get("artifact"), ensure_ascii=False) if line.get("artifact") else None,
             json.dumps(line, ensure_ascii=False)))

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try: ensure_schema(conn, self.DDL)
        finally: conn.close()

    def for_proposal(self, proposal_id: str) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM review_proposal_events WHERE proposal_id=? ORDER BY created_at",
                (proposal_id,)).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally: conn.close()

    def latest_for_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT payload_json FROM review_proposal_events WHERE proposal_id=? ORDER BY created_at DESC LIMIT 1",
                (proposal_id,)).fetchone()
            return json.loads(row["payload_json"]) if row else None
        finally: conn.close()
