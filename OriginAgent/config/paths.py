"""Runtime path helpers derived from the active config context."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from OriginAgent.config.loader import APP_DATA_DIR_NAME, get_config_path
from OriginAgent.utils.helpers import ensure_dir


def get_data_dir() -> Path:
    """Return the instance-level runtime data directory."""
    return ensure_dir(get_config_path().parent)


def get_runtime_subdir(name: str) -> Path:
    """Return a named runtime subdirectory under the instance data dir."""
    return ensure_dir(get_data_dir() / name)


def get_media_dir(channel: str | None = None) -> Path:
    """Return the media directory, optionally namespaced per channel."""
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_cron_dir() -> Path:
    """Return the cron storage directory."""
    return get_runtime_subdir("cron")


def get_logs_dir() -> Path:
    """Return the logs directory."""
    return get_runtime_subdir("logs")


def get_webui_dir() -> Path:
    """Return the directory for WebUI-only persisted display transcripts."""
    return get_runtime_subdir("webui")


def get_workspace_path(workspace: str | None = None) -> Path:
    """Resolve and ensure the agent workspace path."""
    path = Path(workspace).expanduser() if workspace else get_config_path().parent / "workspace"
    return ensure_dir(path)


def get_workspace_upload_dir(
    workspace: str | Path | None = None,
    channel: str | None = None,
) -> Path:
    """Return the workspace-local upload staging directory."""
    base = ensure_dir(get_workspace_path(workspace) / "uploads")
    return ensure_dir(base / channel) if channel else base


def get_workspace_inbox_dir(
    workspace: str | Path | None = None,
    source: str | None = None,
) -> Path:
    """Return the workspace-local future ingress inbox directory."""
    base = ensure_dir(get_workspace_path(workspace) / "inbox")
    return ensure_dir(base / source) if source else base


def is_default_workspace(workspace: str | Path | None) -> bool:
    """Return whether a workspace resolves to OriginAgent's default workspace path."""
    default = get_config_path().parent / "workspace"
    current = Path(workspace).expanduser() if workspace is not None else default
    return current.resolve(strict=False) == default.resolve(strict=False)


def get_cli_history_path() -> Path:
    """Return the shared CLI history file path."""
    return get_config_path().parent / "history" / "cli_history"


def get_bridge_install_dir() -> Path:
    """Return the shared WhatsApp bridge installation directory."""
    return get_config_path().parent / "bridge"


def get_legacy_sessions_dir() -> Path:
    """Return the legacy global session directory used for migration fallback.

    Prefers the lowercase ``~/.originagent/sessions`` (current convention). On
    case-sensitive filesystems (Linux/macOS), falls back to the legacy uppercase
    ``~/.OriginAgent/sessions`` when the lowercase directory is absent, so users
    upgrading from older releases keep finding their historical sessions. When
    neither exists, the lowercase default is returned for new installs.
    """
    lowercase_path = Path.home() / APP_DATA_DIR_NAME / "sessions"
    uppercase_path = Path.home() / ".OriginAgent" / "sessions"
    if lowercase_path.exists():
        return lowercase_path
    if uppercase_path.exists():
        return uppercase_path
    return lowercase_path


def check_and_warn_legacy_case_mismatch() -> bool:
    """Warn if a legacy uppercase ``~/.OriginAgent`` dir exists without a lowercase counterpart.

    On case-sensitive filesystems an old install may have created ``~/.OriginAgent``
    while the current convention is ``~/.originagent``. This emits a migration hint
    without performing any destructive move (automatic migration is a destructive
    operation and is deferred to the user per project rule 23). Returns True when a
    mismatch was detected and a warning was emitted.
    """
    lowercase_dir = Path.home() / APP_DATA_DIR_NAME
    uppercase_dir = Path.home() / ".OriginAgent"
    if uppercase_dir.exists() and not lowercase_dir.exists():
        logger.warning(
            "Found legacy data directory ~/.OriginAgent (uppercase). "
            "Please rename it to ~/.originagent (lowercase) to match the current "
            "convention. Automatic migration is not performed to avoid data loss."
        )
        return True
    return False
