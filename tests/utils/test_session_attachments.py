from pathlib import Path

from OriginAgent.config.loader import get_config_path, set_config_path
from OriginAgent.config.paths import get_media_dir
from OriginAgent.utils import session_attachments
from OriginAgent.utils.session_attachments import (
    MAX_SESSION_REPLAY_MEDIA_BYTES,
    merge_turn_media_into_last_assistant,
    stage_media_paths_for_session_replay,
)


def test_stage_media_paths_copies_trusted_workspace_file(tmp_path, monkeypatch) -> None:
    previous = get_config_path()
    set_config_path(tmp_path / "config.json")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(session_attachments, "get_workspace_path", lambda: workspace)
    try:
        source = workspace / "image.png"
        source.write_bytes(b"png")

        staged = stage_media_paths_for_session_replay([str(source)])

        assert len(staged) == 1
        assert Path(staged[0]).is_file()
        assert Path(staged[0]).resolve().is_relative_to(get_media_dir("websocket").resolve())
    finally:
        set_config_path(previous)


def test_stage_media_paths_rejects_urls_untrusted_and_oversized(tmp_path, monkeypatch) -> None:
    previous = get_config_path()
    set_config_path(tmp_path / "config.json")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(session_attachments, "get_workspace_path", lambda: workspace)
    try:
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"png")
        oversized = workspace / "oversized.png"
        oversized.write_bytes(b"0" * (MAX_SESSION_REPLAY_MEDIA_BYTES + 1))

        staged = stage_media_paths_for_session_replay(
            ["https://example.com/a.png", str(outside), str(oversized)]
        )

        assert staged == []
    finally:
        set_config_path(previous)


def test_merge_turn_media_dedupes_into_last_assistant(tmp_path, monkeypatch) -> None:
    previous = get_config_path()
    set_config_path(tmp_path / "config.json")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(session_attachments, "get_workspace_path", lambda: workspace)
    try:
        source = workspace / "image.png"
        source.write_bytes(b"png")
        messages = [{"role": "assistant", "content": "done"}]

        merge_turn_media_into_last_assistant(messages, [str(source), str(source)], [])

        assert len(messages[0]["media"]) == 1
        assert Path(messages[0]["media"][0]).is_file()
    finally:
        set_config_path(previous)


def test_stage_media_paths_uses_explicit_runtime_workspace_not_default(tmp_path, monkeypatch) -> None:
    previous = get_config_path()
    set_config_path(tmp_path / "config.json")
    runtime_workspace = tmp_path / "runtime-workspace"
    runtime_workspace.mkdir()
    default_workspace = tmp_path / "workspace"
    default_workspace.mkdir()
    monkeypatch.setattr(session_attachments, "get_workspace_path", lambda: runtime_workspace)
    try:
        source = runtime_workspace / "image.png"
        source.write_bytes(b"png")
        default_source = default_workspace / "default.png"
        default_source.write_bytes(b"png")

        staged = stage_media_paths_for_session_replay([str(source), str(default_source)])

        assert len(staged) == 1
        assert Path(staged[0]).is_file()
    finally:
        set_config_path(previous)
