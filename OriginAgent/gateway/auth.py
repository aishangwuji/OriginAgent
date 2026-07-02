"""Gateway authentication: token generation, validation, HMAC signing.

Extracted from WebSocketChannel to its own focused component.
"""

from __future__ import annotations

import email.utils
import http
import json
import secrets
import time
from typing import Any

from websockets.http11 import Headers, Response


# ── Static helpers (originally in websocket.py, extracted to break
#    circular dependency between gateway.auth and websocket.py). ──────


from hmac import compare_digest
from urllib.parse import parse_qs, urlparse

from OriginAgent.gateway._helpers import http_error, http_json_response


# ── Helper functions (originally in websocket.py) ──────────────────────


def _bearer_token(headers: Any) -> str | None:
    """Pull a Bearer token out of standard or query-style headers."""
    auth = getattr(headers, "authorization", None) or getattr(headers, "Authorization", None)
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def _parse_query(path_with_query: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(path_with_query).query)


def _query_first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _headers_to_dict(headers: Any) -> dict[str, str]:
    """Convert websockets/http header-like object to a plain dict (lowercased keys)."""
    result: dict[str, str] = {}
    if hasattr(headers, "get_all"):
        for k in headers.get_all():
            result.setdefault(k.lower(), getattr(headers, "get", lambda k, d=None: d)(k, "") or "")
    elif isinstance(headers, dict):
        for k, v in headers.items():
            result[k.lower()] = str(v or "")
    elif hasattr(headers, "raw_items"):
        for k, v in headers.raw_items():
            result[k.lower()] = v
    return result


def _issue_route_secret_matches(headers: Any, configured_secret: str) -> bool:
    provided = _bearer_token(headers) or _query_first(
        _parse_query(getattr(headers, "path", "")), "secret"
    )
    if provided is None:
        return False
    return compare_digest(provided.encode("utf-8"), configured_secret.encode("utf-8"))


def _remote_addr(connection: Any) -> tuple[str, int]:
    """Return (host, port) for a websocket connection."""
    peer = getattr(connection, "remote_address", None)
    if peer is not None and len(peer) >= 2:
        return (peer[0], int(peer[1]))
    return ("127.0.0.1", 0)


class GatewayAuth:
    """Token generation, validation, and HMAC media URL signing."""

    _MAX_ISSUED_TOKENS = 1000

    def __init__(self, config: Any) -> None:
        self._config = config
        self._issued_tokens: dict[str, float] = {}
        self._api_tokens: dict[str, float] = {}

    # ── Issued token lifecycle (single-use, for WebSocket handshake) ─────

    def purge_expired_issued_tokens(self) -> None:
        """Remove issued tokens whose TTL has expired."""
        now = time.monotonic()
        for token_key, expiry in list(self._issued_tokens.items()):
            if now > expiry:
                self._issued_tokens.pop(token_key, None)

    def take_issued_token_if_valid(self, token_value: str | None) -> bool:
        """Validate and consume one issued token (single use per connection attempt).

        Uses single-step pop to minimize the window between lookup and removal;
        safe under asyncio's single-threaded cooperative model.
        """
        if not token_value:
            return False
        self.purge_expired_issued_tokens()
        expiry = self._issued_tokens.pop(token_value, None)
        if expiry is None:
            return False
        if time.monotonic() > expiry:
            return False
        return True

    def handle_token_issue_http(self, connection: Any, request: Any, logger: Any) -> Any:
        """Handle a token-issue HTTP request (mints a single-use connection token)."""
        secret = self._config.token_issue_secret.strip()
        if secret:
            if not _issue_route_secret_matches(request.headers, secret):
                return connection.respond(401, "Unauthorized")
        else:
            logger.warning(
                "token_issue_path is set but token_issue_secret is empty; "
                "any client can obtain connection tokens — set token_issue_secret for production."
            )
        self.purge_expired_issued_tokens()
        if len(self._issued_tokens) >= self._MAX_ISSUED_TOKENS:
            logger.error(
                "too many outstanding issued tokens ({}), rejecting issuance",
                len(self._issued_tokens),
            )
            return http_json_response({"error": "too many outstanding tokens"}, status=429)
        token_value = f"nbwt_{secrets.token_urlsafe(32)}"
        self._issued_tokens[token_value] = time.monotonic() + float(self._config.token_ttl_s)
        return http_json_response(
            {"token": token_value, "expires_in": self._config.token_ttl_s}
        )

    # ── API token lifecycle (multi-use, for REST surface) ────────────────

    def purge_expired_api_tokens(self) -> None:
        """Remove API tokens whose TTL has expired."""
        now = time.monotonic()
        for token_key, expiry in list(self._api_tokens.items()):
            if now > expiry:
                self._api_tokens.pop(token_key, None)

    def check_api_token(self, request: Any) -> bool:
        """Validate a request against the API token pool (multi-use, TTL-bound)."""
        self.purge_expired_api_tokens()
        token = _bearer_token(request.headers) or _query_first(
            _parse_query(request.path), "token"
        )
        if not token:
            return False
        expiry = self._api_tokens.get(token)
        if expiry is None or time.monotonic() > expiry:
            self._api_tokens.pop(token, None)
            return False
        return True

    # ── Dual-registration (token used in both pools) ────────────────────

    @property
    def total_token_count(self) -> int:
        """Total number of issued + API tokens currently tracked."""
        return len(self._issued_tokens) + len(self._api_tokens)

    def register_dual_token(self, token: str, ttl_s: float) -> None:
        """Register *token* in both pools with the same TTL.

        The WS handshake consumes the issued-token copy while the REST
        surface keeps validating the API-token copy until TTL expiry.
        """
        expiry = time.monotonic() + ttl_s
        self._issued_tokens[token] = expiry
        self._api_tokens[token] = expiry

    def clear_all(self) -> None:
        """Remove all tracked tokens (used on config reload)."""
        self._issued_tokens.clear()
        self._api_tokens.clear()
