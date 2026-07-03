from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from OriginAgent.evolution import EvolutionModuleManager, EvolutionTelemetryRecorder
from OriginAgent.evolution.events import EventType, EvolutionEvent
from OriginAgent.evolution.ledger import canonical_dump
from OriginAgent.evolution.manifest import MODULE_SCHEMA_VERSION
from OriginAgent.evolution.telemetry import compute_proof_bundle_hash


def _write_skill_package(root: Path, manifest_updates: dict[str, Any] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": MODULE_SCHEMA_VERSION,
        "module_id": "calendar-helper",
        "module_type": "skill",
        "version": "1.0.0",
    }
    manifest.update(manifest_updates or {})
    (root / "evolution_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    frontmatter = {
        "name": "calendar-helper",
        "description": "Calendar helper",
        "metadata": {
            "OriginAgent": {
                "proposal_status": "proposed",
                "verification_status": "unverified",
                "lifecycle_status": "proposed",
                "created_by": "background_review",
            }
        },
    }
    (root / "SKILL.md").write_text(
        "---\n" + yaml.safe_dump(frontmatter, sort_keys=False) + "---\n\n# Calendar Helper\n",
        encoding="utf-8",
    )
    return root


def _stage_and_verify(
    workspace: Path,
    source: Path,
) -> tuple[EvolutionModuleManager, str]:
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)
    assert staged.ok, staged.error
    verified = manager.verify(staged.artifact_digest)
    assert verified.ok, verified.error
    return manager, staged.artifact_digest


def _event_rows(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / "memory" / "evolution_events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_telemetry_recorded_event_is_createable_and_written(tmp_path: Path) -> None:
    event = EvolutionEvent.new(EventType.TELEMETRY_RECORDED, artifact_digest="digest")
    assert event.event_type == "telemetry_recorded"

    workspace = tmp_path / "workspace"
    _manager, digest = _stage_and_verify(workspace, _write_skill_package(tmp_path / "source"))

    result = EvolutionTelemetryRecorder(workspace).record(
        digest,
        event_kind="runtime_error",
        status="failed",
        message="Provider failed",
    )

    assert result.ok is True
    assert result.event is not None
    assert result.event.event_type == "telemetry_recorded"
    assert (workspace / result.telemetry_path).exists()
    assert _event_rows(workspace)[-1]["event_type"] == "telemetry_recorded"


def test_telemetry_sanitizes_paths_url_query_secrets_and_traceback(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _manager, digest = _stage_and_verify(workspace, _write_skill_package(tmp_path / "source"))
    home = str(Path.home())
    message = (
        f"failed at {workspace.resolve()} and {home} "
        "https://example.test/path?token=abc123 secret=plain password=hunter2"
    )
    stack = f'File "{workspace.resolve()}\\private.py", line 1, in run\nraise RuntimeError("secret=plain")'

    EvolutionTelemetryRecorder(workspace).record(
        digest,
        event_kind="runtime_error",
        status="failed",
        error_code="boom",
        exception_type="RuntimeError",
        message=message,
        stack=stack,
        denial_chain=({"reason": f"blocked {workspace.resolve()}?token=abc123"},),
    )

    telemetry_text = (
        workspace / "memory" / "evolution_telemetry" / digest / "telemetry.jsonl"
    ).read_text(encoding="utf-8")
    ledger_text = (workspace / "memory" / "evolution_events.jsonl").read_text(encoding="utf-8")
    serialized = telemetry_text + ledger_text
    assert str(workspace.resolve()) not in serialized
    assert str(workspace.resolve()).replace("\\", "/") not in serialized
    assert home not in serialized
    assert "?token=abc123" not in serialized
    assert "plain" not in serialized
    assert "hunter2" not in serialized
    assert "private.py" not in serialized
    assert "RuntimeError" in serialized


def test_message_redacted_is_truncated_after_sanitization(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _manager, digest = _stage_and_verify(workspace, _write_skill_package(tmp_path / "source"))

    EvolutionTelemetryRecorder(workspace).record(
        digest,
        event_kind="long_error",
        status="failed",
        message=f"{workspace.resolve()} " + ("x" * 800),
    )

    row = json.loads(
        (workspace / "memory" / "evolution_telemetry" / digest / "telemetry.jsonl").read_text(
            encoding="utf-8"
        )
    )
    assert len(row["message_redacted"]) <= 300
    assert str(workspace.resolve()) not in row["message_redacted"]


def test_telemetry_digest_is_stable_for_field_order_and_changes_on_append(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    recorder = EvolutionTelemetryRecorder(workspace)
    digest = "digest"
    telemetry_path = workspace / "memory" / "evolution_telemetry" / digest / "telemetry.jsonl"
    telemetry_path.parent.mkdir(parents=True)
    first = {"telemetry_id": "telemetry_a", "created_at": "2026-05-22T00:00:00+00:00", "status": "ok"}
    second = {"status": "failed", "created_at": "2026-05-22T00:00:01+00:00", "telemetry_id": "telemetry_b"}
    telemetry_path.write_text(
        json.dumps(second, ensure_ascii=False) + "\n" + json.dumps(first, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    digest_one = recorder.telemetry_digest(digest)
    telemetry_path.write_text(
        json.dumps(dict(reversed(list(first.items()))), ensure_ascii=False)
        + "\n"
        + json.dumps(dict(reversed(list(second.items()))), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    assert recorder.telemetry_digest(digest) == digest_one
    telemetry_path.write_text(
        telemetry_path.read_text(encoding="utf-8")
        + json.dumps({"telemetry_id": "telemetry_c", "created_at": "2026-05-22T00:00:02+00:00"})
        + "\n",
        encoding="utf-8",
    )
    assert recorder.telemetry_digest(digest) != digest_one


def test_state_branch_digest_is_empty_without_branch_and_recomputable_with_branch(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_and_verify(workspace, _write_skill_package(tmp_path / "source"))
    recorder = EvolutionTelemetryRecorder(workspace)

    assert recorder.state_branch_digest(digest) == ""
    branch = manager.create_state_branch(digest)
    assert branch.ok, branch.error
    branch_json = json.loads(
        (workspace / "memory" / "evolution_branches" / branch.branch_id / "branch.json").read_text(
            encoding="utf-8"
        )
    )
    expected = hashlib.sha256(
        canonical_dump(
            [
                {
                    "artifact_digest": digest,
                    "base_facts_hash": branch_json["base_facts_hash"],
                    "branch_id": branch.branch_id,
                    "status": "active",
                }
            ]
        )
    ).hexdigest()

    assert recorder.state_branch_digest(digest) == expected


def test_token_budget_not_declared_allows_preflight(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_and_verify(workspace, _write_skill_package(tmp_path / "source"))

    result = manager.preflight_token_budget(digest, payload_texts=("hello",))

    assert result.ok is True
    assert result.status == "budget_not_declared"
    assert result.frozen is False


def test_preflight_estimates_tokens_and_rejects_single_request_over_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_and_verify(
        workspace,
        _write_skill_package(tmp_path / "source", {"context_budget": {"token_budget": 10}}),
    )

    result = manager.preflight_token_budget(digest, payload_texts=("x" * 32,))

    assert result.ok is False
    assert result.status == "exceeded"
    assert result.estimated_tokens == 10
    assert _event_rows(workspace)[-1]["event_type"] == "telemetry_recorded"


def test_preflight_returns_budget_token_and_postflight_requires_matching_token(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_and_verify(
        workspace,
        _write_skill_package(tmp_path / "source", {"context_budget": {"token_budget": 20}}),
    )
    preflight = manager.preflight_token_budget(digest, estimated_tokens=3)
    assert preflight.ok
    assert preflight.budget_token

    invalid = manager.record_postflight_usage(
        digest,
        budget_token="budget_missing",
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )
    assert invalid.ok is False
    assert invalid.status == "invalid_budget_token"

    recorded = manager.record_postflight_usage(
        digest,
        budget_token=preflight.budget_token,
        usage={"prompt_tokens": 1, "completion_tokens": 2},
    )
    assert recorded.ok is True
    assert recorded.status == "recorded"
    assert recorded.consumed_tokens == 3


def test_expired_pending_budget_token_is_released_on_later_preflight(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_and_verify(
        workspace,
        _write_skill_package(tmp_path / "source", {"context_budget": {"token_budget": 10}}),
    )
    first = manager.preflight_token_budget(digest, estimated_tokens=7)
    assert first.ok
    budget_path = workspace / "memory" / "evolution_telemetry" / digest / "budget.json"
    budget = json.loads(budget_path.read_text(encoding="utf-8"))
    for pending in budget["pending"].values():
        pending["expires_at"] = "2000-01-01T00:00:00+00:00"
    budget_path.write_text(json.dumps(budget), encoding="utf-8")

    second = manager.preflight_token_budget(digest, estimated_tokens=7)

    assert second.ok is True
    assert second.status == "approved"


def test_postflight_over_budget_freezes_future_preflight(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_and_verify(
        workspace,
        _write_skill_package(tmp_path / "source", {"context_budget": {"token_budget": 10}}),
    )
    preflight = manager.preflight_token_budget(digest, estimated_tokens=1)
    assert preflight.ok

    exceeded = manager.record_postflight_usage(
        digest,
        budget_token=preflight.budget_token,
        usage={"prompt_tokens": 4, "completion_tokens": 7},
    )
    blocked = manager.preflight_token_budget(digest, estimated_tokens=1)
    budget = json.loads(
        (workspace / "memory" / "evolution_telemetry" / digest / "budget.json").read_text(
            encoding="utf-8"
        )
    )

    assert exceeded.ok is False
    assert exceeded.status == "budget_exceeded"
    assert exceeded.frozen is True
    assert blocked.status == "blocked"
    assert blocked.frozen is True
    assert budget["frozen"] is True
    assert budget["frozen_reason"] == "token_budget_exceeded"
    assert budget["frozen_at"]


def test_build_proof_bundle_for_verified_artifact_is_recomputable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_and_verify(workspace, _write_skill_package(tmp_path / "source"))

    result = manager.build_proof_bundle(digest)

    assert result.ok is True
    assert result.bundle is not None
    bundle_path = workspace / result.proof_bundle_path
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert compute_proof_bundle_hash(bundle) == bundle["proof_bundle_hash"]
    tampered = dict(bundle)
    tampered["module_id"] = "other"
    assert compute_proof_bundle_hash(tampered) != bundle["proof_bundle_hash"]
    assert str(workspace.resolve()) not in json.dumps(bundle, ensure_ascii=False)
    assert bundle["activation_event_hash"] == ""


def test_active_artifact_proof_bundle_contains_activation_and_capability_digest(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_and_verify(
        workspace,
        _write_skill_package(tmp_path / "source", {"permissions": {"read_files": True}}),
    )
    activated = manager.activate_module(digest, approved_by="test")
    assert activated.ok, activated.error

    result = manager.build_proof_bundle(digest)

    assert result.ok is True
    assert result.bundle is not None
    assert result.bundle["activation_event_hash"]
    assert result.bundle["capability_snapshot_digest"]


def test_proof_bundle_requires_verified_artifact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(_write_skill_package(tmp_path / "source"))
    assert staged.ok

    result = manager.build_proof_bundle(staged.artifact_digest)

    assert result.ok is False
    assert result.status == "unverified"


def test_proof_bundle_fails_when_ledger_chain_is_broken(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_and_verify(workspace, _write_skill_package(tmp_path / "source"))
    event_path = workspace / "memory" / "evolution_events.jsonl"
    event_path.write_text(
        event_path.read_text(encoding="utf-8").replace('"module_verified"', '"module_failed"', 1),
        encoding="utf-8",
    )

    result = manager.build_proof_bundle(digest)

    assert result.ok is False
    assert result.status == "ledger_broken"


def test_proof_bundle_fails_when_verifier_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_and_verify(workspace, _write_skill_package(tmp_path / "source"))
    (workspace / "memory" / "evolution_staging" / digest / "artifact" / "SKILL.md").write_text(
        "# changed\n",
        encoding="utf-8",
    )

    result = manager.build_proof_bundle(digest)

    assert result.ok is False
    assert result.status == "verification_failed"


def test_manager_telemetry_wrappers(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_and_verify(
        workspace,
        _write_skill_package(tmp_path / "source", {"context_budget": {"token_budget": 20}}),
    )

    telemetry = manager.record_telemetry(digest, event_kind="manual", status="ok")
    preflight = manager.preflight_token_budget(digest, estimated_tokens=2)
    postflight = manager.record_postflight_usage(
        digest,
        budget_token=preflight.budget_token,
        usage={"total_tokens": 2},
    )
    proof = manager.build_proof_bundle(digest)

    assert telemetry.ok is True
    assert preflight.ok is True
    assert postflight.ok is True
    assert proof.ok is True
    assert datetime.fromisoformat(proof.bundle["created_at"]).tzinfo is not None  # type: ignore[index]
