"""Read-only sandbox evaluation for governed evolution proposals."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from OriginAgent.agent.facts import ValidationIssue
from OriginAgent.utils.helpers import ensure_dir, truncate_text

_DEFAULT_ALLOWED_TOOLS = {"read_file", "glob", "grep"}
_MAX_MESSAGE_CHARS = 500


@dataclass(frozen=True)
class SandboxEvaluationResult:
    """Serializable result attached to an auto-evolution proposal payload."""

    status: str
    mode: str = "sandbox"
    read_only: bool = True
    isolated_workspace: bool = True
    checked_at: str = ""
    issues: list[dict[str, str]] = field(default_factory=list)
    replay_summary: dict[str, int] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    cached: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class SandboxEvaluator:
    """Evaluate proposed workflows with read-only static replay constraints."""

    def __init__(self, workspace: Path, config: Any | None = None) -> None:
        self.workspace = Path(workspace)
        self.config = config
        self.memory_dir = ensure_dir(self.workspace / "memory")
        self.cache_path = self.memory_dir / "evolution_sandbox_cache.jsonl"
        self._lock = FileLock(str(self.memory_dir / ".evolution_sandbox.lock"))

    def evaluate_workflow_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        sandbox_config = getattr(self.config, "sandbox", None)
        enabled = bool(getattr(sandbox_config, "enabled", True))
        if not enabled:
            return SandboxEvaluationResult(
                status="skipped",
                mode="sandbox",
                read_only=True,
                isolated_workspace=True,
                checked_at=_now_iso(),
                replay_summary={"steps_checked": 0, "blocked_steps": 0, "sample_count": 0},
            ).to_json()

        cache_key = self._cache_key(payload)
        cached = self._read_cached(cache_key)
        if cached is not None:
            cached["cached"] = True
            return cached

        result = self._evaluate_uncached(payload)
        self._write_cached(cache_key, result)
        return result

    def evaluate_trial_workflow_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Evaluate whether a verified workflow is safe to trial-run.

        This is a hard gate for trial mode, not an executor. It enforces the
        v1.1 rule that trial remains isolated and read-only before any future
        runtime can consider invoking an artifact.
        """

        trial_config = getattr(self.config, "trial", None)
        enabled = bool(getattr(trial_config, "enabled", True))
        if not enabled:
            return SandboxEvaluationResult(
                status="skipped",
                mode="trial",
                read_only=True,
                isolated_workspace=True,
                checked_at=_now_iso(),
                replay_summary={"steps_checked": 0, "blocked_steps": 0, "sample_count": 0},
                policy=self._trial_policy(),
            ).to_json()
        return self._evaluate_trial_uncached(payload)

    def _evaluate_uncached(self, payload: dict[str, Any]) -> dict[str, Any]:
        checked_at = _now_iso()
        issues: list[ValidationIssue] = []
        steps = payload.get("steps")
        if not isinstance(steps, list):
            issues.append(_issue("sandbox_steps_not_list", "reject", "Workflow steps must be a list."))
            steps = []

        allowed_tools = self._allowed_tools()
        blocked_steps = 0
        checked_steps = 0
        with tempfile.TemporaryDirectory(prefix="originagent_sandbox_") as tmp:
            sandbox_root = Path(tmp).resolve()
            for index, step in enumerate(steps):
                if not isinstance(step, dict):
                    issues.append(_issue(
                        "sandbox_step_not_mapping",
                        "reject",
                        f"Workflow step {index + 1} must be a mapping.",
                    ))
                    blocked_steps += 1
                    continue
                checked_steps += 1
                tool = str(step.get("tool") or "").strip()
                if tool and tool not in allowed_tools:
                    issues.append(_issue(
                        "sandbox_tool_blocked",
                        "pending",
                        f"Workflow step {index + 1} references non-read-only tool `{tool}`.",
                    ))
                    blocked_steps += 1
                    continue
                path_issue = self._check_step_paths(step, sandbox_root, index)
                if path_issue is not None:
                    issues.append(path_issue)
                    blocked_steps += 1

        status = "passed"
        if any(issue.severity == "reject" for issue in issues):
            status = "failed"
        elif blocked_steps:
            status = "blocked"
        sample_count = _sample_count(payload, self.config)
        return SandboxEvaluationResult(
            status=status,
            mode="sandbox",
            read_only=True,
            isolated_workspace=True,
            checked_at=checked_at,
            issues=[asdict(issue) for issue in issues],
            replay_summary={
                "steps_checked": checked_steps,
                "blocked_steps": blocked_steps,
                "sample_count": sample_count,
            },
            policy={
                "allowed_tools": sorted(allowed_tools),
                "isolated_workspace": True,
                "read_only": True,
            },
        ).to_json()

    def _evaluate_trial_uncached(self, payload: dict[str, Any]) -> dict[str, Any]:
        checked_at = _now_iso()
        issues: list[ValidationIssue] = []
        trial_config = getattr(self.config, "trial", None)
        isolated_workspace = bool(getattr(trial_config, "isolated_workspace", True))
        read_only_only = bool(getattr(trial_config, "read_only_tools_only", True))
        allowed_tools = self._allowed_tools()
        blocked_tools = self._blocked_trial_tools()
        steps = payload.get("steps")
        if not isinstance(steps, list):
            issues.append(_issue("trial_steps_not_list", "reject", "Workflow steps must be a list."))
            steps = []
        if not isolated_workspace:
            issues.append(_issue(
                "trial_isolation_required",
                "reject",
                "Trial execution requires an isolated temporary workspace.",
            ))

        blocked_steps = 0
        checked_steps = 0
        with self._trial_temp_directory() as tmp:
            trial_root = Path(tmp).resolve()
            for index, step in enumerate(steps):
                if not isinstance(step, dict):
                    issues.append(_issue(
                        "trial_step_not_mapping",
                        "reject",
                        f"Workflow step {index + 1} must be a mapping.",
                    ))
                    blocked_steps += 1
                    continue
                checked_steps += 1
                tool = str(step.get("tool") or "").strip()
                if tool and tool in blocked_tools:
                    issues.append(_issue(
                        "trial_tool_blocked",
                        "pending",
                        f"Workflow step {index + 1} references blocked trial tool `{tool}`.",
                    ))
                    blocked_steps += 1
                    continue
                if read_only_only and tool and tool not in allowed_tools:
                    issues.append(_issue(
                        "trial_tool_not_read_only",
                        "pending",
                        f"Workflow step {index + 1} references non-read-only trial tool `{tool}`.",
                    ))
                    blocked_steps += 1
                    continue
                path_issue = self._check_step_paths(step, trial_root, index, issue_prefix="trial")
                if path_issue is not None:
                    issues.append(path_issue)
                    blocked_steps += 1

        status = "passed"
        if any(issue.severity == "reject" for issue in issues):
            status = "failed"
        elif blocked_steps:
            status = "blocked"
        return SandboxEvaluationResult(
            status=status,
            mode="trial",
            read_only=read_only_only,
            isolated_workspace=isolated_workspace,
            checked_at=checked_at,
            issues=[asdict(issue) for issue in issues],
            replay_summary={
                "steps_checked": checked_steps,
                "blocked_steps": blocked_steps,
                "sample_count": _sample_count(payload, self.config),
            },
            policy=self._trial_policy(),
        ).to_json()

    def _check_step_paths(
        self,
        step: dict[str, Any],
        sandbox_root: Path,
        index: int,
        *,
        issue_prefix: str = "sandbox",
    ) -> ValidationIssue | None:
        for key in ("path", "file", "file_path", "target_path", "pattern"):
            raw = step.get(key)
            if raw is None:
                continue
            value = str(raw).strip()
            if not value:
                continue
            try:
                candidate = (sandbox_root / value).resolve()
                candidate.relative_to(sandbox_root)
            except (OSError, ValueError):
                return _issue(
                    f"{issue_prefix}_path_outside_root",
                    "reject",
                    f"Workflow step {index + 1} path `{truncate_text(value, 80)}` leaves sandbox root.",
                )
        return None

    def _allowed_tools(self) -> set[str]:
        sandbox_config = getattr(self.config, "sandbox", None)
        raw_tools = getattr(sandbox_config, "read_only_tools", None) or []
        tools = {str(item).strip() for item in raw_tools if str(item).strip()}
        return tools or set(_DEFAULT_ALLOWED_TOOLS)

    def _blocked_trial_tools(self) -> set[str]:
        trial_config = getattr(self.config, "trial", None)
        raw_tools = getattr(trial_config, "blocked_tools", None) or []
        return {str(item).strip() for item in raw_tools if str(item).strip()}

    def _trial_policy(self) -> dict[str, Any]:
        trial_config = getattr(self.config, "trial", None)
        return {
            "enabled": bool(getattr(trial_config, "enabled", True)),
            "isolated_workspace": bool(getattr(trial_config, "isolated_workspace", True)),
            "read_only_tools_only": bool(getattr(trial_config, "read_only_tools_only", True)),
            "allowed_tools": sorted(self._allowed_tools()),
            "blocked_tools": sorted(self._blocked_trial_tools()),
            "temp_dir_configured": bool(str(getattr(trial_config, "temp_dir", "") or "").strip()),
        }

    def _trial_temp_directory(self) -> tempfile.TemporaryDirectory[str]:
        trial_config = getattr(self.config, "trial", None)
        configured = str(getattr(trial_config, "temp_dir", "") or "").strip()
        if configured:
            base = ensure_dir(Path(configured))
            return tempfile.TemporaryDirectory(prefix="originagent_trial_", dir=str(base))
        return tempfile.TemporaryDirectory(prefix="originagent_trial_")

    def _cache_key(self, payload: dict[str, Any]) -> str:
        evolution = payload.get("evolution") if isinstance(payload.get("evolution"), dict) else {}
        opportunity_id = str(evolution.get("opportunity_id") or "")
        target_state_hash = str(payload.get("target_state_hash") or "")
        return f"{opportunity_id}:{target_state_hash}"

    def _read_cached(self, cache_key: str) -> dict[str, Any] | None:
        if not cache_key.strip(":"):
            return None
        ttl_hours = _cache_ttl_hours(self.config)
        if ttl_hours <= 0:
            return None
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
        with self._lock:
            for record in self._read_cache_unlocked():
                if record.get("cache_key") != cache_key:
                    continue
                checked_at = _parse_datetime(str(record.get("checked_at") or ""))
                if checked_at is None or checked_at < cutoff:
                    return None
                result = record.get("result") if isinstance(record.get("result"), dict) else None
                return dict(result) if result is not None else None
        return None

    def _write_cached(self, cache_key: str, result: dict[str, Any]) -> None:
        if not cache_key.strip(":"):
            return
        record = {
            "cache_key": cache_key,
            "checked_at": str(result.get("checked_at") or _now_iso()),
            "result": result,
        }
        with self._lock:
            records = [
                existing for existing in self._read_cache_unlocked()
                if existing.get("cache_key") != cache_key
            ]
            records.append(record)
            tmp_path = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            try:
                with tmp_path.open("w", encoding="utf-8") as handle:
                    for item in records[-200:]:
                        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_path, self.cache_path)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise

    def _read_cache_unlocked(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        with suppress(FileNotFoundError):
            with self.cache_path.open("r", encoding="utf-8") as handle:
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
        return records


def sandbox_status_counts(workspace: Path) -> dict[str, int]:
    """Aggregate sandbox status counts from pending/recent auto-evolution proposals."""

    from OriginAgent.agent.background_review import ReviewProposalStore
    from OriginAgent.agent.evolution import AUTO_EVOLUTION_ORIGIN

    counts: dict[str, int] = {}
    for record in ReviewProposalStore(workspace).list_records(origin=AUTO_EVOLUTION_ORIGIN, limit=50):
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        sandbox = payload.get("sandbox") if isinstance(payload.get("sandbox"), dict) else {}
        status = str(sandbox.get("status") or "").strip()
        if status:
            counts[status] = counts.get(status, 0) + 1
    return counts


def trial_policy_status(config: Any | None = None) -> dict[str, Any]:
    sandbox_config = getattr(config, "sandbox", None)
    trial_config = getattr(config, "trial", None)
    raw_allowed = getattr(sandbox_config, "read_only_tools", None) or []
    allowed_tools = {str(item).strip() for item in raw_allowed if str(item).strip()}
    raw_blocked = getattr(trial_config, "blocked_tools", None) or []
    blocked_tools = {str(item).strip() for item in raw_blocked if str(item).strip()}
    return {
        "enabled": bool(getattr(trial_config, "enabled", True)),
        "isolated_workspace": bool(getattr(trial_config, "isolated_workspace", True)),
        "read_only_tools_only": bool(getattr(trial_config, "read_only_tools_only", True)),
        "allowed_tools": sorted(allowed_tools or set(_DEFAULT_ALLOWED_TOOLS)),
        "blocked_tools": sorted(blocked_tools),
        "temp_dir_configured": bool(str(getattr(trial_config, "temp_dir", "") or "").strip()),
    }


def _sample_count(payload: dict[str, Any], config: Any | None) -> int:
    sandbox_config = getattr(config, "sandbox", None)
    max_samples = int(getattr(sandbox_config, "max_replay_samples", 3) or 0)
    evolution = payload.get("evolution") if isinstance(payload.get("evolution"), dict) else {}
    evidence = evolution.get("evidence_sources") if isinstance(evolution.get("evidence_sources"), list) else []
    return min(len(evidence), max(0, max_samples))


def _cache_ttl_hours(config: Any | None) -> int:
    sandbox_config = getattr(config, "sandbox", None)
    try:
        return int(getattr(sandbox_config, "cache_ttl_hours", 24) or 0)
    except (TypeError, ValueError):
        return 24


def _issue(code: str, severity: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,
        message=truncate_text(message, _MAX_MESSAGE_CHARS),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    with suppress(ValueError):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None
