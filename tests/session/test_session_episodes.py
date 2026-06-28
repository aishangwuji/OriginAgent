"""Tests for the Episode-based session model (Phase 1)."""

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from OriginAgent.session.manager import (
    Episode,
    Session,
    SessionManager,
    _serialize_episodes,
    _deserialize_episodes,
)


# Some test runners on Windows may have permission issues with the default
# pytest tmp_path (C:\Users\<user>\AppData\Local\Temp\pytest-of-<user>).
# Use a project-local temp dir for I/O tests as a fallback.
_PROJECT_LOCAL_TMP = Path(__file__).resolve().parent.parent.parent / "_test_tmp"


def _local_tmp_dir() -> Path:
    """Return a writable temporary directory for I/O tests."""
    _PROJECT_LOCAL_TMP.mkdir(parents=True, exist_ok=True)
    try:
        test_dir = Path(tempfile.mkdtemp(dir=str(_PROJECT_LOCAL_TMP)))
        return test_dir
    except (PermissionError, OSError):
        # Last resort: system temp
        return Path(tempfile.mkdtemp())


def _clean_test_dir(path: Path) -> None:
    """Recursively remove a test directory."""
    if path.exists():
        try:
            import shutil
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _fresh_session() -> Session:
    """Return a clean session with no messages or episodes."""
    return Session(key="test:episodes")


def _session_with_messages(count: int, *, key: str = "test:msgs") -> Session:
    """Return a session with *count* user-assistant pairs, bypassing add_message
    so we can test backfill behaviour."""
    s = Session(key=key)
    # Directly manipulate messages list to simulate loading from disk.
    s.episodes = []
    s.active_episode_index = 0
    for i in range(count):
        s.messages.append({
            "role": "user",
            "content": f"user msg {i}",
            "timestamp": datetime.now().isoformat(),
        })
        s.messages.append({
            "role": "assistant",
            "content": f"assistant msg {i}",
            "timestamp": datetime.now().isoformat(),
        })
    # Trigger backfill manually since __post_init__ already ran before messages existed.
    s.__post_init__()
    return s


# ─── Episode Dataclass ───────────────────────────────────────────────────────


class TestEpisodeDataclass:
    def test_episode_default_status_is_active(self):
        ep = Episode(episode_id=str(uuid.uuid4()))
        assert ep.status == "active"

    def test_episode_can_be_closed(self):
        ep = Episode(episode_id=str(uuid.uuid4()), status="closed")
        assert ep.status == "closed"

    def test_episode_requires_episode_id(self):
        with pytest.raises(TypeError):
            Episode()  # type: ignore[call-arg]


# ─── Episode Lifecycle via add_message ───────────────────────────────────────


class TestAddMessageCreatesEpisode:
    def test_episode_created_on_first_add_message(self):
        s = _fresh_session()
        assert len(s.episodes) == 0

        s.add_message("user", "hello")

        assert len(s.episodes) == 1
        assert s.episodes[0].status == "active"
        assert s.episodes[0].msg_start == 0
        assert s.episodes[0].msg_end == 1

    def test_episode_id_set_on_message(self):
        s = _fresh_session()
        s.add_message("user", "hello")
        msg = s.messages[0]
        assert "episode_id" in msg
        assert msg["episode_id"] == s.episodes[0].episode_id

    def test_all_messages_in_same_episode_get_same_id(self):
        s = _fresh_session()
        s.add_message("user", "a")
        s.add_message("assistant", "b")
        s.add_message("user", "c")

        eid = s.episodes[0].episode_id
        for msg in s.messages:
            assert msg["episode_id"] == eid
        assert s.episodes[0].msg_end == 3

    def test_episode_msg_end_grows_with_messages(self):
        s = _fresh_session()
        for i in range(10):
            s.add_message("user", f"msg {i}")
        assert s.episodes[0].msg_end == 10


class TestStartNewEpisode:
    def test_start_new_episode_closes_previous(self):
        s = _fresh_session()
        s.add_message("user", "topic A")
        s.add_message("assistant", "reply A")

        s.start_new_episode(label="topic B")
        s.add_message("user", "topic B")

        assert len(s.episodes) == 2
        assert s.episodes[0].status == "closed"
        assert s.episodes[0].ended_at is not None
        assert s.episodes[1].status == "active"
        assert s.episodes[1].label == "topic B"

    def test_new_episode_messages_get_correct_id(self):
        s = _fresh_session()
        s.add_message("user", "A")
        a_id = s.episodes[0].episode_id

        s.start_new_episode()
        s.add_message("user", "B")
        b_id = s.episodes[1].episode_id

        assert s.messages[0]["episode_id"] == a_id
        assert s.messages[1]["episode_id"] == b_id
        assert a_id != b_id

    def test_start_new_episode_from_empty_session(self):
        """start_new_episode on an empty session creates the first episode."""
        s = _fresh_session()
        ep = s.start_new_episode("first")
        assert len(s.episodes) == 1
        assert ep.status == "active"
        assert ep.label == "first"

    def test_start_new_episode_preserves_previous_indices(self):
        s = _fresh_session()
        for i in range(5):
            s.add_message("user", f"old {i}")
        old_ep = s.episodes[0]

        s.start_new_episode()
        s.add_message("user", "new")

        assert old_ep.msg_start == 0
        assert old_ep.msg_end == 5
        assert s.episodes[1].msg_start == 5
        assert s.episodes[1].msg_end == 6


class TestCloseActiveEpisode:
    def test_close_active_episode_then_add_auto_creates(self):
        s = _fresh_session()
        s.add_message("user", "hello")
        s.close_active_episode()
        assert s.episodes[0].status == "closed"

        s.add_message("user", "world")
        assert len(s.episodes) == 2
        assert s.episodes[1].status == "active"
        assert s.messages[1]["episode_id"] == s.episodes[1].episode_id

    def test_close_active_episode_on_empty_session(self):
        """close on an empty session should not crash."""
        s = _fresh_session()
        s.close_active_episode()  # no-op
        assert len(s.episodes) == 0


# ─── get_episode_history ────────────────────────────────────────────────────


class TestGetEpisodeHistory:
    def test_returns_correct_messages_for_episode(self):
        s = _fresh_session()
        s.add_message("user", "ep0 msg0")
        s.start_new_episode()
        s.add_message("user", "ep1 msg0")
        s.add_message("assistant", "ep1 reply")
        s.start_new_episode()
        s.add_message("user", "ep2 msg0")

        hist0 = s.get_episode_history(s.episodes[0].episode_id)
        assert len(hist0) == 1
        assert hist0[0]["content"] == "ep0 msg0"

        hist1 = s.get_episode_history(s.episodes[1].episode_id)
        assert len(hist1) == 2
        assert hist1[0]["content"] == "ep1 msg0"
        assert hist1[1]["content"] == "ep1 reply"

        hist2 = s.get_episode_history(s.episodes[2].episode_id)
        assert len(hist2) == 1
        assert hist2[0]["content"] == "ep2 msg0"

    def test_returns_empty_for_nonexistent_id(self):
        s = _fresh_session()
        s.add_message("user", "hello")
        hist = s.get_episode_history("nonexistent-id")
        assert hist == []

    def test_returns_empty_session_with_no_episodes(self):
        s = Session(key="test:bare")
        hist = s.get_episode_history("anything")
        assert hist == []

    def test_respects_max_tokens(self, monkeypatch):
        s = _fresh_session()
        s.add_message("user", "u1")
        s.add_message("assistant", "a1")
        s.add_message("user", "u2")
        s.add_message("assistant", "a2")

        token_map = {"u1": 100, "a1": 100, "u2": 100, "a2": 100}
        monkeypatch.setattr(
            "OriginAgent.session.manager.estimate_message_tokens",
            lambda message: token_map.get(message.get("content"), 0),
        )

        eid = s.episodes[0].episode_id
        hist = s.get_episode_history(eid, max_tokens=120)
        assert [m["content"] for m in hist] == ["u2", "a2"]

    def test_annotates_timestamps(self):
        s = _fresh_session()
        s.messages.append({
            "role": "user",
            "content": "hello",
            "timestamp": "2026-06-27T12:00:00",
            "episode_id": str(uuid.uuid4()),
        })
        # Manually set up episode
        eid = str(uuid.uuid4())
        s.episodes.append(Episode(
            episode_id=eid,
            msg_start=0,
            msg_end=1,
            status="active",
        ))
        # Add via add_message for second message
        # (first message manually appended above to control timestamp)
        s.add_message("assistant", "world")

        hist = s.get_episode_history(eid, include_timestamps=True)
        assert hist[0]["content"].startswith("[Message Time: 2026-06-27T12:00:00]")

    def test_sanitizes_assistant_replay_artifacts(self):
        s = _fresh_session()
        s.messages.append({
            "role": "assistant",
            "content": "[Message Time: bad]\nsome reply\n[image: /fake/path.png]",
            "timestamp": datetime.now().isoformat(),
            "episode_id": str(uuid.uuid4()),
        })
        eid = str(uuid.uuid4())
        s.episodes.append(Episode(episode_id=eid, msg_start=0, msg_end=1, status="active"))

        hist = s.get_episode_history(eid)
        assert hist[0]["content"] == "some reply"


# ─── Backfill (__post_init__) ────────────────────────────────────────────────


class TestBackfill:
    def test_backfills_single_episode_for_existing_messages(self):
        s = _session_with_messages(3)
        # __post_init__ should have been called
        assert len(s.episodes) == 1
        assert s.episodes[0].msg_start == 0
        assert s.episodes[0].msg_end == 6  # 3 user + 3 assistant

    def test_backfilled_episode_is_active(self):
        s = _session_with_messages(1)
        assert s.episodes[0].status == "active"

    def test_backfill_sets_episode_id_on_all_messages(self):
        s = _session_with_messages(2)
        eid = s.episodes[0].episode_id
        for msg in s.messages:
            assert msg.get("episode_id") == eid

    def test_no_backfill_when_episodes_already_exist(self):
        """__post_init__ only runs once; subsequent add_message should not re-backfill."""
        s = _session_with_messages(3)
        assert len(s.episodes) == 1  # backfill created one episode

        # add_message works normally with the existing episode
        s.add_message("user", "new msg")
        assert s.messages[-1]["episode_id"] == s.episodes[0].episode_id
        assert len(s.episodes) == 1  # no duplicate episodes

    def test_no_backfill_when_no_messages(self):
        s = Session(key="test:empty")
        assert len(s.episodes) == 0

    def test_backfill_preserves_first_message_timestamp(self):
        ts = "2026-01-15T10:30:00"
        s = Session(key="test:ts")
        s.episodes = []
        s.active_episode_index = 0
        s.messages.append({
            "role": "user",
            "content": "first",
            "timestamp": ts,
        })
        s.__post_init__()
        assert s.episodes[0].started_at is not None
        assert s.episodes[0].started_at.isoformat() == ts


# ─── ensure_active_episode ──────────────────────────────────────────────────


class TestEnsureActiveEpisode:
    def test_creates_episode_when_list_empty(self):
        s = _fresh_session()
        assert len(s.episodes) == 0

        ep = s.ensure_active_episode()
        assert len(s.episodes) == 1
        assert ep.status == "active"
        assert ep.msg_start == 0
        assert ep.msg_end == 0

    def test_returns_existing_active_episode(self):
        s = _fresh_session()
        s.add_message("user", "hello")
        first = s.episodes[0]

        ep = s.ensure_active_episode()
        assert ep is first

    def test_creates_new_when_current_is_closed(self):
        s = _fresh_session()
        s.add_message("user", "hello")
        s.close_active_episode()

        ep = s.ensure_active_episode()
        assert len(s.episodes) == 2
        assert ep.status == "active"
        assert s.episodes[1] is ep


# ─── clear ───────────────────────────────────────────────────────────────────


class TestClear:
    def test_clear_resets_episodes(self):
        s = _fresh_session()
        s.add_message("user", "hello")
        s.start_new_episode()
        s.add_message("user", "world")
        assert len(s.episodes) == 2

        s.clear()
        assert s.episodes == []
        assert s.active_episode_index == 0

    def test_clear_then_add_message_creates_new_episode(self):
        s = _fresh_session()
        s.add_message("user", "hello")
        s.clear()
        s.add_message("user", "world")
        assert len(s.episodes) == 1
        assert s.episodes[0].msg_start == 0
        assert s.episodes[0].msg_end == 1


# ─── retain_recent_legal_suffix → _rebuild_episode_indices ──────────────────


class TestRebuildEpisodeIndices:
    def test_retain_recent_legal_suffix_adjusts_indices(self):
        s = _fresh_session()
        for i in range(10):
            s.add_message("user", f"msg {i}")
        assert s.episodes[0].msg_end == 10

        s.retain_recent_legal_suffix(4)

        assert len(s.messages) == 4
        # After trimming 6 from front, indices should be adjusted.
        # The single episode should now cover [0..4)
        assert len(s.episodes) == 1
        assert s.episodes[0].msg_start == 0
        assert s.episodes[0].msg_end == 4
        assert s.episodes[0].status == "active"

    def test_rebuild_clamps_indices_after_trim(self):
        """After trimming messages from the front, episode indices are clamped
        to the surviving message list. For Phase 1 this is advisory metadata —
        indices stay valid rather than pointing out of bounds."""
        s = _fresh_session()
        # Episode 0: messages 0-4 (5 messages)
        for i in range(5):
            s.add_message("user", f"A{i}")
        s.start_new_episode()
        # Episode 1: messages 5-14 (10 messages)
        for i in range(10):
            s.add_message("user", f"B{i}")

        s.retain_recent_legal_suffix(6)

        # Both episodes survive with clamped indices within bounds.
        for ep in s.episodes:
            assert 0 <= ep.msg_start <= len(s.messages)
            assert ep.msg_end <= len(s.messages)
            assert ep.msg_start < ep.msg_end

    def test_rebuild_preserves_closed_status(self):
        """A closed episode stays closed after trimming front messages."""
        s = _fresh_session()
        for i in range(10):
            s.add_message("user", f"msg {i}")
        s.close_active_episode()
        assert s.episodes[0].status == "closed"

        s.retain_recent_legal_suffix(4)

        # The tail of the closed episode survives; status is preserved.
        assert len(s.episodes) == 1
        assert s.episodes[0].status == "closed"

    def test_rebuild_creates_episode_if_messages_remain_but_none(self):
        """If trimming removes all episodes but messages remain, a new catch-all episode is created."""
        s = _fresh_session()
        s.add_message("user", "msg 0")
        s.add_message("user", "msg 1")
        s.add_message("user", "msg 2")
        # Manually clear episodes to simulate edge case
        s.episodes = []

        s._rebuild_episode_indices()

        assert len(s.episodes) == 1
        assert s.episodes[0].msg_start == 0
        assert s.episodes[0].msg_end == 3

    def test_direct_enforce_file_cap_adjusts_episodes(self):
        """Integration: enforce_file_cap must not leave stale episode indices."""
        test_dir = _local_tmp_dir()
        try:
            manager = SessionManager(test_dir)
            s = manager.get_or_create("test:cap")
            for i in range(50):
                s.add_message("user", f"msg {i}")

            s.enforce_file_cap(limit=20)

            assert len(s.messages) <= 20
            assert len(s.episodes) >= 1
            for ep in s.episodes:
                assert 0 <= ep.msg_start <= len(s.messages)
                assert ep.msg_end <= len(s.messages)
                assert ep.msg_start < ep.msg_end
        finally:
            _clean_test_dir(test_dir)


# ─── Serialization roundtrip ─────────────────────────────────────────────────


class TestEpisodeSerialization:
    def test_serialize_deserialize_roundtrip(self):
        s = _fresh_session()
        s.add_message("user", "A")
        s.start_new_episode("ep1")
        s.add_message("user", "B")
        s.add_message("assistant", "C")

        serialized = _serialize_episodes(s.episodes)
        deserialized = _deserialize_episodes(serialized)

        assert len(deserialized) == 2
        assert deserialized[0].episode_id == s.episodes[0].episode_id
        assert deserialized[0].status == "closed"
        assert deserialized[0].msg_start == 0
        assert deserialized[0].msg_end == 1
        assert deserialized[1].episode_id == s.episodes[1].episode_id
        assert deserialized[1].status == "active"
        assert deserialized[1].label == "ep1"
        assert deserialized[1].msg_start == 1
        assert deserialized[1].msg_end == 3

    def test_serialize_deserialize_empty_list(self):
        assert _serialize_episodes([]) == []
        assert _deserialize_episodes([]) == []

    def test_serialize_includes_dates(self):
        s = _fresh_session()
        s.add_message("user", "hello")
        s.close_active_episode()

        serialized = _serialize_episodes(s.episodes)
        assert "started_at" in serialized[0]
        assert "ended_at" in serialized[0]
        assert serialized[0]["started_at"] is not None
        assert serialized[0]["ended_at"] is not None

    def test_deserialize_missing_dates(self):
        data = [{"episode_id": "e1", "msg_start": 0, "msg_end": 5, "status": "active"}]
        eps = _deserialize_episodes(data)
        assert eps[0].started_at is None
        assert eps[0].ended_at is None

    def test_json_compatible(self):
        """Verify serialized episodes are JSON-safe."""
        s = _fresh_session()
        s.add_message("user", "hello")
        serialized = _serialize_episodes(s.episodes)
        roundtripped = json.dumps(serialized)
        parsed = json.loads(roundtripped)
        assert len(parsed) == 1
        assert parsed[0]["episode_id"] == s.episodes[0].episode_id

    def test_save_and_load_roundtrip(self):
        """Full save/load roundtrip through SessionManager."""
        test_dir = _local_tmp_dir()
        try:
            manager = SessionManager(test_dir)

            s = manager.get_or_create("test:rt")
            s.add_message("user", "hello")
            s.start_new_episode("second")
            s.add_message("user", "world")
            manager.save(s)

            manager.invalidate("test:rt")
            loaded = manager.get_or_create("test:rt")

            assert len(loaded.episodes) == 2
            assert loaded.episodes[0].label == ""
            assert loaded.episodes[1].label == "second"
            assert loaded.episodes[0].status == "closed"
            assert loaded.episodes[1].status == "active"
            assert loaded.episodes[0].msg_end == 1
            assert loaded.episodes[1].msg_start == 1
            assert loaded.episodes[1].msg_end == 2
            assert loaded.active_episode_index == 1
        finally:
            _clean_test_dir(test_dir)

    def test_old_session_without_episodes_backfilled_on_load(self):
        """A session JSONL without episode data should backfill on load."""
        test_dir = _local_tmp_dir()
        try:
            manager = SessionManager(test_dir)

            # Create a session file *without* episodes (old format)
            s = Session(key="test:old")
            s.add_message("user", "old msg")
            # Manually strip episodes to simulate old file
            s.episodes = []
            manager.save(s)
            # Remove episodes from the saved file metadata
            sessions_dir = test_dir / "sessions"
            path = sessions_dir / "test_old.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            meta = json.loads(lines[0])
            meta.pop("episodes", None)
            meta.pop("active_episode_index", None)
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(meta, ensure_ascii=False) + "\n")
                for line in lines[1:]:
                    f.write(line + "\n")

            manager.invalidate("test:old")
            loaded = manager.get_or_create("test:old")

            # Should have backfilled one episode
            assert len(loaded.episodes) == 1
            assert loaded.episodes[0].msg_start == 0
            assert loaded.episodes[0].msg_end == 1
        finally:
            _clean_test_dir(test_dir)


# ─── Edge Cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_kwargs_passed_through_add_message(self):
        s = _fresh_session()
        s.add_message("user", "hello", extra_field="extra", _command=False)
        assert s.messages[0]["extra_field"] == "extra"
        assert s.messages[0]["_command"] is False
        assert "episode_id" in s.messages[0]

    def test_multiple_episodes_lifecycle(self):
        """Full lifecycle: messages across several episode transitions."""
        s = _fresh_session()
        for i in range(3):
            s.add_message("user", f"ep0 msg{i}")

        s.start_new_episode("transition")
        for i in range(2):
            s.add_message("user", f"ep1 msg{i}")

        s.close_active_episode()
        s.add_message("user", "ep2 msg0")  # auto-creates episode

        assert len(s.episodes) == 3
        assert s.episodes[0].status == "closed"
        assert s.episodes[0].msg_end == 3
        assert s.episodes[1].status == "closed"
        assert s.episodes[1].msg_end == 5
        assert s.episodes[2].status == "active"
        assert s.episodes[2].msg_start == 5
        assert s.episodes[2].msg_end == 6

        # Verify get_episode_history returns correct slices
        assert len(s.get_episode_history(s.episodes[0].episode_id)) == 3
        assert len(s.get_episode_history(s.episodes[1].episode_id)) == 2
        assert len(s.get_episode_history(s.episodes[2].episode_id)) == 1

    def test_ensure_active_episode_does_not_duplicate(self):
        s = _fresh_session()
        ep1 = s.ensure_active_episode()
        ep2 = s.ensure_active_episode()
        assert len(s.episodes) == 1
        assert ep1 is ep2
