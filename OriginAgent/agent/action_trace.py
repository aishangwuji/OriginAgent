"""action_trace — system-side automatic capture of action→result.

This is the **data layer** of the action-result causal chain, the concrete
embodiment of the perception/elaboration phase in BDI / ACT-R / Soar / EPIC:

- **BDI**: every action produces a percept that updates Belief — this module
  is that percept capture.
- **ACT-R**: each production firing creates a declarative chunk — this module
  records that chunk.
- **Soar**: after operator application, the elaboration phase adds results
  to working memory — this module performs that augmentation.
- **EPIC**: the perceptual processor captures motor-action feedback — this
  module is that capture path.

The **evaluation layer** (``EvaluateActionTool``) and the **state-machine
layer** (``TaskStateTool``) build on top of this data to form the complete
causal chain:

    action → result → evaluation → state transition

Design decisions (rules 5 / 7 / 18 / 22 / 32 / 33):
- State is persisted to ``session.metadata["_action_trace"]`` — same
  session-scoped storage as ``_task_state``, keeping the causal chain
  co-located with the session it belongs to.
- ``record_action_trace`` re-reads ``session.metadata`` on every call
  (rule 5: no stale snapshots; rule 7: shared state is re-read to avoid
  zombie references if the session object is rebuilt mid-turn).
- Parameters are summarized with sensitive-field redaction (rule 18:
  api_key / token / password / secret never enter the trace in plaintext).
- FIFO cap bounds growth (rule 22: resource governance) — 50 entries is
  enough to see ~10 recent task cycles without bloating session.metadata.
- No speculative fields: every field has a known consumer (context
  injection, evaluate_action, task_state transition referencing).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# ─── Constants ──────────────────────────────────────────────────────────────

_METADATA_KEY = "_action_trace"

# FIFO cap. 50 entries ≈ 10 recent task cycles (each cycle ~5 actions).
# Large enough for context injection + evaluation, small enough to avoid
# bloating session.metadata across long-running sessions.
_MAX_TRACE = 50

# Sensitive parameter key fragments — values for keys containing these
# substrings are redacted to ``<redacted>`` before entering the trace.
# This is rule 18's data-layer enforcement: secrets never enter the
# causal chain in plaintext, even though the trace is session-scoped.
_SENSITIVE_KEY_FRAGMENTS: frozenset[str] = frozenset({
    "key", "token", "password", "secret", "credential", "apikey",
})

# Maximum length of summarized strings (params + results). 200 chars is
# enough to identify the action/result without dumping full content.
_SUMMARY_MAX_CHARS = 200

# Maximum length of an individual param value before truncation.
_PARAM_VALUE_MAX_CHARS = 50

# Maximum number of param keys included in the summary.
_MAX_PARAM_KEYS = 3

# Keywords that indicate a tool result was a policy denial.
_DENIED_KEYWORDS: frozenset[str] = frozenset({
    "denied", "policy", "not allowed", "permission",
})


# ─── Helpers ────────────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    """ISO 8601 UTC timestamp for trace entries."""
    return datetime.now(timezone.utc).isoformat()


def _is_sensitive_key(key: str) -> bool:
    """Check if a parameter key name matches a sensitive pattern."""
    key_lower = str(key).lower()
    return any(frag in key_lower for frag in _SENSITIVE_KEY_FRAGMENTS)


def _summarize_params(params: Any) -> str:
    """Summarize tool call parameters into a compact, safe string.

    Extracts up to ``_MAX_PARAM_KEYS`` key=value pairs. Sensitive keys
    (containing 'key', 'token', 'password', etc.) are redacted to
    ``<redacted>`` (rule 18). Long values are truncated. The final
    string is capped at ``_SUMMARY_MAX_CHARS``.

    Non-sensitive keys are prioritized — this ensures useful parameter
    info (path, command, query) is retained even when the first few
    params happen to be sensitive (e.g., api_key, token). Sensitive
    keys are still included (redacted) so the Agent knows it passed them.
    """
    if not isinstance(params, dict):
        return str(params)[:_SUMMARY_MAX_CHARS]

    # Partition: non-sensitive first (useful info), sensitive last (redacted).
    # This ordering ensures path/command/query survive the _MAX_PARAM_KEYS
    # cap even when preceded by api_key/token/password.
    non_sensitive = [(k, v) for k, v in params.items() if not _is_sensitive_key(str(k))]
    sensitive = [(k, v) for k, v in params.items() if _is_sensitive_key(str(k))]
    selected = (non_sensitive + sensitive)[:_MAX_PARAM_KEYS]

    items: list[str] = []
    for key, value in selected:
        if _is_sensitive_key(str(key)):
            items.append(f"{key}=<redacted>")
        else:
            val_str = str(value)
            if len(val_str) > _PARAM_VALUE_MAX_CHARS:
                val_str = val_str[:_PARAM_VALUE_MAX_CHARS] + "..."
            items.append(f"{key}={val_str}")

    summary = ", ".join(items)
    if len(summary) > _SUMMARY_MAX_CHARS:
        summary = summary[:_SUMMARY_MAX_CHARS] + "..."
    return summary


def _summarize_result(result: Any) -> str:
    """Summarize a tool result into a compact string.

    Handles:
    - ``str``: passed through with truncation.
    - ``list`` (content blocks): extracts ``text`` fields, joined by space.
    - ``dict``: extracts ``text`` or ``content`` field if present.
    - ``None``: returns ``<none>``.
    - Other types: stringified.

    Always capped at ``_SUMMARY_MAX_CHARS``.
    """
    if result is None:
        return "<none>"

    if isinstance(result, str):
        text = result
    elif isinstance(result, list):
        parts: list[str] = []
        for block in result:
            if isinstance(block, dict):
                block_text = block.get("text") or block.get("content") or ""
                if block_text:
                    parts.append(str(block_text))
            elif isinstance(block, str):
                parts.append(block)
        text = " ".join(parts) if parts else str(result)
    elif isinstance(result, dict):
        text = result.get("text") or result.get("content") or str(result)
    else:
        text = str(result)

    text = str(text)
    if len(text) > _SUMMARY_MAX_CHARS:
        text = text[:_SUMMARY_MAX_CHARS] + "..."
    return text


def _detect_denied(result: Any, event: dict[str, str]) -> bool:
    """Detect whether a tool call was denied by policy.

    Checks two signals:
    1. ``event["status"] == "denied"`` — explicit denial status from runner.
    2. Result text contains denial keywords (denied, policy, not allowed,
       permission).

    Either signal triggers ``True``. This dual-path detection is robust
    against tools that return denial messages with ``status="ok"`` (e.g.,
    capability-gated tools that return an error string rather than raising).
    """
    if event.get("status") == "denied":
        return True
    result_str = str(result).lower()
    return any(kw in result_str for kw in _DENIED_KEYWORDS)


# ─── Public API ─────────────────────────────────────────────────────────────


def record_action_trace(
    spec: Any,
    tool_call: Any,
    result: Any,
    event: dict[str, str],
    error: BaseException | None,
    iteration: int,
) -> str | None:
    """Record an action_trace entry to ``session.metadata``.

    Called by ``runner._execute_tools`` after each tool batch completes.
    Writes a structured entry capturing the action (tool name + params)
    and its result (summary + success/denied status), then enforces the
    FIFO cap.

    Args:
        spec: ``AgentRunSpec`` with ``sessions`` and ``session_key``.
        tool_call: ``ToolCallRequest`` with ``id``, ``name``, ``arguments``.
        result: The tool's return value (str / list / dict / None).
        event: The runner's event dict with ``status`` field.
        error: Exception raised by the tool, or ``None``.
        iteration: The runner loop iteration number.

    Returns:
        The ``action_id`` (tool_call.id, or a generated fallback), or
        ``None`` if sessions/session_key are unavailable (e.g., dream or
        subagent paths that don't need session-level persistence).
    """
    sessions = getattr(spec, "sessions", None)
    session_key = getattr(spec, "session_key", None)
    if sessions is None or not session_key:
        return None

    session = sessions.get_or_create(session_key)

    # action_id: prefer tool_call.id (LLM-generated, unique per call).
    # Fall back to a generated id if tool_call.id is missing/empty.
    raw_id = getattr(tool_call, "id", None)
    if raw_id:
        action_id = str(raw_id)
    else:
        # Fallback: timestamp-based, monotonic within a turn.
        action_id = f"act_{_utcnow_iso().replace(':', '').replace('-', '').replace('+', '')[:16]}"

    # Extract params (tool_call.arguments is the canonical field).
    params = getattr(tool_call, "arguments", None) or {}

    entry: dict[str, Any] = {
        "action_id": action_id,
        "tool_name": str(getattr(tool_call, "name", "<unknown>")),
        "params_summary": _summarize_params(params),
        "result_summary": _summarize_result(result),
        "success": error is None and event.get("status") == "ok",
        "denied": _detect_denied(result, event),
        "ts": _utcnow_iso(),
        "iteration": iteration,
    }

    # Re-read trace from session.metadata (rule 5/7: no stale cache).
    trace = session.metadata.get(_METADATA_KEY)
    if trace is None:
        trace = []
        session.metadata[_METADATA_KEY] = trace
    trace.append(entry)

    # FIFO cap: drop oldest entries beyond _MAX_TRACE.
    if len(trace) > _MAX_TRACE:
        del trace[: len(trace) - _MAX_TRACE]

    return action_id


def get_action_trace(session: Any) -> list[dict[str, Any]]:
    """Read the full action_trace from a session.

    Returns a **copy** — callers may mutate the returned list without
    affecting session.metadata. Re-reads on every call (rule 5).
    """
    trace = session.metadata.get(_METADATA_KEY, [])
    return list(trace)


def get_recent_actions(session: Any, limit: int = 5) -> list[dict[str, Any]]:
    """Return the most recent N action_trace entries.

    Args:
        session: The session to read from.
        limit: Maximum number of entries to return. Defaults to 5.

    Returns:
        A list of the most recent entries (oldest-first within the slice).
        Returns an empty list if the trace is empty or absent.
    """
    trace = get_action_trace(session)
    if limit <= 0:
        return []
    return trace[-limit:]
