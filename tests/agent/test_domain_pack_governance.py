from __future__ import annotations

import json
from pathlib import Path

from OriginAgent.agent.domain_pack_governance import DomainPackGovernanceService
from OriginAgent.agent.domain_packs import DomainPackManager
from OriginAgent.config.schema import DomainPacksConfig
from OriginAgent.config.schema import Config


def _write_pack_source(root: Path, pack_id: str, *, version: str = "0.1.0", with_missing_skill: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    lines = [
        f"id: {pack_id}",
        f"name: {pack_id.title()}",
        f"version: {version}",
    ]
    if with_missing_skill:
        lines.extend([
            "skills:",
            "  - missing-skill",
        ])
    (root / "domain_pack.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "CAPABILITIES.md").write_text(f"# {pack_id.title()}\n", encoding="utf-8")
    return root


def _event_rows(workspace: Path) -> list[dict]:
    path = workspace / "memory" / "domain_pack_events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_install_upgrade_eval_and_uninstall_workspace_domain_pack(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config = Config()
    service = DomainPackGovernanceService(
        workspace,
        config_loader=lambda: config,
        config_saver=lambda _config: None,
    )
    source_v1 = _write_pack_source(tmp_path / "research-v1", "research", version="0.1.0")

    installed = service.install(str(source_v1), reason="seed local pack")

    assert installed.ok is True
    assert installed.status == "installed"
    assert installed.pack is not None
    assert installed.pack["id"] == "research"
    assert installed.pack["source"] == "workspace"
    assert installed.pack["enabled"] is True
    assert installed.pack["active"] is False
    assert (workspace / "domain_packs" / "research" / "domain_pack.yaml").exists()

    activated = service.set_active("research", active=True, reason="make discoverable")
    assert activated.ok is True
    assert activated.pack is not None
    assert activated.pack["active_requested"] is True

    source_v2 = _write_pack_source(tmp_path / "research-v2", "research", version="0.2.0")
    upgraded = service.upgrade("research", str(source_v2), reason="upgrade local pack")

    assert upgraded.ok is True
    assert upgraded.status == "upgraded"
    assert upgraded.pack is not None
    assert upgraded.pack["version"] == "0.2.0"
    assert upgraded.pack["active_requested"] is True

    evaluated = service.eval_pack("research")

    assert evaluated.ok is True
    assert evaluated.eval_result is not None
    assert evaluated.eval_result["status"] == "ok"
    assert evaluated.eval_result["checks"]

    uninstalled = service.uninstall("research", reason="cleanup")

    assert uninstalled.ok is True
    assert uninstalled.status == "uninstalled"
    assert not (workspace / "domain_packs" / "research").exists()
    assert "research" not in config.agents.defaults.domain_packs.active
    assert "research" not in config.agents.defaults.domain_packs.disabled
    assert [event["action"] for event in _event_rows(workspace)] == [
        "install",
        "activate",
        "upgrade",
        "eval",
        "uninstall",
    ]


def test_install_rejects_invalid_pack_declarations(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config = Config()
    service = DomainPackGovernanceService(
        workspace,
        config_loader=lambda: config,
        config_saver=lambda _config: None,
    )
    invalid_source = _write_pack_source(
        tmp_path / "invalid-pack",
        "research",
        with_missing_skill=True,
    )

    result = service.install(str(invalid_source), reason="should fail")

    assert result.ok is False
    assert result.status == "failed"
    assert "missing SKILL.md" in result.error
    assert not (workspace / "domain_packs" / "research").exists()


def test_stats_with_live_manager_preserves_active_runtime_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    pack_dir = workspace / "domain_packs" / "research"
    _write_pack_source(pack_dir, "research")
    manager = DomainPackManager(
        workspace,
        config=DomainPacksConfig(active=["research"]),
        builtin_dir=tmp_path / "empty",
    )
    service = DomainPackGovernanceService(workspace, domain_pack_manager=manager)

    stats = service.stats()

    assert stats["active_domain_pack_count"] == 1
    record = service.get_record("research")
    assert record is not None
    assert record["active"] is True
    assert manager.get_pack("research") is not None
    assert manager.get_pack("research").active is True
