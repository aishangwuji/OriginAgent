from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import tomllib


def test_source_checkout_import_uses_pyproject_version_without_metadata() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    expected = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    script = textwrap.dedent(
        f"""
        import sys
        import types

        sys.path.insert(0, {str(repo_root)!r})
        fake = types.ModuleType("OpenHome.OpenHome")
        fake.OpenHome = object
        fake.RunResult = object
        sys.modules["OpenHome.OpenHome"] = fake

        import OpenHome

        print(OpenHome.__version__)
        """
    )

    proc = subprocess.run(
        [sys.executable, "-S", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected


def test_originagent_package_alias_imports_from_source_checkout() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    expected = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    script = textwrap.dedent(
        f"""
        import sys
        import types

        sys.path.insert(0, {str(repo_root)!r})
        fake = types.ModuleType("OpenHome.OpenHome")
        fake.OpenHome = object
        fake.RunResult = object
        sys.modules["OpenHome.OpenHome"] = fake

        import OriginAgent

        print(OriginAgent.__version__)
        print(OriginAgent.OriginAgent is object)
        """
    )

    proc = subprocess.run(
        [sys.executable, "-S", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [expected, "True"]


def test_pyproject_exposes_originagent_console_script() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    scripts = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["scripts"]

    assert scripts["originagent"] == "OpenHome.cli.commands:app"
    assert scripts["openhome"] == "OpenHome.cli.commands:app"
