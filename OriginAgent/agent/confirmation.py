"""Low-friction runtime confirmations for safety-gated actions."""

from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from filelock import FileLock
from loguru import logger

from OriginAgent.agent.action_safety import ActionDecision, ActionRequest
from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.action_privacy import sanitize_metadata
from OriginAgent.config.schema import ConfirmationConfig
from OriginAgent.utils.helpers import ensure_dir, truncate_text

VALID_KINDS = {"action_confirmation", "fact_confirmation", "notify_only", "tool_approval"}
VALID_STATUSES = {
    "pending",
    "notified",
    "confirmed_once",
    "confirmed_persistent",
    "rejected",
    "expired",
    "cancelled",
    "deferred",
}
VALID_RESULT_DECISIONS = {"confirmed", "rejected", "persistent", "expired", "cancelled", "unclear"}
ACTION_SNAPSHOT_FORBIDDEN_KEYS = {
    "mac",
    "ip",
    "ssid",
    "bssid",
    "image",
    "photo",
    "face_embedding",
    "voice_embedding",
    "raw_audio",
    "raw_video",
    "access_log",
    "door_log",
    "device_fingerprint",
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "password",
    "private_key",
    "secret",
    "token",
}
_DEFAULT_CONFIRMATION_CONFIG = ConfirmationConfig()
PROMPT_MAX_CHARS = _DEFAULT_CONFIRMATION_CONFIG.prompt_max_chars
REASON_MAX_CHARS = _DEFAULT_CONFIRMATION_CONFIG.reason_max_chars
NOTIFY_TTL = timedelta(seconds=_DEFAULT_CONFIRMATION_CONFIG.notify_ttl_seconds)
CONFIRM_TTL_BY_RISK = {
    key: timedelta(seconds=max(0, int(value)))
    for key, value in _DEFAULT_CONFIRMATION_CONFIG.confirm_ttl_by_risk.items()
}

CONFIRM_ONCE_EXACT_PHRASES = {
    # 原有固定短语
    "是",
    "可以",
    "继续",
    "确认",
    "就这次",
    "只是这次",
    "yes",
    "continue",
    "just this time",
    # 方案 C1: 自然语言扩充（中文常用确认）
    "好",
    "好的",
    "行",
    "行吧",
    "批准",
    "同意",
    "没问题",
    "可以的",
    "去吧",
    "准了",
    "授权",
    "嗯",
    "嗯嗯",
    "ok",
    "approve",
    "authorized",
    "you decide",
}
CONFIRM_ONCE_BOUNDARY_PHRASES = {"yes", "continue", "just this time", "ok", "approve", "yes, go ahead", "go ahead"}
REJECT_EXACT_PHRASES = {
    # 原有
    "不",
    "不可以",
    "不是",
    "取消",
    "不要",
    "别执行",
    "no",
    "cancel",
    # 方案 C1: 自然语言扩充
    "不行",
    "别",
    "算了",
    "先不要",
    "不批准",
    "不同意",
    "拒绝",
    "先别",
    "stop",
    "reject",
    "denied",
}
REJECT_BOUNDARY_PHRASES = {"no", "cancel", "stop", "reject", "denied"}
PERSISTENT_PHRASES = {
    "以后都这样",
    "设为规则",
    "以后不用问",
    "always",
    "remember this",
    "永久",
    "永远",
    "permanent",
}
# 方案 C1: 持久授权 TTL 解析正则（支持"3 天/1 天/12 小时/3600 秒/7 days/12 hours"等）
_PERSISTENT_TTL_PATTERN = re.compile(
    r"(?P<value>\d+)\s*(?P<unit>天|日|days?|day|小时|hours?|hour|hrs?|h|分钟|minutes?|minute|min|m|秒|seconds?|second|sec|s)",
    re.IGNORECASE,
)
# 默认持久 TTL（用户未指定时）：7 天（与"以后都这样"语义匹配，但仍受 grant_store 分级限制）
_DEFAULT_PERSISTENT_TTL_SECONDS = 7 * 24 * 3600

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_OPENAI_KEY_RE = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")
_GITHUB_TOKEN_RE = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password)\b(\s*[:=]\s*)([\"']?)"
    r"[^\"'\s,;]{8,}([\"']?)"
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_CHINA_ID_RE = re.compile(
    r"(?<!\d)\d{6}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])"
    r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{16,}(?!\d)")


@dataclass
class ConfirmationRequest:
    confirmation_id: str
    kind: str
    status: str
    prompt: str
    action: str | None
    scope: str | None
    trigger: str | None
    risk: str | None
    requested_by: str | None
    decision_reason: str
    presence_status: str
    related_fact_ids: list[str]
    created_at: str
    expires_at: str
    requires_presence_empty: bool = False
    uses_facts: list[str] = field(default_factory=list)
    action_payload: dict[str, str] = field(default_factory=dict)
    idempotency_key: str | None = None
    consumed_at: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.confirmation_id = _required_str(self.confirmation_id, "confirmation_id")
        self.kind = _normalize_kind(self.kind)
        self.status = _normalize_status(self.status)
        self.prompt = _sanitize_text(self.prompt, PROMPT_MAX_CHARS)
        self.decision_reason = _sanitize_text(self.decision_reason, REASON_MAX_CHARS)
        self.presence_status = str(self.presence_status or "unknown")
        self.related_fact_ids = _normalize_string_list(self.related_fact_ids)
        self.created_at = _required_str(self.created_at, "created_at")
        self.expires_at = _required_str(self.expires_at, "expires_at")
        self.requires_presence_empty = bool(self.requires_presence_empty)
        self.uses_facts = _normalize_string_list(self.uses_facts)
        self.action_payload = _sanitize_action_payload_snapshot(self.action_payload)
        self.idempotency_key = _optional_str(self.idempotency_key)
        self.consumed_at = _optional_str(self.consumed_at)
        self.metadata = _sanitize_confirmation_metadata(self.metadata)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ConfirmationRequest":
        raw_payload = raw.get("action_payload")
        return cls(
            confirmation_id=_required_str(raw.get("confirmation_id"), "confirmation_id"),
            kind=_required_str(raw.get("kind"), "kind"),
            status=_required_str(raw.get("status"), "status"),
            prompt=str(raw.get("prompt", "")),
            action=_optional_str(raw.get("action")),
            scope=_optional_str(raw.get("scope")),
            trigger=_optional_str(raw.get("trigger")),
            risk=_optional_str(raw.get("risk")),
            requested_by=_optional_str(raw.get("requested_by")),
            decision_reason=str(raw.get("decision_reason", "")),
            presence_status=str(raw.get("presence_status", "unknown")),
            related_fact_ids=_normalize_string_list(raw.get("related_fact_ids", [])),
            created_at=_required_str(raw.get("created_at"), "created_at"),
            expires_at=_required_str(raw.get("expires_at"), "expires_at"),
            requires_presence_empty=bool(raw.get("requires_presence_empty", False)),
            uses_facts=_normalize_string_list(raw.get("uses_facts", [])),
            action_payload=_sanitize_action_payload_snapshot(
                raw_payload if isinstance(raw_payload, dict) else {}
            ),
            idempotency_key=_optional_str(raw.get("idempotency_key")),
            consumed_at=_optional_str(raw.get("consumed_at")),
            metadata=(
                raw.get("metadata")
                if isinstance(raw.get("metadata"), dict)
                else {}
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConfirmationResult:
    confirmation_id: str
    decision: str
    reason: str
    applies_once: bool = True

    def __post_init__(self) -> None:
        self.confirmation_id = str(self.confirmation_id)
        self.decision = _normalize_result_decision(self.decision)
        self.reason = _sanitize_text(self.reason, REASON_MAX_CHARS)


class ConfirmationPromptBuilder(Protocol):
    def build(
        self,
        request: ActionRequest,
        decision: ActionDecision,
        *,
        kind: str,
    ) -> str:
        ...


class DefaultConfirmationPromptBuilder:
    def build(
        self,
        request: ActionRequest,
        decision: ActionDecision,
        *,
        kind: str,
    ) -> str:
        action_text = _human_action(request)
        if kind == "notify_only":
            return _sanitize_text(f"I will notify you about {action_text}.", PROMPT_MAX_CHARS)
        if request.requires_presence_empty and decision.presence_status != "empty":
            return _sanitize_text(
                f"This action requires confirming the space is empty, but that is not established. Continue with {action_text} just this once?",
                PROMPT_MAX_CHARS,
            )
        if decision.presence_status == "unknown":
            return _sanitize_text(
                f"Occupancy is unknown, so {action_text} will not run automatically. Continue now?",
                PROMPT_MAX_CHARS,
            )
        if "fact" in decision.reason.casefold() or decision.pending_facts:
            return _sanitize_text(
                f"{action_text} depends on an unconfirmed fact. Continue just this once or cancel?",
                PROMPT_MAX_CHARS,
            )
        return _sanitize_text(
            f"Continue with {action_text} just this once?",
            PROMPT_MAX_CHARS,
        )


class PendingConfirmationStore:
    def __init__(
        self,
        workspace: Path,
        *,
        pending_file: Path | None = None,
        lock_factory: Callable[[], FileLock] | None = None,
    ):
        self.workspace = workspace
        self.memory_dir = ensure_dir(workspace / "memory")
        self.pending_file = pending_file or self.memory_dir / "pending_confirmations.json"
        self._lock_file = self.memory_dir / ".lock"
        self._lock_factory = lock_factory
        self._cached_signature: tuple[int, int] | None = None
        self._cached_raw_confirmations: list[dict[str, Any]] = []

    def _locked(self) -> FileLock:
        if self._lock_factory is not None:
            return self._lock_factory()
        return FileLock(str(self._lock_file))

    def read_all(self) -> list[ConfirmationRequest]:
        with self._locked():
            return self.read_all_unlocked()

    def read_all_unlocked(self) -> list[ConfirmationRequest]:
        items = self._load_raw_confirmations_unlocked()
        confirmations: list[ConfirmationRequest] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            with suppress(ValueError):
                confirmations.append(ConfirmationRequest.from_dict(item))
        return confirmations

    def write_all(self, confirmations: list[ConfirmationRequest]) -> None:
        with self._locked():
            self.write_all_unlocked(confirmations)

    def write_all_unlocked(self, confirmations: list[ConfirmationRequest]) -> None:
        raw_confirmations = [
            confirmation.to_dict()
            for confirmation in sorted(
                confirmations,
                key=lambda item: (item.created_at, item.confirmation_id),
            )
        ]
        payload = {"confirmations": raw_confirmations}
        _write_text_atomic(
            self.pending_file,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        self._refresh_cache_from_raw(raw_confirmations)

    def upsert(self, confirmation: ConfirmationRequest) -> ConfirmationRequest:
        with self._locked():
            confirmations = self.read_all_unlocked()
            replaced = False
            for index, existing in enumerate(confirmations):
                if existing.confirmation_id == confirmation.confirmation_id:
                    confirmations[index] = confirmation
                    replaced = True
                    break
            if not replaced:
                confirmations.append(confirmation)
            self.write_all_unlocked(confirmations)
        return confirmation

    def get(self, confirmation_id: str) -> ConfirmationRequest | None:
        with self._locked():
            return self.get_unlocked(confirmation_id)

    def get_unlocked(self, confirmation_id: str) -> ConfirmationRequest | None:
        for confirmation in self.read_all_unlocked():
            if confirmation.confirmation_id == confirmation_id:
                return confirmation
        return None

    def claim_consumption(
        self,
        confirmation_id: str,
        *,
        now: datetime | None = None,
        kinds: tuple[str, ...] | None = None,
    ) -> ConfirmationRequest | None:
        current_time = _normalize_datetime(now)
        consumed_at = _format_datetime(current_time)
        allowed_kinds = tuple(str(kind or "").strip() for kind in (kinds or ("action_confirmation",)))
        with self._locked():
            confirmations = self.read_all_unlocked()
            for confirmation in confirmations:
                if confirmation.confirmation_id != confirmation_id:
                    continue
                if _is_expired(confirmation, now=current_time):
                    confirmation.status = "expired"
                    self.write_all_unlocked(confirmations)
                    return None
                if (
                    confirmation.kind not in allowed_kinds
                    or confirmation.status != "confirmed_once"
                    or confirmation.consumed_at is not None
                ):
                    return None
                confirmation.consumed_at = consumed_at
                self.write_all_unlocked(confirmations)
                return confirmation
        return None

    def expire_confirmation_if_needed(
        self,
        confirmation_id: str,
        *,
        now: datetime | None = None,
    ) -> ConfirmationRequest | None:
        current_time = _normalize_datetime(now)
        with self._locked():
            confirmations = self.read_all_unlocked()
            for confirmation in confirmations:
                if confirmation.confirmation_id != confirmation_id:
                    continue
                if _is_expired(confirmation, now=current_time):
                    confirmation.status = "expired"
                    self.write_all_unlocked(confirmations)
                return confirmation
        return None

    def _load_raw_confirmations_unlocked(self) -> list[dict[str, Any]]:
        signature = self._file_signature()
        if signature is not None and signature == self._cached_signature:
            return [dict(item) for item in self._cached_raw_confirmations]
        try:
            raw = json.loads(self.pending_file.read_text(encoding="utf-8"))
            items = raw.get("confirmations", []) if isinstance(raw, dict) else []
            if not isinstance(items, list):
                raise ValueError("confirmations must be a list")
        except FileNotFoundError:
            self._refresh_cache_from_raw([])
            return []
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            logger.warning(
                "Failed to read pending confirmations from {}; treating as empty",
                self.pending_file,
            )
            self._refresh_cache_from_raw([])
            return []
        normalized = [dict(item) for item in items if isinstance(item, dict)]
        self._refresh_cache_from_raw(normalized)
        return [dict(item) for item in normalized]

    def _refresh_cache_from_raw(self, items: list[dict[str, Any]]) -> None:
        self._cached_raw_confirmations = [dict(item) for item in items]
        self._cached_signature = self._file_signature()

    def _file_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.pending_file.stat()
        except FileNotFoundError:
            return None
        except OSError:
            return None
        return (int(stat.st_mtime_ns), int(stat.st_size))


class ConfirmationManager:
    def __init__(
        self,
        workspace: Path,
        *,
        store: PendingConfirmationStore | None = None,
        audit_logger: AuditLogger | None = None,
        prompt_builder: ConfirmationPromptBuilder | None = None,
        config: ConfirmationConfig | None = None,
    ):
        self.workspace = workspace
        self.store = store or PendingConfirmationStore(workspace)
        self.audit_logger = audit_logger
        self.prompt_builder = prompt_builder or DefaultConfirmationPromptBuilder()
        self.config = config or ConfirmationConfig()

    def create_from_action_decision(
        self,
        request: ActionRequest,
        decision: ActionDecision,
        *,
        now: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        action_payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ConfirmationRequest | None:
        if decision.decision in {"allow", "deny"}:
            return None
        if decision.decision == "ask_confirmation":
            kind = "action_confirmation"
            status = "pending"
        elif decision.decision == "notify_only":
            kind = "notify_only"
            status = "notified"
        else:
            return None

        created_at = _normalize_datetime(now)
        expires_at = created_at + _ttl_for(kind, request.risk, self.config)
        confirmation = ConfirmationRequest(
            confirmation_id=f"confirmation_{uuid.uuid4().hex[:12]}",
            kind=kind,
            status=status,
            prompt=self.prompt_builder.build(request, decision, kind=kind),
            action=request.action,
            scope=request.scope,
            trigger=request.trigger,
            risk=request.risk,
            requested_by=request.requested_by,
            decision_reason=decision.reason,
            presence_status=decision.presence_status,
            related_fact_ids=[
                *decision.pending_facts,
                *decision.supporting_facts,
            ],
            created_at=_format_datetime(created_at),
            expires_at=_format_datetime(expires_at),
            requires_presence_empty=request.requires_presence_empty,
            uses_facts=list(request.uses_facts),
            action_payload=_sanitize_action_payload_snapshot(action_payload or {}),
            idempotency_key=idempotency_key,
            consumed_at=None,
            metadata=_sanitize_confirmation_metadata(metadata or {}),
        )
        stored = self.store.upsert(confirmation)
        self._audit_confirmation_event(
            stored,
            decision=("notified" if stored.kind == "notify_only" else "created"),
            reason=stored.decision_reason,
            now=created_at,
            metadata={
                "confirmation_status": stored.status,
                "confirmation_kind": stored.kind,
                "expires_at": stored.expires_at,
                "consumed": stored.consumed_at is not None,
            },
        )
        return stored

    def create_tool_approval(
        self,
        *,
        tool_name: str,
        prompt: str,
        decision_reason: str,
        requested_by: str | None = None,
        trigger: str | None = None,
        risk: str | None = "high",
        scope: str | None = None,
        session_key: str | None = None,
        metadata: dict[str, Any] | None = None,
        action_payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> ConfirmationRequest:
        current_time = _normalize_datetime(now)
        if idempotency_key:
            for existing in self.store.read_all():
                if (
                    existing.kind == "tool_approval"
                    and existing.status == "pending"
                    and not _is_expired(existing, now=current_time)
                    and existing.idempotency_key == idempotency_key
                ):
                    return existing
        expires_at = current_time + _ttl_for("tool_approval", risk, self.config)
        confirmation_metadata = dict(metadata or {})
        if session_key:
            confirmation_metadata.setdefault("session_key", session_key)
        confirmation_metadata.setdefault("tool_name", tool_name)
        # 方案 B: cron 触发的 tool_approval 标记为 deferred，等用户下次对话时主动询问
        # 用户触发的 tool_approval 不标记（用户就在线，可立即询问）
        if trigger == "scheduled":
            confirmation_metadata.setdefault("deferred_for_user", "true")
        confirmation = ConfirmationRequest(
            confirmation_id=f"confirmation_{uuid.uuid4().hex[:12]}",
            kind="tool_approval",
            status="pending",
            prompt=_sanitize_text(prompt, PROMPT_MAX_CHARS),
            action=f"tool:{tool_name}",
            scope=_optional_str(scope),
            trigger=_optional_str(trigger),
            risk="high" if not _optional_str(risk) else _optional_str(risk),
            requested_by=_optional_str(requested_by),
            decision_reason=_sanitize_text(decision_reason, REASON_MAX_CHARS),
            presence_status="unknown",
            related_fact_ids=[],
            created_at=_format_datetime(current_time),
            expires_at=_format_datetime(expires_at),
            action_payload=_sanitize_action_payload_snapshot(action_payload or {}),
            idempotency_key=_optional_str(idempotency_key),
            metadata=_sanitize_confirmation_metadata(confirmation_metadata),
        )
        stored = self.store.upsert(confirmation)
        self._audit_confirmation_event(
            stored,
            decision="created",
            reason=stored.decision_reason,
            now=current_time,
            metadata={
                "confirmation_status": stored.status,
                "confirmation_kind": stored.kind,
                "expires_at": stored.expires_at,
                "consumed": stored.consumed_at is not None,
            },
        )
        return stored

    def resolve_user_reply(
        self,
        confirmation_id: str,
        reply: str,
        *,
        now: datetime | None = None,
    ) -> ConfirmationResult:
        current_time = _normalize_datetime(now)
        audit_event: dict[str, Any] | None = None
        with self.store._locked():
            confirmations = self.store.read_all_unlocked()
            target = next(
                (
                    confirmation
                    for confirmation in confirmations
                    if confirmation.confirmation_id == confirmation_id
                ),
                None,
            )
            if target is None:
                result = ConfirmationResult(
                    confirmation_id=confirmation_id,
                    decision="unclear",
                    reason="confirmation not found",
                )
                audit_event = {
                    "confirmation_id": confirmation_id,
                    "decision": "unclear_reply",
                    "reason": result.reason,
                    "now": current_time,
                    "metadata": {"confirmation_status": "missing"},
                }
            elif _is_expired(target, now=current_time):
                target.status = "expired"
                self.store.write_all_unlocked(confirmations)
                result = ConfirmationResult(
                    confirmation_id=target.confirmation_id,
                    decision="expired",
                    reason="confirmation expired",
                )
                audit_event = self._confirmation_audit_event(
                    target,
                    decision="expired",
                    reason=result.reason,
                    now=current_time,
                )
            elif target.kind == "notify_only":
                result = ConfirmationResult(
                    confirmation_id=target.confirmation_id,
                    decision="rejected",
                    reason="notification is not executable",
                )
                audit_event = self._confirmation_audit_event(
                    target,
                    decision="rejected",
                    reason=result.reason,
                    now=current_time,
                )
            elif target.status != "pending":
                result = ConfirmationResult(
                    confirmation_id=target.confirmation_id,
                    decision="unclear",
                    reason=f"confirmation is {target.status}",
                )
                audit_event = self._confirmation_audit_event(
                    target,
                    decision="unclear_reply",
                    reason=result.reason,
                    now=current_time,
                )
            else:
                classification = _classify_reply(reply)
                if classification == "confirmed":
                    target.status = "confirmed_once"
                    self.store.write_all_unlocked(confirmations)
                    result = ConfirmationResult(
                        confirmation_id=target.confirmation_id,
                        decision="confirmed",
                        reason="confirmed once",
                        applies_once=True,
                    )
                    audit_event = self._confirmation_audit_event(
                        target,
                        decision="confirmed_once",
                        reason=result.reason,
                        now=current_time,
                    )
                elif classification == "rejected":
                    target.status = "rejected"
                    self.store.write_all_unlocked(confirmations)
                    result = ConfirmationResult(
                        confirmation_id=target.confirmation_id,
                        decision="rejected",
                        reason="user rejected",
                    )
                    audit_event = self._confirmation_audit_event(
                        target,
                        decision="rejected",
                        reason=result.reason,
                        now=current_time,
                    )
                elif classification == "persistent":
                    # 方案 C3: 持久授权支持（受 grant_store 分级限制）
                    # 标记 confirmation 为 confirmed_persistent，让 _consume_tool_approval_reply
                    # 能识别并传递 TTL 给 issue_tool_approval_grant
                    target.status = "confirmed_persistent"
                    self.store.write_all_unlocked(confirmations)
                    result = ConfirmationResult(
                        confirmation_id=target.confirmation_id,
                        decision="persistent",
                        reason="user granted persistent approval",
                        applies_once=False,
                    )
                    audit_event = self._confirmation_audit_event(
                        target,
                        decision="confirmed_persistent",
                        reason=result.reason,
                        now=current_time,
                    )
                else:
                    result = ConfirmationResult(
                        confirmation_id=target.confirmation_id,
                        decision="unclear",
                        reason="reply was unclear",
                    )
                    audit_event = self._confirmation_audit_event(
                        target,
                        decision="unclear_reply",
                        reason=result.reason,
                        now=current_time,
                    )
        if audit_event is not None:
            self._audit_confirmation_event_by_fields(**audit_event)
        return result

    def expire_old(self, now: datetime | None = None) -> int:
        current_time = _normalize_datetime(now)
        expired_events: list[dict[str, Any]] = []
        with self.store._locked():
            confirmations = self.store.read_all_unlocked()
            changed = 0
            for confirmation in confirmations:
                if confirmation.status in {"pending", "notified"} and _is_expired(
                    confirmation,
                    now=current_time,
                ):
                    confirmation.status = "expired"
                    changed += 1
                    expired_events.append(self._confirmation_audit_event(
                        confirmation,
                        decision="expired",
                        reason="confirmation expired",
                        now=current_time,
                    ))
            if changed:
                self.store.write_all_unlocked(confirmations)
        for audit_event in expired_events:
            self._audit_confirmation_event_by_fields(**audit_event)
        return changed

    def claim_consumption(
        self,
        confirmation_id: str,
        *,
        now: datetime | None = None,
        kinds: tuple[str, ...] | None = None,
    ) -> ConfirmationRequest | None:
        current_time = _normalize_datetime(now)
        confirmation = self.store.claim_consumption(
            confirmation_id,
            now=current_time,
            kinds=kinds,
        )
        if confirmation is not None:
            self._audit_confirmation_event(
                confirmation,
                decision="consumed",
                reason="confirmation consumed",
                now=current_time,
                metadata={
                    "confirmation_status": confirmation.status,
                    "confirmation_kind": confirmation.kind,
                    "expires_at": confirmation.expires_at,
                    "consumed": confirmation.consumed_at is not None,
                },
            )
        return confirmation

    def retry_confirmation(
        self,
        confirmation_id: str,
        requested_by: str,
        now: datetime | None = None,
    ) -> ConfirmationRequest:
        current_time = _normalize_datetime(now)
        with self.store._locked():
            confirmations = self.store.read_all_unlocked()
            target = next(
                (
                    confirmation
                    for confirmation in confirmations
                    if confirmation.confirmation_id == confirmation_id
                ),
                None,
            )
            if target is None:
                raise ValueError("confirmation_not_found")
            if target.kind not in {"action_confirmation", "tool_approval"}:
                raise ValueError("retry_not_allowed")
            if target.kind in {"notify_only", "fact_confirmation"}:
                raise ValueError("retry_not_allowed")
            if target.status in {"rejected", "cancelled"}:
                raise ValueError("retry_not_allowed")
            if target.status != "expired":
                raise ValueError("retry_not_allowed")
            if target.consumed_at is not None:
                raise ValueError("retry_not_allowed")
            if not target.action or not target.prompt or not target.decision_reason:
                raise ValueError("retry_not_allowed")
            root_id = str(
                target.metadata.get("retry_root_confirmation_id")
                or target.metadata.get("retried_from_confirmation_id")
                or target.confirmation_id
            ).strip() or target.confirmation_id
            existing_count = int(target.metadata.get("retry_count") or "0")
            retry_count = existing_count + 1
            if retry_count > 3:
                raise ValueError("retry_limit_reached")
            expires_at = current_time + _ttl_for(target.kind, target.risk, self.config)
            metadata = dict(target.metadata or {})
            metadata["retried_from_confirmation_id"] = target.confirmation_id
            metadata["retry_root_confirmation_id"] = root_id
            metadata["retry_count"] = str(retry_count)
            retried = ConfirmationRequest(
                confirmation_id=f"confirmation_{uuid.uuid4().hex[:12]}",
                kind=target.kind,
                status="pending",
                prompt=target.prompt,
                action=target.action,
                scope=target.scope,
                trigger=target.trigger,
                risk=target.risk,
                requested_by=_optional_str(requested_by) or target.requested_by,
                decision_reason=target.decision_reason,
                presence_status=target.presence_status,
                related_fact_ids=list(target.related_fact_ids),
                created_at=_format_datetime(current_time),
                expires_at=_format_datetime(expires_at),
                requires_presence_empty=target.requires_presence_empty,
                uses_facts=list(target.uses_facts),
                action_payload=dict(target.action_payload),
                idempotency_key=None,
                consumed_at=None,
                metadata=metadata,
            )
            confirmations.append(retried)
            self.store.write_all_unlocked(confirmations)
            stored = retried
        self._audit_confirmation_event(
            stored,
            decision="retried",
            reason="confirmation retried",
            now=current_time,
            metadata={
                "retried_from_confirmation_id": confirmation_id,
                "retry_root_confirmation_id": root_id,
                "retry_count": retry_count,
            },
        )
        return stored

    def expire_confirmation_if_needed(
        self,
        confirmation_id: str,
        *,
        now: datetime | None = None,
    ) -> ConfirmationRequest | None:
        current_time = _normalize_datetime(now)
        before = self.store.get(confirmation_id)
        confirmation = self.store.expire_confirmation_if_needed(
            confirmation_id,
            now=current_time,
        )
        if (
            confirmation is not None
            and confirmation.status == "expired"
            and before is not None
            and before.status != "expired"
        ):
            self._audit_confirmation_event(
                confirmation,
                decision="expired",
                reason="confirmation expired",
                now=current_time,
            )
        return confirmation

    def list_pending(self) -> list[ConfirmationRequest]:
        return self.list_action_confirmations()

    def list_action_confirmations(self) -> list[ConfirmationRequest]:
        return [
            confirmation
            for confirmation in self.store.read_all()
            if confirmation.kind == "action_confirmation"
            and confirmation.status == "pending"
            and not _is_expired(confirmation)
        ]

    def list_notifications(self) -> list[ConfirmationRequest]:
        return [
            confirmation
            for confirmation in self.store.read_all()
            if confirmation.kind == "notify_only"
            and confirmation.status == "notified"
            and not _is_expired(confirmation)
        ]

    def list_tool_approvals(
        self,
        *,
        session_key: str | None = None,
        include_non_pending: bool = False,
        now: datetime | None = None,
    ) -> list[ConfirmationRequest]:
        current_time = _normalize_datetime(now)
        results: list[ConfirmationRequest] = []
        for confirmation in self.store.read_all():
            if confirmation.kind != "tool_approval":
                continue
            if session_key and confirmation.metadata.get("session_key") != session_key:
                continue
            if not include_non_pending:
                if confirmation.status != "pending" or _is_expired(confirmation, now=current_time):
                    continue
            results.append(confirmation)
        results.sort(key=lambda item: (item.created_at, item.confirmation_id))
        return results

    def latest_pending_tool_approval(
        self,
        session_key: str,
        *,
        now: datetime | None = None,
    ) -> ConfirmationRequest | None:
        approvals = self.list_tool_approvals(session_key=session_key, now=now)
        if not approvals:
            return None
        return approvals[-1]

    def list_deferred_tool_approvals(
        self,
        *,
        session_key: str | None = None,
        now: datetime | None = None,
    ) -> list[ConfirmationRequest]:
        """方案 B: 列出 cron 触发且等待用户下次对话时询问的 tool_approvals。

        只返回 status=pending 且 metadata.deferred_for_user=true 的 confirmation。
        已被用户处理（confirmed/rejected/expired）的不返回。
        """
        current_time = _normalize_datetime(now)
        results: list[ConfirmationRequest] = []
        for confirmation in self.store.read_all():
            if confirmation.kind != "tool_approval":
                continue
            if confirmation.status != "pending":
                continue
            if confirmation.metadata.get("deferred_for_user") != "true":
                continue
            if _is_expired(confirmation, now=current_time):
                continue
            if session_key and confirmation.metadata.get("session_key") != session_key:
                continue
            results.append(confirmation)
        results.sort(key=lambda item: (item.created_at, item.confirmation_id))
        return results

    def _audit_confirmation_event(
        self,
        confirmation: ConfirmationRequest,
        *,
        decision: str,
        reason: str,
        now: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._audit_confirmation_event_by_fields(
            **self._confirmation_audit_event(
                confirmation,
                decision=decision,
                reason=reason,
                now=now,
                metadata=metadata,
            )
        )

    def _confirmation_audit_event(
        self,
        confirmation: ConfirmationRequest,
        *,
        decision: str,
        reason: str,
        now: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged_metadata = {
            "confirmation_status": confirmation.status,
            "confirmation_kind": confirmation.kind,
            "expires_at": confirmation.expires_at,
            "consumed": confirmation.consumed_at is not None,
            **(metadata or {}),
        }
        return {
            "confirmation_id": confirmation.confirmation_id,
            "decision": decision,
            "reason": reason,
            "now": now,
            "actor_id": confirmation.requested_by,
            "action": confirmation.action,
            "scope": confirmation.scope,
            "risk": confirmation.risk,
            "trigger": confirmation.trigger,
            "metadata": merged_metadata,
        }

    def _audit_confirmation_event_by_fields(
        self,
        *,
        confirmation_id: str,
        decision: str,
        reason: str,
        now: datetime,
        actor_id: str | None = None,
        action: str | None = None,
        scope: str | None = None,
        risk: str | None = None,
        trigger: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.audit_logger is None:
            return
        try:
            self.audit_logger.log_confirmation_event(
                confirmation_id=confirmation_id,
                actor_id=actor_id,
                action=action,
                scope=scope,
                risk=risk,
                trigger=trigger,
                decision=decision,
                reason=reason,
                metadata=metadata or {},
                created_at=now,
            )
        except Exception as exc:
            logger.warning("Audit confirmation event write failed: {}", exc)


def maybe_create_confirmation(
    request: ActionRequest,
    decision: ActionDecision,
    manager: ConfirmationManager,
) -> ConfirmationRequest | None:
    return manager.create_from_action_decision(request, decision)


def _build_prompt(
    request: ActionRequest,
    decision: ActionDecision,
    *,
    kind: str,
) -> str:
    return DefaultConfirmationPromptBuilder().build(request, decision, kind=kind)


def _human_action(request: ActionRequest) -> str:
    if request.scope:
        return f"'{request.action}' ({request.scope})"
    return f"'{request.action}'"


def _ttl_for(
    kind: str,
    risk: str | None,
    config: ConfirmationConfig | None = None,
) -> timedelta:
    cfg = config or _DEFAULT_CONFIRMATION_CONFIG
    if kind == "notify_only":
        return timedelta(seconds=max(0, int(cfg.notify_ttl_seconds)))
    ttl_map = {
        str(key or "").strip().lower(): timedelta(seconds=max(0, int(value)))
        for key, value in dict(cfg.confirm_ttl_by_risk).items()
    }
    if "low" not in ttl_map:
        ttl_map["low"] = timedelta(seconds=10 * 60)
    return ttl_map.get((risk or "low").strip().lower(), ttl_map["low"])


def classify_confirmation_reply(reply: str) -> str:
    return _classify_reply(reply)


def classify_confirmation_reply_with_ttl(reply: str) -> tuple[str, int | None]:
    """方案 C1: 分类回复并解析持久授权的 TTL。

    返回 (decision, ttl_seconds)：
    - ("persistent", Some(n)): 用户要求持久授权并指定了 TTL（如"以后都这样 3 天"）
    - ("persistent", None): 用户要求持久授权但未指定 TTL（使用默认 7 天）
    - ("confirmed", None): 单次确认
    - ("rejected", None): 拒绝
    - ("unclear", None): 含糊不清

    ttl_seconds 的实际生效仍受 grant_store 分级限制（exec/cron/spawn 不能持久）。
    """
    decision = _classify_reply(reply)
    if decision != "persistent":
        return (decision, None)
    # 解析 TTL（如"3 天"、"12 小时"、"7 days"）
    normalized = re.sub(r"\s+", " ", reply.strip().casefold())
    match = _PERSISTENT_TTL_PATTERN.search(normalized)
    if match is None:
        return ("persistent", None)  # 用户说"以后都这样"但没给时长
    value = int(match.group("value"))
    unit = match.group("unit").lower()
    # 单位换算到秒
    if unit in {"天", "日", "day", "days"}:
        return ("persistent", value * 24 * 3600)
    if unit in {"小时", "hour", "hours", "hrs", "hr", "h"}:
        return ("persistent", value * 3600)
    if unit in {"分钟", "minute", "minutes", "min", "m"}:
        return ("persistent", value * 60)
    if unit in {"秒", "second", "seconds", "sec", "s"}:
        return ("persistent", value)
    return ("persistent", None)


def _classify_reply(reply: str) -> str:
    normalized = re.sub(r"\s+", " ", reply.strip().casefold())
    if not normalized:
        return "unclear"
    if any(phrase in normalized for phrase in PERSISTENT_PHRASES):
        return "persistent"
    if normalized in REJECT_EXACT_PHRASES:
        return "rejected"
    if normalized in CONFIRM_ONCE_EXACT_PHRASES:
        return "confirmed"
    if _contains_boundary_phrase(normalized, REJECT_BOUNDARY_PHRASES):
        return "rejected"
    if _contains_boundary_phrase(normalized, CONFIRM_ONCE_BOUNDARY_PHRASES):
        return "confirmed"
    # 方案 C1: 复合短语兜底匹配（如"好的，授权吧"、"行，做吧"）
    # 只要包含确认关键词且不含拒绝关键词，就视为 confirmed
    if _contains_confirmation_keyword(normalized) and not _contains_rejection_keyword(normalized):
        return "confirmed"
    return "unclear"


# 方案 C1: 确认关键词子串匹配（处理"好的，授权吧"等复合短语）
_CONFIRM_KEYWORDS = {
    "好", "行", "可以", "批准", "同意", "授权", "准了", "没问题", "去吧",
    "ok", "approve", "yes",
}
_REJECT_KEYWORDS = {
    "不", "别", "取消", "拒绝", "算了", "stop", "no", "cancel", "reject",
}


def _contains_confirmation_keyword(text: str) -> bool:
    return any(keyword in text for keyword in _CONFIRM_KEYWORDS)


def _contains_rejection_keyword(text: str) -> bool:
    return any(keyword in text for keyword in _REJECT_KEYWORDS)


def _contains_boundary_phrase(text: str, phrases: set[str]) -> bool:
    return any(
        re.search(rf"\b{re.escape(phrase)}\b", text)
        for phrase in phrases
    )


def _sanitize_confirmation_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    sanitized = sanitize_metadata(metadata)
    return {
        key: _sanitize_text(value, REASON_MAX_CHARS)
        for key, value in sanitized.items()
    }


def _sanitize_action_payload_snapshot(payload: dict[str, Any]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    if not isinstance(payload, dict):
        return sanitized
    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        normalized_key = key.strip()
        if not normalized_key:
            continue
        if normalized_key.casefold() in ACTION_SNAPSHOT_FORBIDDEN_KEYS:
            continue
        if value is None:
            continue
        sanitized[normalized_key] = _sanitize_text(
            _stringify_action_snapshot_value(_sanitize_action_snapshot_value(value)),
            REASON_MAX_CHARS,
        )
    return sanitized


def _sanitize_action_snapshot_value(value: Any) -> Any:
    if isinstance(value, dict):
        nested: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            normalized_key = key.strip()
            if not normalized_key:
                continue
            if normalized_key.casefold() in ACTION_SNAPSHOT_FORBIDDEN_KEYS:
                continue
            if item is None:
                continue
            nested[normalized_key] = _sanitize_action_snapshot_value(item)
        return nested
    if isinstance(value, list):
        return [
            _sanitize_action_snapshot_value(item)
            for item in value
            if item is not None
        ]
    if isinstance(value, str):
        return _sanitize_text(value, REASON_MAX_CHARS)
    return value


def _stringify_action_snapshot_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _sanitize_text(text: str, limit: int) -> str:
    cleaned = str(text or "")
    cleaned = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", cleaned)
    cleaned = _BEARER_TOKEN_RE.sub("[REDACTED_BEARER_TOKEN]", cleaned)
    cleaned = _OPENAI_KEY_RE.sub("[REDACTED_SECRET]", cleaned)
    cleaned = _GITHUB_TOKEN_RE.sub("[REDACTED_SECRET]", cleaned)
    cleaned = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}"
            f"{match.group(3)}[REDACTED_SECRET]{match.group(4)}"
        ),
        cleaned,
    )
    cleaned = _EMAIL_RE.sub("[REDACTED_EMAIL]", cleaned)
    cleaned = _CHINA_ID_RE.sub("[REDACTED_ID]", cleaned)
    cleaned = _LONG_NUMBER_RE.sub("[REDACTED_LONG_NUMBER]", cleaned)
    return truncate_text(cleaned, limit)[:limit]


def _is_expired(
    confirmation: ConfirmationRequest,
    *,
    now: datetime | None = None,
) -> bool:
    current_time = _normalize_datetime(now)
    return _parse_datetime(confirmation.expires_at) <= current_time


def _normalize_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid datetime: {value!r}") from exc
    return _normalize_datetime(parsed)


def _format_datetime(value: datetime) -> str:
    return _normalize_datetime(value).isoformat()


def _required_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    stripped = value.strip()
    return stripped or None


def _normalize_kind(kind: str) -> str:
    normalized = kind.strip().lower()
    if normalized not in VALID_KINDS:
        raise ValueError(f"invalid confirmation kind: {kind!r}")
    return normalized


def _normalize_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized not in VALID_STATUSES:
        raise ValueError(f"invalid confirmation status: {status!r}")
    return normalized


def _normalize_result_decision(decision: str) -> str:
    normalized = decision.strip().lower()
    if normalized not in VALID_RESULT_DECISIONS:
        raise ValueError(f"invalid confirmation result: {decision!r}")
    return normalized


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if stripped:
            result.append(stripped)
    return sorted(set(result))


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        _fsync_parent(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _fsync_parent(path: Path) -> None:
    with suppress(PermissionError, OSError):
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
