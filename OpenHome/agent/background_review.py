"""Controlled background review proposal generation and review application.

Background review writes pending proposals first.  Human review is required
before any proposal can be applied to long-term memory.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock
from loguru import logger

from OpenHome.agent.auxiliary_llm import AuxiliaryLLMRouter, call_llm
from OpenHome.agent.domain_packs import DomainPackManager
from OpenHome.agent.facts import (
    HIGH_RISK_CATEGORIES,
    HIGH_RISK_KEYWORDS,
    TEMPORARY_LANGUAGE,
    UNCERTAIN_LANGUAGE,
    VALID_CATEGORIES,
    VALID_OWNERS,
    FactRecord,
)
from OpenHome.agent.memory import MemoryStore, redact_memory_text
from OpenHome.providers.base import LLMProvider
from OpenHome.utils.helpers import truncate_text
from OpenHome.utils.prompt_templates import render_template

DEFAULT_ALLOWED_PROPOSAL_TYPES = ("memory", "fact", "skill", "workflow")
PROPOSAL_STORE_RELATIVE = Path("memory") / "review_proposals.jsonl"
PROPOSAL_EVENT_STORE_RELATIVE = Path("memory") / "review_proposal_events.jsonl"
_TITLE_MAX_CHARS = 160
_CONTENT_MAX_CHARS = 2400
_RATIONALE_MAX_CHARS = 1200
_EVIDENCE_MAX_ITEMS = 5
_EVIDENCE_MAX_CHARS = 500
_MESSAGE_MAX_CHARS = 1600
_REVIEW_REASON_MAX_CHARS = 1000
_TERMINAL_REVIEW_STATUSES = {"applied", "rejected", "deferred"}
_APPLICABLE_PROPOSAL_TYPES = {"memory", "fact"}


@dataclass(frozen=True)
class ReviewProposal:
    """One pending background review proposal."""

    id: str
    created_at: str
    session_key: str
    turn_id: str
    proposal_type: str
    domain_id: str
    title: str
    content: str
    rationale: str = ""
    confidence: float | None = None
    evidence: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    source_message_id: str | None = None
    status: str = "pending"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewProposalEvent:
    """One append-only human review decision event."""

    event_id: str
    proposal_id: str
    status: str
    created_at: str
    reason: str = ""
    fact_id: str | None = None
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewDecisionResult:
    """Outcome of applying or recording a review decision."""

    proposal_id: str
    status: str
    action: str
    ok: bool
    message: str
    proposal: dict[str, Any] | None = None
    event: dict[str, Any] | None = None
    fact_id: str | None = None
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackgroundReviewResult:
    """Runtime outcome for observability and tests."""

    status: str
    proposals_written: int = 0
    reason: str = ""


class ReviewProposalStore:
    """Append-only JSONL store for pending review proposals."""

    def __init__(
        self,
        workspace: Path,
        *,
        path: Path | None = None,
        event_path: Path | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.path = path or (self.workspace / PROPOSAL_STORE_RELATIVE)
        self.event_path = event_path or (self.workspace / PROPOSAL_EVENT_STORE_RELATIVE)
        self._lock_path = self.path.parent / ".review_proposals.lock"
        self._memory_store = MemoryStore(self.workspace)

    def _locked(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self._lock_path))

    def append_many(self, proposals: list[ReviewProposal]) -> int:
        if not proposals:
            return 0
        with self._locked():
            with self.path.open("a", encoding="utf-8") as handle:
                for proposal in proposals:
                    handle.write(json.dumps(proposal.to_json(), ensure_ascii=False) + "\n")
        return len(proposals)

    def _read_jsonl(self, path: Path, *, label: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(raw, dict):
                        rows.append(raw)
        except FileNotFoundError:
            return []
        except OSError:
            logger.exception("Failed to read background review {} store", label)
            return []
        return rows

    def _iter_proposals_unlocked(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.path, label="proposal")

    def _iter_events_unlocked(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.event_path, label="event")

    def _latest_events_unlocked(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for event in self._iter_events_unlocked():
            proposal_id = str(event.get("proposal_id") or "")
            status = str(event.get("status") or "")
            if not proposal_id or not status:
                continue
            latest[proposal_id] = event
        return latest

    def _merged_records_unlocked(self) -> list[dict[str, Any]]:
        latest_events = self._latest_events_unlocked()
        records: list[dict[str, Any]] = []
        for raw in self._iter_proposals_unlocked():
            proposal_id = str(raw.get("id") or "")
            record = dict(raw)
            record["status"] = str(record.get("status") or "pending")
            event = latest_events.get(proposal_id)
            fact_id = None
            if event is not None:
                record["status"] = str(event.get("status") or record["status"])
                record["review_event"] = dict(event)
                reason = str(event.get("reason") or "")
                if reason:
                    record["review_reason"] = reason
                fact_id = event.get("fact_id")
            if isinstance(fact_id, str) and fact_id:
                record["applied_fact_id"] = fact_id
            records.append(_redacted_record(record))
        return records

    def iter_all(self) -> list[dict[str, Any]]:
        with self._locked():
            return self._merged_records_unlocked()

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 10), 50))
        return list(reversed(self.iter_all()))[:limit]

    def list_records(
        self,
        *,
        status: str | None = None,
        proposal_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 50))
        status = (status or "").strip().lower()
        proposal_type = (proposal_type or "").strip().lower()
        records = list(reversed(self.iter_all()))
        if status:
            records = [r for r in records if str(r.get("status") or "pending") == status]
        if proposal_type:
            records = [
                r for r in records
                if str(r.get("proposal_type") or r.get("type") or "") == proposal_type
            ]
        return records[:limit]

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        proposal_id = proposal_id.strip()
        if not proposal_id:
            return None
        with self._locked():
            for record in self._merged_records_unlocked():
                if record.get("id") == proposal_id:
                    return record
        return None

    def stats(self) -> dict[str, Any]:
        records = self.iter_all()
        pending = sum(
            1 for record in records if record.get("status", "pending") == "pending"
        )
        last_created_at = None
        for record in records:
            created_at = record.get("created_at")
            if isinstance(created_at, str) and (
                last_created_at is None or created_at > last_created_at
            ):
                last_created_at = created_at
        return {
            "proposal_count": len(records),
            "pending_count": pending,
            "last_created_at": last_created_at,
        }

    def apply(self, proposal_id: str, *, reason: str = "") -> ReviewDecisionResult:
        proposal_id = proposal_id.strip()
        if not proposal_id:
            return ReviewDecisionResult(
                proposal_id="",
                status="missing",
                action="apply",
                ok=False,
                message="proposal_id is required",
                error="missing_proposal_id",
            )
        with self._locked():
            record = self._find_unlocked(proposal_id)
            if record is None:
                return ReviewDecisionResult(
                    proposal_id=proposal_id,
                    status="missing",
                    action="apply",
                    ok=False,
                    message="Review proposal was not found.",
                    error="not_found",
                )
            terminal = self._terminal_result(record, action="apply")
            if terminal is not None:
                return terminal
            failed = self._failed_apply_result(record)
            if failed is not None:
                return failed

            proposal_type = _proposal_type(record)
            if proposal_type not in _APPLICABLE_PROPOSAL_TYPES:
                event = self._append_event_unlocked(
                    proposal_id,
                    status="failed",
                    reason=reason or f"{proposal_type} proposals cannot be applied in P6.",
                    error="unsupported_proposal_type",
                )
                return ReviewDecisionResult(
                    proposal_id=proposal_id,
                    status="failed",
                    action="apply",
                    ok=False,
                    message="Only memory and fact proposals can be applied in this phase.",
                    proposal=self._find_unlocked(proposal_id),
                    event=event,
                    error="unsupported_proposal_type",
                )

            try:
                fact = self._apply_to_memory(record)
            except Exception as exc:
                logger.exception("Failed to apply background review proposal {}", proposal_id)
                event = self._append_event_unlocked(
                    proposal_id,
                    status="failed",
                    reason=reason,
                    error=str(exc),
                )
                return ReviewDecisionResult(
                    proposal_id=proposal_id,
                    status="failed",
                    action="apply",
                    ok=False,
                    message="Failed to apply review proposal.",
                    proposal=self._find_unlocked(proposal_id),
                    event=event,
                    error=str(exc),
                )

            event = self._append_event_unlocked(
                proposal_id,
                status="applied",
                reason=reason,
                fact_id=fact.fact_id,
            )
            return ReviewDecisionResult(
                proposal_id=proposal_id,
                status="applied",
                action="apply",
                ok=True,
                message="Review proposal applied.",
                proposal=self._find_unlocked(proposal_id),
                event=event,
                fact_id=fact.fact_id,
            )

    def reject(self, proposal_id: str, *, reason: str = "") -> ReviewDecisionResult:
        return self._record_terminal_decision(proposal_id, status="rejected", reason=reason)

    def defer(self, proposal_id: str, *, reason: str = "") -> ReviewDecisionResult:
        return self._record_terminal_decision(proposal_id, status="deferred", reason=reason)

    def _find_unlocked(self, proposal_id: str) -> dict[str, Any] | None:
        for record in self._merged_records_unlocked():
            if record.get("id") == proposal_id:
                return record
        return None

    def _terminal_result(self, record: dict[str, Any], *, action: str) -> ReviewDecisionResult | None:
        status = str(record.get("status") or "pending")
        if status not in _TERMINAL_REVIEW_STATUSES:
            return None
        return ReviewDecisionResult(
            proposal_id=str(record.get("id") or ""),
            status=status,
            action=action,
            ok=True,
            message=f"Review proposal is already {status}.",
            proposal=record,
            event=record.get("review_event") if isinstance(record.get("review_event"), dict) else None,
            fact_id=record.get("applied_fact_id") if isinstance(record.get("applied_fact_id"), str) else None,
        )

    def _failed_apply_result(self, record: dict[str, Any]) -> ReviewDecisionResult | None:
        status = str(record.get("status") or "pending")
        event = record.get("review_event")
        if status != "failed" or not isinstance(event, dict):
            return None
        return ReviewDecisionResult(
            proposal_id=str(record.get("id") or ""),
            status="failed",
            action="apply",
            ok=False,
            message="Review proposal apply already failed.",
            proposal=record,
            event=event,
            error=str(event.get("error") or "failed"),
        )

    def _record_terminal_decision(
        self,
        proposal_id: str,
        *,
        status: str,
        reason: str = "",
    ) -> ReviewDecisionResult:
        proposal_id = proposal_id.strip()
        action = status
        if not proposal_id:
            return ReviewDecisionResult(
                proposal_id="",
                status="missing",
                action=action,
                ok=False,
                message="proposal_id is required",
                error="missing_proposal_id",
            )
        with self._locked():
            record = self._find_unlocked(proposal_id)
            if record is None:
                return ReviewDecisionResult(
                    proposal_id=proposal_id,
                    status="missing",
                    action=action,
                    ok=False,
                    message="Review proposal was not found.",
                    error="not_found",
                )
            terminal = self._terminal_result(record, action=action)
            if terminal is not None:
                return terminal
            event = self._append_event_unlocked(proposal_id, status=status, reason=reason)
            return ReviewDecisionResult(
                proposal_id=proposal_id,
                status=status,
                action=action,
                ok=True,
                message=f"Review proposal {status}.",
                proposal=self._find_unlocked(proposal_id),
                event=event,
            )

    def _append_event_unlocked(
        self,
        proposal_id: str,
        *,
        status: str,
        reason: str = "",
        fact_id: str | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        reason = _clean_text(reason, _REVIEW_REASON_MAX_CHARS)
        error = _clean_text(error, _REVIEW_REASON_MAX_CHARS)
        event = ReviewProposalEvent(
            event_id=f"review_event_{uuid.uuid4().hex}",
            proposal_id=proposal_id,
            status=status,
            created_at=datetime.now(timezone.utc).isoformat(),
            reason=reason,
            fact_id=fact_id,
            error=error,
        ).to_json()
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        with self.event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def _apply_to_memory(self, record: dict[str, Any]) -> FactRecord:
        fact_fields = _fact_fields_from_proposal(record)
        return self._memory_store.upsert_fact_and_rebuild_memory(**fact_fields)


class BackgroundReviewService:
    """Generate controlled learning proposals after successful user turns."""

    def __init__(
        self,
        *,
        workspace: Path,
        provider: LLMProvider,
        model: str,
        router: AuxiliaryLLMRouter | None = None,
        config: Any | None = None,
        config_loader: Callable[[], Any] | None = None,
        domain_pack_manager: DomainPackManager | None = None,
        store: ReviewProposalStore | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.provider = provider
        self.model = model
        self.router = router
        self._config = config
        self._config_loader = config_loader
        self.domain_pack_manager = domain_pack_manager
        self.store = store or ReviewProposalStore(self.workspace)
        self._running = 0
        self._last_result: BackgroundReviewResult | None = None

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        self.provider = provider
        self.model = model

    def refresh_config(self) -> None:
        if self._config_loader is None:
            return
        try:
            self._config = self._config_loader()
        except Exception:
            logger.exception("Failed to refresh background review config")

    @property
    def config(self) -> Any:
        if self._config is None:
            from OpenHome.config.schema import BackgroundReviewConfig

            self._config = BackgroundReviewConfig()
        return self._config

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", False))

    def runtime_status(self) -> dict[str, Any]:
        stats = self.store.stats()
        return {
            "background_review_enabled": self.enabled,
            "background_review_running_count": self._running,
            "background_review_proposal_count": stats["proposal_count"],
            "background_review_pending_count": stats["pending_count"],
            "background_review_last_created_at": stats["last_created_at"],
            "background_review_last_result": (
                asdict(self._last_result) if self._last_result is not None else None
            ),
        }

    async def review_turn(
        self,
        *,
        session_key: str,
        turn_id: str,
        channel: str,
        chat_id: str,
        message_id: str | None,
        messages: list[dict[str, Any]],
    ) -> BackgroundReviewResult:
        self.refresh_config()
        cfg = self.config
        if not bool(getattr(cfg, "enabled", False)):
            return self._remember_result(
                BackgroundReviewResult(status="skipped", reason="disabled")
            )

        max_concurrent = max(1, int(getattr(cfg, "max_concurrent_reviews", 1) or 1))
        if self._running >= max_concurrent:
            return self._remember_result(
                BackgroundReviewResult(status="skipped", reason="concurrency_limit")
            )

        self._running += 1
        try:
            prompt = self._build_prompt(
                session_key=session_key,
                turn_id=turn_id,
                channel=channel,
                chat_id=chat_id,
                message_id=message_id,
                messages=messages,
            )
            response = await call_llm(
                task="background_review",
                router=self.router,
                provider=self.provider,
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": render_template(
                            "agent/background_review.md",
                            strip=True,
                            allowed_types=", ".join(self._allowed_types()),
                            max_proposals=int(getattr(cfg, "max_proposals_per_turn", 8) or 8),
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                tools=None,
                tool_choice=None,
                max_tokens=2048,
                temperature=0.1,
            )
            if response.finish_reason == "error":
                return self._remember_result(
                    BackgroundReviewResult(status="llm_error", reason=response.content or "error")
                )
            proposals = self._parse_response(
                response.content or "",
                session_key=session_key,
                turn_id=turn_id,
                message_id=message_id,
            )
            written = self.store.append_many(proposals)
            return self._remember_result(
                BackgroundReviewResult(status="ok", proposals_written=written)
            )
        except Exception as exc:
            logger.exception("Background review failed")
            return self._remember_result(
                BackgroundReviewResult(status="error", reason=str(exc))
            )
        finally:
            self._running = max(0, self._running - 1)

    def _remember_result(self, result: BackgroundReviewResult) -> BackgroundReviewResult:
        self._last_result = result
        return result

    def _allowed_types(self) -> tuple[str, ...]:
        raw = getattr(self.config, "allowed_proposal_types", None) or DEFAULT_ALLOWED_PROPOSAL_TYPES
        allowed = []
        for item in raw:
            value = str(item).strip().lower()
            if value in DEFAULT_ALLOWED_PROPOSAL_TYPES and value not in allowed:
                allowed.append(value)
        return tuple(allowed or DEFAULT_ALLOWED_PROPOSAL_TYPES)

    def _allowed_domain_ids(self) -> set[str]:
        allowed = {"core"}
        manager = self.domain_pack_manager
        if manager is None:
            return allowed
        try:
            for pack in manager.list_packs():
                if getattr(pack, "active", False) and getattr(pack, "available", False):
                    allowed.add(pack.id)
        except Exception:
            logger.exception("Failed to read domain pack ids for background review")
        return allowed

    def _build_prompt(
        self,
        *,
        session_key: str,
        turn_id: str,
        channel: str,
        chat_id: str,
        message_id: str | None,
        messages: list[dict[str, Any]],
    ) -> str:
        cfg = self.config
        max_recent = max(1, int(getattr(cfg, "max_recent_messages", 12) or 12))
        max_prompt = max(1000, int(getattr(cfg, "max_prompt_chars", 16000) or 16000))
        recent = messages[-max_recent:]
        lines = [
            "## Review Scope",
            f"- session_key: {session_key}",
            f"- turn_id: {turn_id}",
            f"- channel: {channel}",
            f"- chat_id: {chat_id}",
            f"- message_id: {message_id or ''}",
            f"- allowed_domain_ids: {', '.join(sorted(self._allowed_domain_ids()))}",
            f"- allowed_proposal_types: {', '.join(self._allowed_types())}",
            "",
            "## Recent Messages",
        ]
        for index, message in enumerate(recent, start=1):
            role = str(message.get("role") or "unknown")
            timestamp = str(message.get("timestamp") or "")
            text = _message_text(message)
            text = truncate_text(redact_memory_text(text), _MESSAGE_MAX_CHARS)
            lines.append(f"[{index}] role={role} timestamp={timestamp}")
            lines.append(text or "(empty)")
            lines.append("")
        return truncate_text("\n".join(lines).strip(), max_prompt)

    def _parse_response(
        self,
        text: str,
        *,
        session_key: str,
        turn_id: str,
        message_id: str | None,
    ) -> list[ReviewProposal]:
        payload = _load_json_payload(text)
        if not isinstance(payload, dict):
            return []
        raw_proposals = payload.get("proposals")
        if not isinstance(raw_proposals, list):
            return []

        allowed_types = set(self._allowed_types())
        allowed_domains = self._allowed_domain_ids()
        max_items = int(getattr(self.config, "max_proposals_per_turn", 8) or 8)
        proposals: list[ReviewProposal] = []
        now = datetime.now(timezone.utc).isoformat()
        for raw in raw_proposals:
            if len(proposals) >= max_items:
                break
            if not isinstance(raw, dict):
                continue
            proposal_type = str(raw.get("type") or raw.get("proposal_type") or "").strip().lower()
            domain_id = str(raw.get("domain_id") or raw.get("domain") or "core").strip()
            title = _clean_text(raw.get("title"), _TITLE_MAX_CHARS)
            content = _clean_text(raw.get("content"), _CONTENT_MAX_CHARS)
            if proposal_type not in allowed_types or domain_id not in allowed_domains:
                continue
            if not title or not content:
                continue
            confidence = _confidence(raw.get("confidence"))
            rationale = _clean_text(raw.get("rationale") or raw.get("reason"), _RATIONALE_MAX_CHARS)
            evidence = _evidence(raw.get("evidence"))
            payload = raw.get("payload")
            if not isinstance(payload, dict):
                payload = raw.get("fact") if isinstance(raw.get("fact"), dict) else {}
            payload = _redact_json_payload(payload) if isinstance(payload, dict) else {}
            proposals.append(
                ReviewProposal(
                    id=f"review_{uuid.uuid4().hex}",
                    created_at=now,
                    session_key=session_key,
                    turn_id=turn_id,
                    source_message_id=message_id,
                    proposal_type=proposal_type,
                    domain_id=domain_id,
                    title=title,
                    content=content,
                    rationale=rationale,
                    confidence=confidence,
                    evidence=evidence,
                    payload=payload,
                )
            )
        return proposals


def _proposal_type(record: dict[str, Any]) -> str:
    return str(record.get("proposal_type") or record.get("type") or "").strip().lower()


def _proposal_payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload")
    if isinstance(payload, dict):
        nested = payload.get("fact")
        if isinstance(nested, dict):
            merged = dict(payload)
            merged.update(nested)
            return merged
        return payload
    return {}


def _redact_json_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact_memory_text(value)
    if isinstance(value, list):
        return [_redact_json_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_json_payload(item) for key, item in value.items()}
    return value


def _redacted_record(record: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(record)
    for key in ("title", "content", "rationale", "review_reason"):
        if isinstance(cleaned.get(key), str):
            cleaned[key] = redact_memory_text(cleaned[key])
    evidence = cleaned.get("evidence")
    if isinstance(evidence, list):
        cleaned["evidence"] = [
            redact_memory_text(str(item)) for item in evidence if str(item).strip()
        ]
    if isinstance(cleaned.get("payload"), dict):
        cleaned["payload"] = _redact_json_payload(cleaned["payload"])
    if isinstance(cleaned.get("review_event"), dict):
        cleaned["review_event"] = _redact_json_payload(cleaned["review_event"])
    return cleaned


def _safe_category(value: Any, default: str = "note") -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in VALID_CATEGORIES else default


def _safe_owner(value: Any, default: str = "user") -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in VALID_OWNERS else default


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    haystack = text.casefold()
    return any(needle.casefold() in haystack for needle in needles)


def _pending_confirmation_required(*, category: str, scope: str, content: str, evidence: str) -> bool:
    combined = " ".join([category, scope, content, evidence])
    return (
        category in HIGH_RISK_CATEGORIES
        or _contains_any(combined, HIGH_RISK_KEYWORDS)
        or _contains_any(combined, TEMPORARY_LANGUAGE)
        or _contains_any(combined, UNCERTAIN_LANGUAGE)
    )


def _review_confidence(record: dict[str, Any], payload: dict[str, Any]) -> float:
    raw = payload.get("confidence")
    if raw is None:
        raw = record.get("confidence")
    confidence = _confidence(raw)
    return 0.7 if confidence is None else confidence


def _fact_fields_from_proposal(record: dict[str, Any]) -> dict[str, Any]:
    proposal_type = _proposal_type(record)
    payload = _proposal_payload(record)
    content = _clean_text(payload.get("content") or record.get("content"), _CONTENT_MAX_CHARS)
    if not content:
        raise ValueError("proposal content cannot be empty")

    if proposal_type == "memory":
        category = "note"
        scope = "review.memory"
        owner = "user"
    else:
        category = _safe_category(payload.get("category"), "note")
        scope = str(payload.get("scope") or "review.fact").strip() or "review.fact"
        owner = _safe_owner(payload.get("owner"), "user")

    evidence_items = record.get("evidence") if isinstance(record.get("evidence"), list) else []
    evidence = next((str(item).strip() for item in evidence_items if str(item).strip()), "")
    if not evidence:
        evidence = str(record.get("rationale") or record.get("title") or "").strip()
    evidence = _clean_text(evidence, _EVIDENCE_MAX_CHARS)
    requires_confirmation = _pending_confirmation_required(
        category=category,
        scope=scope,
        content=content,
        evidence=evidence,
    )
    return {
        "content": content,
        "category": category,
        "scope": scope,
        "owner": owner,
        "source_cursors": [],
        "source_excerpt": evidence,
        "confidence": _review_confidence(record, payload),
        "expires_at": payload.get("expires_at") if isinstance(payload.get("expires_at"), str) else None,
        "requires_confirmation": True if requires_confirmation else False,
        "status": "pending_confirmation" if requires_confirmation else "active",
        "supersedes_fact_id": (
            payload.get("supersedes_fact_id")
            if isinstance(payload.get("supersedes_fact_id"), str)
            else None
        ),
    }


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                block_text = block.get("text")
                if isinstance(block_text, str):
                    parts.append(block_text)
        text = "\n".join(parts)
    else:
        text = ""
    if not text and message.get("tool_calls"):
        names = []
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = function.get("name") or call.get("name")
                if name:
                    names.append(str(name))
        if names:
            text = "[assistant requested tools: " + ", ".join(names) + "]"
    return text


def _load_json_payload(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Background review returned invalid JSON")
        return None


def _clean_text(value: Any, max_chars: int) -> str:
    if value is None:
        return ""
    return truncate_text(redact_memory_text(str(value).strip()), max_chars)


def _confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(number, 1.0))


def _evidence(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = [item for item in value if isinstance(item, str)]
    else:
        candidates = []
    cleaned: list[str] = []
    for item in candidates[:_EVIDENCE_MAX_ITEMS]:
        text = _clean_text(item, _EVIDENCE_MAX_CHARS)
        if text:
            cleaned.append(text)
    return cleaned
