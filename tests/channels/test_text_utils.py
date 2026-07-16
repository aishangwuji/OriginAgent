"""Unit tests for shared channel text/media utilities (C4).

Covers :func:`parse_local_media_ref` extracted from dingtalk/qq to provide a
single source of truth for local-media reference resolution.
"""

from pathlib import Path

from OriginAgent.channels._text_utils import parse_local_media_ref


def test_parse_local_media_ref_empty_returns_none() -> None:
    assert parse_local_media_ref("") is None


def test_parse_local_media_ref_nonexistent_path_returns_none(tmp_path: Path) -> None:
    assert parse_local_media_ref(str(tmp_path / "missing.txt")) is None


def test_parse_local_media_ref_plain_path_resolves(tmp_path: Path) -> None:
    f = tmp_path / "media.bin"
    f.write_bytes(b"hello")
    result = parse_local_media_ref(str(f))
    assert result is not None
    assert result == f
    assert result.read_bytes() == b"hello"


def test_parse_local_media_ref_file_uri_nonexistent_returns_none(tmp_path: Path) -> None:
    # Exercises the file:// parsing branch (urlparse + unquote + is_file).
    uri = (tmp_path / "missing.bin").as_uri()
    assert parse_local_media_ref(uri) is None
