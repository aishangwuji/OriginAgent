"""Controlled background review proposal generation.

P5 deliberately stops at proposals.  This module may write
``memory/review_proposals.jsonl`` but must not mutate MEMORY.md, facts.jsonl,
formal skills, workflows, or domain pack files.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from OpenHome.agent.auxiliary_llm import AuxiliaryLLMRouter, call_llm
from OpenHome.agent.domain_packs import DomainPackManager
from OpenHome.agent.memory import redact_memory_text
from OpenHome.providers.base import LLMProvider
from OpenHome.utils.helpers import truncate_text
from OpenHome.utils.prompt_templates import render_template

DEFAULT_ALLOWED_PROPOSAL_TYPES = ("memory", "fact", "skill", "workflow")
PROPOSAL_STORE_RELATIVE = Path("memory") / "review_proposals.jsonl"
_TITLE_MAX_CHARS = 160
_CONTENT_MAX_CHARS = 2400
_RATIONALE_MAX_CHARS = 1200
_EVIDENCE_MAX_ITEMS = 5
_EVIDENCE_MAX_CHARS = 500
_MESSAGE_MAX_CHARS = 1600


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
    source_message_id: str | None = None
    status: str = "pending"

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

    def __init__(self, workspace: Path, *, path: Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.path = path or (self.workspace / PROPOSAL_STORE_RELATIVE)

    def append_many(self, proposals: list[ReviewProposal]) -> int:
        if not proposals:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for proposal in proposals:
                handle.write(json.dumps(proposal.to_json(), ensure_ascii=False) + "\n")
        return len(proposals)

    def iter_all(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(raw, dict):
                        records.append(raw)
        except FileNotFoundError:
            return []
        except OSError:
            logger.exception("Failed to read background review proposal store")
            return []
        return records

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 10), 50))
        return list(reversed(self.iter_all()))[:limit]

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
                )
            )
        return proposals


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
