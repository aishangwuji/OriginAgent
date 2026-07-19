"""Pairing store for DM sender approval.

Persistent storage at ``~/.OriginAgent/pairing.json`` keeps approved senders
and pending pairing codes per channel.  The store is designed for
private-assistant scale: small JSON file, simple locking, no external DB.
"""

from __future__ import annotations

import json
import secrets
import string
import threading
import time
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from OriginAgent.config.paths import get_data_dir
from OriginAgent.utils.helpers import _write_text_atomic

# threading.Lock is used so store functions remain callable from both sync CLI
# and async channel handlers.  At private-assistant scale (small JSON file,
# sub-millisecond operations) the brief block is acceptable.
_LOCK = threading.Lock()
_ALPHABET = string.ascii_uppercase + string.digits
_CODE_LENGTH = 8  # e.g. ABCD-EFGH
_TTL_DEFAULT_S = 600  # 10 minutes


def _redact_code(code: str) -> str:
    return f"{code[:2]}***{code[-2:]}" if len(code) >= 4 else "***"


def _store_path() -> Path:
    return get_data_dir() / "pairing.json"


def _load() -> dict[str, Any]:
    path = _store_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"approved": {}, "pending": {}, "hint_shown": {}}
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupted pairing store, resetting")
        return {"approved": {}, "pending": {}, "hint_shown": {}}

    # Convert approved lists to sets for O(1) lookup
    for channel, users in data.get("approved", {}).items():
        data["approved"][channel] = set(users)
    # hint_shown has the same shape as approved (channel → sender_id set).
    # Older stores created before the claim-hint feature will not have it;
    # default to an empty dict so callers can treat it uniformly.
    for channel, users in data.get("hint_shown", {}).items():
        data["hint_shown"][channel] = set(users)
    return data


def _save(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Convert sets back to lists for JSON serialization
    payload = {
        "approved": {ch: sorted(list(users)) for ch, users in data.get("approved", {}).items()},
        "pending": dict(data.get("pending", {})),
        "hint_shown": {ch: sorted(list(users)) for ch, users in data.get("hint_shown", {}).items()},
    }
    _write_text_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False))


def _gc_pending(data: dict[str, Any]) -> None:
    """Remove expired pending entries in-place."""
    now = time.time()
    pending: dict[str, Any] = data.get("pending", {})
    expired = [code for code, info in pending.items() if info.get("expires_at", 0) < now]
    for code in expired:
        del pending[code]


def generate_code(
    channel: str,
    sender_id: str,
    ttl: int = _TTL_DEFAULT_S,
) -> str:
    """Create a new pairing code for *sender_id* on *channel*.

    Returns the code (e.g. ``"ABCD-EFGH"``).
    """
    with _LOCK:
        data = _load()
        _gc_pending(data)
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))
        code = f"{raw[:4]}-{raw[4:]}"

        data.setdefault("pending", {})[code] = {
            "channel": channel,
            "sender_id": sender_id,
            "created_at": time.time(),
            "expires_at": time.time() + ttl,
        }
        _save(data)
        logger.info("Generated pairing code {} for {}@{}", _redact_code(code), sender_id, channel)
        return code


def approve_code(code: str) -> tuple[str, str] | None:
    """Approve a pending pairing code.

    Returns ``(channel, sender_id)`` on success, or ``None`` if the code
    does not exist or has expired.
    """
    with _LOCK:
        data = _load()
        _gc_pending(data)
        pending: dict[str, Any] = data.get("pending", {})
        info = pending.pop(code, None)
        if info is None:
            return None
        channel = info["channel"]
        sender_id = info["sender_id"]
        data.setdefault("approved", {}).setdefault(channel, set()).add(sender_id)
        _save(data)
        logger.info("Approved pairing code {} for {}@{}", _redact_code(code), sender_id, channel)
        return channel, sender_id


def deny_code(code: str) -> bool:
    """Reject and discard a pending pairing code.

    Returns ``True`` if the code existed and was removed.
    """
    with _LOCK:
        data = _load()
        _gc_pending(data)
        pending: dict[str, Any] = data.get("pending", {})
        if code in pending:
            del pending[code]
            _save(data)
            logger.info("Denied pairing code {}", _redact_code(code))
            return True
        return False


def is_approved(channel: str, sender_id: str) -> bool:
    """Check whether *sender_id* has been approved on *channel*."""
    with _LOCK:
        data = _load()
        approved: dict[str, set[str]] = data.get("approved", {})
        return str(sender_id) in approved.get(channel, set())


def list_pending() -> list[dict[str, Any]]:
    """Return all non-expired pending pairing requests."""
    with _LOCK:
        data = _load()
        _gc_pending(data)
        return [
            {"code": code, **info}
            for code, info in data.get("pending", {}).items()
        ]


def revoke(channel: str, sender_id: str) -> bool:
    """Remove an approved sender from *channel*.

    Returns ``True`` if the sender was present and removed.
    """
    with _LOCK:
        data = _load()
        approved: dict[str, set[str]] = data.get("approved", {})
        users = approved.get(channel, set())
        if sender_id in users:
            users.discard(sender_id)
            if not users:
                del approved[channel]
            _save(data)
            logger.info("Revoked {} from {}", sender_id, channel)
            return True
        return False


def get_approved(channel: str) -> list[str]:
    """Return all approved sender IDs for *channel*."""
    with _LOCK:
        data = _load()
        return sorted(data.get("approved", {}).get(channel, set()))


def is_hint_shown(channel: str, sender_id: str) -> bool:
    """Return True if the claim hint has already been shown to *sender_id* on *channel*.

    Used to deduplicate the ``__pairing_pending__`` claim hint so a paired-
    but-unbound sender sees it at most once (rule 12 idempotency).
    """
    with _LOCK:
        data = _load()
        return str(sender_id) in data.get("hint_shown", {}).get(channel, set())


def mark_hint_shown(channel: str, sender_id: str) -> None:
    """Record that the claim hint has been shown to *sender_id* on *channel*.

    Idempotent: repeated calls with the same arguments have no additional
    effect. Persisted to ``pairing.json`` so dedup survives process restarts.
    """
    with _LOCK:
        data = _load()
        data.setdefault("hint_shown", {}).setdefault(channel, set()).add(str(sender_id))
        _save(data)


def format_claim_hint(claimable_tenants: list[tuple[str, str]]) -> str:
    """Format the claim-hint message shown to a ``__pairing_pending__`` sender.

    Args:
        claimable_tenants: list of ``(tenant_id, display_name)`` tuples,
            pre-filtered by the caller to only those with
            ``claimable_by_pairing=True``. Filtering at the caller keeps this
            function pure and ensures non-claimable tenant info is never
            leaked to an unbound sender (rule 18 security boundary).
    """
    if not claimable_tenants:
        return (
            "You have been approved, but no claimable identity is currently available. "
            "Please ask the owner to enable a claimable tenant."
        )
    lines = ["You have been approved! To bind your identity, send:"]
    for tid, name in claimable_tenants:
        lines.append(f"  /pairing claim {tid}   # {name}")
    lines.append("")
    lines.append("Once claimed, you will have full access to your assistant.")
    return "\n".join(lines)


def format_pairing_reply(code: str) -> str:
    """Return the pairing-code message sent to unrecognised DM senders."""
    return (
        "Hi there! This assistant only responds to approved users.\n\n"
        f"Your pairing code is: `{code}`\n\n"
        "To get access, ask the owner to approve this code:\n"
        f"- In this chat: send `/pairing approve {code}`"
    )


def format_expiry(expires_at: float) -> str:
    """Return a human-readable expiry string (e.g. ``"120s"`` or ``"expired"``)."""
    remaining = int(expires_at - time.time())
    return f"{remaining}s" if remaining > 0 else "expired"


def handle_pairing_command(
    channel: str,
    subcommand_text: str,
    *,
    sender_id: str | None = None,
    tenant_registry: Any = None,
    on_claim_success: Callable[[Any], None] | None = None,
) -> str:
    """Execute a pairing subcommand and return the reply text.

    This is a pure function (no side effects other than store mutations)
    so it can be used from both the CLI and the agent CommandRouter.

    For the ``claim`` subcommand, *sender_id* and *tenant_registry* must be
    supplied so the handler can verify the sender has been approved via
    pairing (rule 18 security boundary) and is not already bound to another
    tenant (rule 12 idempotency).

    *on_claim_success* is an optional callback invoked with the claimed
    ``Tenant`` after a successful ``claim_pairing``. It lets the caller
    perform post-claim work (BDI lazy-load, session migration) without
    leaking that concern into the pairing store (rule 16 domain isolation).
    Exceptions raised by the callback are logged but do not undo the
    binding — the binding already happened, and the success reply is still
    returned so the user is not left without feedback. The operator can
    then re-trigger any failed post-claim work on the next message.
    """
    parts = subcommand_text.split()
    sub = parts[0] if parts else "list"
    arg = parts[1] if len(parts) > 1 else None

    if sub in ("list",):
        pending = list_pending()
        if not pending:
            return "No pending pairing requests."
        lines = ["Pending pairing requests:"]
        for item in pending:
            expiry = format_expiry(item.get("expires_at", 0))
            lines.append(
                f"- `{item['code']}` | {item['channel']} | {item['sender_id']} | {expiry}"
            )
        return "\n".join(lines)

    elif sub == "approve":
        if arg is None:
            return "Usage: `/pairing approve <code>`"
        result = approve_code(arg)
        if result is None:
            return f"Invalid or expired pairing code: `{arg}`"
        ch, sid = result
        return f"Approved pairing code `{arg}` — {sid} can now access {ch}"

    elif sub == "deny":
        if arg is None:
            return "Usage: `/pairing deny <code>`"
        if deny_code(arg):
            return f"Denied pairing code `{arg}`"
        return f"Pairing code `{arg}` not found or already expired"

    elif sub == "revoke":
        if len(parts) == 2:
            return (
                f"Revoked {arg} from {channel}"
                if revoke(channel, arg)
                else f"{arg} was not in the approved list for {channel}"
            )
        if len(parts) == 3:
            return (
                f"Revoked {parts[2]} from {arg}"
                if revoke(arg, parts[2])
                else f"{parts[2]} was not in the approved list for {arg}"
            )
        return "Usage: `/pairing revoke <user_id>` or `/pairing revoke <channel> <user_id>`"

    elif sub == "claim":
        if arg is None:
            return "Usage: `/pairing claim <tenant_id>`"
        if sender_id is None or tenant_registry is None:
            return "Claim command requires sender_id and tenant_registry context"
        # Idempotency (rule 12): if the sender is already bound to a real
        # tenant (either pre-bound via config or claimed via a previous
        # /pairing claim call), refuse to silently rebind them. This check
        # comes BEFORE the is_approved check because a pre-bound sender
        # (bound via config) is not in the pairing store but is still
        # already claimed — telling them "not approved" would be misleading.
        # lookup() falls back to the guest tenant for unbound senders, so a
        # non-guest result means a real binding already exists.
        existing = tenant_registry.lookup(channel, sender_id)
        if existing is not None and existing.tenant_id != "guest":
            return (
                f"You are already bound to tenant `{existing.tenant_id}`. "
                "Contact owner to change."
            )
        # Authorization (rule 18): sender must have been approved via pairing
        # on this channel. Without this check, any user could claim any tenant.
        if not is_approved(channel, sender_id):
            return (
                f"You have not been approved on {channel}. "
                "Ask the owner to approve your pairing code first."
            )
        # Boundary validation (rule 3): tenant_id must exist and be claimable.
        tenant = tenant_registry.get(arg)
        if tenant is None:
            return f"Unknown tenant: `{arg}`"
        if not getattr(tenant, "claimable_by_pairing", False):
            return f"Tenant `{arg}` is not claimable via pairing"
        tenant_registry.claim_pairing(channel, sender_id, arg)
        if on_claim_success is not None:
            # Reason: the pairing store stays domain-isolated from agent_host
            # (rule 16) — post-claim BDI lazy-load and session migration are
            # the caller's responsibility. Swallow exceptions so a callback
            # failure does not surface as a claim failure to the user (the
            # binding is already persisted). The operator sees the error log.
            try:
                on_claim_success(tenant)
            except Exception:
                logger.exception(
                    "on_claim_success callback failed for tenant={} sender={}@{}",
                    arg, sender_id, channel,
                )
        return f"Claimed tenant `{arg}` successfully. Welcome, {tenant.display_name}!"

    return (
        "Unknown pairing command.\n"
        "Usage: `/pairing [list|approve <code>|deny <code>|"
        "revoke <user_id>|revoke <channel> <user_id>|claim <tenant_id>]`"
    )
