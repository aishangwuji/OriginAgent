from __future__ import annotations

from pathlib import Path

from OriginAgent.agent.context import ContextBuilder
from OriginAgent.agent.domain_packs import DomainPackManager
from OriginAgent.config.schema import DomainPacksConfig


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


def _write_domain_skill(pack_dir: Path, name: str, body: str = "# Skill\n") -> Path:
    skill_dir = pack_dir / "skills" / name
    skill_dir.mkdir(parents=True)
    path = skill_dir / "SKILL.md"
    path.write_text(body, encoding="utf-8")
    return path


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
    assert "id" in packs["missing-id"].unavailable_reason
    assert packs["bad-id"].status == "invalid"
    assert "pattern" in packs["bad-id"].unavailable_reason
    # When name is empty, Pydantic validation fails before manifest id is extracted.
    # The pack id falls back to the directory name ("missing-name") since _invalid
    # cannot read the YAML id.
    assert packs.get("missing-name") is not None
    assert packs["missing-name"].status == "invalid"
    assert "name" in packs["missing-name"].unavailable_reason


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
        requires_bins=["definitely-not-originagent-bin"],
        capabilities_text="# Bin",
    )
    _write_pack(
        root,
        "missing-env",
        pack_id="missing_env",
        requires_env=["ORIGINAGENT_TEST_MISSING_ENV"],
        capabilities_text="# Env",
    )
    _write_pack(root, "disabled", pack_id="disabled", capabilities_text="# Disabled")
    monkeypatch.delenv("ORIGINAGENT_TEST_MISSING_ENV", raising=False)

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
    assert "CLI: definitely-not-originagent-bin" in packs["missing_bin"].unavailable_reason
    assert "ENV: ORIGINAGENT_TEST_MISSING_ENV" in packs["missing_env"].unavailable_reason


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

    assert "# Self Model" in prompt
    assert "- Active domains: `research`" in prompt
    assert "`office`" not in prompt
    assert "# Domain Packs" not in prompt
    assert "# Active Domain Packs" not in prompt


def test_context_prompt_omits_domain_section_when_disabled_or_empty(tmp_path: Path) -> None:
    empty_manager = DomainPackManager(tmp_path, builtin_dir=tmp_path / "empty")
    empty_prompt = ContextBuilder(tmp_path, domain_pack_manager=empty_manager).build_system_prompt()
    assert "# Self Model" in empty_prompt
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
    assert "# Self Model" in disabled_prompt
    assert "# Domain Packs" not in disabled_prompt


def test_builtin_smart_home_domain_pack_is_available_and_explicitly_activated(
    tmp_path: Path,
) -> None:
    manager = DomainPackManager(tmp_path)
    pack = manager.get_pack("smart_home")

    assert pack is not None
    assert pack.source == "builtin"
    assert pack.status == "available"
    assert pack.active is False
    assert [tool.id for tool in pack.tools] == [
        "originagent_device_lighting_set_power",
        "originagent_device_lighting_set_brightness",
        "originagent_device_lighting_set_color_temperature",
    ]
    assert {skill.virtual_id for skill in pack.skills} == {
        "domain:smart_home/lighting-control",
        "domain:smart_home/safety-confirmation",
        "domain:smart_home/automation-design",
    }
    assert "lighting" in pack.triggers
    assert manager.active_skill_entries() == []
    assert manager.build_active_context() == ""

    active_manager = DomainPackManager(tmp_path, config=DomainPacksConfig(active=["smart_home"]))
    active_pack = active_manager.get_pack("smart_home")

    assert active_pack is not None
    assert active_pack.active is True
    assert [entry["name"] for entry in active_manager.active_skill_entries()] == [
        "domain:smart_home/lighting-control",
        "domain:smart_home/safety-confirmation",
        "domain:smart_home/automation-design",
    ]
    assert "originagent_device_lighting_set_power" in active_manager.build_active_context()

    disabled_manager = DomainPackManager(
        tmp_path,
        config=DomainPacksConfig(active=["smart_home"], disabled=["smart_home"]),
    )
    disabled_pack = disabled_manager.get_pack("smart_home")

    assert disabled_pack is not None
    assert disabled_pack.status == "unavailable"
    assert disabled_pack.active is False
    assert disabled_pack.unavailable_reason == "disabled by config"


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


def test_manifest_parses_domain_skills_and_tools_without_raising(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / "domain_packs",
        "research",
        pack_id="research",
        name="Research",
        capabilities_text="# Research",
    )
    _write_domain_skill(pack, "source-synthesis")
    tools_dir = pack / "tools"
    tools_dir.mkdir()
    (tools_dir / "search.py").write_text("# placeholder\n", encoding="utf-8")
    manifest = pack / "domain_pack.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "\n"
        + "\n".join(
            [
                "skills:",
                "  - source-synthesis",
                "  - Missing Skill",
                "  - missing-file",
                "tools:",
                "  - id: research_search",
                "    module: tools.search",
                "    class: ResearchSearchTool",
                "    permissions: []",
                "    audit: minimal",
                "  - id: research_missing_module",
                "    module: tools.nonexistent",
                "    class: ResearchSearchTool",
                "    permissions: []",
                "    audit: minimal",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    manager = DomainPackManager(
        tmp_path,
        config=DomainPacksConfig(active=["research"]),
    )
    pack_state = manager.get_pack("research")

    assert pack_state is not None
    assert [skill.id for skill in pack_state.skills] == [
        "source-synthesis",
        "Missing Skill",
        "missing-file",
    ]
    assert pack_state.skills[0].virtual_id == "domain:research/source-synthesis"
    assert pack_state.skills[0].status == "available"
    assert pack_state.skills[1].status == "skipped"
    assert "skill id must match" in pack_state.skills[1].unavailable_reason
    assert pack_state.skills[2].status == "skipped"
    assert "missing SKILL.md" in pack_state.skills[2].unavailable_reason
    assert [entry["name"] for entry in manager.active_skill_entries()] == [
        "domain:research/source-synthesis"
    ]

    tools = {tool.id: tool for tool in pack_state.tools}
    assert tools["research_search"].status == "available"
    assert tools["research_search"].permissions == ()
    assert tools["research_missing_module"].status == "skipped"
    assert "missing tool module file" in tools["research_missing_module"].unavailable_reason


def test_manifest_parses_domain_runtime_contribution(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / "domain_packs",
        "research",
        pack_id="research",
        name="Research",
        capabilities_text="# Research",
    )
    runtime_dir = pack / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "contribution.py").write_text(
        "from OriginAgent.agent.domain_packs import DomainRuntimeContribution\n"
        "def build_runtime_contribution(context):\n"
        "    return DomainRuntimeContribution(tool_context={'demo': context.pack.id})\n",
        encoding="utf-8",
    )
    manifest = pack / "domain_pack.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "\nruntime:\n  module: runtime.contribution\n  factory: build_runtime_contribution\n",
        encoding="utf-8",
    )

    manager = DomainPackManager(tmp_path, config=DomainPacksConfig(active=["research"]))
    pack_state = manager.get_pack("research")
    contributions = manager.active_runtime_contributions(
        workspace=tmp_path,
        config=object(),
    )

    assert pack_state is not None
    assert pack_state.runtime is not None
    assert pack_state.runtime.status == "available"
    assert [contribution.tool_context for contribution in contributions] == [{"demo": "research"}]
    assert contributions[0].action_continuity_provider is None
    assert contributions[0].action_continuity_writeback_adapter is None


def test_inactive_domain_pack_does_not_expose_skill_entries_or_tools(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / "domain_packs",
        "research",
        pack_id="research",
        name="Research",
        capabilities_text="# Research",
    )
    _write_domain_skill(pack, "source-synthesis")
    tools_dir = pack / "tools"
    tools_dir.mkdir()
    (tools_dir / "search.py").write_text("# placeholder\n", encoding="utf-8")
    (pack / "domain_pack.yaml").write_text(
        (pack / "domain_pack.yaml").read_text(encoding="utf-8")
        + "\nskills:\n  - source-synthesis\n"
        + "tools:\n"
        + "  - id: research_search\n"
        + "    module: tools.search\n"
        + "    class: ResearchSearchTool\n"
        + "    permissions: []\n",
        encoding="utf-8",
    )

    manager = DomainPackManager(tmp_path)

    assert manager.active_skill_entries() == []
    assert manager.active_tool_declarations() == []


def test_validator_uses_pydantic_schema_for_structured_errors(tmp_path: Path) -> None:
    """Verify that the validator now catches Pydantic-level errors."""
    from OriginAgent.agent.domain_packs import DomainPackRuntimeConfig, DomainPackValidator

    root = tmp_path / "domain_packs"
    (root / "bad").mkdir(parents=True)
    (root / "bad" / "domain_pack.yaml").write_text(
        "id: good_id\nname: Good\nversion: bad_version\n", encoding="utf-8"
    )
    (root / "bad" / "CAPABILITIES.md").write_text("# Bad\n", encoding="utf-8")
    pack = DomainPackValidator(runtime_config=DomainPackRuntimeConfig(), strict_declarations=True).validate_pack(root / "bad", source="workspace")
    assert pack.status == "invalid"
    assert "version" in pack.unavailable_reason or "version" in pack.validation_summary
