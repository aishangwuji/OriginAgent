"""REST API handlers for the WebSocket channel HTTP surface.

Extracted from WebSocketChannel (~3222 lines) to reduce the God Class.
All ``/api/*`` and ``/webui/bootstrap`` route handlers live here.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlparse

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from OriginAgent.gateway._helpers import http_error, http_json_response, http_response
from OriginAgent.gateway.auth import GatewayAuth

if TYPE_CHECKING:
    from OriginAgent.channels.websocket import WebSocketChannel


# ── Module-level helpers (extracted verbatim from websocket.py) ──────────────


def _strip_trailing_slash(path: str) -> str:
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path or "/"


def _parse_request_path(path_with_query: str) -> tuple[str, dict[str, list[str]]]:
    parsed = urlparse("ws://x" + path_with_query)
    path = _strip_trailing_slash(parsed.path or "/")
    return path, parse_qs(parsed.query, keep_blank_values=True)


def _parse_query(path_with_query: str) -> dict[str, list[str]]:
    return _parse_request_path(path_with_query)[1]


def _query_first(query: dict[str, list[str]], key: str) -> str | None:
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

_LOCALHOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_API_KEY_RE = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")


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
    from urllib.parse import urlparse

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
    import ipaddress
    import socket

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


def _decode_api_key(raw_key: str) -> str | None:
    key = unquote(raw_key)
    if _API_KEY_RE.match(key) is None:
        return None
    return key


def _is_localhost(connection: Any) -> bool:
    addr = getattr(connection, "remote_address", None)
    if not addr:
        return False
    host = addr[0] if isinstance(addr, tuple) else addr
    if not isinstance(host, str):
        return False
    if host.startswith("::ffff:"):
        host = host[7:]
    return host in _LOCALHOSTS


def _issue_route_secret_matches(headers: Any, configured_secret: str) -> bool:
    if not configured_secret:
        return True
    authorization = headers.get("Authorization") or headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
        import hmac

        return hmac.compare_digest(supplied, configured_secret)
    header_token = headers.get("X-OriginAgent-Auth") or headers.get("x-OriginAgent-auth")
    if not header_token:
        return False
    import hmac

    return hmac.compare_digest(header_token.strip(), configured_secret)


def _resolve_bootstrap_model_name(
    runtime_name: Any,
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


def _default_model_name_from_config() -> str | None:
    try:
        from OriginAgent.config.loader import load_config

        model = load_config().resolve_preset().model.strip()
        return model or None
    except Exception as e:
        logger.debug("webui bootstrap could not load model name: {}", e)
        return None


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


def _ui_media_kind_for_path(path: str | Path) -> str:
    import mimetypes

    from OriginAgent.utils.attachments import describe_attachment

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


# ── RestApi class ────────────────────────────────────────────────────────────


class RestApi:
    """REST API handlers for the WebSocket channel HTTP surface.

    Extracted from WebSocketChannel to reduce the God Class. Each method
    handles one route; ``dispatch()`` routes inbound HTTP requests.
    """

    def __init__(self, channel: WebSocketChannel) -> None:
        self._ch = channel

    # ── Proxied channel accessors ──────────────────────────────────────────

    @property
    def config(self) -> Any:
        return self._ch.config

    def _load_config(self) -> Any:
        return self._ch._load_config()

    def _check_api_token(self, request: WsRequest) -> bool:
        return self._ch._gateway_auth.check_api_token(request)

    def _expected_path(self) -> str:
        return self._ch._expected_path()

    # ── Routing ────────────────────────────────────────────────────────────

    async def dispatch(self, request: WsRequest, connection: Any = None) -> Response | None:
        """Route an inbound REST API request to the appropriate handler.

        Returns ``None`` when the path does not match a known REST route —
        the caller should fall through to WebSocket upgrade / static serving.
        """
        got, query = _parse_request_path(request.path)

        # WebUI bootstrap: mints tokens for the embedded UI.
        if got == "/webui/bootstrap":
            return self._handle_webui_bootstrap(request, connection)

        # REST surface for the embedded UI.
        if got == "/api/sessions":
            return self._handle_sessions_list(request)

        if got == "/api/settings":
            return self._handle_settings(request)

        if got == "/api/commands":
            return self._handle_commands(request)

        if got == "/api/self":
            return self._handle_self(request)

        if got == "/api/reviews":
            return self._handle_reviews_list(request)

        if got == "/api/skills":
            return self._handle_skills_list(request)

        if got == "/api/domains":
            return self._handle_domains_list(request)

        if got == "/api/settings/update":
            return self._handle_settings_update(request)

        if got == "/api/settings/provider/update":
            return self._handle_settings_provider_update(request)

        if got == "/api/settings/provider/models":
            return await self._handle_settings_provider_models(request)

        if got == "/api/settings/web-search/update":
            return self._handle_settings_web_search_update(request)

        if got == "/api/settings/learning/background-review/update":
            return self._handle_settings_learning_background_review_update(request)

        if got == "/api/settings/runtime/update":
            return self._handle_settings_runtime_update(request)

        if got == "/api/settings/local-awareness/audio/update":
            return self._handle_settings_local_awareness_audio_update(request)

        if got == "/api/settings/mcp/upsert":
            return self._handle_settings_mcp_upsert(request)

        if got == "/api/settings/mcp/home-assistant/upsert":
            return self._handle_settings_mcp_home_assistant_upsert(request)

        if got == "/api/settings/mcp/delete":
            return self._handle_settings_mcp_delete(request)

        m = re.match(r"^/api/reviews/([^/]+)$", got)
        if m:
            return self._handle_review_detail(request, m.group(1))

        m = re.match(r"^/api/reviews/([^/]+)/(apply|approve|reject|defer)$", got)
        if m:
            return self._handle_review_action(request, m.group(1), m.group(2))

        m = re.match(r"^/api/skills/([^/]+)$", got)
        if m:
            return self._handle_skill_detail(request, m.group(1))

        m = re.match(r"^/api/skills/([^/]+)/(verify|activate|deprecate|reject|always)$", got)
        if m:
            return self._handle_skill_action(request, m.group(1), m.group(2))

        m = re.match(r"^/api/domains/([^/]+)$", got)
        if m:
            return self._handle_domain_detail(request, m.group(1))

        m = re.match(
            r"^/api/domains/([^/]+)/(upgrade|enable|disable|activate|deactivate|uninstall|eval)$",
            got,
        )
        if m:
            return self._handle_domain_action(request, m.group(1), m.group(2))

        if got == "/api/domains/install":
            return self._handle_domains_install(request)

        m = re.match(r"^/api/sessions/([^/]+)/messages$", got)
        if m:
            return self._handle_session_messages(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/webui-thread$", got)
        if m:
            return self._handle_webui_thread_get(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/delete$", got)
        if m:
            return self._handle_session_delete(request, m.group(1))

        # -- Meta-cognition / evolution routes (delegated to channels/routes/cognition.py) --
        from OriginAgent.channels.routes.cognition import (
            handle_evolution_signals,
            handle_evolution_status,
            handle_meta_cognition_status,
            handle_signal_action,
        )

        if got == "/api/cognition/status":
            return handle_meta_cognition_status(
                request,
                check_token=self._check_api_token,
                get_introspection=self._ch._runtime_introspection,
            )

        if got == "/api/evolution/status":
            return handle_evolution_status(
                request,
                check_token=self._check_api_token,
                load_config=self._load_config,
            )

        if got == "/api/evolution/signals":
            return handle_evolution_signals(
                request,
                check_token=self._check_api_token,
                load_config=self._load_config,
            )

        m = re.match(r"^/api/evolution/signals/([^/]+)/(suppress|resume)$", got)
        if m:
            return handle_signal_action(
                request,
                m.group(1),
                m.group(2),
                check_token=self._check_api_token,
                load_config=self._load_config,
            )

        if got.startswith("/api/") and not got.startswith("/api/media/"):
            return http_error(404, "not found")

        return None

    # ── WebUI bootstrap ────────────────────────────────────────────────────

    def _handle_webui_bootstrap(self, request: WsRequest, connection: Any = None) -> Response:
        secret = self.config.token_issue_secret.strip() or self.config.token.strip()
        if secret:
            if not _issue_route_secret_matches(request.headers, secret):
                return http_error(401, "Unauthorized")
        elif connection is None or not _is_localhost(connection):
            return http_error(403, "webui bootstrap is localhost-only")
        self._ch._gateway_auth.purge_expired_issued_tokens()
        self._ch._gateway_auth.purge_expired_api_tokens()
        if self._ch._gateway_auth.total_token_count >= GatewayAuth._MAX_ISSUED_TOKENS:
            return http_json_response({"error": "too many outstanding tokens"}, status=429)
        token = f"nbwt_{secrets.token_urlsafe(32)}"
        self._ch._gateway_auth.register_dual_token(token, float(self.config.token_ttl_s))
        return http_json_response(
            {
                "token": token,
                "ws_path": self._expected_path(),
                "expires_in": self.config.token_ttl_s,
                "model_name": _resolve_bootstrap_model_name(self._ch._runtime_model_name),
                "runtime_mode": self._bootstrap_runtime_mode_payload(),
                "config_doctor": self._bootstrap_config_doctor_payload(),
            }
        )

    # ── Session list ───────────────────────────────────────────────────────

    def _handle_sessions_list(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        if self._ch._session_manager is None:
            return http_error(503, "session manager unavailable")
        sessions = self._ch._session_manager.list_sessions()
        cleaned_map: dict[str, dict[str, Any]] = {
            str(s["key"]): {k: v for k, v in s.items() if k != "path"}
            for s in sessions
            if isinstance(s.get("key"), str) and s["key"].startswith("websocket:")
        }
        for transcript_session in self._list_webui_transcript_sessions():
            key = str(transcript_session["key"])
            existing = cleaned_map.get(key)
            if existing is None:
                cleaned_map[key] = transcript_session
                continue
            if not existing.get("preview") and transcript_session.get("preview"):
                existing["preview"] = transcript_session["preview"]
            existing_updated_at = existing.get("updated_at")
            transcript_updated_at = transcript_session.get("updated_at")
            if (
                isinstance(transcript_updated_at, str)
                and transcript_updated_at
                and (
                    not isinstance(existing_updated_at, str)
                    or not existing_updated_at
                    or transcript_updated_at > existing_updated_at
                )
            ):
                existing["updated_at"] = transcript_updated_at
            if not existing.get("created_at") and transcript_session.get("created_at"):
                existing["created_at"] = transcript_session["created_at"]
        cleaned = sorted(
            cleaned_map.values(),
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        return http_json_response({"sessions": cleaned})

    @staticmethod
    def _transcript_preview_from_lines(lines: list[dict[str, Any]]) -> str:
        def _normalize(value: Any) -> str:
            text = value if isinstance(value, str) else ""
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 120:
                text = text[:119].rstrip() + "..."
            return text

        fallback = ""
        for line in lines:
            if not isinstance(line, dict):
                continue
            text = _normalize(line.get("text"))
            if not text:
                continue
            if line.get("event") == "user":
                return text
            if not fallback and line.get("event") in {"message", "delta"}:
                fallback = text
        return fallback

    def _list_webui_transcript_sessions(self) -> list[dict[str, Any]]:
        from OriginAgent.config.paths import get_webui_dir
        from OriginAgent.utils.webui_transcript import read_transcript_lines

        webui_dir = get_webui_dir()
        if not webui_dir.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        for path in webui_dir.glob("websocket_*.jsonl"):
            lines = read_transcript_lines(path.stem.replace("_", ":", 1))
            if not lines:
                continue
            chat_id = next(
                (
                    str(line.get("chat_id"))
                    for line in lines
                    if isinstance(line, dict) and isinstance(line.get("chat_id"), str) and line.get("chat_id")
                ),
                "",
            )
            if not chat_id:
                continue
            timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(path.stat().st_mtime))
            rows.append(
                {
                    "key": f"websocket:{chat_id}",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "title": "",
                    "preview": self._transcript_preview_from_lines(lines),
                }
            )
        return rows

    # ── Settings ───────────────────────────────────────────────────────────

    def _settings_payload(self, *, requires_restart: bool = False) -> dict[str, Any]:
        from OriginAgent.config.loader import get_config_path, load_config
        from OriginAgent.providers.model_fetch_contract import get_provider_model_catalog_kind
        from OriginAgent.providers.registry import PROVIDERS, find_by_name

        config = self._load_config()
        defaults = config.agents.defaults
        provider_name = config.get_provider_name(defaults.model) or defaults.provider
        provider = config.get_provider(defaults.model)
        selected_provider = provider_name
        if defaults.provider != "auto":
            spec = find_by_name(defaults.provider)
            provider_config = getattr(config.providers, spec.name, None) if spec else None
            if spec and (
                spec.is_oauth
                or spec.is_local
                or spec.is_direct
                or bool(provider_config and provider_config.api_key)
            ):
                selected_provider = spec.name
            elif spec and provider_name == defaults.provider:
                selected_provider = spec.name
        providers = []
        for spec in PROVIDERS:
            provider_config = getattr(config.providers, spec.name, None)
            if provider_config is None or spec.is_oauth or spec.is_local:
                continue
            providers.append(
                {
                    "name": spec.name,
                    "label": spec.label,
                    "configured": bool(provider_config.api_key),
                    "api_key_hint": _mask_secret_hint(provider_config.api_key),
                    "api_base": provider_config.api_base,
                    "default_api_base": spec.default_api_base or None,
                    "model_catalog_kind": get_provider_model_catalog_kind(spec.name),
                }
            )
        search_config = config.tools.web.search
        search_provider = (
            search_config.provider
            if search_config.provider in _WEB_SEARCH_PROVIDER_BY_NAME
            else "duckduckgo"
        )
        return {
            "agent": {
                "model": defaults.model,
                "provider": selected_provider,
                "resolved_provider": provider_name,
                "has_api_key": bool(provider and provider.api_key),
            },
            "providers": providers,
            "web_search": {
                "provider": search_provider,
                "api_key_hint": _mask_secret_hint(search_config.api_key),
                "base_url": search_config.base_url or None,
                "providers": list(_WEB_SEARCH_PROVIDER_OPTIONS),
            },
            "learning": {
                "background_review": {
                    "enabled": bool(defaults.learning.background_review.enabled),
                },
                "curator": {
                    "enabled": bool(defaults.learning.curator.enabled),
                },
            },
            "voice": _settings_voice_payload(config),
            "runtime_controls": _settings_runtime_controls_payload(config),
            "mcp": {
                "servers": [
                    _mcp_server_payload(name, server)
                    for name, server in sorted(config.tools.mcp_servers.items())
                ],
            },
            "runtime": {
                "config_path": str(get_config_path().expanduser()),
            },
            "requires_restart": requires_restart,
        }

    def _bootstrap_runtime_mode_payload(self) -> dict[str, Any]:
        from OriginAgent.agent.runtime_mode import build_runtime_mode_summary

        config = self._load_config()
        summary = build_runtime_mode_summary(config=config).to_dict()
        return {
            "mode": summary["mode"],
            "enabled_capabilities": summary["enabled_capabilities"],
            "voice": summary["voice"],
            "channels": summary["channels"],
            "providers": summary["providers"],
        }

    def _bootstrap_config_doctor_payload(self) -> dict[str, Any]:
        from OriginAgent.config.doctor import build_config_doctor_report
        from OriginAgent.config.loader import get_config_path

        config = self._load_config()
        doctor = build_config_doctor_report(
            config=config,
            config_path=get_config_path(),
        ).to_dict()
        return {
            "unknown_fields": doctor["unknown_fields"],
            "conflicts": doctor["conflicts"],
            "capability_warnings": doctor["capability_warnings"],
            "ignored_fields": doctor["ignored_fields"],
            "legacy_channel_sections": doctor["legacy_channel_sections"],
        }

    def _handle_settings(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        return http_json_response(self._settings_payload())

    def _handle_commands(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        lang = _query_first(_parse_query(request.path), "lang") or ""
        from OriginAgent.command.builtin import builtin_command_palette

        return http_json_response({"commands": builtin_command_palette(lang=lang)})

    # ── Self model ─────────────────────────────────────────────────────────

    def _self_model_service(self):
        from OriginAgent.agent.confirmation import PendingConfirmationStore
        from OriginAgent.agent.domain_packs import DomainPackManager
        from OriginAgent.agent.self_model import SelfModelService
        from OriginAgent.config.loader import load_config

        config = self._load_config()
        manager = DomainPackManager(
            config.workspace_path,
            config=config.agents.defaults.domain_packs,
        )
        return SelfModelService(
            config.workspace_path,
            sessions=self._ch._session_manager,
            confirmation_store=PendingConfirmationStore(config.workspace_path),
            audit_mode=config.tools.audit.mode,
            runtime_profile=config.runtime.profile,
            domain_pack_manager=manager,
            background_review_enabled=bool(config.agents.defaults.learning.background_review.enabled),
            curator_enabled=bool(config.agents.defaults.learning.curator.enabled),
            nearline_memory_config=config.agents.defaults.nearline_memory,
        )

    def _handle_self(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        return http_json_response({"self_model": self._self_model_service().build()})

    # ── Review proposals ───────────────────────────────────────────────────

    def _review_store(self):
        from OriginAgent.agent.background_review import ReviewProposalStore
        from OriginAgent.config.loader import load_config

        return ReviewProposalStore(self._load_config().workspace_path)

    def _handle_reviews_list(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        status = _query_first(query, "status")
        proposal_type = _query_first(query, "type")
        origin = _query_first(query, "origin")
        limit_raw = _query_first(query, "limit")
        try:
            limit = int(limit_raw) if limit_raw is not None else 50
        except ValueError:
            return http_error(400, "limit must be an integer")
        store = self._review_store()
        return http_json_response({
            "proposals": store.list_records(
                status=status,
                proposal_type=proposal_type,
                origin=origin,
                limit=limit,
            ),
            "stats": store.stats(
                status=status,
                proposal_type=proposal_type,
                origin=origin,
            ),
        })

    def _handle_review_detail(self, request: WsRequest, proposal_id: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        proposal_id = unquote(proposal_id)
        store = self._review_store()
        proposal = store.get(proposal_id)
        if proposal is None:
            return http_error(404, "review proposal not found")
        return http_json_response({"proposal": proposal.to_json()})

    def _handle_review_action(self, request: WsRequest, proposal_id: str, action: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        from OriginAgent.agent.background_review import ReviewProposalStore
        from OriginAgent.config.loader import load_config

        proposal_id = unquote(proposal_id)
        query = _parse_query(request.path)
        reason = _query_first(query, "reason") or ""
        store = ReviewProposalStore(self._load_config().workspace_path)
        proposal = store.get(proposal_id)
        if proposal is None:
            return http_error(404, "review proposal not found")
        try:
            if action == "apply":
                result = store.apply(proposal_id)
            elif action == "approve":
                result = store.approve(proposal_id, reason=reason)
            elif action == "reject":
                result = store.reject(proposal_id, reason=reason)
            elif action == "defer":
                result = store.defer(proposal_id, reason=reason)
            else:
                return http_error(400, f"unknown action: {action}")
        except Exception as exc:
            return http_error(500, str(exc))
        return http_json_response({"proposal": result.to_json() if result else None})

    # ── Skills ─────────────────────────────────────────────────────────────

    def _skills_loader(self):
        from OriginAgent.agent.domain_packs import DomainPackManager
        from OriginAgent.agent.skills import SkillsLoader
        from OriginAgent.config.loader import load_config

        config = self._load_config()
        manager = DomainPackManager(
            config.workspace_path,
            config=config.agents.defaults.domain_packs,
        )
        return SkillsLoader(config.workspace_path, domain_pack_manager=manager)

    def _handle_skills_list(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        source = (_query_first(query, "source") or "").strip()
        status = (_query_first(query, "status") or "").strip()
        limit_raw = _query_first(query, "limit")
        try:
            limit = max(1, min(int(limit_raw) if limit_raw is not None else 50, 200))
        except ValueError:
            return http_error(400, "limit must be an integer")
        loader = self._skills_loader()
        records = loader.list_skill_records(filter_unavailable=False)
        if source:
            records = [record for record in records if str(record.get("source") or "") == source]
        if status:
            records = [
                record for record in records
                if str(record.get("lifecycle_status") or "") == status
            ]
        records = sorted(records, key=lambda item: (str(item.get("source") or ""), str(item.get("name") or "")))
        stats = loader.lifecycle.stats(loader.list_skills(filter_unavailable=False))
        return http_json_response({"skills": records[:limit], "stats": stats})

    def _handle_skill_detail(self, request: WsRequest, skill_name: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        skill_name = unquote(skill_name)
        loader = self._skills_loader()
        record = loader.get_skill_record(skill_name)
        if record is None:
            return http_error(404, "skill not found")
        return http_json_response({
            "skill": record,
            "stats": loader.lifecycle.stats(loader.list_skills(filter_unavailable=False)),
        })

    def _handle_skill_action(self, request: WsRequest, skill_name: str, action: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        from OriginAgent.agent.skill_lifecycle import SkillLifecycleResult

        skill_name = unquote(skill_name)
        query = _parse_query(request.path)
        reason = _query_first(query, "reason") or ""
        loader = self._skills_loader()
        record = loader.get_skill_record(skill_name)
        if record is None:
            result = SkillLifecycleResult(
                skill_name=skill_name,
                status="missing",
                action=action,
                ok=False,
                message="Skill was not found.",
                error="not_found",
            )
        elif record.get("source") != "workspace":
            result = SkillLifecycleResult(
                skill_name=skill_name,
                status=str(record.get("lifecycle_status") or "unknown"),
                action=action,
                ok=False,
                message=str(record.get("disabled_reason") or "Only workspace skills can be changed."),
                skill=record,
                error="read_only",
            )
        elif action == "always":
            enabled_raw = (_query_first(query, "enabled") or "").strip().lower()
            enabled = enabled_raw in {"1", "true", "yes", "on"}
            result = loader.lifecycle.transition(
                skill_name,
                action="always",
                enabled=enabled,
                reason=reason,
            )
        else:
            result = loader.lifecycle.transition(skill_name, action=action, reason=reason)
        status = 404 if result.error == "not_found" else 200
        return http_json_response({
            "result": result.to_json(),
            "skill": result.skill,
            "stats": loader.lifecycle.stats(loader.list_skills(filter_unavailable=False)),
        }, status=status)

    # ── Domains ────────────────────────────────────────────────────────────

    def _domain_governance_service(self):
        from OriginAgent.agent.domain_pack_governance import DomainPackGovernanceService
        from OriginAgent.agent.domain_packs import DomainPackManager
        from OriginAgent.config.loader import load_config

        config = self._load_config()
        manager = DomainPackManager(
            config.workspace_path,
            config=config.agents.defaults.domain_packs,
        )
        return DomainPackGovernanceService(config.workspace_path, domain_pack_manager=manager)

    def _handle_domains_list(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        source = (_query_first(query, "source") or "").strip()
        status = (_query_first(query, "status") or "").strip()
        limit_raw = _query_first(query, "limit")
        try:
            limit = max(1, min(int(limit_raw) if limit_raw is not None else 50, 200))
        except ValueError:
            return http_error(400, "limit must be an integer")
        service = self._domain_governance_service()
        return http_json_response({
            "domains": service.list_records(source=source or None, status=status or None, limit=limit),
            "stats": service.stats(),
        })

    def _handle_domain_detail(self, request: WsRequest, pack_id: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        pack_id = unquote(pack_id)
        service = self._domain_governance_service()
        record = service.get_record(pack_id)
        if record is None:
            return http_error(404, "domain pack not found")
        return http_json_response({"domain": record, "stats": service.stats()})

    def _handle_domains_install(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        source = _query_first(query, "source") or ""
        reason = _query_first(query, "reason") or ""
        service = self._domain_governance_service()
        result = service.install(source, reason=reason)
        status = 200 if result.ok else 400
        return http_json_response({
            "result": result.to_json(),
            "domain": result.pack,
            "stats": service.stats(),
        }, status=status)

    def _handle_domain_action(self, request: WsRequest, pack_id: str, action: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        pack_id = unquote(pack_id)
        query = _parse_query(request.path)
        reason = _query_first(query, "reason") or ""
        source = _query_first(query, "source") or ""
        service = self._domain_governance_service()
        if action == "upgrade":
            result = service.upgrade(pack_id, source, reason=reason)
        elif action == "enable":
            result = service.set_enabled(pack_id, enabled=True, reason=reason)
        elif action == "disable":
            result = service.set_enabled(pack_id, enabled=False, reason=reason)
        elif action == "activate":
            result = service.set_active(pack_id, active=True, reason=reason)
        elif action == "deactivate":
            result = service.set_active(pack_id, active=False, reason=reason)
        elif action == "uninstall":
            result = service.uninstall(pack_id, reason=reason)
        elif action == "eval":
            result = service.eval_pack(pack_id)
        else:
            return http_error(400, "unknown domain action")
        status = 200 if result.ok or result.error == "read_only" else 400
        if result.error == "not_found":
            status = 404
        return http_json_response({
            "result": result.to_json(),
            "domain": result.pack,
            "stats": service.stats(),
        }, status=status)

    # ── Settings updates ───────────────────────────────────────────────────

    def _handle_settings_update(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        from OriginAgent.config.loader import load_config, save_config
        from OriginAgent.providers.registry import find_by_name

        query = _parse_query(request.path)
        config = self._load_config()
        defaults = config.agents.defaults
        changed = False

        model = _query_first(query, "model")
        if model is not None:
            model = model.strip()
            if not model:
                return http_error(400, "model is required")
            if defaults.model != model:
                defaults.model = model
                changed = True

        provider = _query_first(query, "provider")
        if provider is not None:
            provider = provider.strip()
            if not provider:
                return http_error(400, "provider is required")
            if find_by_name(provider) is None:
                return http_error(400, "unknown provider")
            provider_config = getattr(config.providers, provider, None)
            if provider_config is None or not provider_config.api_key:
                return http_error(400, "provider is not configured")
            if defaults.provider != provider:
                defaults.provider = provider
                changed = True

        if changed:
            save_config(config)
        return http_json_response(self._settings_payload(requires_restart=False))

    def _handle_settings_provider_update(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        from OriginAgent.config.loader import load_config, save_config
        from OriginAgent.providers.registry import find_by_name

        query = _parse_query(request.path)
        provider_name = (_query_first(query, "provider") or "").strip()
        if not provider_name:
            return http_error(400, "provider is required")
        spec = find_by_name(provider_name)
        if spec is None or spec.is_oauth or spec.is_local:
            return http_error(400, "unknown provider")

        config = self._load_config()
        provider_config = getattr(config.providers, spec.name, None)
        if provider_config is None:
            return http_error(400, "unknown provider")

        changed = False
        if "api_key" in query or "apiKey" in query:
            api_key = _query_first(query, "api_key")
            if api_key is None:
                api_key = _query_first(query, "apiKey")
            api_key = (api_key or "").strip() or None
            if provider_config.api_key != api_key:
                provider_config.api_key = api_key
                changed = True

        if "api_base" in query or "apiBase" in query:
            api_base = _query_first(query, "api_base")
            if api_base is None:
                api_base = _query_first(query, "apiBase")
            api_base = (api_base or "").strip() or None
            if provider_config.api_base != api_base:
                provider_config.api_base = api_base
                changed = True

        if changed:
            save_config(config)
        return http_json_response(self._settings_payload(requires_restart=False))

    async def _handle_settings_provider_models(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        from OriginAgent.config.loader import load_config
        from OriginAgent.providers.model_fetch_contract import (
            ProviderModelFetchError,
            ProviderModelFetchHttpError,
            build_provider_model_fetch_request,
            fetch_provider_models,
        )

        query = _parse_query(request.path)
        provider_name = (_query_first(query, "provider") or "").strip()
        api_key = _query_first(query, "api_key")
        if api_key is None:
            api_key = _query_first(query, "apiKey")
        api_base = _query_first(query, "api_base")
        if api_base is None:
            api_base = _query_first(query, "apiBase")
        force_refresh = _query_bool(query, "force_refresh")
        if force_refresh is None:
            force_refresh = _query_bool(query, "forceRefresh") or False
        if provider_name:
            provider_config = getattr(self._load_config().providers, provider_name, None)
            if provider_config is not None:
                api_key = api_key or provider_config.api_key
                api_base = api_base or provider_config.api_base

        try:
            request_contract = build_provider_model_fetch_request(
                provider_name,
                api_key=api_key,
                api_base=api_base,
                force_refresh=force_refresh,
            )
        except ProviderModelFetchError as exc:
            return http_json_response(exc.to_json(), status=exc.status)

        try:
            response = await fetch_provider_models(request_contract)
        except ProviderModelFetchHttpError as exc:
            status = 500
            if exc.status is not None:
                status = exc.status
            return http_json_response(exc.to_json(), status=status)

        payload = response.to_json()
        payload["phase"] = "fetch"
        return http_json_response(payload)

    def _handle_settings_web_search_update(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        from OriginAgent.config.loader import load_config, save_config

        query = _parse_query(request.path)
        provider_name = (_query_first(query, "provider") or "").strip().lower()
        provider_option = _WEB_SEARCH_PROVIDER_BY_NAME.get(provider_name)
        if provider_option is None:
            return http_error(400, "unknown web search provider")

        config = self._load_config()
        search_config = config.tools.web.search
        previous_provider = search_config.provider
        changed = False

        def set_value(attr: str, value: str | None) -> None:
            nonlocal changed
            if getattr(search_config, attr) != value:
                setattr(search_config, attr, value)
                changed = True

        if search_config.provider != provider_name:
            search_config.provider = provider_name
            changed = True

        credential = provider_option["credential"]
        if credential == "none":
            set_value("api_key", "")
            set_value("base_url", "")
        elif credential == "base_url":
            base_url = _query_first(query, "base_url")
            if base_url is None:
                base_url = _query_first(query, "baseUrl")
            base_url = base_url.strip() if base_url is not None else None
            if not base_url and previous_provider == provider_name and search_config.base_url:
                base_url = search_config.base_url
            if not base_url:
                return http_error(400, "base_url is required")
            set_value("base_url", base_url)
            set_value("api_key", "")
        else:
            api_key = _query_first(query, "api_key")
            if api_key is None:
                api_key = _query_first(query, "apiKey")
            api_key = api_key.strip() if api_key is not None else None
            if not api_key and previous_provider == provider_name and search_config.api_key:
                api_key = search_config.api_key
            if not api_key:
                return http_error(400, "api_key is required")
            set_value("api_key", api_key)
            set_value("base_url", "")

        if changed:
            save_config(config)
        return http_json_response(self._settings_payload(requires_restart=False))

    def _handle_settings_learning_background_review_update(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        from OriginAgent.config.loader import load_config, save_config

        query = _parse_query(request.path)
        enabled = _query_bool(query, "enabled")
        if enabled is None:
            return http_error(400, "enabled must be true or false")

        config = self._load_config()
        target = config.agents.defaults.learning.background_review
        if target.enabled != enabled:
            target.enabled = enabled
            save_config(config)
        return http_json_response(self._settings_payload(requires_restart=False))

    def _handle_settings_runtime_update(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        from OriginAgent.config.loader import load_config, save_config

        query = _parse_query(request.path)
        raw = _query_first(query, "config")
        if not raw:
            return http_error(400, "config is required")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return http_error(400, "config must be valid JSON")
        if not isinstance(data, dict):
            return http_error(400, "config must be an object")

        config = self._load_config()
        defaults = config.agents.defaults
        evolution = defaults.learning.evolution
        changed = False

        def set_bool(target: Any, attr: str, value: Any) -> None:
            nonlocal changed
            if not isinstance(value, bool):
                raise ValueError(f"{attr} must be true or false")
            if getattr(target, attr) != value:
                setattr(target, attr, value)
                changed = True

        def set_choice(target: Any, attr: str, value: Any, allowed: set[str]) -> None:
            nonlocal changed
            if not isinstance(value, str):
                raise ValueError(f"{attr} must be a string")
            candidate = value.strip()
            if candidate not in allowed:
                allowed_text = ", ".join(sorted(allowed))
                raise ValueError(f"{attr} must be one of: {allowed_text}")
            if getattr(target, attr) != candidate:
                setattr(target, attr, candidate)
                changed = True

        try:
            channels = data.get("channels")
            if isinstance(channels, dict):
                if "send_progress" in channels:
                    set_bool(config.channels, "send_progress", channels["send_progress"])
                if "send_tool_hints" in channels:
                    set_bool(config.channels, "send_tool_hints", channels["send_tool_hints"])
                if "show_reasoning" in channels:
                    set_bool(config.channels, "show_reasoning", channels["show_reasoning"])

            agent = data.get("agent")
            if isinstance(agent, dict):
                if "unified_session" in agent:
                    set_bool(defaults, "unified_session", agent["unified_session"])
                if "cold_archive_enabled" in agent:
                    set_bool(defaults, "cold_archive_enabled", agent["cold_archive_enabled"])
                if "allow_agent_initiated_messages" in agent:
                    set_bool(defaults, "allow_agent_initiated_messages", agent["allow_agent_initiated_messages"])
                if "enable_backend_cognition" in agent:
                    set_bool(defaults, "enable_backend_cognition", agent["enable_backend_cognition"])
                if "auxiliary_enabled" in agent:
                    set_bool(defaults.auxiliary, "enabled", agent["auxiliary_enabled"])
                if "domain_packs_enabled" in agent:
                    set_bool(defaults.domain_packs, "enabled", agent["domain_packs_enabled"])
                if "provider_retry_mode" in agent:
                    set_choice(defaults, "provider_retry_mode", agent["provider_retry_mode"], _PROVIDER_RETRY_MODE_OPTIONS)
                if "dream_annotate_line_ages" in agent:
                    set_bool(defaults.dream, "annotate_line_ages", agent["dream_annotate_line_ages"])

            learning = data.get("learning")
            if isinstance(learning, dict):
                if "background_review_enabled" in learning:
                    set_bool(defaults.learning.background_review, "enabled", learning["background_review_enabled"])
                if "curator_enabled" in learning:
                    set_bool(defaults.learning.curator, "enabled", learning["curator_enabled"])

            evolution_cfg = data.get("evolution")
            if isinstance(evolution_cfg, dict):
                if "mode" in evolution_cfg:
                    set_choice(evolution, "mode", evolution_cfg["mode"], _EVOLUTION_MODE_OPTIONS)
                if "allow_manual_override" in evolution_cfg:
                    set_bool(evolution, "allow_manual_override", evolution_cfg["allow_manual_override"])
                if "dry_run" in evolution_cfg:
                    set_bool(evolution, "dry_run", evolution_cfg["dry_run"])
                if "outcome_archive_enabled" in evolution_cfg:
                    set_bool(evolution, "outcome_archive_enabled", evolution_cfg["outcome_archive_enabled"])
                if "dependency_stale_cleanup_enabled" in evolution_cfg:
                    set_bool(evolution, "dependency_stale_cleanup_enabled", evolution_cfg["dependency_stale_cleanup_enabled"])
                if "auto_verify_workflows" in evolution_cfg:
                    set_bool(evolution, "auto_verify_workflows", evolution_cfg["auto_verify_workflows"])
                if "skill_candidates_enabled" in evolution_cfg:
                    set_bool(evolution, "skill_candidates_enabled", evolution_cfg["skill_candidates_enabled"])
                if "feedback_calibration_enabled" in evolution_cfg:
                    set_bool(evolution, "feedback_calibration_enabled", evolution_cfg["feedback_calibration_enabled"])
                if "sandbox_enabled" in evolution_cfg:
                    set_bool(evolution.sandbox, "enabled", evolution_cfg["sandbox_enabled"])
                if "trial_enabled" in evolution_cfg:
                    set_bool(evolution.trial, "enabled", evolution_cfg["trial_enabled"])
                if "trial_isolated_workspace" in evolution_cfg:
                    set_bool(evolution.trial, "isolated_workspace", evolution_cfg["trial_isolated_workspace"])
                if "trial_read_only_tools_only" in evolution_cfg:
                    set_bool(evolution.trial, "read_only_tools_only", evolution_cfg["trial_read_only_tools_only"])

            gateway = data.get("gateway")
            if isinstance(gateway, dict) and "heartbeat_enabled" in gateway:
                set_bool(config.gateway.heartbeat, "enabled", gateway["heartbeat_enabled"])

            tiered_router_data = data.get("tiered_router")
            if isinstance(tiered_router_data, dict):
                tr = config.gateway.tiered_router
                if "enabled" in tiered_router_data:
                    set_bool(tr, "enabled", tiered_router_data["enabled"])
                if "default_tier" in tiered_router_data:
                    val = str(tiered_router_data["default_tier"]).strip()
                    if val:
                        if getattr(tr, "default_tier") != val:
                            setattr(tr, "default_tier", val)
                            changed = True

            security = data.get("security")
            if isinstance(security, dict):
                if "pairing_enabled" in security:
                    set_bool(config.security.pairing, "enabled", security["pairing_enabled"])
                if "pairing_allow_self_approve" in security:
                    set_bool(config.security.pairing, "allow_self_approve", security["pairing_allow_self_approve"])

            search = data.get("search")
            if isinstance(search, dict):
                if "web_enabled" in search:
                    set_bool(config.tools.web, "enable", search["web_enabled"])
                if "web_fetch_use_jina_reader" in search:
                    set_bool(config.tools.web.fetch, "use_jina_reader", search["web_fetch_use_jina_reader"])
                if "session_search_enabled" in search:
                    set_bool(config.tools.session_search, "enabled", search["session_search_enabled"])
                if "session_search_backend" in search:
                    set_choice(config.tools.session_search, "backend", search["session_search_backend"], _SESSION_SEARCH_BACKEND_OPTIONS)
                if "session_search_semantic_enabled" in search:
                    set_bool(config.tools.session_search, "semantic_enabled", search["session_search_semantic_enabled"])
                if "session_search_rebuild_on_start" in search:
                    set_bool(config.tools.session_search, "rebuild_on_start", search["session_search_rebuild_on_start"])
                if "content_read_enabled" in search:
                    set_bool(config.tools.content_read, "enabled", search["content_read_enabled"])
                if "content_read_use_jina_reader" in search:
                    set_bool(config.tools.content_read, "use_jina_reader", search["content_read_use_jina_reader"])

            execution = data.get("execution")
            if isinstance(execution, dict):
                if "exec_enabled" in execution:
                    set_bool(config.tools.exec, "enable", execution["exec_enabled"])
                if "exec_profile" in execution:
                    set_choice(config.tools.exec, "profile", execution["exec_profile"], _EXEC_PROFILE_OPTIONS)
                if "exec_allow_unsafe_exec" in execution:
                    set_bool(config.tools.exec, "allow_unsafe_exec", execution["exec_allow_unsafe_exec"])
                if "exec_shell_syntax_policy" in execution:
                    set_choice(config.tools.exec, "shell_syntax_policy", execution["exec_shell_syntax_policy"], _EXEC_SHELL_SYNTAX_POLICY_OPTIONS)
                if "my_enabled" in execution:
                    set_bool(config.tools.my, "enable", execution["my_enabled"])
                if "my_allow_set" in execution:
                    set_bool(config.tools.my, "allow_set", execution["my_allow_set"])
                if "restrict_to_workspace" in execution:
                    set_bool(config.tools, "restrict_to_workspace", execution["restrict_to_workspace"])

            media = data.get("media")
            if isinstance(media, dict) and "image_generation_enabled" in media:
                set_bool(config.tools.image_generation, "enabled", media["image_generation_enabled"])

            devices = data.get("devices")
            if isinstance(devices, dict):
                if "device_enabled" in devices:
                    set_bool(config.tools.device, "enabled", devices["device_enabled"])
                if "device_lighting_enabled" in devices:
                    set_bool(config.tools.device, "lighting_enabled", devices["device_lighting_enabled"])
                if "device_mode" in devices:
                    set_choice(config.tools.device, "mode", devices["device_mode"], _DEVICE_MODE_OPTIONS)
                if "device_backend" in devices:
                    set_choice(config.tools.device, "backend", devices["device_backend"], _DEVICE_BACKEND_OPTIONS)

            subagent = data.get("subagent")
            if isinstance(subagent, dict) and "mode" in subagent:
                set_choice(defaults.subagent_policy, "mode", subagent["mode"], {"normal", "restricted"})

            audit = data.get("audit")
            if isinstance(audit, dict):
                if "audit_mode" in audit:
                    set_choice(config.tools.audit, "mode", audit["audit_mode"], _AUDIT_MODE_OPTIONS)
                if "audit_security_on_policy_denial" in audit:
                    set_bool(config.tools.audit, "security_on_policy_denial", audit["audit_security_on_policy_denial"])

            runtime = data.get("runtime")
            if isinstance(runtime, dict) and "profile" in runtime:
                set_choice(config.runtime, "profile", runtime["profile"], _RUNTIME_PROFILE_OPTIONS)
        except ValueError as exc:
            return http_error(400, str(exc))

        if changed:
            save_config(config)
        return http_json_response(self._settings_payload(requires_restart=True))

    # ── Local-awareness audio settings ─────────────────────────────────────

    def _handle_settings_local_awareness_audio_update(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        from OriginAgent.config.loader import load_config, save_config

        query = _parse_query(request.path)
        raw = _query_first(query, "config")
        if not raw:
            return http_error(400, "config is required")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return http_error(400, "config must be valid JSON")
        if not isinstance(data, dict):
            return http_error(400, "config must be an object")

        config = self._load_config()
        audio = config.tools.local_awareness.audio
        changed = False

        def set_bool(target: Any, attr: str, value: Any) -> None:
            nonlocal changed
            if not isinstance(value, bool):
                raise ValueError(f"{attr} must be true or false")
            if getattr(target, attr) != value:
                setattr(target, attr, value)
                changed = True

        def set_int(target: Any, attr: str, value: Any, *, minimum: int, maximum: int) -> None:
            nonlocal changed
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{attr} must be an integer")
            if value < minimum or value > maximum:
                raise ValueError(f"{attr} must be between {minimum} and {maximum}")
            if getattr(target, attr) != value:
                setattr(target, attr, value)
                changed = True

        def set_optional_string(target: Any, attr: str, value: Any) -> None:
            nonlocal changed
            if value is None:
                normalized = None
            elif isinstance(value, str):
                normalized = value.strip() or None
            else:
                raise ValueError(f"{attr} must be a string or null")
            if getattr(target, attr) != normalized:
                setattr(target, attr, normalized)
                changed = True

        def set_required_string(target: Any, attr: str, value: Any) -> None:
            nonlocal changed
            if not isinstance(value, str):
                raise ValueError(f"{attr} must be a string")
            normalized = value.strip()
            if not normalized:
                raise ValueError(f"{attr} is required")
            if getattr(target, attr) != normalized:
                setattr(target, attr, normalized)
                changed = True

        def set_transcription_provider(value: Any) -> None:
            nonlocal changed
            if not isinstance(value, str):
                raise ValueError("transcription_provider must be a string")
            normalized = value.strip().lower()
            if normalized not in _TRANSCRIPTION_PROVIDER_OPTIONS:
                allowed = ", ".join(sorted(_TRANSCRIPTION_PROVIDER_OPTIONS))
                raise ValueError(f"transcription_provider must be one of: {allowed}")
            if audio.transcription_provider != normalized:
                audio.transcription_provider = normalized
                changed = True

        def set_transcription_language(value: Any) -> None:
            nonlocal changed
            if value is None:
                normalized = None
            elif isinstance(value, str):
                normalized = value.strip().lower() or None
                if normalized is not None and not re.fullmatch(r"[a-z]{2,3}", normalized):
                    raise ValueError("transcription_language must be a 2-3 letter lowercase ISO code")
            else:
                raise ValueError("transcription_language must be a string or null")
            if config.channels.transcription_language != normalized:
                config.channels.transcription_language = normalized
                changed = True

        try:
            if "input_enabled" in data:
                set_bool(audio, "input_enabled", data["input_enabled"])
            if "output_enabled" in data:
                set_bool(audio, "output_enabled", data["output_enabled"])
            if "transcription_enabled" in data:
                set_bool(audio, "transcription_enabled", data["transcription_enabled"])
            if "tts_enabled" in data:
                set_bool(audio, "tts_enabled", data["tts_enabled"])
            if "require_confirmation" in data:
                set_bool(audio, "require_confirmation", data["require_confirmation"])
            if "max_record_seconds" in data:
                set_int(audio, "max_record_seconds", data["max_record_seconds"], minimum=1, maximum=60)
            if "device_id" in data:
                set_optional_string(audio, "device_id", data["device_id"])
            if "voice" in data:
                set_optional_string(audio, "voice", data["voice"])
            if "save_dir" in data:
                set_required_string(audio, "save_dir", data["save_dir"])
            if "transcription_provider" in data:
                set_transcription_provider(data["transcription_provider"])
            if "transcription_language" in data:
                set_transcription_language(data["transcription_language"])
        except ValueError as exc:
            return http_error(400, str(exc))

        if changed:
            save_config(config)
        return http_json_response(self._settings_payload(requires_restart=True))

    # ── MCP server settings ────────────────────────────────────────────────

    def _handle_settings_mcp_upsert(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        from pydantic import ValidationError

        from OriginAgent.config.loader import load_config, save_config
        from OriginAgent.config.schema import MCPServerConfig

        query = _parse_query(request.path)
        raw = _query_first(query, "config")
        if not raw:
            return http_error(400, "config is required")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return http_error(400, "config must be valid JSON")
        if not isinstance(data, dict):
            return http_error(400, "config must be an object")

        name = str(data.pop("name", "")).strip()
        if _MCP_SERVER_NAME_RE.fullmatch(name) is None:
            return http_error(400, "invalid MCP server name")

        config = self._load_config()
        existing = config.tools.mcp_servers.get(name)
        if existing is not None:
            data = _merge_mcp_secret_fields(data, existing)

        try:
            server = MCPServerConfig.model_validate(data)
        except ValidationError as exc:
            return http_error(400, f"invalid MCP server config: {exc.errors()[0]['msg']}")

        transport_type = server.type
        if not transport_type:
            transport_type = "stdio" if server.command else "streamableHttp" if server.url else None
        if transport_type == "stdio" and not server.command.strip():
            return http_error(400, "command is required")
        if transport_type in {"sse", "streamableHttp"} and not server.url.strip():
            return http_error(400, "url is required")
        if transport_type is None:
            return http_error(400, "command or url is required")

        config.tools.mcp_servers[name] = server
        save_config(config)
        return http_json_response(self._settings_payload(requires_restart=True))

    def _handle_settings_mcp_home_assistant_upsert(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        from OriginAgent.config.loader import load_config, save_config
        from OriginAgent.config.schema import MCPServerConfig

        query = _parse_query(request.path)
        name = (_query_first(query, "name") or "home_assistant").strip()
        if _MCP_SERVER_NAME_RE.fullmatch(name) is None:
            return http_error(400, "invalid MCP server name")

        address = (_query_first(query, "address") or "").strip()
        url = _home_assistant_mcp_url(address)
        if url is None:
            return http_error(400, "Home Assistant URL must start with http:// or https://")
        parsed = urlparse(url)
        if parsed.hostname is None:
            return http_error(400, "Home Assistant URL is missing a hostname")

        token = (_query_first(query, "token") or "").strip()
        config = self._load_config()
        existing = config.tools.mcp_servers.get(name)
        existing_auth = (existing.headers.get("Authorization", "") if existing else "").strip()
        if token:
            authorization = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        elif existing_auth:
            authorization = existing_auth
        else:
            return http_error(400, "Home Assistant token is required")

        config.tools.mcp_servers[name] = MCPServerConfig(
            type="streamableHttp",
            url=url,
            headers={"Authorization": authorization},
            tool_timeout=30,
            enabled_tools=["*"],
        )

        for cidr in _host_exact_cidrs(parsed.hostname):
            if cidr not in config.tools.ssrf_whitelist:
                config.tools.ssrf_whitelist.append(cidr)

        save_config(config)
        return http_json_response(self._settings_payload(requires_restart=True))

    def _handle_settings_mcp_delete(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        from OriginAgent.config.loader import load_config, save_config

        query = _parse_query(request.path)
        name = (_query_first(query, "name") or "").strip()
        if _MCP_SERVER_NAME_RE.fullmatch(name) is None:
            return http_error(400, "invalid MCP server name")

        config = self._load_config()
        deleted = name in config.tools.mcp_servers
        if deleted:
            config.tools.mcp_servers.pop(name, None)
            save_config(config)
        payload = self._settings_payload(requires_restart=deleted)
        payload["deleted"] = deleted
        return http_json_response(payload)

    # ── Session read / write / delete ──────────────────────────────────────

    @staticmethod
    def _is_webui_session_key(key: str) -> bool:
        return key.startswith("websocket:")

    def _handle_session_messages(self, request: WsRequest, key: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        if self._ch._session_manager is None:
            return http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return http_error(400, "invalid session key")
        if not self._is_webui_session_key(decoded_key):
            return http_error(404, "session not found")
        data = self._ch._session_manager.read_session_file(decoded_key)
        if data is None:
            return http_error(404, "session not found")
        messages = data.get("messages")
        if isinstance(messages, list):
            from OriginAgent.utils.subagent_channel_display import scrub_subagent_messages_for_channel

            scrub_subagent_messages_for_channel(messages)
        self._ch._augment_media_urls(data)
        return http_json_response(data)

    def _handle_webui_thread_get(self, request: WsRequest, key: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return http_error(400, "invalid session key")
        if not self._is_webui_session_key(decoded_key):
            return http_error(404, "session not found")
        from OriginAgent.utils.webui_transcript import build_webui_thread_response

        data = build_webui_thread_response(
            decoded_key,
            augment_user_media=self._ch._augment_transcript_user_media,
        )
        if data is None:
            data = self._build_webui_thread_from_session(decoded_key)
        elif isinstance(data.get("messages"), list):
            from OriginAgent.utils.subagent_channel_display import scrub_subagent_messages_for_channel

            scrub_subagent_messages_for_channel(data["messages"])
        if data is None:
            return http_error(404, "webui thread not found")
        return http_json_response(data)

    def _build_webui_thread_from_session(self, key: str) -> dict[str, Any] | None:
        data = self._ch._session_manager.read_session_file(key) if self._ch._session_manager else None
        if data is None:
            return None
        messages = data.get("messages")
        if isinstance(messages, list):
            from OriginAgent.utils.subagent_channel_display import scrub_subagent_messages_for_channel

            scrub_subagent_messages_for_channel(messages)
        self._ch._augment_media_urls(data)
        if not isinstance(messages, list):
            return None
        ui_messages: list[dict[str, Any]] = []
        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                continue
            if msg.get("_command"):
                continue
            role = msg.get("role")
            if role not in {"user", "assistant", "tool"}:
                continue
            content = msg.get("content")
            if not isinstance(content, str):
                content = "" if content is None else str(content)
            row: dict[str, Any] = {
                "id": f"legacy-{idx}",
                "role": role,
                "content": content,
                "createdAt": _timestamp_ms(msg.get("timestamp")),
            }
            media = msg.get("media_urls")
            if isinstance(media, list) and media:
                row["media"] = [
                    {
                        "kind": _ui_media_kind_for_path(str(m.get("name") or "")),
                        "url": str(m["url"]),
                        "name": str(m.get("name") or ""),
                    }
                    for m in media
                    if isinstance(m, dict) and m.get("url")
                ]
                if row["media"] and all(item.get("kind") == "image" for item in row["media"]):
                    row["images"] = [
                        {"url": item.get("url"), "name": item.get("name")}
                        for item in row["media"]
                    ]
            if row["content"].strip() or row.get("media"):
                ui_messages.append(row)
        if not ui_messages:
            return None
        return {"schemaVersion": 3, "sessionKey": key, "messages": ui_messages}

    def _handle_session_delete(self, request: WsRequest, key: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        if self._ch._session_manager is None:
            return http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return http_error(400, "invalid session key")
        if not self._is_webui_session_key(decoded_key):
            return http_error(404, "session not found")
        deleted = self._ch._session_manager.delete_session(decoded_key)
        from OriginAgent.utils.webui_thread_disk import delete_webui_thread

        deleted = delete_webui_thread(decoded_key) or deleted
        return http_json_response({"deleted": bool(deleted)})
