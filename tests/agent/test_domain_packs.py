from __future__ import annotations

from pathlib import Path

from OpenHome.agent.context import ContextBuilder
from OpenHome.agent.domain_packs import DomainPackManager
from OpenHome.config.schema import DomainPacksConfig


def _write_pack(
    root: Path,
    dirname: str,
    *,
    pack_id: str | None = None,
    name: str = "Demo",
    version: str = "0.1.0",
    enabled: bool | None = None,
    capabilities: list[str] | None = None,
    requires_bins: list[str] | None = None,
    requires_env: list[str] | None = None,
    capabilities_text: str | None = None,
) -> Path:
    pack_dir = root / dirname
    pack_dir.mkdir(parents=True)
    lines: list[str] = []
    if pack_id is not None:
        lines.append(f"id: {pack_id}")
    if name is not None:
        lines.append(f"name: {name}")
    if version is not None:
        lines.append(f"version: {version}")
    if enabled is not None:
        lines.append(f"enabled: {'true' if enabled else 'false'}")
    if capabilities is not None:
        lines.append("capabilities:")
        lines.extend(f"  - {item}" for item in capabilities)
    if requires_bins is not None or requires_env is not None:
        lines.append("requires:")
        if requires_bins is not None:
            lines.append("  bins:")
            lines.extend(f"    - {item}" for item in requires_bins)
        if requires_env is not None:
            lines.append("  env:")
            lines.extend(f"    - {item}" for item in requires_env)
    (pack_dir / "domain_pack.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if capabilities_text is not None:
        (pack_dir / "CAPABILITIES.md").write_text(capabilities_text, encoding="utf-8")
    return pack_dir


def test_discovers_builtin_and_workspace_with_workspace_override(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    builtin = tmp_path / "builtin"
    _write_pack(
        builtin,
        "research",
        pack_id="research",
        name="Built-in Research",
        capabilities_text="# Built-in",
    )
    _write_pack(
        workspace / "domain_packs",
        "research-local",
        pack_id="research",
        name="Workspace Research",
        capabilities_text="# Workspace",
    )
    _write_pack(
        builtin,
        "office",
        pack_id="office",
        name="Office",
        capabilities_text="# Office",
    )

    manager = DomainPackManager(workspace, builtin_dir=builtin)
    packs = {pack.id: pack for pack in manager.list_packs()}

    assert sorted(packs) == ["office", "research"]
    assert packs["research"].source == "workspace"
    assert packs["research"].name == "Workspace Research"
    assert packs["office"].source == "builtin"


def test_invalid_manifests_are_reported_without_raising(tmp_path: Path) -> None:
    root = tmp_path / "workspace" / "domain_packs"
    (root / "missing-manifest").mkdir(parents=True)
    _write_pack(root, "missing-id", name="Missing ID", capabilities_text="# Missing")
    _write_pack(root, "bad-id", pack_id="Bad ID", name="Bad", capabilities_text="# Bad")
    _write_pack(root, "missing-name", pack_id="missing_name", name="", capabilities_text="# Missing")

    manager = DomainPackManager(tmp_path / "workspace", builtin_dir=tmp_path / "empty")
    packs = {pack.id: pack for pack in manager.list_packs()}

    assert packs["missing-manifest"].status == "invalid"
    assert "missing domain_pack.yaml" in packs["missing-manifest"].unavailable_reason
    assert packs["missing-id"].status == "invalid"
    assert "missing required field: id" in packs["missing-id"].unavailable_reason
    assert packs["bad-id"].status == "invalid"
    assert "id must match" in packs["bad-id"].unavailable_reason
    assert packs["missing_name"].status == "invalid"
    assert "missing required field(s): name" in packs["missing_name"].unavailable_reason


def test_unavailable_reasons_and_active_available_only(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / "domain_packs"
    _write_pack(root, "ok", pack_id="ok", name="OK", capabilities_text="# OK")
    _write_pack(root, "manifest-off", pack_id="manifest_off", enabled=False, capabilities_text="# Off")
    _write_pack(root, "missing-caps", pack_id="missing_caps")
    _write_pack(
        root,
        "missing-bin",
        pack_id="missing_bin",
        requires_bins=["definitely-not-openhome-bin"],
        capabilities_text="# Bin",
    )
    _write_pack(
        root,
        "missing-env",
        pack_id="missing_env",
        requires_env=["OPENHOME_TEST_MISSING_ENV"],
        capabilities_text="# Env",
    )
    _write_pack(root, "disabled", pack_id="disabled", capabilities_text="# Disabled")
    monkeypatch.delenv("OPENHOME_TEST_MISSING_ENV", raising=False)

    manager = DomainPackManager(
        workspace,
        config=DomainPacksConfig(disabled=["disabled"], active=["ok", "disabled", "missing_caps"]),
        builtin_dir=tmp_path / "empty",
    )
    packs = {pack.id: pack for pack in manager.list_packs()}

    assert packs["ok"].status == "available"
    assert packs["ok"].active is True
    assert packs["disabled"].status == "unavailable"
    assert packs["disabled"].active is False
    assert packs["disabled"].unavailable_reason == "disabled by config"
    assert packs["manifest_off"].unavailable_reason == "disabled by manifest"
    assert packs["missing_caps"].unavailable_reason == "missing CAPABILITIES.md"
    assert "CLI: definitely-not-openhome-bin" in packs["missing_bin"].unavailable_reason
    assert "ENV: OPENHOME_TEST_MISSING_ENV" in packs["missing_env"].unavailable_reason


def test_context_prompt_injects_summary_and_active_capabilities(tmp_path: Path) -> None:
    root = tmp_path / "domain_packs"
    _write_pack(
        root,
        "research",
        pack_id="research",
        name="Research",
        capabilities=["search_sources"],
        capabilities_text="# Research Capabilities\n\nUse sources carefully.",
    )
    _write_pack(
        root,
        "office",
        pack_id="office",
        name="Office",
        capabilities=["draft_docs"],
        capabilities_text="# Office Capabilities\n\nDraft docs.",
    )

    builder = ContextBuilder(
        tmp_path,
        domain_packs_config=DomainPacksConfig(active=["research"], max_capability_chars=32),
    )
    prompt = builder.build_system_prompt()

    assert "# Domain Packs" in prompt
    assert "`research` [workspace] Research v0.1.0: active" in prompt
    assert "`office` [workspace] Office v0.1.0: available" in prompt
    assert "# Active Domain Packs" in prompt
    assert "# Research Capabilities" in prompt
    assert "[Domain capabilities truncated]" in prompt
    assert "# Office Capabilities" not in prompt


def test_context_prompt_omits_domain_section_when_disabled_or_empty(tmp_path: Path) -> None:
    empty_prompt = ContextBuilder(tmp_path).build_system_prompt()
    assert "# Domain Packs" not in empty_prompt

    _write_pack(
        tmp_path / "domain_packs",
        "research",
        pack_id="research",
        name="Research",
        capabilities_text="# Research",
    )
    disabled_prompt = ContextBuilder(
        tmp_path,
        domain_packs_config=DomainPacksConfig(enabled=False),
    ).build_system_prompt()
    assert "# Domain Packs" not in disabled_prompt


def test_zero_capability_limit_keeps_full_active_context(tmp_path: Path) -> None:
    _write_pack(
        tmp_path / "domain_packs",
        "research",
        pack_id="research",
        name="Research",
        capabilities_text="# Research\n\n" + ("Detailed capability text. " * 20),
    )

    manager = DomainPackManager(
        tmp_path,
        config=DomainPacksConfig(active=["research"], max_capability_chars=0),
    )
    active_context = manager.build_active_context()

    assert "Detailed capability text." in active_context
    assert "[Domain capabilities truncated]" not in active_context
