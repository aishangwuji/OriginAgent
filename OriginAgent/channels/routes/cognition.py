"""Meta-cognition and evolution HTTP route handlers (extracted from websocket.py)."""

from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import unquote

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

# -- helpers imported from the parent channel --------------------------------


def _parse_query(path: str) -> dict[str, list[str]]:
    """Parse query string from a request path."""
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(path).query)


def _query_first(query: dict[str, list[str]], key: str) -> str | None:
    """Return the first value for a query key, or None."""
    values = query.get(key)
    return values[0] if values else None


def _http_error(status: int, message: str) -> Response:
    """Return a plain-text HTTP error response."""
    return Response(status, "OK", {}, message.encode("utf-8"))


def _http_json_response(data: Any) -> Response:
    """Return a JSON HTTP response."""
    import json

    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return Response(200, "OK", {"Content-Type": "application/json; charset=utf-8"}, body)


# -- disabled payload (shared between handlers) ------------------------------


_DISABLED_PAYLOAD = {
    "contract_version": "meta_cognition.v1.freeze",
    "enabled": False,
    "trigger_collection_enabled": False,
    "structured_reflection_enabled": False,
    "pattern_consolidation_enabled": False,
    "evolution_bridge_enabled": False,
    "runtime_status": {"accepted_total": 0, "suppressed_total": 0},
    "recent_triggers": [],
    "recent_decisions": [],
    "recent_journals": [],
    "recent_reflections": [],
    "recent_confidence_traces": [],
    "recent_patterns": [],
    "recent_evolution_seeds": [],
    "decision_counts": {},
    "suppression_reason_counts": {},
    "uncertainty_stats": {"avg": 0.0, "max": 0.0, "high_count": 0, "threshold": 0.5},
    "artifact_status": {},
    "working_memory_bridge": {"enabled": False, "last_status": "disabled", "decision_counts": {}},
    "memory_candidate_bridge": {"enabled": False, "last_status": "disabled", "decision_counts": {}},
    "bridge_decision_counts": {},
    "pattern_counts": {},
    "seed_counts": {},
    "last_signal_upserts": [],
    "fast_path_decision_counts": {},
}


# -- route handlers ----------------------------------------------------------


def handle_meta_cognition_status(
    request: WsRequest,
    *,
    check_token: Callable[[WsRequest], bool],
    get_introspection: Callable[[], dict[str, Any] | None] | None,
) -> Response:
    """GET /api/cognition/status — return meta-cognition summary."""
    if not check_token(request):
        return _http_error(401, "Unauthorized")
    if get_introspection is not None:
        try:
            summary = get_introspection()
            if summary is not None:
                return _http_json_response(summary)
        except Exception as exc:
            return _http_error(500, f"introspection error: {exc}")
    return _http_json_response(_DISABLED_PAYLOAD)


def handle_evolution_status(
    request: WsRequest,
    *,
    check_token: Callable[[WsRequest], bool],
    load_config: Callable[[], Any],
) -> Response:
    """GET /api/evolution/status — return evolution control plane status."""
    if not check_token(request):
        return _http_error(401, "Unauthorized")
    from OriginAgent.agent.evolution_control_plane import EvolutionControlPlane

    config = load_config()
    ctrl = EvolutionControlPlane(config.workspace_path)
    return _http_json_response(ctrl.status())


def handle_evolution_signals(
    request: WsRequest,
    *,
    check_token: Callable[[WsRequest], bool],
    load_config: Callable[[], Any],
) -> Response:
    """GET /api/evolution/signals?status=...&kind=...&limit=..."""
    if not check_token(request):
        return _http_error(401, "Unauthorized")
    from OriginAgent.agent.evolution_control_plane import EvolutionControlPlane

    config = load_config()
    ctrl = EvolutionControlPlane(config.workspace_path)
    query = _parse_query(request.path)
    status = _query_first(query, "status") or None
    kind = _query_first(query, "kind") or None
    limit_raw = _query_first(query, "limit")
    try:
        limit = int(limit_raw) if limit_raw is not None else 50
    except ValueError:
        limit = 50
    result = ctrl.list_signals(status=status, kind=kind, limit=limit)
    return _http_json_response(result)


def handle_signal_action(
    request: WsRequest,
    signal_id: str,
    action: str,
    *,
    check_token: Callable[[WsRequest], bool],
    load_config: Callable[[], Any],
) -> Response:
    """POST /api/evolution/signals/{id}/suppress|resume"""
    if not check_token(request):
        return _http_error(401, "Unauthorized")
    if action not in ("suppress", "resume"):
        return _http_error(400, "action must be suppress or resume")
    from OriginAgent.agent.evolution import OpportunitySignalStore

    config = load_config()
    store = OpportunitySignalStore(config.workspace_path)
    query = _parse_query(request.path)
    reason = _query_first(query, "reason") or "WebUI action"
    signal_id = unquote(signal_id)
    try:
        if action == "suppress":
            result = store.suppress_signal(signal_id, reason=reason)
            ok = result is not None
            signal = result.to_record() if hasattr(result, "to_record") else None
        else:
            result = store.resume_signal(signal_id)
            ok = result is not None
            signal = result.to_record() if hasattr(result, "to_record") else None
    except Exception as exc:
        return _http_error(500, str(exc))
    return _http_json_response({"ok": ok, "signal": signal})


# -- route registration ------------------------------------------------------


def register_routes(dispatch: dict[str, Callable[..., Response]], channel: Any) -> None:
    """Register meta-cognition and evolution routes on the channel's dispatch table."""
    dispatch["/api/cognition/status"] = lambda r: handle_meta_cognition_status(
        r,
        check_token=channel._check_api_token,
        get_introspection=channel._runtime_introspection,
    )
    dispatch["/api/evolution/status"] = lambda r: handle_evolution_status(
        r,
        check_token=channel._check_api_token,
        load_config=channel._load_config,
    )
    dispatch["/api/evolution/signals"] = lambda r: handle_evolution_signals(
        r,
        check_token=channel._check_api_token,
        load_config=channel._load_config,
    )
    # Regex-based route for signal actions
    _signal_pattern = re.compile(r"^/api/evolution/signals/([^/]+)/(suppress|resume)$")
    dispatch[_signal_pattern] = lambda r, m=None: handle_signal_action(
        r,
        m.group(1) if m else "",
        m.group(2) if m else "",
        check_token=channel._check_api_token,
        load_config=channel._load_config,
    )
