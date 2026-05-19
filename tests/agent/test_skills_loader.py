"""Tests for OpenHome.agent.skills.SkillsLoader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from OpenHome.agent.domain_packs import DomainPackManager
from OpenHome.agent.skills import SkillsLoader
from OpenHome.config.schema import DomainPacksConfig


def _write_skill(
    base: Path,
    name: str,
    *,
    metadata_json: dict | None = None,
    body: str = "# Skill\n",
) -> Path:
    """Create ``base / name / SKILL.md`` with optional OpenHome metadata JSON."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True)
    lines = ["---"]
    if metadata_json is not None:
        payload = json.dumps({"OpenHome": metadata_json}, separators=(",", ":"))
        lines.append(f'metadata: {payload}')
    lines.extend(["---", "", body])
    path = skill_dir / "SKILL.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_list_skills_empty_when_skills_dir_missing(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    assert loader.list_skills(filter_unavailable=False) == []


def test_list_skills_empty_when_skills_dir_exists_but_empty(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "skills").mkdir(parents=True)
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    assert loader.list_skills(filter_unavailable=False) == []


def test_list_skills_workspace_entry_shape_and_source(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    skill_path = _write_skill(skills_root, "alpha", body="# Alpha")
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    entries = loader.list_skills(filter_unavailable=False)
    assert entries == [
        {"name": "alpha", "path": str(skill_path), "source": "workspace"},
    ]


def test_list_skills_skips_non_directories_and_missing_skill_md(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    (skills_root / "not_a_dir.txt").write_text("x", encoding="utf-8")
    (skills_root / "no_skill_md").mkdir()
    ok_path = _write_skill(skills_root, "ok", body="# Ok")
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    entries = loader.list_skills(filter_unavailable=False)
    names = {entry["name"] for entry in entries}
    assert names == {"ok"}
    assert entries[0]["path"] == str(ok_path)


def test_list_skills_workspace_shadows_builtin_same_name(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    ws_path = _write_skill(ws_skills, "dup", body="# Workspace wins")

    builtin = tmp_path / "builtin"
    _write_skill(builtin, "dup", body="# Builtin")

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    entries = loader.list_skills(filter_unavailable=False)
    assert len(entries) == 1
    assert entries[0]["source"] == "workspace"
    assert entries[0]["path"] == str(ws_path)


def test_list_skills_merges_workspace_and_builtin(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    ws_path = _write_skill(ws_skills, "ws_only", body="# W")
    builtin = tmp_path / "builtin"
    bi_path = _write_skill(builtin, "bi_only", body="# B")

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    entries = sorted(loader.list_skills(filter_unavailable=False), key=lambda item: item["name"])
    assert entries == [
        {"name": "bi_only", "path": str(bi_path), "source": "builtin"},
        {"name": "ws_only", "path": str(ws_path), "source": "workspace"},
    ]


def test_list_skills_builtin_omitted_when_dir_missing(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    ws_path = _write_skill(ws_skills, "solo", body="# S")
    missing_builtin = tmp_path / "no_such_builtin"

    loader = SkillsLoader(workspace, builtin_skills_dir=missing_builtin)
    entries = loader.list_skills(filter_unavailable=False)
    assert entries == [{"name": "solo", "path": str(ws_path), "source": "workspace"}]


def test_list_skills_filter_unavailable_excludes_unmet_bin_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    _write_skill(
        skills_root,
        "needs_bin",
        metadata_json={"requires": {"bins": ["OpenHome_test_fake_binary"]}},
    )
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    def fake_which(cmd: str) -> str | None:
        if cmd == "OpenHome_test_fake_binary":
            return None
        return "/usr/bin/true"

    monkeypatch.setattr("OpenHome.agent.skills.shutil.which", fake_which)

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    assert loader.list_skills(filter_unavailable=True) == []


def test_list_skills_filter_unavailable_includes_when_bin_requirement_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    skill_path = _write_skill(
        skills_root,
        "has_bin",
        metadata_json={"requires": {"bins": ["OpenHome_test_fake_binary"]}},
    )
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    def fake_which(cmd: str) -> str | None:
        if cmd == "OpenHome_test_fake_binary":
            return "/fake/OpenHome_test_fake_binary"
        return None

    monkeypatch.setattr("OpenHome.agent.skills.shutil.which", fake_which)

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    entries = loader.list_skills(filter_unavailable=True)
    assert entries == [
        {"name": "has_bin", "path": str(skill_path), "source": "workspace"},
    ]


def test_list_skills_filter_unavailable_false_keeps_unmet_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    skill_path = _write_skill(
        skills_root,
        "blocked",
        metadata_json={"requires": {"bins": ["OpenHome_test_fake_binary"]}},
    )
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    monkeypatch.setattr("OpenHome.agent.skills.shutil.which", lambda _cmd: None)

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    entries = loader.list_skills(filter_unavailable=False)
    assert entries == [
        {"name": "blocked", "path": str(skill_path), "source": "workspace"},
    ]


def test_list_skills_filter_unavailable_excludes_unmet_env_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    _write_skill(
        skills_root,
        "needs_env",
        metadata_json={"requires": {"env": ["OPENHOME_SKILLS_TEST_ENV_VAR"]}},
    )
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    monkeypatch.delenv("OPENHOME_SKILLS_TEST_ENV_VAR", raising=False)

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    assert loader.list_skills(filter_unavailable=True) == []


def test_list_skills_openclaw_metadata_parsed_for_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    skill_dir = skills_root / "openclaw_skill"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    oc_payload = json.dumps({"openclaw": {"requires": {"bins": ["OpenHome_oc_bin"]}}}, separators=(",", ":"))
    skill_path.write_text(
        "\n".join(["---", f"metadata: {oc_payload}", "---", "", "# OC"]),
        encoding="utf-8",
    )
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    monkeypatch.setattr("OpenHome.agent.skills.shutil.which", lambda _cmd: None)

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    assert loader.list_skills(filter_unavailable=True) == []

    monkeypatch.setattr(
        "OpenHome.agent.skills.shutil.which",
        lambda cmd: "/x" if cmd == "OpenHome_oc_bin" else None,
    )
    entries = loader.list_skills(filter_unavailable=True)
    assert entries == [
        {"name": "openclaw_skill", "path": str(skill_path), "source": "workspace"},
    ]


def test_disabled_skills_excluded_from_list(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    _write_skill(ws_skills, "alpha", body="# Alpha")
    beta_path = _write_skill(ws_skills, "beta", body="# Beta")
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin, disabled_skills={"alpha"})
    entries = loader.list_skills(filter_unavailable=False)
    assert len(entries) == 1
    assert entries[0]["name"] == "beta"
    assert entries[0]["path"] == str(beta_path)


def test_disabled_skills_empty_set_no_effect(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    _write_skill(ws_skills, "alpha", body="# Alpha")
    _write_skill(ws_skills, "beta", body="# Beta")
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin, disabled_skills=set())
    entries = loader.list_skills(filter_unavailable=False)
    assert len(entries) == 2


def test_disabled_skills_excluded_from_build_skills_summary(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    _write_skill(ws_skills, "alpha", body="# Alpha")
    _write_skill(ws_skills, "beta", body="# Beta")
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin, disabled_skills={"alpha"})
    summary = loader.build_skills_summary()
    assert "alpha" not in summary
    assert "beta" in summary


def test_disabled_skills_excluded_from_get_always_skills(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    _write_skill(ws_skills, "alpha", metadata_json={"always": True}, body="# Alpha")
    _write_skill(ws_skills, "beta", metadata_json={"always": True}, body="# Beta")
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin, disabled_skills={"alpha"})
    always = loader.get_always_skills()
    assert "alpha" not in always
    assert "beta" in always


def test_disabled_skill_cannot_be_loaded_directly(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    _write_skill(ws_skills, "alpha", body="# Alpha")
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin, disabled_skills={"alpha"})

    assert loader.load_skill("alpha") is None
    assert loader.load_skills_for_context(["alpha"]) == ""


def test_load_skill_rejects_path_traversal_names(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    _write_skill(ws_skills, "alpha", body="# Alpha")
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)

    assert loader.load_skill("../alpha") is None
    assert loader.load_skill("..\\alpha") is None
    assert loader.load_skill("alpha/beta") is None


# -- multiline description tests (YAML folded > and literal |) -----------------


def test_build_skills_summary_folded_description(tmp_path: Path) -> None:
    """description: > (YAML folded scalar) should be parsed correctly."""
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    skill_dir = ws_skills / "pdf"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\n"
        "name: pdf\n"
        "description: >\n"
        "  Use this skill when visual quality and design identity matter for a PDF.\n"
        "  CREATE (generate from scratch): \"make a PDF\".\n"
        "---\n\n# PDF Skill\n",
        encoding="utf-8",
    )
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    summary = loader.build_skills_summary()
    assert "pdf" in summary
    assert "visual quality" in summary


def test_build_skills_summary_literal_description(tmp_path: Path) -> None:
    """description: | (YAML literal scalar) should be parsed correctly."""
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    skill_dir = ws_skills / "multi"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\n"
        "name: multi\n"
        "description: |\n"
        "  Line one of description.\n"
        "  Line two of description.\n"
        "---\n\n# Multi\n",
        encoding="utf-8",
    )
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    meta = loader.get_skill_metadata("multi")
    assert meta is not None
    desc = meta.get("description")
    assert isinstance(desc, str)
    assert "Line one" in desc
    assert "Line two" in desc


def test_get_skill_metadata_handles_yaml_types(tmp_path: Path) -> None:
    """yaml.safe_load returns native types; always should be True, not 'true'."""
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    skill_dir = ws_skills / "typed"
    skill_dir.mkdir(parents=True)
    payload = json.dumps({"OpenHome": {"requires": {"bins": ["gh"]}, "always": True}}, separators=(",", ":"))
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\n"
        "name: typed\n"
        f"metadata: {payload}\n"
        "always: true\n"
        "---\n\n# Typed\n",
        encoding="utf-8",
    )
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    meta = loader.get_skill_metadata("typed")
    assert meta is not None
    # YAML parsed 'true' to Python True
    assert meta.get("always") is True
    # metadata is a parsed dict, not a JSON string
    assert isinstance(meta.get("metadata"), dict)


def test_domain_pack_skill_uses_virtual_id_and_does_not_shadow_plain_skill(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace_skill = _write_skill(
        workspace / "skills",
        "source-synthesis",
        body="# Workspace Plain Skill\n",
    )
    pack = workspace / "domain_packs" / "research"
    (pack / "skills" / "source-synthesis").mkdir(parents=True)
    (pack / "domain_pack.yaml").write_text(
        "id: research\n"
        "name: Research\n"
        "version: 0.1.0\n"
        "skills:\n"
        "  - source-synthesis\n",
        encoding="utf-8",
    )
    (pack / "CAPABILITIES.md").write_text("# Research\n", encoding="utf-8")
    domain_skill_path = pack / "skills" / "source-synthesis" / "SKILL.md"
    domain_skill_path.write_text(
        "---\nname: source-synthesis\ndescription: Domain skill.\n---\n\n# Domain Skill\n",
        encoding="utf-8",
    )

    manager = DomainPackManager(
        workspace,
        config=DomainPacksConfig(active=["research"]),
        builtin_dir=tmp_path / "empty",
    )
    loader = SkillsLoader(
        workspace,
        builtin_skills_dir=tmp_path / "builtin",
        domain_pack_manager=manager,
    )

    entries = sorted(loader.list_skills(filter_unavailable=False), key=lambda item: item["name"])

    assert entries == [
        {"name": "domain:research/source-synthesis", "path": str(domain_skill_path), "source": "domain:research"},
        {"name": "source-synthesis", "path": str(workspace_skill), "source": "workspace"},
    ]
    assert "# Workspace Plain Skill" in (loader.load_skill("source-synthesis") or "")
    assert "# Domain Skill" in (loader.load_skill("domain:research/source-synthesis") or "")
    assert loader.load_skill("domain:research/../source-synthesis") is None


def test_domain_pack_skill_requirements_and_always_apply_only_when_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "ws"
    pack = workspace / "domain_packs" / "research"
    skill_dir = pack / "skills" / "source-synthesis"
    skill_dir.mkdir(parents=True)
    (pack / "domain_pack.yaml").write_text(
        "id: research\n"
        "name: Research\n"
        "version: 0.1.0\n"
        "skills:\n"
        "  - source-synthesis\n",
        encoding="utf-8",
    )
    (pack / "CAPABILITIES.md").write_text("# Research\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "description: Domain skill.\n"
        "metadata:\n"
        "  OpenHome:\n"
        "    always: true\n"
        "    requires:\n"
        "      env:\n"
        "        - OPENHOME_DOMAIN_SKILL_ENV\n"
        "---\n\n# Domain Skill\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENHOME_DOMAIN_SKILL_ENV", raising=False)
    inactive_manager = DomainPackManager(
        workspace,
        config=DomainPacksConfig(active=[]),
        builtin_dir=tmp_path / "empty",
    )
    inactive_loader = SkillsLoader(
        workspace,
        builtin_skills_dir=tmp_path / "builtin",
        domain_pack_manager=inactive_manager,
    )
    active_manager = DomainPackManager(
        workspace,
        config=DomainPacksConfig(active=["research"]),
        builtin_dir=tmp_path / "empty",
    )
    active_loader = SkillsLoader(
        workspace,
        builtin_skills_dir=tmp_path / "builtin",
        domain_pack_manager=active_manager,
    )

    assert inactive_loader.list_skills(filter_unavailable=False) == []
    assert active_loader.list_skills(filter_unavailable=True) == []
    assert active_loader.get_always_skills() == []

    monkeypatch.setenv("OPENHOME_DOMAIN_SKILL_ENV", "1")
    assert active_loader.get_always_skills() == ["domain:research/source-synthesis"]


def test_builtin_smart_home_domain_skills_load_with_exact_core_tool_names(
    tmp_path: Path,
) -> None:
    manager = DomainPackManager(
        tmp_path,
        config=DomainPacksConfig(active=["smart_home"]),
    )
    loader = SkillsLoader(
        tmp_path,
        builtin_skills_dir=tmp_path / "builtin",
        domain_pack_manager=manager,
    )

    lighting = loader.load_skill("domain:smart_home/lighting-control") or ""
    safety = loader.load_skill("domain:smart_home/safety-confirmation") or ""

    assert "openhome_device_lighting_set_power" in lighting
    assert "openhome_device_lighting_set_brightness" in lighting
    assert "openhome_device_lighting_set_color_temperature" in lighting
    assert "`set_light_power`" not in lighting
    assert "`set_brightness`" not in lighting
    assert "does not replace OpenHome's system safety layer" in safety
    assert "Final confirmation" in safety
    assert loader.load_skill("lighting-control") is None
