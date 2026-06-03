from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

import OriginAgent.evolution.ledger as ledger_module
from OriginAgent.evolution import EvolutionIdentityStore, EvolutionLedger, EvolutionModuleManager
from OriginAgent.evolution.events import EventType, EvolutionEvent
from OriginAgent.evolution.identity import verify_event_signature
from OriginAgent.evolution.manifest import MODULE_SCHEMA_VERSION


def _write_skill_package(
    root: Path,
    *,
    module_id: str = "calendar-helper",
    manifest_updates: dict[str, Any] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": MODULE_SCHEMA_VERSION,
        "module_id": module_id,
        "module_type": "skill",
        "version": "1.0.0",
    }
    manifest.update(manifest_updates or {})
    (root / "evolution_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    frontmatter = {
        "name": module_id,
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


def _stage_verify_activate(
    workspace: Path,
    source: Path,
) -> tuple[EvolutionModuleManager, str]:
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)
    assert staged.ok, staged.error
    verified = manager.verify(staged.artifact_digest)
    assert verified.ok, verified.error
    activated = manager.activate_module(staged.artifact_digest)
    assert activated.ok, activated.error
    return manager, staged.artifact_digest


def _event_rows(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / "memory" / "evolution_events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _activation_metadata(workspace: Path, artifact_digest: str) -> dict[str, Any]:
    return json.loads(
        (
            workspace
            / "memory"
            / "evolution_activations"
            / artifact_digest
            / "activation.json"
        ).read_text(encoding="utf-8")
    )


def test_identity_store_generates_and_reuses_key(tmp_path: Path) -> None:
    identity_path = tmp_path / "identity.json"

    first = EvolutionIdentityStore(identity_path)
    second = EvolutionIdentityStore(identity_path)

    assert identity_path.exists()
    assert first.public_key_b64 == second.public_key_b64
    data = json.loads(identity_path.read_text(encoding="utf-8"))
    assert data["protection"] == "plaintext"
    assert data["protection_warning"]
    if os.name != "nt":
        assert stat.S_IMODE(identity_path.stat().st_mode) == 0o600


def test_signed_ledger_append_and_signature_verification(tmp_path: Path) -> None:
    identity = EvolutionIdentityStore(tmp_path / "identity.json")
    ledger = EvolutionLedger(tmp_path, identity_store=identity, sign_events=True)

    event = ledger.append(EvolutionEvent.new(EventType.MODULE_PROPOSED, module_id="alpha"))

    assert event.actor_public_key == identity.public_key_b64
    assert event.signature
    assert verify_event_signature(event.to_dict()) is True
    tampered_hash = {**event.to_dict(), "event_hash": "0" * 64}
    tampered_signature = {**event.to_dict(), "signature": "bad"}
    assert verify_event_signature(tampered_hash) is False
    assert verify_event_signature(tampered_signature) is False


def test_verify_chain_counts_unsigned_and_invalid_signatures(tmp_path: Path) -> None:
    unsigned_ledger = EvolutionLedger(tmp_path)
    unsigned_ledger.append(EvolutionEvent.new(EventType.MODULE_PROPOSED, module_id="alpha"))
    unsigned = unsigned_ledger.verify_chain(verify_signatures=True)
    assert unsigned.ok is True
    assert unsigned.unsigned_event_count == 1

    signed_workspace = tmp_path / "signed"
    identity = EvolutionIdentityStore(tmp_path / "identity.json")
    signed_ledger = EvolutionLedger(signed_workspace, identity_store=identity, sign_events=True)
    signed_ledger.append(EvolutionEvent.new(EventType.MODULE_PROPOSED, module_id="alpha"))
    event_path = signed_workspace / "memory" / "evolution_events.jsonl"
    row = json.loads(event_path.read_text(encoding="utf-8"))
    row["signature"] = "bad"
    event_path.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    invalid = signed_ledger.verify_chain(verify_signatures=True)
    assert invalid.ok is True
    assert invalid.invalid_signature_count == 1


def test_ledger_status_reports_broken_and_rotation_recommendation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = EvolutionLedger(tmp_path / "empty").status()
    assert empty.chain_integrity == "ok"
    assert empty.event_count == 0

    workspace = tmp_path / "workspace"
    ledger = EvolutionLedger(workspace)
    ledger.append(EvolutionEvent.new(EventType.MODULE_PROPOSED, module_id="alpha"))
    event_path = workspace / "memory" / "evolution_events.jsonl"
    event_path.write_text(
        event_path.read_text(encoding="utf-8").replace('"alpha"', '"beta"', 1),
        encoding="utf-8",
    )
    broken = ledger.status()
    assert broken.chain_integrity == "broken"
    assert broken.error

    rotate_workspace = tmp_path / "rotate"
    rotate_ledger = EvolutionLedger(rotate_workspace)
    rotate_ledger.append(EvolutionEvent.new(EventType.MODULE_PROPOSED, module_id="alpha"))
    rotate_ledger.append(EvolutionEvent.new(EventType.MODULE_MANIFEST_VALIDATED, module_id="alpha"))
    monkeypatch.setattr(ledger_module, "LEDGER_ROTATION_EVENT_THRESHOLD", 1)
    assert rotate_ledger.status().rotation_recommended is True


def test_runtime_status_wraps_ledger_status(tmp_path: Path) -> None:
    manager = EvolutionModuleManager(tmp_path / "workspace")
    status = manager.runtime_status()

    assert status.chain_integrity == "ok"
    assert status.event_path == "memory/evolution_events.jsonl"


def test_rollback_failure_marks_dirty_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_verify_activate(workspace, _write_skill_package(tmp_path / "source"))

    class FakeLifecycle:
        def __init__(self, _workspace: Path) -> None:
            pass

        def transition(self, _name: str, *, action: str, **_kwargs: Any):
            assert action == "deprecate"
            return _Transition(False, "external cleanup failed")

    monkeypatch.setattr("OriginAgent.evolution.activation.SkillLifecycleStore", FakeLifecycle)

    result = manager.rollback_module(digest)
    metadata = _activation_metadata(workspace, digest)

    assert result.ok is False
    assert result.status == "dirty_rollback"
    assert [event.event_type for event in result.events] == [
        "module_rollback_started",
        "module_rollback_failed",
        "dirty_rollback",
    ]
    assert metadata["status"] == "dirty_rollback"
    assert metadata["dirty_rollback_at"]
    assert _event_rows(workspace)[-1]["event_type"] == "dirty_rollback"


def test_dirty_rollback_blocks_restage_until_force_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_verify_activate(workspace, _write_skill_package(tmp_path / "source"))

    class FakeLifecycle:
        def __init__(self, _workspace: Path) -> None:
            pass

        def transition(self, _name: str, *, action: str, **_kwargs: Any):
            assert action == "deprecate"
            return _Transition(False, "external cleanup failed")

    monkeypatch.setattr("OriginAgent.evolution.activation.SkillLifecycleStore", FakeLifecycle)
    assert manager.rollback_module(digest).status == "dirty_rollback"

    blocked = manager.stage(_write_skill_package(tmp_path / "source2", manifest_updates={"version": "1.0.1"}))
    assert blocked.ok is False
    assert blocked.status == "dirty_rollback_blocked"
    assert blocked.events[-1].event_type == "module_failed"
    assert blocked.events[-1].result["status"] == "dirty_rollback_blocked"

    cleaned = manager.force_clean_module(digest, reason="operator accepted residual risk")
    restaged = manager.stage(_write_skill_package(tmp_path / "source3", manifest_updates={"version": "1.0.2"}))
    assert cleaned.ok is True
    assert restaged.ok is True


def test_record_teardown_failure_and_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_verify_activate(workspace, _write_skill_package(tmp_path / "source"))

    class FakeLifecycle:
        def __init__(self, _workspace: Path) -> None:
            pass

        def transition(self, _name: str, *, action: str, **_kwargs: Any):
            assert action == "deprecate"
            return _Transition(False, "external cleanup failed")

    monkeypatch.setattr("OriginAgent.evolution.activation.SkillLifecycleStore", FakeLifecycle)
    assert manager.rollback_module(digest).status == "dirty_rollback"

    failed = manager.record_teardown(
        digest,
        succeeded=False,
        reason="cleanup failed",
        residual_resources=(str((workspace / "private").resolve()), "token=secret"),
    )
    metadata = _activation_metadata(workspace, digest)
    assert failed.ok is False
    assert failed.status == "teardown_failed"
    assert [event.event_type for event in failed.events] == ["teardown_started", "teardown_failed"]
    assert metadata["status"] == "dirty_rollback"

    succeeded = manager.record_teardown(digest, succeeded=True, reason="operator cleaned resources")
    metadata = _activation_metadata(workspace, digest)
    assert succeeded.ok is True
    assert succeeded.status == "teardown_succeeded"
    assert [event.event_type for event in succeeded.events] == ["teardown_started", "teardown_succeeded"]
    assert metadata["status"] == "rolled_back"

    event_text = (workspace / "memory" / "evolution_events.jsonl").read_text(encoding="utf-8")
    assert str(workspace.resolve()) not in event_text
    assert "token=secret" not in event_text


@dataclass(frozen=True)
class _Transition:
    ok: bool
    error: str
    message: str = ""
