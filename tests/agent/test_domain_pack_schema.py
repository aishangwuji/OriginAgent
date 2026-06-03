"""Tests for Pydantic schema models for domain_pack.yaml validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from OriginAgent.agent.domain_pack_schema import DomainPackManifest, DomainPackManifestError


def test_parses_minimal_valid_manifest() -> None:
    raw = {"id": "research", "name": "Research", "version": "0.1.0"}
    manifest = DomainPackManifest.from_dict(raw)
    assert manifest.id == "research"
    assert manifest.name == "Research"
    assert manifest.version == "0.1.0"
    assert manifest.enabled is True
    assert manifest.description == ""
    assert manifest.capabilities == []
    assert manifest.tools == []
    assert manifest.runtime is None


def test_parses_full_manifest() -> None:
    raw = {
        "id": "smart_home",
        "name": "Smart Home",
        "version": "0.1.0",
        "enabled": True,
        "description": "Smart home control",
        "capabilities": ["inspect_device_state"],
        "skills": ["lighting-control", "safety-confirmation"],
        "tools": [
            {
                "id": "originagent_device_lighting_set_power",
                "module": "tools.device",
                "class": "LightingSetPowerTool",
                "permissions": ["device:lighting"],
                "audit": "security",
            }
        ],
        "runtime": {
            "module": "runtime.contribution",
            "factory": "build_runtime_contribution",
        },
    }
    manifest = DomainPackManifest.from_dict(raw)
    assert manifest.id == "smart_home"
    assert len(manifest.tools) == 1
    assert manifest.tools[0].id == "originagent_device_lighting_set_power"
    assert manifest.tools[0].class_name == "LightingSetPowerTool"
    assert manifest.tools[0].permissions == ["device:lighting"]
    assert manifest.tools[0].audit == "security"
    assert manifest.runtime is not None
    assert manifest.runtime.module == "runtime.contribution"


def test_rejects_invalid_id() -> None:
    raw = {"id": "Bad ID!", "name": "Bad", "version": "0.1.0"}
    with pytest.raises(DomainPackManifestError, match="id"):
        DomainPackManifest.from_dict(raw)


def test_rejects_empty_name() -> None:
    raw = {"id": "test", "name": "", "version": "0.1.0"}
    with pytest.raises(DomainPackManifestError, match="name"):
        DomainPackManifest.from_dict(raw)


def test_rejects_invalid_tool_id() -> None:
    raw = {
        "id": "test",
        "name": "Test",
        "version": "0.1.0",
        "tools": [
            {"id": "invalid tool id!", "module": "tools.x", "class": "X", "permissions": []},
        ],
    }
    with pytest.raises(DomainPackManifestError, match="tool"):
        DomainPackManifest.from_dict(raw)


def test_rejects_tool_without_permissions_field() -> None:
    raw = {
        "id": "test",
        "name": "Test",
        "version": "0.1.0",
        "tools": [
            {"id": "test_tool", "module": "tools.x", "class": "X"},
        ],
    }
    with pytest.raises(DomainPackManifestError, match="permissions"):
        DomainPackManifest.from_dict(raw)


def test_rejects_invalid_runtime_module() -> None:
    raw = {
        "id": "test",
        "name": "Test",
        "version": "0.1.0",
        "runtime": {"module": "invalid.path", "factory": "build"},
    }
    with pytest.raises(DomainPackManifestError, match="module"):
        DomainPackManifest.from_dict(raw)


def test_rejects_runtime_without_module() -> None:
    raw = {
        "id": "test",
        "name": "Test",
        "version": "0.1.0",
        "runtime": {"factory": "build"},
    }
    with pytest.raises(DomainPackManifestError, match="module"):
        DomainPackManifest.from_dict(raw)


def test_loads_manifest_from_yaml(tmp_path: Path) -> None:
    manifest_path = tmp_path / "domain_pack.yaml"
    manifest_path.write_text(
        "id: research\nname: Research\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    manifest = DomainPackManifest.from_yaml(manifest_path)
    assert manifest.id == "research"
    assert manifest.name == "Research"


def test_loads_manifest_from_yaml_with_bad_yaml(tmp_path: Path) -> None:
    manifest_path = tmp_path / "domain_pack.yaml"
    manifest_path.write_text("{{{bad", encoding="utf-8")
    with pytest.raises(DomainPackManifestError, match="YAML"):
        DomainPackManifest.from_yaml(manifest_path)


def test_skills_supports_string_and_dict_items() -> None:
    raw = {
        "id": "test",
        "name": "Test",
        "version": "0.1.0",
        "skills": ["lighting", {"id": "alarms"}],
    }
    manifest = DomainPackManifest.from_dict(raw)
    assert manifest.skills == ["lighting", "alarms"]


def test_empty_permissions_list_is_valid() -> None:
    raw = {
        "id": "test",
        "name": "Test",
        "version": "0.1.0",
        "tools": [
            {"id": "read_only_tool", "module": "tools.x", "class": "ReadTool", "permissions": []},
        ],
    }
    manifest = DomainPackManifest.from_dict(raw)
    assert manifest.tools[0].permissions == []
