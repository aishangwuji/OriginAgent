"""Capability grants for delegated runtime tasks."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock

from OriginAgent.agent.confirmation import ConfirmationRequest
from OriginAgent.cron.types import CronPayload
from OriginAgent.security.capabilities import CapabilitySnapshot, CapabilitySource, CapabilityTrigger
from OriginAgent.security.policy import PolicyDeniedError

GrantSource = Literal["admin_config", "user_confirmation", "test"]
_GRANT_ERROR_MESSAGE = "Capability grant is missing, expired, or revoked."
_TOOL_APPROVAL_GRANT_TTL = timedelta(minutes=10)
# 方案 C3: 高危工具不能持久授权（规则 18 安全边界分级）
# 这些工具即使用户说"以后都这样"，也只给单次 10 分钟 TTL
_HIGH_RISK_TOOLS_NO_PERSISTENT = {"exec", "cron", "spawn"}
# 方案 C1: 默认持久 TTL（用户未指定时）
_DEFAULT_PERSISTENT_TTL = timedelta(days=7)


@dataclass(frozen=True)
class CapabilityGrant:
    grant_id: str
    created_by: str
    created_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    source: GrantSource = "test"
    can_exec: bool = False
    can_read_files: bool = False
    can_write_files: bool = False
    can_send_cross_target: bool = False
    can_create_cron: bool = False
    can_spawn: bool = False
    allowed_device_domains: tuple[str, ...] = ()
    allowed_mcp_scopes: tuple[str, ...] = ()
    approval_confirmation_id: str | None = None
    session_key: str | None = None
    tool_name: str | None = None
    purpose: str | None = None
    metadata: dict[str, Any] | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        expires = _parse_datetime(self.expires_at)
        if expires is None:
            return True
        now = _normalize_datetime(now or datetime.now(timezone.utc))
        return expires <= now

    def is_revoked(self) -> bool:
        return bool(self.revoked_at)

    def is_active(self, now: datetime | None = None) -> bool:
        return not self.is_revoked() and not self.is_expired(now)

    def to_snapshot(self, *, trigger: CapabilityTrigger = "scheduled") -> CapabilitySnapshot:
        # C5a grants are cron-only; subagent grants need a separate C5b conversion path.
        return self._to_snapshot_for(source="cron", trigger=trigger)

    def to_subagent_snapshot(self) -> CapabilitySnapshot:
        return self._to_snapshot_for(source="subagent", trigger="subagent")

    def _to_snapshot_for(
        self,
        *,
        source: CapabilitySource,
        trigger: CapabilityTrigger,
    ) -> CapabilitySnapshot:
        return CapabilitySnapshot(
            version=1,
            source=source,
            trigger=trigger,
            can_exec=self.can_exec,
            can_read_files=self.can_read_files,
            can_write_files=self.can_write_files,
            can_send_cross_target=self.can_send_cross_target,
            can_create_cron=self.can_create_cron,
            can_spawn=self.can_spawn,
            allowed_device_domains=tuple(self.allowed_device_domains),
            allowed_mcp_scopes=tuple(self.allowed_mcp_scopes),
        )

    def summary(self) -> dict[str, Any]:
        flags = [
            self.can_exec,
            self.can_read_files,
            self.can_write_files,
            self.can_send_cross_target,
            self.can_create_cron,
            self.can_spawn,
        ]
        return {
            "grant_ref": _grant_ref(self.grant_id),
            "source": self.source,
            "active": self.is_active(),
            "expired": self.is_expired(),
            "revoked": self.is_revoked(),
            "expires_at": self.expires_at,
            "enabled_flags_count": sum(1 for flag in flags if flag),
            "allowed_device_domains": list(self.allowed_device_domains),
            "allowed_mcp_scopes": list(self.allowed_mcp_scopes),
            "tool_name": self.tool_name,
            "purpose": self.purpose,
            "session_key_present": bool(self.session_key),
            "approval_confirmation_present": bool(self.approval_confirmation_id),
        }

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["allowed_device_domains"] = list(self.allowed_device_domains)
        data["allowed_mcp_scopes"] = list(self.allowed_mcp_scopes)
        data["metadata"] = None if self.metadata is None else dict(self.metadata)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityGrant":
        return cls(
            grant_id=str(data.get("grant_id") or data.get("grantId") or ""),
            created_by=str(data.get("created_by") or data.get("createdBy") or ""),
            created_at=str(data.get("created_at") or data.get("createdAt") or ""),
            expires_at=data.get("expires_at") or data.get("expiresAt"),
            revoked_at=data.get("revoked_at") or data.get("revokedAt"),
            source=data.get("source") or "test",
            can_exec=bool(data.get("can_exec") or data.get("canExec")),
            can_read_files=bool(data.get("can_read_files") or data.get("canReadFiles")),
            can_write_files=bool(data.get("can_write_files") or data.get("canWriteFiles")),
            can_send_cross_target=bool(
                data.get("can_send_cross_target") or data.get("canSendCrossTarget")
            ),
            can_create_cron=bool(data.get("can_create_cron") or data.get("canCreateCron")),
            can_spawn=bool(data.get("can_spawn") or data.get("canSpawn")),
            allowed_device_domains=tuple(
                data.get("allowed_device_domains")
                or data.get("allowedDeviceDomains")
                or ()
            ),
            allowed_mcp_scopes=tuple(
                data.get("allowed_mcp_scopes")
                or data.get("allowedMcpScopes")
                or ()
            ),
            approval_confirmation_id=(
                data.get("approval_confirmation_id")
                or data.get("approvalConfirmationId")
            ),
            session_key=data.get("session_key") or data.get("sessionKey"),
            tool_name=data.get("tool_name") or data.get("toolName"),
            purpose=data.get("purpose"),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
        )


class CapabilityGrantStore:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.path = self.workspace / "memory" / "security" / "capability_grants.json"
        self._lock_path = self.workspace / "memory" / "security" / ".capability_grants.lock"
        self._cached_signature: tuple[int, int] | None = None
        self._cached_raw_grants: list[dict[str, Any]] = []

    def _locked(self) -> FileLock:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self._lock_path))

    def put(self, grant: CapabilityGrant) -> None:
        with self._locked():
            grants = {item.grant_id: item for item in self._list_all_unlocked()}
            grants[grant.grant_id] = grant
            self._save_unlocked(list(grants.values()))

    def get(self, grant_id: str) -> CapabilityGrant | None:
        for grant in self.list_all():
            if grant.grant_id == grant_id:
                return grant
        return None

    def revoke(self, grant_id: str, *, revoked_at: datetime | None = None) -> bool:
        with self._locked():
            grants = self._list_all_unlocked()
            found = False
            revoked = _format_datetime(revoked_at or datetime.now(timezone.utc))
            updated: list[CapabilityGrant] = []
            for grant in grants:
                if grant.grant_id == grant_id:
                    found = True
                    updated.append(
                        CapabilityGrant(
                            grant_id=grant.grant_id,
                            created_by=grant.created_by,
                            created_at=grant.created_at,
                            expires_at=grant.expires_at,
                            revoked_at=revoked,
                            source=grant.source,
                            can_exec=grant.can_exec,
                            can_read_files=grant.can_read_files,
                            can_write_files=grant.can_write_files,
                            can_send_cross_target=grant.can_send_cross_target,
                            can_create_cron=grant.can_create_cron,
                            can_spawn=grant.can_spawn,
                            allowed_device_domains=grant.allowed_device_domains,
                            allowed_mcp_scopes=grant.allowed_mcp_scopes,
                            approval_confirmation_id=grant.approval_confirmation_id,
                            session_key=grant.session_key,
                            tool_name=grant.tool_name,
                            purpose=grant.purpose,
                            metadata=(dict(grant.metadata) if isinstance(grant.metadata, dict) else grant.metadata),
                        )
                    )
                else:
                    updated.append(grant)
            if found:
                self._save_unlocked(updated)
            return found

    def list_all(self) -> list[CapabilityGrant]:
        with self._locked():
            return self._list_all_unlocked()

    def _list_all_unlocked(self) -> list[CapabilityGrant]:
        grants = self._load_raw_unlocked()
        result: list[CapabilityGrant] = []
        for item in grants:
            if not isinstance(item, dict):
                continue
            grant = CapabilityGrant.from_dict(item)
            if grant.grant_id:
                result.append(grant)
        return result

    def _load_raw_unlocked(self) -> list[dict[str, Any]]:
        signature = self._file_signature()
        if signature is not None and signature == self._cached_signature:
            return [dict(item) for item in self._cached_raw_grants]
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._refresh_cache_from_raw([])
            return []
        except (json.JSONDecodeError, OSError, TypeError):
            self._refresh_cache_from_raw([])
            return []
        grants = data.get("grants", []) if isinstance(data, dict) else []
        normalized = [dict(item) for item in grants if isinstance(item, dict)]
        self._refresh_cache_from_raw(normalized)
        return [dict(item) for item in normalized]

    def list_active(self) -> list[CapabilityGrant]:
        return [grant for grant in self.list_all() if grant.is_active()]

    def list_active_for_session(
        self,
        session_key: str,
        *,
        tool_name: str | None = None,
        now: datetime | None = None,
    ) -> list[CapabilityGrant]:
        items = [
            grant
            for grant in self.list_all()
            if grant.is_active(now)
            and grant.session_key == session_key
        ]
        if tool_name is not None:
            items = [grant for grant in items if grant.tool_name == tool_name]
        return items

    def latest_active_for_session(
        self,
        session_key: str,
        *,
        tool_name: str | None = None,
        now: datetime | None = None,
    ) -> CapabilityGrant | None:
        items = self.list_active_for_session(session_key, tool_name=tool_name, now=now)
        if not items:
            return None
        items.sort(key=lambda grant: (grant.created_at, grant.grant_id))
        return items[-1]

    def latest_active_for_confirmation(
        self,
        confirmation_id: str,
        *,
        now: datetime | None = None,
    ) -> CapabilityGrant | None:
        items = [
            grant
            for grant in self.list_all()
            if grant.is_active(now)
            and grant.approval_confirmation_id == confirmation_id
        ]
        if not items:
            return None
        items.sort(key=lambda grant: (grant.created_at, grant.grant_id))
        return items[-1]

    def _save_unlocked(self, grants: list[CapabilityGrant]) -> None:
        payload = {
            "version": 1,
            "grants": [grant.to_dict() for grant in grants],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            self._refresh_cache_from_raw(payload["grants"])
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _refresh_cache_from_raw(self, grants: list[dict[str, Any]]) -> None:
        self._cached_raw_grants = [dict(item) for item in grants]
        self._cached_signature = self._file_signature()

    def _file_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None
        except OSError:
            return None
        return (int(stat.st_mtime_ns), int(stat.st_size))


def snapshot_for_cron_payload(
    payload: CronPayload,
    grant_store: CapabilityGrantStore,
) -> CapabilitySnapshot:
    if not payload.grant_id:
        return CapabilitySnapshot.scheduled_default()
    grant = grant_store.get(payload.grant_id)
    if grant is None:
        _raise_grant_denied("capability_grant_missing")
    if grant.is_revoked():
        _raise_grant_denied("capability_grant_revoked")
    if grant.is_expired():
        _raise_grant_denied("capability_grant_expired")
    return grant.to_snapshot(trigger="scheduled")


def issue_tool_approval_grant(
    confirmation: ConfirmationRequest,
    grant_store: CapabilityGrantStore,
    *,
    approved_by: str | None = None,
    now: datetime | None = None,
    ttl_seconds: int | None = None,
    persistent: bool = False,
) -> CapabilityGrant:
    """方案 C3: issue tool_approval grant，支持持久授权和自定义 TTL。

    参数：
    - ttl_seconds: 用户指定的持久 TTL（秒）。None 时使用默认 10 分钟。
    - persistent: 是否标记为持久授权。高危工具（exec/cron/spawn）会被强制降级为单次。

    分级规则（规则 18 安全边界）：
    - read_file/write_file/message：可持久，TTL 由用户指定
    - exec/cron/spawn：即使 persistent=True 也只给 10 分钟单次 TTL
    """
    existing = grant_store.latest_active_for_confirmation(
        confirmation.confirmation_id,
        now=now,
    )
    if existing is not None:
        return existing
    current_time = _normalize_datetime(now or datetime.now(timezone.utc))
    flags = _parse_grant_flags(confirmation)
    metadata = dict(confirmation.metadata or {})
    metadata["confirmation_kind"] = confirmation.kind
    tool_name = metadata.get("tool_name") or ""

    # 方案 C3: 分级持久判定
    # 优先从函数参数读取，其次从 confirmation.metadata 自动检测（支持测试和旧调用方）
    metadata_persistent = str(metadata.get("persistent", "")).lower() == "true"
    effective_persistent_flag = persistent or metadata_persistent
    # 从 metadata 自动读取 ttl_seconds（支持测试和旧调用方）
    metadata_ttl = metadata.get("ttl_seconds")
    if ttl_seconds is None and metadata_ttl:
        try:
            ttl_seconds = int(metadata_ttl)
        except (TypeError, ValueError):
            ttl_seconds = None

    is_high_risk = tool_name in _HIGH_RISK_TOOLS_NO_PERSISTENT
    effective_persistent = effective_persistent_flag and not is_high_risk
    if effective_persistent_flag and is_high_risk:
        # 高危工具强制降级为单次（不传递 persistent 标记）
        metadata.pop("persistent", None)
        metadata["persistent_downgraded"] = "true"
        effective_ttl = _TOOL_APPROVAL_GRANT_TTL
    elif effective_persistent:
        # 低危工具可持久，TTL 由用户指定或使用默认 7 天
        if ttl_seconds and ttl_seconds > 0:
            effective_ttl = timedelta(seconds=max(1, ttl_seconds))
        else:
            effective_ttl = _DEFAULT_PERSISTENT_TTL
        metadata["persistent"] = "true"
    else:
        # 单次授权
        effective_ttl = _TOOL_APPROVAL_GRANT_TTL

    grant = CapabilityGrant(
        grant_id=f"grant_{uuid.uuid4().hex[:12]}",
        created_by=str(approved_by or confirmation.requested_by or "user_confirmation"),
        created_at=_format_datetime(current_time),
        expires_at=_format_datetime(current_time + effective_ttl),
        source="user_confirmation",
        can_exec=bool(flags.get("can_exec", False)),
        can_read_files=bool(flags.get("can_read_files", False)),
        can_write_files=bool(flags.get("can_write_files", False)),
        can_send_cross_target=bool(flags.get("can_send_cross_target", False)),
        can_create_cron=bool(flags.get("can_create_cron", False)),
        can_spawn=bool(flags.get("can_spawn", False)),
        allowed_device_domains=tuple(flags.get("allowed_device_domains") or ()),
        allowed_mcp_scopes=tuple(flags.get("allowed_mcp_scopes") or ()),
        approval_confirmation_id=confirmation.confirmation_id,
        session_key=metadata.get("session_key"),
        tool_name=metadata.get("tool_name"),
        purpose=f"tool_approval:{metadata.get('tool_name') or confirmation.action or 'unknown'}",
        metadata=metadata | flags,
    )
    grant_store.put(grant)
    return grant


def issue_evolution_override_grant(
    *,
    confirmation: ConfirmationRequest,
    grant_store: CapabilityGrantStore,
    action_kind: str,
    target_id: str,
    reason_digest: str,
    approved_by: str | None = None,
    now: datetime | None = None,
) -> CapabilityGrant:
    existing = grant_store.latest_active_for_confirmation(
        confirmation.confirmation_id,
        now=now,
    )
    if existing is not None and existing.purpose == "evolution_override":
        return existing
    current_time = _normalize_datetime(now or datetime.now(timezone.utc))
    metadata = dict(confirmation.metadata or {})
    metadata.update({
        "action_kind": str(action_kind or "").strip(),
        "target_id": str(target_id or "").strip(),
        "reason_digest": str(reason_digest or "").strip(),
    })
    grant = CapabilityGrant(
        grant_id=f"grant_{uuid.uuid4().hex[:12]}",
        created_by=str(approved_by or confirmation.requested_by or "user_confirmation"),
        created_at=_format_datetime(current_time),
        expires_at=_format_datetime(current_time + _TOOL_APPROVAL_GRANT_TTL),
        source="user_confirmation",
        can_exec=False,
        can_read_files=False,
        can_write_files=False,
        can_send_cross_target=False,
        can_create_cron=False,
        can_spawn=False,
        approval_confirmation_id=confirmation.confirmation_id,
        session_key=metadata.get("session_key"),
        tool_name="originagent_evolution_control",
        purpose="evolution_override",
        metadata=metadata,
    )
    grant_store.put(grant)
    return grant


def _raise_grant_denied(policy_rule: str) -> None:
    raise PolicyDeniedError(
        _GRANT_ERROR_MESSAGE,
        code=policy_rule,
        boundary="cron",
        policy_rule=policy_rule,
    )


def _grant_ref(grant_id: str) -> str:
    return hashlib.sha256(grant_id.encode("utf-8")).hexdigest()[:12]


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _normalize_datetime(parsed)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return _normalize_datetime(value).isoformat()


def _parse_grant_flags(confirmation: ConfirmationRequest) -> dict[str, Any]:
    raw = confirmation.action_payload.get("grant_flags")
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed
