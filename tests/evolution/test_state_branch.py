from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from OriginAgent.agent.facts import FactStore
from OriginAgent.evolution import EvolutionModuleManager, EvolutionStateBranchStore
from OriginAgent.evolution.manifest import MODULE_SCHEMA_VERSION


def _write_package(root: Path, manifest_updates: dict[str, Any] | None = None) -> Path:
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
    (root / "SKILL.md").write_text("# Calendar Helper\n", encoding="utf-8")
    return root


def _event_rows(workspace: Path) -> list[dict]:
    path = workspace / "memory" / "evolution_events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _verified_digest(tmp_path: Path) -> tuple[Path, str]:
    workspace = tmp_path / "workspace"
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(_write_package(tmp_path / "source"))
    assert staged.ok
    verified = manager.verify(staged.artifact_digest)
    assert verified.ok
    return workspace, staged.artifact_digest


def test_create_branch_writes_files_and_event(tmp_path: Path) -> None:
    workspace, digest = _verified_digest(tmp_path)
    store = EvolutionStateBranchStore(workspace)

    result = store.create_branch(digest)

    assert result.ok is True
    assert result.branch_id.startswith("branch_")
    branch_dir = workspace / "memory" / "evolution_branches" / result.branch_id
    assert (branch_dir / "branch.json").exists()
    assert (branch_dir / "facts_overlay.jsonl").exists()
    assert (branch_dir / "facts_tombstones.jsonl").exists()
    metadata = json.loads((branch_dir / "branch.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "active"
    assert metadata["artifact_digest"] == digest
    assert metadata["base_event_hash"]
    assert metadata["base_facts_hash"]
    assert _event_rows(workspace)[-1]["event_type"] == "state_branch_created"


def test_create_branch_requires_verified_artifact(tmp_path: Path) -> None:
    result = EvolutionStateBranchStore(tmp_path / "workspace").create_branch("digest")

    assert result.ok is False
    assert "verified" in result.error


def test_read_facts_returns_stable_and_branch_view_copies(tmp_path: Path) -> None:
    workspace, digest = _verified_digest(tmp_path)
    stable_fact = FactStore(workspace).upsert_fact("Use quiet notifications")
    store = EvolutionStateBranchStore(workspace)
    branch = store.create_branch(digest)
    assert branch.ok
    store.upsert_fact(branch.branch_id, "Use warm lights")

    stable = store.read_facts(None)
    branch_facts = store.read_facts(branch.branch_id)
    stable[0].content = "mutated"

    assert [fact.content for fact in store.read_facts(None)] == [stable_fact.content]
    assert {fact.content for fact in branch_facts} == {
        "Use quiet notifications",
        "Use warm lights",
    }


def test_branch_upsert_does_not_modify_stable_facts_or_memory(tmp_path: Path) -> None:
    workspace, digest = _verified_digest(tmp_path)
    store = EvolutionStateBranchStore(workspace)
    branch = store.create_branch(digest)
    assert branch.ok

    result = store.upsert_fact(branch.branch_id, "Use warm lights")

    assert result.ok is True
    assert FactStore(workspace).read_all() == []
    assert not (workspace / "memory" / "MEMORY.md").exists()


def test_preview_reports_modified_stable_fact(tmp_path: Path) -> None:
    workspace, digest = _verified_digest(tmp_path)
    FactStore(workspace).upsert_fact("Use warm lights", confidence=0.1)
    store = EvolutionStateBranchStore(workspace)
    branch = store.create_branch(digest)
    assert branch.ok
    store.upsert_fact(branch.branch_id, "Use warm lights", confidence=0.9)

    preview = store.preview_merge(branch.branch_id)

    assert preview.ok is True
    assert len(preview.modified) == 1


def test_preview_reports_deprecated_stable_fact(tmp_path: Path) -> None:
    workspace, digest = _verified_digest(tmp_path)
    fact = FactStore(workspace).upsert_fact("Old preference")
    store = EvolutionStateBranchStore(workspace)
    branch = store.create_branch(digest)
    assert branch.ok
    store.deprecate_fact(branch.branch_id, fact.fact_id)

    preview = store.preview_merge(branch.branch_id)

    assert preview.ok is True
    assert preview.deprecated == (fact.fact_id,)


def test_discard_branch_does_not_modify_stable_facts(tmp_path: Path) -> None:
    workspace, digest = _verified_digest(tmp_path)
    store = EvolutionStateBranchStore(workspace)
    branch = store.create_branch(digest)
    assert branch.ok
    store.upsert_fact(branch.branch_id, "Use warm lights")

    result = store.discard_branch(branch.branch_id)

    assert result.ok is True
    assert FactStore(workspace).read_all() == []
    metadata = json.loads(
        (workspace / "memory" / "evolution_branches" / branch.branch_id / "branch.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["status"] == "discarded"
    assert _event_rows(workspace)[-1]["event_type"] == "state_branch_discarded"


def test_merge_branch_writes_facts_memory_and_event(tmp_path: Path) -> None:
    workspace, digest = _verified_digest(tmp_path)
    store = EvolutionStateBranchStore(workspace)
    branch = store.create_branch(digest)
    assert branch.ok
    store.upsert_fact(branch.branch_id, "Use warm lights")

    result = store.merge_branch(branch.branch_id)

    assert result.ok is True
    assert result.status == "merged"
    assert [fact.content for fact in FactStore(workspace).read_all()] == ["Use warm lights"]
    assert "Use warm lights" in (workspace / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert _event_rows(workspace)[-1]["event_type"] == "state_branch_merged"


def test_merge_conflict_rejects_stable_fact_drift(tmp_path: Path) -> None:
    workspace, digest = _verified_digest(tmp_path)
    facts = FactStore(workspace)
    facts.upsert_fact("Use warm lights", confidence=0.1)
    store = EvolutionStateBranchStore(workspace)
    branch = store.create_branch(digest)
    assert branch.ok
    store.upsert_fact(branch.branch_id, "Use warm lights", confidence=0.9)
    facts.upsert_fact("Use warm lights", confidence=0.8)

    result = store.merge_branch(branch.branch_id)

    assert result.ok is False
    assert result.status == "merge_conflict"
    assert result.conflicts
    assert FactStore(workspace).read_all()[0].confidence == 0.8
    assert _event_rows(workspace)[-1]["event_type"] == "module_failed"
    assert _event_rows(workspace)[-1]["result"]["status"] == "merge_conflict"


def test_repeated_merge_and_discard_fail_for_terminal_branch(tmp_path: Path) -> None:
    workspace, digest = _verified_digest(tmp_path)
    store = EvolutionStateBranchStore(workspace)
    branch = store.create_branch(digest)
    assert branch.ok
    store.upsert_fact(branch.branch_id, "Use warm lights")
    assert store.merge_branch(branch.branch_id).ok is True

    second_merge = store.merge_branch(branch.branch_id)
    discard = store.discard_branch(branch.branch_id)

    assert second_merge.ok is False
    assert discard.ok is False
    assert [fact.content for fact in FactStore(workspace).read_all()] == ["Use warm lights"]


def test_corrupt_branch_file_returns_failed_preview(tmp_path: Path) -> None:
    workspace, digest = _verified_digest(tmp_path)
    store = EvolutionStateBranchStore(workspace)
    branch = store.create_branch(digest)
    assert branch.ok
    branch_json = workspace / "memory" / "evolution_branches" / branch.branch_id / "branch.json"
    branch_json.write_text("{broken", encoding="utf-8")

    preview = store.preview_merge(branch.branch_id)

    assert preview.ok is False
    assert preview.conflicts
