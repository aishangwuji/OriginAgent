from __future__ import annotations

from pathlib import Path

from OriginAgent.agent.domain_packs import DomainPackRuntimeConfig, DomainPackValidator


def _write_pack(root: Path, pack_id: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "domain_pack.yaml").write_text(
        "id: research\n"
        "name: Research\n"
        "version: 0.1.0\n"
        "skills:\n"
        "  - missing-skill\n",
        encoding="utf-8",
    )
    (root / "CAPABILITIES.md").write_text("# Research\n", encoding="utf-8")
    return root


def test_validator_is_relaxed_for_discovery_but_strict_for_governance(tmp_path: Path) -> None:
    pack_dir = _write_pack(tmp_path / "research", "research")

    relaxed = DomainPackValidator(
        runtime_config=DomainPackRuntimeConfig(),
        strict_declarations=False,
    ).validate_pack(pack_dir, source="workspace")
    strict = DomainPackValidator(
        runtime_config=DomainPackRuntimeConfig(),
        strict_declarations=True,
    ).validate_pack(pack_dir, source="workspace")

    assert relaxed.status == "available"
    assert relaxed.skills[0].status == "skipped"
    assert "missing SKILL.md" in relaxed.skills[0].unavailable_reason
    assert "missing SKILL.md" in relaxed.validation_summary

    assert strict.status == "invalid"
    assert "missing SKILL.md" in strict.unavailable_reason
