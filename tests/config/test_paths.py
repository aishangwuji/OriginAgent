"""Tests for legacy ``.originagent`` vs ``.OriginAgent`` case handling (Task B8)."""

from pathlib import Path

import pytest

from OriginAgent.config import paths as paths_module
from OriginAgent.config import loader
from OriginAgent.config.loader import set_config_path
from OriginAgent.config.paths import (
    check_and_warn_legacy_case_mismatch,
    get_legacy_sessions_dir,
)


@pytest.fixture(autouse=True)
def reset_config_path():
    set_config_path(None)
    yield
    set_config_path(None)


def test_app_data_dir_name_is_lowercase() -> None:
    """APP_DATA_DIR_NAME must be lowercase ``.originagent`` (cross-platform safe)."""
    assert loader.APP_DATA_DIR_NAME == ".originagent"


def test_legacy_sessions_dir_prefers_lowercase_when_exists(
    monkeypatch, tmp_path: Path
) -> None:
    """When lowercase ``~/.originagent/sessions`` exists, return it."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    lowercase_sessions = tmp_path / ".originagent" / "sessions"
    lowercase_sessions.mkdir(parents=True)

    assert get_legacy_sessions_dir() == lowercase_sessions


def test_legacy_sessions_dir_falls_back_to_uppercase(
    monkeypatch, tmp_path: Path
) -> None:
    """When lowercase missing but uppercase ``~/.OriginAgent/sessions`` exists, return uppercase.

    Uses a case-sensitive ``exists`` stub (comparing ``as_posix()`` strings) so the
    fallback branch is exercised deterministically on case-insensitive filesystems
    (Windows), where ``Path.__eq__`` would otherwise treat the two cases as equal.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    uppercase_sessions = tmp_path / ".OriginAgent" / "sessions"
    existing = {uppercase_sessions.as_posix()}

    def fake_exists(self: Path) -> bool:
        return self.as_posix() in existing

    monkeypatch.setattr(Path, "exists", fake_exists)

    assert get_legacy_sessions_dir() == uppercase_sessions


def test_legacy_sessions_dir_defaults_to_lowercase_when_neither_exists(
    monkeypatch, tmp_path: Path
) -> None:
    """When neither directory exists, return the lowercase default (new users)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert get_legacy_sessions_dir() == tmp_path / ".originagent" / "sessions"


def test_warn_legacy_case_mismatch_when_only_uppercase_exists(
    monkeypatch, tmp_path: Path
) -> None:
    """Warn (and return True) when legacy uppercase dir exists without a lowercase counterpart."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    uppercase_dir = tmp_path / ".OriginAgent"
    existing = {uppercase_dir.as_posix()}

    def fake_exists(self: Path) -> bool:
        return self.as_posix() in existing

    monkeypatch.setattr(Path, "exists", fake_exists)

    warnings: list[tuple] = []
    monkeypatch.setattr(paths_module.logger, "warning", lambda *a, **kw: warnings.append((a, kw)))

    assert check_and_warn_legacy_case_mismatch() is True
    assert len(warnings) == 1


def test_warn_legacy_case_mismatch_silent_when_neither_exists(
    monkeypatch, tmp_path: Path
) -> None:
    """Return False (no warning) when no uppercase legacy dir is present."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    warnings: list[tuple] = []
    monkeypatch.setattr(paths_module.logger, "warning", lambda *a, **kw: warnings.append((a, kw)))

    assert check_and_warn_legacy_case_mismatch() is False
    assert warnings == []
