"""WebSocket server channel: OriginAgent acts as a WebSocket server and serves connected clients."""

from __future__ import annotations

import asyncio
import base64
import binascii
import email.utils
import hashlib
import hmac
import http
import ipaddress
import json
import mimetypes
import re
import secrets
import shutil
import ssl
import socket
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Self
from urllib.parse import parse_qs, unquote, urlparse

from loguru import logger
from pydantic import Field, field_validator, model_validator
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from OriginAgent.bus.events import OUTBOUND_META_AGENT_UI, OutboundMessage
from OriginAgent.gateway.auth import GatewayAuth
from OriginAgent.gateway.media_server import MediaServer
from OriginAgent.gateway.rest_api import RestApi
from OriginAgent.bus.queue import MessageBus
from OriginAgent.agent.message_metadata import extract_origin_metadata, origin_label
from OriginAgent.config.doctor import build_config_doctor_report
from OriginAgent.channels.base import BaseChannel
from OriginAgent.command.builtin import builtin_command_palette
from OriginAgent.config.paths import get_media_dir, get_webui_dir, get_workspace_upload_dir
from OriginAgent.agent.runtime_mode import build_runtime_mode_summary
from OriginAgent.config.schema import Base
from OriginAgent.session.goal_state import goal_state_ws_blob
from OriginAgent.utils.attachments import describe_attachment
from OriginAgent.utils.helpers import safe_filename
from OriginAgent.utils.media_decode import (
    FileSizeExceeded,
    save_base64_data_url,
)
from OriginAgent.utils.subagent_channel_display import scrub_subagent_messages_for_channel
from OriginAgent.utils.webui_thread_disk import delete_webui_thread
from OriginAgent.utils.webui_transcript import (
    build_webui_thread_response,
    read_transcript_lines,
)

if TYPE_CHECKING:
    from OriginAgent.session.manager import SessionManager


def _strip_trailing_slash(path: str) -> str:
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path or "/"


def _normalize_config_path(path: str) -> str:
    return _strip_trailing_slash(path)


def _append_buttons_as_text(text: str, buttons: list[list[str]]) -> str:
    labels = [label for row in buttons for label in row if label]
    if not labels:
        return text
    fallback = "\n".join(f"{index}. {label}" for index, label in enumerate(labels, 1))
    return f"{text}\n\n{fallback}" if text else fallback


class WebSocketConfig(Base):
    """WebSocket server channel configuration.

    Clients connect with URLs like ``ws://{host}:{port}{path}?client_id=...&token=...``.
    - ``client_id``: Used for ``allow_from`` authorization; if omitted, a value is generated and logged.
    - ``token``: If non-empty, the ``token`` query param may match this static secret; short-lived tokens
      from ``token_issue_path`` are also accepted.
    - ``token_issue_path``: If non-empty, **GET** (HTTP/1.1) to this path returns JSON
      ``{"token": "...", "expires_in": <seconds>}``; use ``?token=...`` when opening the WebSocket.
      Must differ from ``path`` (the WS upgrade path). If the client runs in the **same process** as
      OriginAgent and shares the asyncio loop, use a thread or async HTTP client for GET—do not call
      blocking ``urllib`` or synchronous ``httpx`` from inside a coroutine.
    - ``token_issue_secret``: If non-empty, token requests must send ``Authorization: Bearer <secret>`` or
      ``X-OriginAgent-Auth: <secret>``.
    - ``websocket_requires_token``: If True, the handshake must include a valid token (static or issued and not expired).
    - Each connection has its own session: a unique ``chat_id`` maps to the agent session internally.
    - ``media`` field in outbound messages contains local filesystem paths; remote clients need a
      shared filesystem or an HTTP file server to access these files.
    """

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/"
    token: str = ""
    token_issue_path: str = ""
    token_issue_secret: str = ""
    token_ttl_s: int = Field(default=300, ge=30, le=86_400)
    websocket_requires_token: bool = True
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    streaming: bool = True
    # Default 36 MB, upper 40 MB: supports up to 4 images at ~6 MB each after
    # client-side Worker normalization (see webui Composer). 4 × 6 MB × 1.37
    # (base64 overhead) + envelope framing stays under 36 MB; the 40 MB ceiling
    # leaves a small margin for sender slop without opening a DoS avenue.
    max_message_bytes: int = Field(default=37_748_736, ge=1024, le=41_943_040)
    ping_interval_s: float = Field(default=20.0, ge=5.0, le=300.0)
    ping_timeout_s: float = Field(default=20.0, ge=5.0, le=300.0)
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    @field_validator("path")
    @classmethod
    def path_must_start_with_slash(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError('path must start with "/"')
        return _normalize_config_path(value)

    @field_validator("token_issue_path")
    @classmethod
    def token_issue_path_format(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if not value.startswith("/"):
            raise ValueError('token_issue_path must start with "/"')
        return _normalize_config_path(value)

    @model_validator(mode="after")
    def token_issue_path_differs_from_ws_path(self) -> Self:
        if not self.token_issue_path:
            return self
        if _normalize_config_path(self.token_issue_path) == _normalize_config_path(self.path):
            raise ValueError("token_issue_path must differ from path (the WebSocket upgrade path)")
        return self

    @model_validator(mode="after")
    def wildcard_host_requires_auth(self) -> Self:
        if self.host not in ("0.0.0.0", "::"):
            return self
        if self.token.strip() or self.token_issue_secret.strip():
            return self
        raise ValueError(
            "host is 0.0.0.0 (all interfaces) but neither token nor "
            "token_issue_secret is set — set one to prevent unauthenticated access"
        )


def _http_json_response(data: dict[str, Any], *, status: int = 200) -> Response:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = Headers(
        [
            ("Date", email.utils.formatdate(usegmt=True)),
            ("Connection", "close"),
            ("Content-Length", str(len(body))),
            ("Content-Type", "application/json; charset=utf-8"),
        ]
    )
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, headers, body)


def publish_runtime_model_update(
    bus: MessageBus,
    model: str,
    model_preset: str | None,
) -> None:
    """Enqueue a runtime model snapshot for all embedded WebUI subscribers."""
    bus.outbound.put_nowait(OutboundMessage(
        channel="websocket",
        chat_id="*",
        content="",
        metadata={
            "_runtime_model_updated": True,
            "model": model,
            "model_preset": model_preset,
        },
    ))


def _default_model_name_from_config() -> str | None:
    """Return the configured default model for readonly webui display."""
    try:
        from OriginAgent.config.loader import load_config

        model = load_config().resolve_preset().model.strip()
        return model or None
    except Exception as e:
        logger.debug("webui bootstrap could not load model name: {}", e)
        return None


def _resolve_bootstrap_model_name(
    runtime_name: Callable[[], str | None] | None,
) -> str | None:
    """Prefer an in-process resolver, else fall back to on-disk config."""
    if runtime_name is not None:
        try:
            raw = runtime_name()
        except Exception as e:
            logger.debug("bootstrap runtime model resolver failed: {}", e)
        else:
            if isinstance(raw, str):
                stripped = raw.strip()
                if stripped:
                    return stripped
    return _default_model_name_from_config()


def _parse_request_path(path_with_query: str) -> tuple[str, dict[str, list[str]]]:
    """Parse normalized path and query parameters in one pass."""
    parsed = urlparse("ws://x" + path_with_query)
    path = _strip_trailing_slash(parsed.path or "/")
    return path, parse_qs(parsed.query, keep_blank_values=True)


def _normalize_http_path(path_with_query: str) -> str:
    """Return the path component (no query string), with trailing slash normalized (root stays ``/``)."""
    return _parse_request_path(path_with_query)[0]


def _parse_query(path_with_query: str) -> dict[str, list[str]]:
    return _parse_request_path(path_with_query)[1]


def _query_first(query: dict[str, list[str]], key: str) -> str | None:
    """Return the first value for *key*, or None."""
    values = query.get(key)
    return values[0] if values else None


def _query_bool(query: dict[str, list[str]], key: str) -> bool | None:
    raw = _query_first(query, key)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _mask_secret_hint(secret: str | None) -> str | None:
    if not secret:
        return None
    if len(secret) <= 8:
        return "••••"
    return f"{secret[:4]}••••{secret[-4:]}"


_WEB_SEARCH_PROVIDER_OPTIONS: tuple[dict[str, str], ...] = (
    {"name": "duckduckgo", "label": "DuckDuckGo", "credential": "none"},
    {"name": "brave", "label": "Brave Search", "credential": "api_key"},
    {"name": "tavily", "label": "Tavily", "credential": "api_key"},
    {"name": "searxng", "label": "SearXNG", "credential": "base_url"},
    {"name": "jina", "label": "Jina", "credential": "api_key"},
    {"name": "kagi", "label": "Kagi", "credential": "api_key"},
    {"name": "olostep", "label": "Olostep", "credential": "api_key"},
)
_WEB_SEARCH_PROVIDER_BY_NAME = {
    provider["name"]: provider for provider in _WEB_SEARCH_PROVIDER_OPTIONS
}

_RUNTIME_PROFILE_OPTIONS = {"default", "safe", "household_safe", "local_dev", "automation"}
_PROVIDER_RETRY_MODE_OPTIONS = {"standard", "persistent"}
_EVOLUTION_MODE_OPTIONS = {"conservative", "curated", "exploratory", "aggressive"}
_SESSION_SEARCH_BACKEND_OPTIONS = {"auto", "literal", "sqlite_fts"}
_EXEC_PROFILE_OPTIONS = {"secure", "local_dev", "disabled"}
_EXEC_SHELL_SYNTAX_POLICY_OPTIONS = {"restricted", "shell"}
_DEVICE_MODE_OPTIONS = {"dry_run", "real"}
_DEVICE_BACKEND_OPTIONS = {"none", "fake", "lighting_client"}
_AUDIT_MODE_OPTIONS = {"off", "minimal", "security"}
_TRANSCRIPTION_PROVIDER_OPTIONS = {"groq", "openai", "volcengine"}

_MCP_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MCP_SECRET_HINT = "••••"
_HA_MCP_PATH = "/api/mcp"


def _settings_runtime_controls_payload(config: Any) -> dict[str, Any]:
    defaults = config.agents.defaults
    evolution = defaults.learning.evolution
    return {
        "channels": {
            "send_progress": bool(config.channels.send_progress),
            "send_tool_hints": bool(config.channels.send_tool_hints),
            "show_reasoning": bool(config.channels.show_reasoning),
        },
        "agent": {
            "unified_session": bool(defaults.unified_session),
            "cold_archive_enabled": bool(defaults.cold_archive_enabled),
            "allow_agent_initiated_messages": bool(defaults.allow_agent_initiated_messages),
            "enable_backend_cognition": bool(defaults.enable_backend_cognition),
            "auxiliary_enabled": bool(defaults.auxiliary.enabled),
            "domain_packs_enabled": bool(defaults.domain_packs.enabled),
            "provider_retry_mode": defaults.provider_retry_mode,
            "dream_annotate_line_ages": bool(defaults.dream.annotate_line_ages),
        },
        "learning": {
            "background_review_enabled": bool(defaults.learning.background_review.enabled),
            "curator_enabled": bool(defaults.learning.curator.enabled),
        },
        "evolution": {
            "mode": evolution.mode,
            "allow_manual_override": bool(evolution.allow_manual_override),
            "dry_run": bool(evolution.dry_run),
            "outcome_archive_enabled": bool(evolution.outcome_archive_enabled),
            "dependency_stale_cleanup_enabled": bool(evolution.dependency_stale_cleanup_enabled),
            "auto_verify_workflows": bool(evolution.auto_verify_workflows),
            "skill_candidates_enabled": bool(evolution.skill_candidates_enabled),
            "feedback_calibration_enabled": bool(evolution.feedback_calibration_enabled),
            "sandbox_enabled": bool(evolution.sandbox.enabled),
            "trial_enabled": bool(evolution.trial.enabled),
            "trial_isolated_workspace": bool(evolution.trial.isolated_workspace),
            "trial_read_only_tools_only": bool(evolution.trial.read_only_tools_only),
        },
        "gateway": {
            "heartbeat_enabled": bool(config.gateway.heartbeat.enabled),
            "tiered_router": {
                "enabled": bool(config.gateway.tiered_router.enabled),
                "default_tier": config.gateway.tiered_router.default_tier,
            },
        },
        "security": {
            "pairing_enabled": bool(config.security.pairing.enabled),
            "pairing_allow_self_approve": bool(config.security.pairing.allow_self_approve),
        },
        "search": {
            "web_enabled": bool(config.tools.web.enable),
            "web_fetch_use_jina_reader": bool(config.tools.web.fetch.use_jina_reader),
            "session_search_enabled": bool(config.tools.session_search.enabled),
            "session_search_backend": config.tools.session_search.backend,
            "session_search_semantic_enabled": bool(config.tools.session_search.semantic_enabled),
            "session_search_rebuild_on_start": bool(config.tools.session_search.rebuild_on_start),
            "content_read_enabled": bool(config.tools.content_read.enabled),
            "content_read_use_jina_reader": bool(config.tools.content_read.use_jina_reader),
        },
        "execution": {
            "exec_enabled": bool(config.tools.exec.enable),
            "exec_profile": config.tools.exec.profile,
            "exec_allow_unsafe_exec": bool(config.tools.exec.allow_unsafe_exec),
            "exec_shell_syntax_policy": config.tools.exec.shell_syntax_policy,
            "my_enabled": bool(config.tools.my.enable),
            "my_allow_set": bool(config.tools.my.allow_set),
            "restrict_to_workspace": bool(config.tools.restrict_to_workspace),
        },
        "media": {
            "image_generation_enabled": bool(config.tools.image_generation.enabled),
        },
        "devices": {
            "device_enabled": bool(config.tools.device.enabled),
            "device_lighting_enabled": bool(config.tools.device.lighting_enabled),
            "device_mode": config.tools.device.mode,
            "device_backend": config.tools.device.backend,
        },
        "subagent": {
            "mode": defaults.subagent_policy.mode,
        },
        "audit": {
            "audit_mode": config.tools.audit.mode,
            "audit_security_on_policy_denial": bool(config.tools.audit.security_on_policy_denial),
        },
        "runtime": {
            "profile": config.runtime.profile,
        },
    }


def _settings_voice_payload(config: Any) -> dict[str, Any]:
    audio = config.tools.local_awareness.audio
    resolved_transcription_provider = (
        str(audio.transcription_provider or config.channels.transcription_provider or "groq")
        .strip()
        .lower()
    )
    if resolved_transcription_provider not in _TRANSCRIPTION_PROVIDER_OPTIONS:
        resolved_transcription_provider = "groq"
    transcription_language = config.channels.transcription_language
    return {
        "input_enabled": bool(audio.input_enabled),
        "output_enabled": bool(audio.output_enabled),
        "transcription_enabled": bool(audio.transcription_enabled),
        "tts_enabled": bool(audio.tts_enabled),
        "require_confirmation": bool(audio.require_confirmation),
        "save_dir": audio.save_dir,
        "max_record_seconds": int(audio.max_record_seconds),
        "device_id": audio.device_id,
        "voice": audio.voice,
        "transcription_provider": resolved_transcription_provider,
        "transcription_language": transcription_language,
        "tts_provider": "volcengine",
        "transcription_provider_options": sorted(_TRANSCRIPTION_PROVIDER_OPTIONS),
    }


def _mcp_masked_mapping(values: dict[str, str]) -> dict[str, str]:
    return {key: _MCP_SECRET_HINT for key, value in values.items() if value}


def _mcp_server_payload(name: str, server: Any) -> dict[str, Any]:
    return {
        "name": name,
        "type": server.type,
        "command": server.command,
        "args": list(server.args),
        "env": _mcp_masked_mapping(server.env),
        "url": server.url,
        "headers": _mcp_masked_mapping(server.headers),
        "tool_timeout": server.tool_timeout,
        "enabled_tools": list(server.enabled_tools),
    }


def _merge_mcp_secret_fields(data: dict[str, Any], existing: Any) -> dict[str, Any]:
    merged = dict(data)
    for field in ("env", "headers"):
        incoming = merged.get(field)
        if incoming is None:
            continue
        if not isinstance(incoming, dict):
            continue
        current = dict(getattr(existing, field, {}) or {})
        cleaned: dict[str, str] = dict(current)
        for key, value in incoming.items():
            key_str = str(key).strip()
            if not key_str:
                continue
            value_str = str(value)
            if value_str == _MCP_SECRET_HINT or value_str == "":
                if key_str in current:
                    cleaned[key_str] = current[key_str]
            else:
                cleaned[key_str] = value_str
        merged[field] = cleaned
    return merged


def _home_assistant_mcp_url(address: str) -> str | None:
    raw = address.strip()
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    base = f"{parsed.scheme}://{parsed.netloc}"
    if _strip_trailing_slash(parsed.path) == _HA_MCP_PATH:
        return f"{base}{_HA_MCP_PATH}"
    return f"{base}{_HA_MCP_PATH}"


def _host_exact_cidrs(hostname: str) -> list[str]:
    hosts = [hostname]
    if hostname.lower() == "localhost":
        hosts = ["127.0.0.1", "::1"]
    cidrs: list[str] = []
    seen: set[str] = set()

    def add_addr(raw_addr: str) -> None:
        try:
            addr = ipaddress.ip_address(raw_addr)
        except ValueError:
            return
        cidr = f"{addr}/{addr.max_prefixlen}"
        if cidr not in seen:
            seen.add(cidr)
            cidrs.append(cidr)

    for host in hosts:
        before_count = len(cidrs)
        add_addr(host)
        if len(cidrs) > before_count:
            continue
        try:
            infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            continue
        for info in infos:
            add_addr(info[4][0])
    return cidrs


def _parse_inbound_payload(raw: str) -> str | None:
    """Parse a client frame into text; return None for empty or unrecognized content."""
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(data, dict):
            for key in ("content", "text", "message"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return None
        return None
    return text


# Accept UUIDs and short scoped keys like "unified:default". Keeps the capability
# namespace small enough to rule out path traversal / quote injection tricks.
_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{1,64}$")


def _is_valid_chat_id(value: Any) -> bool:
    return isinstance(value, str) and _CHAT_ID_RE.match(value) is not None


def _parse_envelope(raw: str) -> dict[str, Any] | None:
    """Return a typed envelope dict if the frame is a new-style JSON envelope, else None.

    A frame qualifies when it parses as a JSON object with a string ``type`` field.
    Legacy frames (plain text, or ``{"content": ...}`` without ``type``) return None;
    callers should fall back to :func:`_parse_inbound_payload` for those.
    """
    text = raw.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    t = data.get("type")
    if not isinstance(t, str):
        return None
    return data


# Per-message media limits. The server-side guard is a touch looser than the
# client's ``Worker`` normalization target (6 MB) — tolerate client slop, but
# still cap total ingress at ``_MAX_IMAGES_PER_MESSAGE * _MAX_IMAGE_BYTES``
# which fits comfortably inside ``max_message_bytes``.
_MAX_IMAGES_PER_MESSAGE = 4
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_VIDEOS_PER_MESSAGE = 1
_MAX_VIDEO_BYTES = 20 * 1024 * 1024
_MAX_AUDIOS_PER_MESSAGE = 2
_MAX_AUDIO_BYTES = 12 * 1024 * 1024
_MAX_DOCUMENTS_PER_MESSAGE = 2
_MAX_DOCUMENT_BYTES = 10 * 1024 * 1024

# Image MIME whitelist — matches the Composer's ``accept`` list. SVG is
# explicitly excluded to avoid the XSS surface inside embedded scripts.
_IMAGE_MIME_ALLOWED: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
})

_VIDEO_MIME_ALLOWED: frozenset[str] = frozenset({
    "video/mp4",
    "video/webm",
    "video/quicktime",
})

_AUDIO_MIME_ALLOWED: frozenset[str] = frozenset({
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/x-m4a",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/webm",
    "audio/flac",
    "audio/aac",
})

_DOCUMENT_MIME_ALLOWED: frozenset[str] = frozenset({
    "application/pdf",
})

_UPLOAD_MIME_ALLOWED: frozenset[str] = (
    _IMAGE_MIME_ALLOWED
    | _VIDEO_MIME_ALLOWED
    | _AUDIO_MIME_ALLOWED
    | _DOCUMENT_MIME_ALLOWED
)

_DATA_URL_MIME_RE = re.compile(r"^data:([^;]+);base64,", re.DOTALL)


def _extract_data_url_mime(url: str) -> str | None:
    """Return the MIME type of a ``data:<mime>;base64,...`` URL, else ``None``."""
    if not isinstance(url, str):
        return None
    m = _DATA_URL_MIME_RE.match(url)
    if not m:
        return None
    return m.group(1).strip().lower() or None


def _ui_media_kind_for_path(path: str | Path) -> str:
    descriptor = describe_attachment(path)
    if descriptor is None:
        p = Path(path)
        mime, _ = mimetypes.guess_type(p.name)
        if mime and mime.startswith("video/"):
            return "video"
        if mime and mime.startswith("audio/"):
            return "audio"
        return "file"
    if descriptor.kind == "image":
        return "image"
    if descriptor.kind == "video":
        return "video"
    if descriptor.kind == "audio":
        return "audio"
    return "file"


_LOCALHOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Matches the legacy chat-id pattern but allows file-system-safe stems too,
# so the API can address sessions whose keys came from non-WebSocket channels.
_API_KEY_RE = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")


def _decode_api_key(raw_key: str) -> str | None:
    """Decode a percent-encoded API path segment, then validate the result."""
    key = unquote(raw_key)
    if _API_KEY_RE.match(key) is None:
        return None
    return key


def _is_localhost(connection: Any) -> bool:
    """Return True if *connection* originated from the loopback interface."""
    addr = getattr(connection, "remote_address", None)
    if not addr:
        return False
    host = addr[0] if isinstance(addr, tuple) else addr
    if not isinstance(host, str):
        return False
    # ``::ffff:127.0.0.1`` is loopback in IPv6-mapped form.
    if host.startswith("::ffff:"):
        host = host[7:]
    return host in _LOCALHOSTS


def _timestamp_ms(value: Any) -> int:
    if isinstance(value, int | float):
        return int(value * 1000 if value < 10_000_000_000 else value)
    if isinstance(value, str) and value:
        try:
            from datetime import datetime

            return int(datetime.fromisoformat(value).timestamp() * 1000)
        except ValueError:
            pass
    return int(time.time() * 1000)


def _http_response(
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "text/plain; charset=utf-8",
    extra_headers: list[tuple[str, str]] | None = None,
) -> Response:
    headers = [
        ("Date", email.utils.formatdate(usegmt=True)),
        ("Connection", "close"),
        ("Content-Length", str(len(body))),
        ("Content-Type", content_type),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, Headers(headers), body)


def _http_error(status: int, message: str | None = None) -> Response:
    body = (message or http.HTTPStatus(status).phrase).encode("utf-8")
    return _http_response(body, status=status)


def _bearer_token(headers: Any) -> str | None:
    """Pull a Bearer token out of standard or query-style headers."""
    auth = headers.get("Authorization") or headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def _is_websocket_upgrade(request: WsRequest) -> bool:
    """Detect an actual WS upgrade; plain HTTP GETs to the same path should fall through."""
    upgrade = request.headers.get("Upgrade") or request.headers.get("upgrade")
    connection = request.headers.get("Connection") or request.headers.get("connection")
    if not upgrade or "websocket" not in upgrade.lower():
        return False
    if not connection or "upgrade" not in connection.lower():
        return False
    return True


def _b64url_encode(data: bytes) -> str:
    """URL-safe base64 without padding — compact + friendly in URL paths."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Reverse of :func:`_b64url_encode`; caller handles ``ValueError``."""
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# Allowed MIME types we actually serve from the media endpoint. Anything
# outside this set is degraded to ``application/octet-stream`` so an
# attacker who somehow gets a signed URL for an unexpected file type can't
# trick the browser into sniffing executable content.
_MEDIA_ALLOWED_MIMES: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "video/mp4",
    "video/webm",
    "video/quicktime",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/x-m4a",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/webm",
    "audio/flac",
    "audio/aac",
    "application/pdf",
})


def _issue_route_secret_matches(headers: Any, configured_secret: str) -> bool:
    """Return True if the token-issue HTTP request carries credentials matching ``token_issue_secret``."""
    if not configured_secret:
        return True
    authorization = headers.get("Authorization") or headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
        return hmac.compare_digest(supplied, configured_secret)
    header_token = headers.get("X-OriginAgent-Auth") or headers.get("x-OriginAgent-auth")
    if not header_token:
        return False
    return hmac.compare_digest(header_token.strip(), configured_secret)


class WebSocketChannel(BaseChannel):
    """Run a local WebSocket server; forward text/JSON messages to the message bus."""

    name = "websocket"
    display_name = "WebSocket"

    def __init__(
        self,
        config: Any,
        bus: MessageBus,
        *,
        session_manager: "SessionManager | None" = None,
        static_dist_path: Path | None = None,
        runtime_model_name: Callable[[], str | None] | None = None,
        runtime_introspection: Callable[[], dict | None] | None = None,
    ):
        if isinstance(config, dict):
            config = WebSocketConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: WebSocketConfig = config
        # chat_id -> connections subscribed to it (fan-out target).
        self._subs: dict[str, set[Any]] = {}
        # connection -> chat_ids it is subscribed to (O(1) cleanup on disconnect).
        self._conn_chats: dict[Any, set[str]] = {}
        # connection -> default chat_id for legacy frames that omit routing.
        self._conn_default: dict[Any, str] = {}
        # Gateway authentication — extracted to gateway/auth.py.
        self._gateway_auth = GatewayAuth(self.config)
        # REST API handlers — extracted to gateway/rest_api.py.
        self._rest_api = RestApi(self)
        self._stop_event: asyncio.Event | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._session_manager = session_manager
        self._static_dist_path: Path | None = (
            static_dist_path.resolve() if static_dist_path is not None else None
        )
        self._runtime_model_name = runtime_model_name
        self._runtime_introspection = runtime_introspection
        # Media server — signed URLs + static SPA serving (gateway/media_server.py).
        self._media_server = MediaServer(secrets.token_bytes(32), self._static_dist_path)
        # Cached config to avoid repeated self._load_config() disk I/O (D2).
        self._cached_config: Any = None
        self._cached_config_path: str | None = None
        self._voice_pipeline = None  # set by ChannelManager
        self._voice_tts_pending: set[str] = set()  # chat_ids awaiting TTS

    def _load_config(self) -> Any:
        """Load config once and cache; reload if the config path has changed."""
        from OriginAgent.config.loader import load_config, get_config_path
        current_path = str(get_config_path())
        if self._cached_config is None or self._cached_config_path != current_path:
            self._cached_config = load_config()
            self._cached_config_path = current_path
        return self._cached_config

    # -- Subscription bookkeeping -------------------------------------------

    def _attach(self, connection: Any, chat_id: str) -> None:
        """Idempotently subscribe *connection* to *chat_id*."""
        self._subs.setdefault(chat_id, set()).add(connection)
        self._conn_chats.setdefault(connection, set()).add(chat_id)

    def _cleanup_connection(self, connection: Any) -> None:
        """Remove *connection* from every subscription set; safe to call multiple times."""
        chat_ids = self._conn_chats.pop(connection, set())
        for cid in chat_ids:
            subs = self._subs.get(cid)
            if subs is None:
                continue
            subs.discard(connection)
            if not subs:
                self._subs.pop(cid, None)
        self._conn_default.pop(connection, None)

    async def _maybe_push_active_goal_state(self, chat_id: str) -> None:
        """Replay persisted goal state after a client subscribes."""
        if self._session_manager is None:
            return
        session = self._session_manager.get_or_create(f"websocket:{chat_id}")
        blob = goal_state_ws_blob(session.metadata)
        if blob.get("active") is True:
            await self.send_goal_state(chat_id, blob)

    async def _send_event(self, connection: Any, event: str, **fields: Any) -> None:
        """Send a control event (attached, error, ...) to a single connection."""
        payload: dict[str, Any] = {"event": event}
        payload.update(fields)
        raw = json.dumps(payload, ensure_ascii=False)
        try:
            await connection.send(raw)
        except ConnectionClosed:
            self._cleanup_connection(connection)
        except Exception as e:
            self.logger.warning("failed to send {} event: {}", event, e)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return WebSocketConfig().model_dump(by_alias=True)

    def _expected_path(self) -> str:
        return _normalize_config_path(self.config.path)

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        cert = self.config.ssl_certfile.strip()
        key = self.config.ssl_keyfile.strip()
        if not cert and not key:
            return None
        if not cert or not key:
            raise ValueError(
                "ssl_certfile and ssl_keyfile must both be set for WSS, or both left empty"
            )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        return ctx

    _MAX_ISSUED_TOKENS = 10_000

    def _purge_expired_issued_tokens(self) -> None:
        self._gateway_auth.purge_expired_issued_tokens()

    def _take_issued_token_if_valid(self, token_value: str | None) -> bool:
        return self._gateway_auth.take_issued_token_if_valid(token_value)

    def _handle_token_issue_http(self, connection: Any, request: Any) -> Any:
        return self._gateway_auth.handle_token_issue_http(connection, request, self.logger)

    # -- HTTP dispatch ------------------------------------------------------

    async def _dispatch_http(self, connection: Any, request: WsRequest) -> Any:
        """Route an inbound HTTP request to a handler or to the WS upgrade path."""
        got, query = _parse_request_path(request.path)

        # 1. Token issue endpoint (legacy, optional, gated by configured secret).
        if self.config.token_issue_path:
            issue_expected = _normalize_config_path(self.config.token_issue_path)
            if got == issue_expected:
                return self._gateway_auth.handle_token_issue_http(connection, request)

        # 2. REST API and /webui/bootstrap handlers (extracted to gateway/rest_api.py).
        result = self._rest_api.dispatch(request, connection)
        if result is not None:
            return result

        # 3. WebSocket upgrade.
        expected_ws = self._expected_path()
        if got == expected_ws and _is_websocket_upgrade(request):
            client_id = _query_first(query, "client_id") or ""
            if len(client_id) > 128:
                client_id = client_id[:128]
            if not self.is_allowed(client_id):
                return connection.respond(403, "Forbidden")
            return self._authorize_websocket_handshake(connection, query)

        # Signed media fetch.
        m = re.match(r"^/api/media/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)$", got)
        if m:
            return self._handle_media_fetch(m.group(1), m.group(2))

        # 4. Static SPA serving (only if a build directory was wired in).
        response = self._media_server.serve_static(got)
        if response is not None:
            return response

        return connection.respond(404, "Not Found")

    # -- Media / transcript helpers (used by both channel and RestApi) ------

    def _try_append_webui_transcript(self, chat_id: str, wire: dict[str, Any]) -> None:
        from OriginAgent.utils.webui_transcript import append_transcript_object

        if wire.get("_transcript_recorded"):
            return
        try:
            dup = json.loads(json.dumps(wire, ensure_ascii=False))
            dup.pop("_transcript_recorded", None)
            append_transcript_object(f"websocket:{chat_id}", dup)
        except (TypeError, ValueError, OSError) as e:
            self.logger.warning("webui transcript append failed: {}", e)

    def append_webui_transcript_event(self, chat_id: str, wire: dict[str, Any]) -> None:
        """Append a prebuilt WebUI transcript event.

        Agent command shortcuts persist to the session directly and may also
        call this hook when the active channel supports the WebUI transcript.
        Keeping the hook public avoids coupling the agent loop to websocket
        channel internals while still making command turns replayable after a
        refresh.
        """
        self._try_append_webui_transcript(chat_id, wire)

    def _augment_transcript_user_media(self, paths: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for pstr in paths:
            path = Path(pstr)
            att = self._sign_or_stage_media_path(path)
            if att is None:
                continue
            kind = _ui_media_kind_for_path(path)
            out.append({"kind": kind, "url": att["url"], "name": att.get("name", path.name)})
        return out

    def _augment_media_urls(self, payload: dict[str, Any]) -> None:
        """Mutate *payload* in place: each message's ``media`` path list is
        replaced by a parallel ``media_urls`` list of signed fetch URLs.

        Messages without media or with non-string path entries are left
        untouched. Paths that cannot be signed or staged (e.g. the file was
        deleted) are silently skipped; the client falls back to the
        historical-replay placeholder tile.
        """
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            media = msg.get("media")
            if not isinstance(media, list) or not media:
                continue
            urls: list[dict[str, str]] = []
            for entry in media:
                if not isinstance(entry, str) or not entry:
                    continue
                signed = self._sign_or_stage_media_path(Path(entry))
                if signed is None:
                    continue
                urls.append(signed)
            if urls:
                msg["media_urls"] = urls
            # Always drop the raw paths from the wire payload.
            msg.pop("media", None)

    def _sign_media_path(self, abs_path: Path) -> str | None:
        return self._media_server.sign_media_path(abs_path)

    def _sign_or_stage_media_path(self, path: Path) -> dict[str, str] | None:
        return self._media_server.sign_or_stage_media_path(path, self.logger)

    def _workspace_upload_dir(self) -> Path:
        workspace: str | Path | None = None
        if self._session_manager is not None:
            candidate = getattr(self._session_manager, "workspace", None)
            if isinstance(candidate, Path):
                workspace = candidate
            elif isinstance(candidate, str) and candidate.strip():
                workspace = candidate
        return get_workspace_upload_dir(workspace, "websocket")

    def _handle_media_fetch(self, sig: str, payload: str) -> Response:
        return self._media_server.handle_media_fetch(sig, payload)

    def _authorize_websocket_handshake(self, connection: Any, query: dict[str, list[str]]) -> Any:
        supplied = _query_first(query, "token")
        static_token = self.config.token.strip()

        if static_token:
            if supplied and hmac.compare_digest(supplied, static_token):
                return None
            if supplied and self._take_issued_token_if_valid(supplied):
                return None
            return connection.respond(401, "Unauthorized")

        if self.config.websocket_requires_token:
            if supplied and self._take_issued_token_if_valid(supplied):
                return None
            return connection.respond(401, "Unauthorized")

        if supplied:
            self._take_issued_token_if_valid(supplied)
        return None

    async def start(self) -> None:
        self._running = True
        self._stop_event = asyncio.Event()

        ssl_context = self._build_ssl_context()
        scheme = "wss" if ssl_context else "ws"

        async def process_request(
            connection: ServerConnection,
            request: WsRequest,
        ) -> Any:
            return await self._dispatch_http(connection, request)

        async def handler(connection: ServerConnection) -> None:
            await self._connection_loop(connection)

        self.logger.info(
            "WebSocket server listening on {}://{}:{}{}",
            scheme,
            self.config.host,
            self.config.port,
            self.config.path,
        )
        if self.config.token_issue_path:
            self.logger.info(
                "WebSocket token issue route: {}://{}:{}{}",
                scheme,
                self.config.host,
                self.config.port,
                _normalize_config_path(self.config.token_issue_path),
            )

        async def runner() -> None:
            async with serve(
                handler,
                self.config.host,
                self.config.port,
                process_request=process_request,
                max_size=self.config.max_message_bytes,
                ping_interval=self.config.ping_interval_s,
                ping_timeout=self.config.ping_timeout_s,
                ssl=ssl_context,
            ):
                assert self._stop_event is not None
                await self._stop_event.wait()

        self._server_task = asyncio.create_task(runner())
        await self._server_task

    async def _connection_loop(self, connection: Any) -> None:
        request = connection.request
        path_part = request.path if request else "/"
        _, query = _parse_request_path(path_part)
        client_id_raw = _query_first(query, "client_id")
        client_id = client_id_raw.strip() if client_id_raw else ""
        if not client_id:
            client_id = f"anon-{uuid.uuid4().hex[:12]}"
        elif len(client_id) > 128:
            self.logger.warning("client_id too long ({} chars), truncating", len(client_id))
            client_id = client_id[:128]

        default_chat_id = str(uuid.uuid4())

        try:
            await connection.send(
                json.dumps(
                    {
                        "event": "ready",
                        "chat_id": default_chat_id,
                        "client_id": client_id,
                    },
                    ensure_ascii=False,
                )
            )
            # Register only after ready is successfully sent to avoid out-of-order sends
            self._conn_default[connection] = default_chat_id
            self._attach(connection, default_chat_id)

            async for raw in connection:
                if isinstance(raw, bytes):
                    try:
                        raw = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        self.logger.warning("ignoring non-utf8 binary frame")
                        continue

                envelope = _parse_envelope(raw)
                if envelope is not None:
                    await self._dispatch_envelope(connection, client_id, envelope)
                    continue

                content = _parse_inbound_payload(raw)
                if content is None:
                    continue
                await self._handle_message(
                    sender_id=client_id,
                    chat_id=default_chat_id,
                    content=content,
                    metadata={"remote": getattr(connection, "remote_address", None)},
                )
        except Exception as e:
            self.logger.debug("connection ended: {}", e)
        finally:
            self._cleanup_connection(connection)

    def _save_envelope_media(
        self,
        media: list[Any],
    ) -> tuple[list[str], str | None]:
        """Decode and persist ``media`` items from a ``message`` envelope.

        Returns ``(paths, None)`` on success or ``([], reason)`` on the first
        failure — the caller is expected to surface ``reason`` to the client
        and skip publishing so no half-formed message ever reaches the agent.
        On failure, any files already written to disk earlier in the same
        call are unlinked so partial ingress doesn't leak orphan files.
        ``reason`` is a short, stable token suitable for UI localization.

        Shape: ``list[{"data_url": str, "name"?: str | None}]``.
        """
        image_count = 0
        video_count = 0
        audio_count = 0
        document_count = 0
        for item in media:
            mime = _extract_data_url_mime(item.get("data_url", "")) if isinstance(item, dict) else None
            if mime in _VIDEO_MIME_ALLOWED:
                video_count += 1
            elif mime in _IMAGE_MIME_ALLOWED:
                image_count += 1
            elif mime in _AUDIO_MIME_ALLOWED:
                audio_count += 1
            elif mime in _DOCUMENT_MIME_ALLOWED:
                document_count += 1
        if image_count > _MAX_IMAGES_PER_MESSAGE:
            return [], "too_many_images"
        if video_count > _MAX_VIDEOS_PER_MESSAGE:
            return [], "too_many_videos"
        if audio_count > _MAX_AUDIOS_PER_MESSAGE:
            return [], "too_many_audio"
        if document_count > _MAX_DOCUMENTS_PER_MESSAGE:
            return [], "too_many_attachments"

        media_dir = self._workspace_upload_dir()
        paths: list[str] = []

        def _abort(reason: str) -> tuple[list[str], str]:
            for p in paths:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError as exc:
                    self.logger.warning(
                        "failed to unlink partial media {}: {}", p, exc
                    )
            return [], reason

        for item in media:
            if not isinstance(item, dict):
                return _abort("malformed")
            data_url = item.get("data_url")
            if not isinstance(data_url, str) or not data_url:
                return _abort("malformed")
            mime = _extract_data_url_mime(data_url)
            if mime is None:
                return _abort("decode")
            if mime not in _UPLOAD_MIME_ALLOWED:
                return _abort("mime")
            if mime in _VIDEO_MIME_ALLOWED:
                max_bytes = _MAX_VIDEO_BYTES
            elif mime in _AUDIO_MIME_ALLOWED:
                max_bytes = _MAX_AUDIO_BYTES
            elif mime in _DOCUMENT_MIME_ALLOWED:
                max_bytes = _MAX_DOCUMENT_BYTES
            else:
                max_bytes = _MAX_IMAGE_BYTES
            try:
                saved = save_base64_data_url(
                    data_url, media_dir, max_bytes=max_bytes,
                )
            except FileSizeExceeded:
                return _abort("size")
            except Exception as exc:
                self.logger.warning("media decode failed: {}", exc)
                return _abort("decode")
            if saved is None:
                return _abort("decode")
            paths.append(saved)
        return paths, None

    async def _dispatch_envelope(
        self,
        connection: Any,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        """Route one typed inbound envelope (``new_chat`` / ``attach`` / ``message``)."""
        t = envelope.get("type")
        if t == "new_chat":
            new_id = str(uuid.uuid4())
            self._attach(connection, new_id)
            await self._send_event(connection, "attached", chat_id=new_id)
            await self._maybe_push_active_goal_state(new_id)
            return
        if t == "attach":
            cid = envelope.get("chat_id")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            self._attach(connection, cid)
            await self._send_event(connection, "attached", chat_id=cid)
            await self._maybe_push_active_goal_state(cid)
            return
        if t == "voice_message":
            await self._handle_voice_message_envelope(connection, envelope)
            return
        if t == "message":
            cid = envelope.get("chat_id")
            content = envelope.get("content")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            if not isinstance(content, str):
                await self._send_event(connection, "error", detail="missing content")
                return

            raw_media = envelope.get("media")
            media_paths: list[str] = []
            if raw_media is not None:
                if not isinstance(raw_media, list):
                    await self._send_event(
                        connection, "error",
                        chat_id=cid, detail="attachment_rejected", reason="malformed",
                    )
                    return
                media_paths, reason = self._save_envelope_media(raw_media)
                if reason is not None:
                    await self._send_event(
                        connection, "error",
                        chat_id=cid, detail="attachment_rejected", reason=reason,
                    )
                    return

            # Allow image-only turns (content may be empty when media is attached).
            if not content.strip() and not media_paths:
                await self._send_event(connection, "error", detail="missing content")
                return

            # Auto-attach on first use so clients can one-shot without a separate attach.
            self._attach(connection, cid)
            metadata: dict[str, Any] = {"remote": getattr(connection, "remote_address", None)}
            if envelope.get("webui") is True:
                metadata["webui"] = True
            lang = envelope.get("lang")
            if isinstance(lang, str) and lang.strip():
                metadata["lang"] = lang.strip()
            image_generation = envelope.get("image_generation")
            if isinstance(image_generation, dict) and image_generation.get("enabled") is True:
                aspect_ratio = image_generation.get("aspect_ratio")
                metadata["image_generation"] = {
                    "enabled": True,
                    "aspect_ratio": aspect_ratio if isinstance(aspect_ratio, str) else None,
                }
            await self._handle_message(
                sender_id=client_id,
                chat_id=cid,
                content=content,
                media=media_paths or None,
                metadata=metadata,
            )
            return
        await self._send_event(connection, "error", detail=f"unknown type: {t!r}")

    async def _handle_voice_message_envelope(
        self, connection, envelope: dict
    ) -> None:
        """Handle a 'voice_message' envelope — transcribe and inject into agent loop."""
        chat_id = str(envelope.get("chat_id") or "")
        if not chat_id:
            await self._send_event(
                connection, "error", message="voice_message requires chat_id"
            )
            return

        audio_data_url = (
            envelope.get("audio_data_url")
            or envelope.get("data_url")
            or ""
        )
        if not audio_data_url:
            await self._send_event(
                connection, "error", message="voice_message requires audio_data_url"
            )
            return

        # Save audio data to a file in uploads/perception
        from OriginAgent.config.paths import get_workspace_upload_dir
        from OriginAgent.voice.audio import save_audio_data_url

        uploads_dir = get_workspace_upload_dir() / "perception"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        saved_path = save_audio_data_url(audio_data_url, uploads_dir, prefix="voice_ws_")
        if saved_path is None:
            await self._send_event(
                connection, "error", message="could not decode audio data"
            )
            return

        # Use the voice pipeline if available
        voice_pipeline = getattr(self, "_voice_pipeline", None)
        if voice_pipeline is None:
            await self._send_event(
                connection, "error", message="voice pipeline not available"
            )
            return

        result = await voice_pipeline.process_voice_message(
            audio_path=saved_path,
            chat_id=chat_id,
            channel_name="websocket",
            sender_id=getattr(connection, "id", "ws-client"),
            metadata={"_voice_source": "websocket"},
        )

        # Mark for auto-TTS when the agent responds
        self._voice_tts_pending.add(chat_id)

        await self._send_event(
            connection,
            "voice_processed",
            transcribed_text=result.transcribed_text,
            status=result.status,
        )

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        meta = metadata or {}
        if meta.get("webui"):
            user_obj: dict[str, Any] = {
                "event": "user",
                "chat_id": chat_id,
                "text": content,
            }
            if media:
                user_obj["media_paths"] = list(media)
            self._try_append_webui_transcript(chat_id, user_obj)
        await super()._handle_message(
            sender_id,
            chat_id,
            content,
            media,
            metadata,
            session_key,
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._stop_event:
            self._stop_event.set()
        if self._server_task:
            try:
                await self._server_task
            except Exception as e:
                self.logger.warning("server task error during shutdown: {}", e)
            self._server_task = None
        self._subs.clear()
        self._conn_chats.clear()
        self._conn_default.clear()
        self._gateway_auth.clear_all()

    async def _safe_send_to(self, connection: Any, raw: str, *, label: str = "") -> None:
        """Send a raw frame to one connection, cleaning up on ConnectionClosed."""
        try:
            await connection.send(raw)
        except ConnectionClosed:
            self._cleanup_connection(connection)
            self.logger.warning("connection gone{}", label)
        except Exception:
            self.logger.exception("send failed{}", label)
            raise

    async def send(self, msg: OutboundMessage) -> None:
        if msg.metadata.get("_runtime_model_updated"):
            await self.send_runtime_model_updated(
                model_name=msg.metadata.get("model"),
                model_preset=msg.metadata.get("model_preset"),
            )
            return

        # Snapshot the subscriber set so ConnectionClosed cleanups mid-iteration are safe.
        conns = list(self._subs.get(msg.chat_id, ()))
        if not conns:
            if (
                msg.metadata.get("_progress")
                or msg.metadata.get("_turn_end")
                or msg.metadata.get("_session_updated")
                or msg.metadata.get("_goal_status")
                or msg.metadata.get("_goal_state_sync")
            ):
                self.logger.debug("no active subscribers for chat_id={}", msg.chat_id)
            else:
                self.logger.warning("no active subscribers for chat_id={}", msg.chat_id)
            return
        # Signal that the agent has fully finished processing the current turn.
        if msg.metadata.get("_goal_state_sync"):
            blob = msg.metadata.get("goal_state")
            await self.send_goal_state(msg.chat_id, blob if isinstance(blob, dict) else {"active": False})
            return
        if msg.metadata.get("_goal_status"):
            status = msg.metadata.get("goal_status")
            if status in {"running", "idle"}:
                started_at = msg.metadata.get("started_at", msg.metadata.get("goal_started_at"))
                await self.send_goal_status(
                    msg.chat_id,
                    status,
                    started_at=started_at if isinstance(started_at, int | float) else None,
                )
            return
        if msg.metadata.get("_turn_end"):
            lat = msg.metadata.get("latency_ms")
            gs = msg.metadata.get("goal_state")
            await self.send_turn_end(
                msg.chat_id,
                latency_ms=int(lat) if isinstance(lat, int | float) else None,
                goal_state=gs if isinstance(gs, dict) else None,
            )
            return
        if msg.metadata.get("_session_updated"):
            await self.send_session_updated(msg.chat_id)
            return
        text = msg.content
        if msg.buttons:
            text = _append_buttons_as_text(text, msg.buttons)
        payload: dict[str, Any] = {
            "event": "message",
            "chat_id": msg.chat_id,
            "text": text,
        }
        if msg.metadata.get("_webui_transcript_recorded"):
            payload["_transcript_recorded"] = True
        if msg.buttons:
            payload["buttons"] = msg.buttons
            payload["button_prompt"] = msg.content
        if msg.media:
            payload["media"] = msg.media
            urls: list[dict[str, str]] = []
            for entry in msg.media:
                signed = self._sign_or_stage_media_path(Path(entry))
                if signed is not None:
                    urls.append(signed)
            if urls:
                payload["media_urls"] = urls
        if msg.reply_to:
            payload["reply_to"] = msg.reply_to
        lat = msg.metadata.get("latency_ms")
        if isinstance(lat, int | float):
            payload["latency_ms"] = int(lat)
        if msg.metadata.get("_tool_events"):
            payload["tool_events"] = msg.metadata["_tool_events"]
        agent_ui = msg.metadata.get(OUTBOUND_META_AGENT_UI)
        if agent_ui is not None:
            payload["agent_ui"] = agent_ui
        origin_meta = extract_origin_metadata(msg.metadata)
        if origin_meta:
            payload["origin"] = origin_meta
        label = origin_label(msg.metadata)
        if label:
            payload["origin_label"] = label
        # Mark intermediate agent breadcrumbs (tool-call hints, generic
        # progress strings) so WS clients can render them as subordinate
        # trace rows rather than conversational replies.
        if msg.metadata.get("_tool_hint"):
            payload["kind"] = "tool_hint"
        elif msg.metadata.get("_progress"):
            payload["kind"] = "progress"
        self._try_append_webui_transcript(msg.chat_id, payload)
        payload.pop("_transcript_recorded", None)
        raw = json.dumps(payload, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" ")

        # Auto-TTS for voice messages: if the sender used a voice_message
        # input, synthesize the response and push a voice_audio event.
        if (
            msg.chat_id in self._voice_tts_pending
            and self._voice_pipeline is not None
            and self._voice_pipeline._tts is not None
        ):
            self._voice_tts_pending.discard(msg.chat_id)
            tts = self._voice_pipeline._tts
            try:
                tts_path = await tts.synthesize(msg.content)
                if tts_path is not None:
                    audio_url = self._sign_media_path(tts_path)
                    if audio_url is not None:
                        audio_payload: dict[str, Any] = {
                            "event": "voice_audio",
                            "chat_id": msg.chat_id,
                            "audio_url": audio_url,
                        }
                        audio_raw = json.dumps(audio_payload, ensure_ascii=False)
                        for connection in conns:
                            await self._safe_send_to(
                                connection, audio_raw, label="voice_audio"
                            )
            except Exception:
                self.logger.exception("Auto-TTS failed for {}", msg.chat_id)

    async def send_reasoning_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Push one chunk of model reasoning for in-place WebUI rendering."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns or not delta:
            return
        meta = metadata or {}
        body: dict[str, Any] = {
            "event": "reasoning_delta",
            "chat_id": chat_id,
            "text": delta,
        }
        if meta.get("_stream_id") is not None:
            body["stream_id"] = meta["_stream_id"]
        self._try_append_webui_transcript(chat_id, body)
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" reasoning ")

    async def send_reasoning_end(
        self,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Close the current reasoning stream segment."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        meta = metadata or {}
        body: dict[str, Any] = {"event": "reasoning_end", "chat_id": chat_id}
        if meta.get("_stream_id") is not None:
            body["stream_id"] = meta["_stream_id"]
        self._try_append_webui_transcript(chat_id, body)
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" reasoning_end ")

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        meta = metadata or {}
        if meta.get("_stream_end"):
            body: dict[str, Any] = {"event": "stream_end", "chat_id": chat_id}
        else:
            body = {
                "event": "delta",
                "chat_id": chat_id,
                "text": delta,
            }
        if meta.get("_stream_id") is not None:
            body["stream_id"] = meta["_stream_id"]
        self._try_append_webui_transcript(chat_id, body)
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" stream ")

    async def send_turn_end(
        self,
        chat_id: str,
        latency_ms: int | None = None,
        *,
        goal_state: dict[str, Any] | None = None,
    ) -> None:
        """Signal that the agent has fully finished processing the current turn."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {"event": "turn_end", "chat_id": chat_id}
        if latency_ms is not None:
            body["latency_ms"] = int(latency_ms)
        if goal_state is not None:
            body["goal_state"] = goal_state
        self._try_append_webui_transcript(chat_id, body)
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" turn_end ")

    async def send_goal_state(self, chat_id: str, blob: dict[str, Any]) -> None:
        """Push persisted goal-state snapshot for one chat."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body = {"event": "goal_state", "chat_id": chat_id, "goal_state": blob}
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" goal_state ")

    async def send_goal_status(
        self,
        chat_id: str,
        status: str,
        *,
        started_at: float | None = None,
    ) -> None:
        """Push running/idle status for the current turn strip."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {"event": "goal_status", "chat_id": chat_id, "status": status}
        if status == "running" and started_at is not None:
            body["started_at"] = started_at
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" goal_status ")

    async def send_session_updated(self, chat_id: str) -> None:
        """Notify clients that session metadata changed outside the main turn."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {"event": "session_updated", "chat_id": chat_id}
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" session_updated ")

    async def send_runtime_model_updated(
        self,
        *,
        model_name: Any,
        model_preset: Any = None,
    ) -> None:
        """Broadcast runtime model changes to every open websocket connection."""
        conns = list(self._conn_chats)
        if not conns or not isinstance(model_name, str) or not model_name.strip():
            return
        body: dict[str, Any] = {
            "event": "runtime_model_updated",
            "model_name": model_name.strip(),
        }
        if isinstance(model_preset, str) and model_preset.strip():
            body["model_preset"] = model_preset.strip()
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" runtime_model_updated ")
