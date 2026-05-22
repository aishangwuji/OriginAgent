from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from OriginAgent.evolution.manifest import MODULE_SCHEMA_VERSION
from OriginAgent.evolution.package import (
    EvolutionPackageError,
    compute_artifact_digest,
    load_package,
    read_package_manifest,
)


def _write_manifest(root: Path, *, module_type: str = "skill") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "evolution_manifest.yaml").write_text(
        "\n".join(
            [
                f"schema_version: {MODULE_SCHEMA_VERSION}",
                "module_id: calendar-helper",
                f"module_type: {module_type}",
                "version: 1.0.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_load_package_reads_valid_evolution_manifest(tmp_path: Path) -> None:
    source = tmp_path / "package"
    _write_manifest(source)
    (source / "SKILL.md").write_text("# Calendar Helper\n", encoding="utf-8")

    package = load_package(source)

    assert package.source_path == source.resolve()
    assert package.source_name == "package"
    assert package.manifest.module_id == "calendar-helper"
    assert package.manifest.module_type == "skill"
    assert package.artifact_digest


def test_read_package_manifest_rejects_missing_manifest(tmp_path: Path) -> None:
    source = tmp_path / "package"
    source.mkdir()

    with pytest.raises(EvolutionPackageError, match="missing evolution_manifest.yaml"):
        read_package_manifest(source)


def test_read_package_manifest_rejects_non_mapping_manifest(tmp_path: Path) -> None:
    source = tmp_path / "package"
    source.mkdir()
    (source / "evolution_manifest.yaml").write_text("- not\n- a mapping\n", encoding="utf-8")

    with pytest.raises(EvolutionPackageError, match="must be a mapping"):
        read_package_manifest(source)


def test_artifact_digest_is_stable_for_same_content(tmp_path: Path) -> None:
    source = tmp_path / "package"
    _write_manifest(source)
    (source / "a.txt").write_text("alpha\n", encoding="utf-8")

    assert compute_artifact_digest(source) == compute_artifact_digest(source)


def test_artifact_digest_changes_when_file_content_changes(tmp_path: Path) -> None:
    source = tmp_path / "package"
    _write_manifest(source)
    file_path = source / "a.txt"
    file_path.write_text("alpha\n", encoding="utf-8")
    before = compute_artifact_digest(source)

    file_path.write_text("beta\n", encoding="utf-8")

    assert compute_artifact_digest(source) != before


def test_artifact_digest_ignores_file_creation_order(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_manifest(first)
    _write_manifest(second)
    (first / "a.txt").write_text("alpha\n", encoding="utf-8")
    (first / "b.txt").write_text("beta\n", encoding="utf-8")
    (second / "b.txt").write_text("beta\n", encoding="utf-8")
    (second / "a.txt").write_text("alpha\n", encoding="utf-8")

    assert compute_artifact_digest(first) == compute_artifact_digest(second)


def test_artifact_digest_uses_posix_relative_paths(tmp_path: Path) -> None:
    source = tmp_path / "package"
    nested = source / "nested"
    _write_manifest(source)
    nested.mkdir()
    (nested / "file.txt").write_text("payload", encoding="utf-8")

    expected = hashlib.sha256()
    for rel_path in ["evolution_manifest.yaml", "nested/file.txt"]:
        expected.update(rel_path.encode("utf-8"))
        expected.update(b"\0")
        expected.update((source / Path(rel_path)).read_bytes())
        expected.update(b"\0")

    assert compute_artifact_digest(source) == expected.hexdigest()


def test_artifact_digest_excludes_cache_and_bytecode_files(tmp_path: Path) -> None:
    source = tmp_path / "package"
    _write_manifest(source)
    (source / "a.txt").write_text("alpha\n", encoding="utf-8")
    before = compute_artifact_digest(source)
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("changed\n", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "module.pyc").write_bytes(b"changed")
    (source / ".pytest_cache").mkdir()
    (source / ".pytest_cache" / "x").write_text("changed\n", encoding="utf-8")
    (source / ".ruff_cache").mkdir()
    (source / ".ruff_cache" / "x").write_text("changed\n", encoding="utf-8")
    (source / "module.pyo").write_bytes(b"changed")

    assert compute_artifact_digest(source) == before


def test_artifact_digest_rejects_symlink(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlink is not supported on this platform")
    source = tmp_path / "package"
    _write_manifest(source)
    target = source / "target.txt"
    target.write_text("target\n", encoding="utf-8")
    link = source / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlink on this platform: {exc}")

    with pytest.raises(EvolutionPackageError, match="symlinks"):
        compute_artifact_digest(source)
