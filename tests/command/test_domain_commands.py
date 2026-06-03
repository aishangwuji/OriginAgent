"""Tests for domain CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from OriginAgent.cli.commands import app


runner = CliRunner()


def test_domain_init_creates_pack_structure(tmp_path: Path, monkeypatch) -> None:
    """Simulate the init command with non-interactive mode."""
    target = tmp_path / "output"
    target.mkdir()
    monkeypatch.chdir(target)

    result = runner.invoke(app, [
        "domain", "init", "testpack",
        "--name", "Test Pack",
        "--description", "A test pack",
        "--no-tools",
        "--no-runtime",
        "--no-skills",
        "--no-workflows",
        "--no-evals",
        "--no-tests",
    ])

    assert result.exit_code == 0, result.output
    pack_dir = target / "domain_packs" / "testpack"
    assert pack_dir.exists()
    assert (pack_dir / "domain_pack.yaml").exists()
    assert (pack_dir / "CAPABILITIES.md").exists()
    assert (pack_dir / "__init__.py").exists()
    # tools/ should NOT exist since we passed --no-tools
    assert not (pack_dir / "tools").exists()


def test_domain_list_shows_packs(tmp_path: Path, monkeypatch) -> None:
    """list should return valid JSON or table output without crashing."""
    monkeypatch.chdir(tmp_path)
    # Create a workspace with one pack
    pack_dir = tmp_path / "domain_packs" / "testpack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "domain_pack.yaml").write_text(
        "id: testpack\nname: Test Pack\nversion: 0.1.0\n", encoding="utf-8"
    )
    (pack_dir / "CAPABILITIES.md").write_text("# Test\n", encoding="utf-8")

    # Create a minimal config that points to the test workspace
    config_dir = tmp_path / ".originagent"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.json"
    config_file.write_text(
        '{"agents": {"defaults": {"workspace": "' + tmp_path.as_posix() + '"}}}',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["domain", "list", "--config", str(config_file)])
    assert result.exit_code == 0, result.output
    assert "testpack" in result.output


def test_domain_init_non_interactive_with_all_components(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "output"
    target.mkdir()
    monkeypatch.chdir(target)

    result = runner.invoke(app, [
        "domain", "init", "fullpack",
        "--name", "Full Pack",
        "--description", "All components",
    ])

    assert result.exit_code == 0, result.output
    pack_dir = target / "domain_packs" / "fullpack"
    assert (pack_dir / "tools" / "example_tool.py").exists()
    assert (pack_dir / "runtime" / "contribution.py").exists()
    assert (pack_dir / "skills" / "example-skill" / "SKILL.md").exists()
    assert (pack_dir / "tests" / "test_domain_pack.py").exists()
    assert (pack_dir / "tests" / "test_tools.py").exists()


def test_domain_validate_valid_pack(tmp_path: Path, monkeypatch) -> None:
    pack_dir = tmp_path / "my_pack"
    pack_dir.mkdir()
    (pack_dir / "domain_pack.yaml").write_text(
        "id: my_pack\nname: My Pack\nversion: 0.1.0\n", encoding="utf-8"
    )
    (pack_dir / "CAPABILITIES.md").write_text("# My Pack\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["domain", "validate", str(pack_dir)])
    assert result.exit_code == 0
    assert "valid" in result.output.lower() or "available" in result.output.lower()


def test_domain_validate_invalid_pack(tmp_path: Path, monkeypatch) -> None:
    pack_dir = tmp_path / "bad_pack"
    pack_dir.mkdir()
    (pack_dir / "domain_pack.yaml").write_text(
        "id: bad_pack\nname: \nversion: \n", encoding="utf-8"
    )
    (pack_dir / "CAPABILITIES.md").write_text("# Bad\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["domain", "validate", str(pack_dir)])
    assert "invalid" in result.output.lower()
