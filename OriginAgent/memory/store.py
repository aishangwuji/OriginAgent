"""Filesystem helpers for the nearline layered memory sidecar."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from filelock import FileLock

from OriginAgent.memory.models import (
    AgentCaseRecord,
    CanonicalMessage,
    EpisodeRecord,
    ForesightRecord,
    MemCell,
    MemoryLayerSummary,
    ProfileSnapshot,
)


class NearlineMemoryStore:
    """Filesystem helper for nearline memory artifacts and cursors."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.root = self.workspace / "memory" / "nearline"
        self.lock_path = self.root / ".lock"
        self.memcells_path = self.root / "memcells.jsonl"
        self.episodes_path = self.root / "episodes.jsonl"
        self.foresights_path = self.root / "foresights.jsonl"
        self.agent_cases_path = self.root / "agent_cases.jsonl"
        self.profiles_path = self.root / "profiles.jsonl"
        self.cursor_path = self.root / ".cursor"
        self.session_cursors_path = self.root / "session_cursors.json"

    def summary(
        self,
        *,
        nearline_enabled: bool,
        pipeline_enabled: bool,
        profile_shadow_write_enabled: bool,
    ) -> dict[str, Any]:
        memcell_count, last_memcell_at = self._count_and_last(self.memcells_path, "ended_at", "timestamp")
        episode_count, last_episode_at = self._count_and_last(self.episodes_path, "timestamp")
        foresight_count, last_foresight_at = self._count_and_last(self.foresights_path, "timestamp", "start_at")
        agent_case_count, last_agent_case_at = self._count_and_last(self.agent_cases_path, "timestamp")
        profile_count, last_profile_at = self._count_and_last(self.profiles_path, "updated_at", "timestamp")
        status = "disabled"
        if nearline_enabled:
            status = "idle"
        if pipeline_enabled:
            status = "enabled"
        return MemoryLayerSummary(
            nearline_enabled=nearline_enabled,
            pipeline_enabled=pipeline_enabled,
            profile_shadow_write_enabled=profile_shadow_write_enabled,
            memcell_count=memcell_count,
            episode_count=episode_count,
            foresight_count=foresight_count,
            agent_case_count=agent_case_count,
            profile_count=profile_count,
            last_memcell_at=last_memcell_at,
            last_episode_at=last_episode_at,
            last_foresight_at=last_foresight_at,
            last_agent_case_at=last_agent_case_at,
            last_profile_at=last_profile_at,
            latest_cursor=self.read_cursor(),
            status=status,
        ).to_json()

    def read_cursor(self) -> int:
        with suppress(OSError, ValueError):
            return int(self.cursor_path.read_text(encoding="utf-8").strip())
        return 0

    def write_cursor(self, cursor: int) -> int:
        cursor = max(0, int(cursor))
        with self._locked():
            self._write_text_atomic(self.cursor_path, str(cursor))
        return cursor

    def advance_cursor(self, cursor: int) -> int:
        cursor = max(0, int(cursor))
        with self._locked():
            current = self.read_cursor()
            if cursor <= current:
                return current
            self._write_text_atomic(self.cursor_path, str(cursor))
            return cursor

    def append_memcells(self, memcells: list[MemCell]) -> int:
        return self._append_jsonl(self.memcells_path, memcells)

    def append_episodes(self, episodes: list[EpisodeRecord]) -> int:
        return self._append_jsonl(self.episodes_path, episodes)

    def append_foresights(self, foresights: list[ForesightRecord]) -> int:
        return self._append_jsonl(self.foresights_path, foresights)

    def append_agent_cases(self, agent_cases: list[AgentCaseRecord]) -> int:
        return self._append_jsonl(self.agent_cases_path, agent_cases)

    def append_profiles(self, profiles: list[ProfileSnapshot]) -> int:
        return self._append_jsonl(self.profiles_path, profiles)

    def read_memcells(self, *, limit: int | None = None) -> list[MemCell]:
        payloads = self._read_jsonl(self.memcells_path, limit=limit)
        return [self._memcell_from_payload(payload) for payload in payloads]

    def read_episodes(self, *, limit: int | None = None) -> list[EpisodeRecord]:
        payloads = self._read_jsonl(self.episodes_path, limit=limit)
        return [self._episode_from_payload(payload) for payload in payloads]

    def read_foresights(self, *, limit: int | None = None) -> list[ForesightRecord]:
        payloads = self._read_jsonl(self.foresights_path, limit=limit)
        return [self._foresight_from_payload(payload) for payload in payloads]

    def read_agent_cases(self, *, limit: int | None = None) -> list[AgentCaseRecord]:
        payloads = self._read_jsonl(self.agent_cases_path, limit=limit)
        return [self._agent_case_from_payload(payload) for payload in payloads]

    def read_profiles(self, *, limit: int | None = None) -> list[ProfileSnapshot]:
        payloads = self._read_jsonl(self.profiles_path, limit=limit)
        return [self._profile_from_payload(payload) for payload in payloads]

    def read_session_cursor(self, session_key: str) -> int:
        session_key = str(session_key or "").strip()
        if not session_key:
            return 0
        with suppress(OSError, ValueError, TypeError):
            payload = self._read_session_cursors()
            return max(0, int(payload.get(session_key, 0)))
        return 0

    def write_session_cursor(self, session_key: str, cursor: int) -> int:
        session_key = str(session_key or "").strip()
        if not session_key:
            return 0
        cursor = max(0, int(cursor))
        with self._locked():
            payload = self._read_session_cursors()
            payload[session_key] = cursor
            self._write_text_atomic(
                self.session_cursors_path,
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
        return cursor

    def advance_session_cursor(self, session_key: str, cursor: int) -> int:
        session_key = str(session_key or "").strip()
        if not session_key:
            return 0
        cursor = max(0, int(cursor))
        with self._locked():
            payload = self._read_session_cursors()
            current = max(0, int(payload.get(session_key, 0) or 0))
            if cursor <= current:
                return current
            payload[session_key] = cursor
            self._write_text_atomic(
                self.session_cursors_path,
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
            return cursor

    def _locked(self) -> FileLock:
        self.root.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self.lock_path))

    @staticmethod
    def _fsync_parent(path: Path) -> None:
        with suppress(PermissionError, OSError):
            fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _write_text_atomic(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            self._fsync_parent(path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _count_and_last(path: Path, *timestamp_keys: str) -> tuple[int, str | None]:
        count = 0
        last_timestamp: str | None = None
        if not path.exists():
            return count, last_timestamp
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    count += 1
                    for key in timestamp_keys:
                        value = payload.get(key)
                        if isinstance(value, str) and value.strip():
                            if last_timestamp is None or value > last_timestamp:
                                last_timestamp = value
                            break
        except OSError:
            return 0, None
        return count, last_timestamp

    def _append_jsonl(self, path: Path, records: list[Any]) -> int:
        payloads = [self._normalize_record(record) for record in records]
        if not payloads:
            return 0
        with self._locked():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                for payload in payloads:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_parent(path)
        return len(payloads)

    @staticmethod
    def _normalize_record(record: Any) -> dict[str, Any]:
        if hasattr(record, "to_json"):
            payload = record.to_json()
        else:
            payload = record
        if not isinstance(payload, dict):
            raise TypeError("Nearline JSONL records must serialize to dict payloads")
        return payload

    @staticmethod
    def _read_jsonl(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not path.exists():
            return rows
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        rows.append(payload)
        except OSError:
            return []
        if limit is not None and limit > 0:
            return rows[-limit:]
        return rows

    def _read_session_cursors(self) -> dict[str, int]:
        if not self.session_cursors_path.exists():
            return {}
        try:
            payload = json.loads(self.session_cursors_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        cursors: dict[str, int] = {}
        for key, value in payload.items():
            if not isinstance(key, str):
                continue
            with suppress(TypeError, ValueError):
                cursors[key] = max(0, int(value))
        return cursors

    @staticmethod
    def _memcell_from_payload(payload: dict[str, Any]) -> MemCell:
        messages = [
            NearlineMemoryStore._message_from_payload(message)
            for message in payload.get("messages", [])
            if isinstance(message, dict)
        ]
        return MemCell(
            memcell_id=str(payload.get("memcell_id") or ""),
            session_key=str(payload.get("session_key") or ""),
            kind=str(payload.get("kind") or "conversation_turn"),
            started_at=str(payload.get("started_at") or ""),
            ended_at=str(payload.get("ended_at") or ""),
            message_ids=[str(item) for item in payload.get("message_ids", []) if isinstance(item, str)],
            roles=[str(item) for item in payload.get("roles", []) if isinstance(item, str)],
            content=str(payload.get("content") or ""),
            messages=messages,
            metadata=dict(payload.get("metadata") or {}),
        )

    @staticmethod
    def _message_from_payload(payload: dict[str, Any]) -> CanonicalMessage:
        return CanonicalMessage(
            message_id=str(payload.get("message_id") or ""),
            session_key=str(payload.get("session_key") or ""),
            role=str(payload.get("role") or "assistant"),
            content=str(payload.get("content") or ""),
            timestamp=str(payload.get("timestamp") or ""),
            channel=str(payload.get("channel") or ""),
            chat_id=str(payload.get("chat_id") or ""),
            sender_id=payload.get("sender_id") if isinstance(payload.get("sender_id"), str) else None,
            tool_name=payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else None,
            tool_call_id=payload.get("tool_call_id") if isinstance(payload.get("tool_call_id"), str) else None,
            metadata=dict(payload.get("metadata") or {}),
        )

    @staticmethod
    def _episode_from_payload(payload: dict[str, Any]) -> EpisodeRecord:
        return EpisodeRecord(
            episode_id=str(payload.get("episode_id") or ""),
            memcell_id=str(payload.get("memcell_id") or ""),
            session_key=str(payload.get("session_key") or ""),
            owner_id=str(payload.get("owner_id") or ""),
            summary=str(payload.get("summary") or ""),
            content=str(payload.get("content") or ""),
            timestamp=str(payload.get("timestamp") or ""),
            source_message_ids=[
                str(item) for item in payload.get("source_message_ids", []) if isinstance(item, str)
            ],
            metadata=dict(payload.get("metadata") or {}),
        )

    @staticmethod
    def _foresight_from_payload(payload: dict[str, Any]) -> ForesightRecord:
        start_at = payload.get("start_at")
        end_at = payload.get("end_at")
        return ForesightRecord(
            foresight_id=str(payload.get("foresight_id") or ""),
            memcell_id=str(payload.get("memcell_id") or ""),
            session_key=str(payload.get("session_key") or ""),
            owner_id=str(payload.get("owner_id") or ""),
            content=str(payload.get("content") or ""),
            evidence=str(payload.get("evidence") or ""),
            start_at=str(start_at) if isinstance(start_at, str) and start_at else None,
            end_at=str(end_at) if isinstance(end_at, str) and end_at else None,
            timestamp=str(payload.get("timestamp") or ""),
            source_message_ids=[
                str(item) for item in payload.get("source_message_ids", []) if isinstance(item, str)
            ],
            metadata=dict(payload.get("metadata") or {}),
        )

    @staticmethod
    def _agent_case_from_payload(payload: dict[str, Any]) -> AgentCaseRecord:
        quality_score = payload.get("quality_score", 0.0)
        try:
            normalized_score = float(quality_score)
        except (TypeError, ValueError):
            normalized_score = 0.0
        return AgentCaseRecord(
            case_id=str(payload.get("case_id") or ""),
            memcell_id=str(payload.get("memcell_id") or ""),
            session_key=str(payload.get("session_key") or ""),
            agent_id=str(payload.get("agent_id") or ""),
            task_intent=str(payload.get("task_intent") or ""),
            approach=str(payload.get("approach") or ""),
            outcome_summary=str(payload.get("outcome_summary") or ""),
            quality_score=normalized_score,
            timestamp=str(payload.get("timestamp") or ""),
            source_message_ids=[
                str(item) for item in payload.get("source_message_ids", []) if isinstance(item, str)
            ],
            metadata=dict(payload.get("metadata") or {}),
        )

    @staticmethod
    def _profile_from_payload(payload: dict[str, Any]) -> ProfileSnapshot:
        return ProfileSnapshot(
            profile_id=str(payload.get("profile_id") or ""),
            owner_id=str(payload.get("owner_id") or ""),
            summary=str(payload.get("summary") or ""),
            explicit_traits=[
                str(item) for item in payload.get("explicit_traits", []) if isinstance(item, str)
            ],
            implicit_traits=[
                str(item) for item in payload.get("implicit_traits", []) if isinstance(item, str)
            ],
            source_memcell_ids=[
                str(item) for item in payload.get("source_memcell_ids", []) if isinstance(item, str)
            ],
            updated_at=str(payload.get("updated_at") or ""),
            metadata=dict(payload.get("metadata") or {}),
        )
