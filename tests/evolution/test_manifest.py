import pytest

from OriginAgent.evolution.manifest import (
    MODULE_SCHEMA_VERSION,
    ManifestValidationError,
    validate_manifest,
)


def _valid_manifest() -> dict[str, object]:
    return {
        "schema_version": MODULE_SCHEMA_VERSION,
        "module_id": "calendar-helper",
        "module_type": "skill",
        "version": "1.0.0",
        "target_originagent": {"min_version": "0.1.0"},
        "target_module_api": "skills.v1",
        "permissions": {"read_files": False},
        "runtime_requirements": {"python": ">=3.11"},
        "context_budget": {"token_budget": 1000},
        "external_endpoints": ["https://api.example.com"],
        "external_side_effects": {"writes_external_state": False},
        "tests": {"path": "tests"},
    }


def test_validate_manifest_accepts_valid_manifest() -> None:
    manifest = validate_manifest(_valid_manifest())

    assert manifest.schema_version == MODULE_SCHEMA_VERSION
    assert manifest.module_id == "calendar-helper"
    assert manifest.module_type == "skill"
    assert manifest.version == "1.0.0"
    assert manifest.external_endpoints == ("https://api.example.com",)


def test_validate_manifest_rejects_missing_schema_version() -> None:
    raw = _valid_manifest()
    raw.pop("schema_version")

    with pytest.raises(ManifestValidationError, match="schema_version"):
        validate_manifest(raw)


def test_validate_manifest_rejects_unknown_schema_version() -> None:
    raw = _valid_manifest()
    raw["schema_version"] = "originagent.evolution.module.v2"

    with pytest.raises(ManifestValidationError, match="unsupported schema_version"):
        validate_manifest(raw)


@pytest.mark.parametrize("field", ["module_id", "module_type", "version"])
def test_validate_manifest_rejects_missing_required_fields(field: str) -> None:
    raw = _valid_manifest()
    raw.pop(field)

    with pytest.raises(ManifestValidationError, match=field):
        validate_manifest(raw)


def test_validate_manifest_rejects_invalid_module_type() -> None:
    raw = _valid_manifest()
    raw["module_type"] = "agent_loop"

    with pytest.raises(ManifestValidationError, match="module_type"):
        validate_manifest(raw)


@pytest.mark.parametrize("module_type", ["skill", "workflow", "domain_pack", "tool"])
def test_validate_manifest_accepts_phase_1_module_types(module_type: str) -> None:
    raw = _valid_manifest()
    raw["module_type"] = module_type

    manifest = validate_manifest(raw)

    assert manifest.module_type == module_type
