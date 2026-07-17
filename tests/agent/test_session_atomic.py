"""Tests for atomic session save and corrupt-file repair."""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from OriginAgent.session.manager import Session, SessionManager


class TestAtomicSave:
    def test_save_creates_valid_jsonl(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        session = Session(key="test:1")
        session.add_message("user", "hello")
        session.add_message("assistant", "hi")

        mgr.save(session)

        path = mgr._get_session_path("test:1")
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

        meta = json.loads(lines[0])
        assert meta["_type"] == "metadata"
        assert meta["key"] == "test:1"

        msg1 = json.loads(lines[1])
        assert msg1["role"] == "user"
        assert msg1["content"] == "hello"

    def test_no_tmp_file_left_after_successful_save(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        session = Session(key="test:clean")
        mgr.save(session)

        tmp_files = list(mgr.sessions_dir.glob("*.tmp"))
        assert tmp_files == []

    def test_tmp_file_cleaned_up_on_write_failure(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        session = Session(key="test:fail")
        path = mgr._get_session_path("test:fail")
        tmp_path_file = path.with_suffix(".jsonl.tmp")

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path_file.write_text("stale")

        class BadMessage:
            def __init__(self, data):
                self.data = data

        original_dumps = json.dumps

        def failing_dumps(obj, **kwargs):
            if isinstance(obj, dict) and obj.get("role") == "assistant":
                raise OSError("simulated disk full")
            return original_dumps(obj, **kwargs)

        session = Session(key="test:fail")
        session.messages = [
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": "will fail"},
        ]

        import unittest.mock
        with unittest.mock.patch("OriginAgent.session.manager.json.dumps", side_effect=failing_dumps):
            try:
                mgr.save(session)
            except OSError:
                pass

        assert not tmp_path_file.exists()

    def test_overwrite_preserves_latest_data(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        session = Session(key="test:overwrite")

        session.add_message("user", "first")
        mgr.save(session)

        session.add_message("user", "second")
        mgr.save(session)

        mgr.invalidate("test:overwrite")
        loaded = mgr.get_or_create("test:overwrite")
        assert len(loaded.messages) == 2
        assert loaded.messages[0]["content"] == "first"
        assert loaded.messages[1]["content"] == "second"

    def test_consecutive_saves_are_consistent(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        session = Session(key="test:consistency")

        for i in range(5):
            session.add_message("user", f"msg{i}")
            mgr.save(session)

        mgr.invalidate("test:consistency")
        loaded = mgr.get_or_create("test:consistency")
        assert len(loaded.messages) == 5
        for i in range(5):
            assert loaded.messages[i]["content"] == f"msg{i}"

    # ─── Windows PermissionError retry (WinError 5) ──────────────────────
    #
    # On Windows, os.replace can fail with PermissionError [WinError 5]
    # when the target file is briefly locked by another process (antivirus,
    # search indexer, concurrent reader). This is a transient error — the
    # save should retry with backoff instead of crashing the turn.

    def test_save_retries_on_permission_error_then_succeeds(self, tmp_path: Path):
        """os.replace fails once with PermissionError, then succeeds on retry."""
        import unittest.mock
        from unittest.mock import patch

        mgr = SessionManager(tmp_path)
        session = Session(key="test:retry-ok")
        session.add_message("user", "hello")

        call_count = {"n": 0}
        original_replace = os.replace

        def flaky_replace(src, dst):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise PermissionError(
                    "[WinError 5] 拒绝访问。",
                    str(src), str(dst),
                )
            return original_replace(src, dst)

        with patch("OriginAgent.session.manager.os.replace", side_effect=flaky_replace):
            mgr.save(session)

        # Should have retried at least once
        assert call_count["n"] >= 2
        # File should exist with correct content
        path = mgr._get_session_path("test:retry-ok")
        assert path.exists()
        loaded = mgr.get_or_create("test:retry-ok")
        assert len(loaded.messages) == 1

    def test_save_fails_after_max_retries(self, tmp_path: Path):
        """When os.replace persistently fails, save raises PermissionError."""
        from unittest.mock import patch

        mgr = SessionManager(tmp_path)
        session = Session(key="test:retry-fail")
        session.add_message("user", "data")

        def always_fail(src, dst):
            raise PermissionError(
                "[WinError 5] 拒绝访问。",
                str(src), str(dst),
            )

        with patch("OriginAgent.session.manager.os.replace", side_effect=always_fail):
            with pytest.raises(PermissionError):
                mgr.save(session)

        # Tmp file should be cleaned up after persistent failure
        path = mgr._get_session_path("test:retry-fail")
        tmp = path.with_suffix(".jsonl.tmp")
        assert not tmp.exists()

    def test_save_retry_does_not_corrupt_data(self, tmp_path: Path):
        """After a successful retry, the saved data is intact."""
        import unittest.mock
        from unittest.mock import patch

        mgr = SessionManager(tmp_path)
        session = Session(key="test:retry-integrity")
        session.add_message("user", "first")
        mgr.save(session)

        session.add_message("assistant", "second")
        call_count = {"n": 0}
        original_replace = os.replace

        def flaky_replace(src, dst):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise PermissionError("[WinError 5]", str(src), str(dst))
            return original_replace(src, dst)

        with patch("OriginAgent.session.manager.os.replace", side_effect=flaky_replace):
            mgr.save(session)

        mgr.invalidate("test:retry-integrity")
        loaded = mgr.get_or_create("test:retry-integrity")
        assert len(loaded.messages) == 2
        assert loaded.messages[0]["content"] == "first"
        assert loaded.messages[1]["content"] == "second"


class TestRepairCorruptFile:
    def _write_corrupt_jsonl(self, path: Path, lines: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_truncated_last_line_recovered(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("test:trunc")

        valid_meta = json.dumps({
            "_type": "metadata",
            "key": "test:trunc",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "metadata": {},
            "last_consolidated": 0,
        })
        valid_msg = json.dumps({"role": "user", "content": "hello"})

        self._write_corrupt_jsonl(path, [
            valid_meta,
            valid_msg,
            '{"role": "assistant", "content": "partial...',
        ])

        session = mgr._load("test:trunc")
        assert session is not None
        assert len(session.messages) == 1
        assert session.messages[0]["content"] == "hello"

    def test_corrupt_metadata_line_skipped(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("test:badmeta")

        self._write_corrupt_jsonl(path, [
            "NOT VALID JSON!!!",
            '{"role": "user", "content": "survived"}',
        ])

        session = mgr._load("test:badmeta")
        assert session is not None
        assert len(session.messages) == 1
        assert session.messages[0]["content"] == "survived"

    def test_all_corrupt_lines_returns_none(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("test:allbad")

        self._write_corrupt_jsonl(path, [
            "garbage line 1",
            "garbage line 2",
            "{{invalid json",
        ])

        session = mgr._load("test:allbad")
        assert session is None

    def test_empty_file_returns_empty_session(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("test:empty")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

        session = mgr._load("test:empty")
        assert session is not None
        assert session.messages == []
        assert session.key == "test:empty"

    def test_repair_preserves_valid_messages_amid_corruption(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("test:mixed")

        self._write_corrupt_jsonl(path, [
            json.dumps({"_type": "metadata", "key": "test:mixed",
                        "created_at": datetime.now().isoformat(),
                        "updated_at": datetime.now().isoformat(),
                        "metadata": {}, "last_consolidated": 0}),
            "BROKEN",
            json.dumps({"role": "user", "content": "msg1"}),
            '{"role": "assistant", "content": "broken',
            json.dumps({"role": "user", "content": "msg2"}),
        ])

        session = mgr._load("test:mixed")
        assert session is not None
        assert len(session.messages) == 2
        assert session.messages[0]["content"] == "msg1"
        assert session.messages[1]["content"] == "msg2"

    def test_repair_with_bad_timestamp_uses_fallback(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("test:badts")

        self._write_corrupt_jsonl(path, [
            json.dumps({"_type": "metadata", "key": "test:badts",
                        "created_at": "not-a-date",
                        "updated_at": "also-bad",
                        "metadata": {}, "last_consolidated": 5}),
            json.dumps({"role": "user", "content": "hi"}),
        ])

        session = mgr._load("test:badts")
        assert session is not None
        assert session.last_consolidated == 5
        assert isinstance(session.created_at, datetime)

    def test_read_session_file_repairs_corrupt_jsonl(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("test:read-repair")

        self._write_corrupt_jsonl(path, [
            json.dumps({
                "_type": "metadata",
                "key": "test:read-repair",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "metadata": {"source": "repair"},
                "last_consolidated": 0,
            }),
            json.dumps({"role": "user", "content": "survived"}),
            '{"role": "assistant", "content": "partial...',
        ])

        payload = mgr.read_session_file("test:read-repair")
        assert payload is not None
        assert payload["key"] == "test:read-repair"
        assert payload["metadata"] == {"source": "repair"}
        assert payload["messages"] == [{"role": "user", "content": "survived"}]

    def test_list_sessions_keeps_repaired_corrupt_file(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("test:list-repair")

        self._write_corrupt_jsonl(path, [
            "NOT VALID JSON",
            json.dumps({
                "_type": "metadata",
                "key": "test:list-repair",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "metadata": {},
                "last_consolidated": 0,
            }),
            json.dumps({"role": "user", "content": "hello"}),
        ])

        sessions = mgr.list_sessions()
        assert any(s["key"] == "test:list-repair" for s in sessions)

    def test_get_or_create_returns_new_session_for_corrupt_file(self, tmp_path: Path):
        mgr = SessionManager(tmp_path)
        path = mgr._get_session_path("test:fallback")

        self._write_corrupt_jsonl(path, ["{{{{"])

        session = mgr.get_or_create("test:fallback")
        assert session is not None
        assert session.messages == []
        assert session.key == "test:fallback"
