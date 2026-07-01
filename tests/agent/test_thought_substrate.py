"""Tests for CogniSphere CS-002: ThoughtFrame + ThoughtSubstrate."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from OriginAgent.agent.meta_cognition_models import ThoughtJournalEntry
from OriginAgent.agent.thought_substrate_models import ThoughtFrame
from OriginAgent.agent.thought_substrate_store import ThoughtSubstrate


# ── ThoughtFrame model tests ─────────────────────────────────────────────────


class TestThoughtFrameRoundtrip:
    def test_full_fields(self) -> None:
        frame = ThoughtFrame(
            frame_id="tf-1",
            session_key="sess-a",
            trigger_refs=["trig-1", "trig-2"],
            observation_summary="user asked about CPU spike",
            active_goal="diagnose CPU cause",
            candidate_hypotheses=["process leak", "db congestion", "scheduled job"],
            intended_strategy="check top, then logs, then db",
            verification_needs=["verify process list", "check cron"],
            simulation_requests=["sim-1"],
            confidence=0.7,
            uncertainty_flags=["missing_log_data"],
            created_at="2026-06-30T12:00:00+00:00",
        )
        data = frame.to_json()
        restored = ThoughtFrame.from_json(data)
        assert restored == frame
        assert restored.frame_id == "tf-1"
        assert restored.session_key == "sess-a"
        assert len(restored.candidate_hypotheses) == 3

    def test_minimal_fields(self) -> None:
        frame = ThoughtFrame(frame_id="tf-2", session_key="sess-b")
        assert frame.frame_id == "tf-2"
        assert frame.session_key == "sess-b"
        assert frame.trigger_refs == []
        assert frame.candidate_hypotheses == []
        assert frame.confidence == 0.0
        assert frame.observation_summary == ""

    def test_default_created_at(self) -> None:
        frame = ThoughtFrame(frame_id="tf-3", session_key="sess-c")
        assert frame.created_at != ""


class TestThoughtFrameNormalization:
    def test_clamps_confidence(self) -> None:
        frame = ThoughtFrame(frame_id="tf-4", session_key="sess-d", confidence=99.9)
        assert frame.confidence == 1.0

    def test_negative_confidence(self) -> None:
        frame = ThoughtFrame(frame_id="tf-5", session_key="sess-e", confidence=-0.5)
        assert frame.confidence == 0.0

    def test_truncates_long_text(self) -> None:
        long = "a" * 1000
        frame = ThoughtFrame(frame_id="tf-6", session_key="sess-f", observation_summary=long)
        assert len(frame.observation_summary) < 500

    def test_limits_list(self) -> None:
        many = [str(i) for i in range(100)]
        frame = ThoughtFrame(frame_id="tf-7", session_key="sess-g", trigger_refs=many)
        assert len(frame.trigger_refs) <= 16

    def test_handles_non_list_input(self) -> None:
        frame = ThoughtFrame(frame_id="tf-8", session_key="sess-h", trigger_refs="not-a-list")  # type: ignore[arg-type]
        assert frame.trigger_refs == []


class TestThoughtFrameFromJson:
    def test_extra_keys_filtered(self) -> None:
        raw = {
            "frame_id": "tf-9",
            "session_key": "sess-i",
            "unknown_key": "should_be_filtered",
        }
        frame = ThoughtFrame.from_json(raw)
        assert frame.frame_id == "tf-9"
        assert not hasattr(frame, "unknown_key")

    def test_missing_keys_get_defaults(self) -> None:
        raw = {"frame_id": "tf-10"}  # missing session_key
        frame = ThoughtFrame.from_json(raw)
        # from_json should not raise because session_key defaults to ""
        assert frame.session_key == ""
        assert frame.confidence == 0.0
        assert frame.trigger_refs == []


# ── ThoughtJournalEntry backward compat tests ────────────────────────────────


class TestJournalBackwardCompat:
    def test_old_journal_without_new_fields(self) -> None:
        """Simulate loading a journal persisted before CS-002."""
        raw = {
            "entry_id": "old-entry-1",
            "session_key": "sess-x",
            "created_at": "2026-06-01T00:00:00+00:00",
            "trigger_type": "tool_failure",
            "strategy_summary": "old strategy",
            "assumptions": [],
            "evidence_refs": [],
            "confidence": 0.5,
            "expected_outcome": "",
            "actual_outcome": "failed",
            "mismatch_summary": "",
            "suggested_next_action": "",
            "summary": "old journal entry",
            "payload": {},
        }
        journal = ThoughtJournalEntry.from_json(raw)
        assert journal.frame_id == ""  # default for backward compat
        assert journal.event_refs == []
        assert journal.retention_hint == "discard"
        assert journal.entry_id == "old-entry-1"
        assert journal.strategy_summary == "old strategy"

    def test_new_journal_roundtrip(self) -> None:
        journal = ThoughtJournalEntry(
            entry_id="new-entry-1",
            session_key="sess-y",
            frame_id="tf-linked-1",
            event_refs=["evt-1", "evt-2"],
            retention_hint="review",
            strategy_summary="new strategy",
            summary="new journal entry",
        )
        data = journal.to_json()
        restored = ThoughtJournalEntry.from_json(data)
        assert restored.frame_id == "tf-linked-1"
        assert restored.event_refs == ["evt-1", "evt-2"]
        assert restored.retention_hint == "review"


# ── ThoughtSubstrate store tests ─────────────────────────────────────────────


class TestThoughtSubstrateOpenClose:
    def test_open_and_get(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        frame = sub.open_frame("sess-1", trigger_refs=["t1"])
        assert frame.session_key == "sess-1"
        assert frame.trigger_refs == ["t1"]

        retrieved = sub.get_open_frame("sess-1")
        assert retrieved is not None
        assert retrieved.frame_id == frame.frame_id

    def test_get_open_frame_nonexistent(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        assert sub.get_open_frame("nobody") is None

    def test_close_persists_journal(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        sub.open_frame("sess-1")
        journal = sub.close_frame("sess-1")
        assert journal is not None
        assert journal.session_key == "sess-1"
        assert journal.frame_id != ""

    def test_close_with_enrichment(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        sub.open_frame("sess-1", observation_summary="CPU spiked", confidence=0.6)
        journal = sub.close_frame(
            "sess-1",
            enrichment={
                "strategy_summary": "check logs first",
                "expected_outcome": "find high load process",
                "actual_outcome": "found java process",
                "mismatch_summary": "",
                "retention_hint": "candidate",
                "evidence_refs": ["t1"],
                "assumptions": ["java process is the culprit"],
            },
        )
        assert journal is not None
        assert journal.strategy_summary == "check logs first"
        assert journal.expected_outcome == "find high load process"
        assert journal.frame_id != ""
        assert journal.retention_hint == "candidate"
        assert "t1" in journal.evidence_refs

    def test_close_no_open_frame(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        journal = sub.close_frame("nobody")
        assert journal is None

    def test_open_auto_closes_previous(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        frame1 = sub.open_frame("sess-1")
        frame2 = sub.open_frame("sess-1")  # should auto-close frame1
        assert frame2.frame_id != frame1.frame_id
        # frame1 should now be closed (no longer retrievable)
        assert sub.get_open_frame("sess-1") is not None  # frame2 is open
        # Both should be persisted
        frames = sub.recent_frames()
        assert len(frames) >= 1  # at least frame1 was written


class TestThoughtSubstratePersistence:
    def test_frames_persisted_to_disk(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        sub.open_frame("sess-1", trigger_refs=["t1"])
        sub.close_frame("sess-1")

        frames_file = tmp_path / "memory" / "thought_substrate" / "frames.jsonl"
        assert frames_file.exists()
        lines = frames_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["session_key"] == "sess-1"

    def test_journals_persisted_to_disk(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        sub.open_frame("sess-1")
        sub.close_frame("sess-1")

        journals_file = tmp_path / "memory" / "thought_substrate" / "journals.jsonl"
        assert journals_file.exists()
        lines = journals_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["session_key"] == "sess-1"
        assert payload["frame_id"] != ""

    def test_recent_frames(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        for i in range(5):
            sub.open_frame(f"sess-{i}")
            sub.close_frame(f"sess-{i}")

        recent = sub.recent_frames(limit=3)
        assert len(recent) <= 3
        # most recent first in -limit slice
        assert recent[-1]["session_key"] == "sess-2"  # 5-3=2

    def test_recent_journals_by_session(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        for session in ["sess-a", "sess-b", "sess-a"]:
            sub.open_frame(session)
            sub.close_frame(session)

        a_journals = sub.recent_journals(session_key="sess-a")
        assert len(a_journals) == 2
        b_journals = sub.recent_journals(session_key="sess-b")
        assert len(b_journals) == 1


class TestThoughtSubstrateSessionIsolation:
    def test_frames_isolated_by_session(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        sub.open_frame("sess-a", observation_summary="session a")
        sub.open_frame("sess-b", observation_summary="session b")
        sub.close_frame("sess-a")
        sub.close_frame("sess-b")

        a_frames = sub.recent_frames(session_key="sess-a")
        b_frames = sub.recent_frames(session_key="sess-b")
        assert len(a_frames) == 1
        assert len(b_frames) == 1
        assert a_frames[0]["observation_summary"] == "session a"
        assert b_frames[0]["observation_summary"] == "session b"

    def test_open_frames_independent(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        sub.open_frame("sess-a")
        sub.open_frame("sess-b")
        assert sub.get_open_frame("sess-a") is not None
        assert sub.get_open_frame("sess-b") is not None
        sub.close_frame("sess-a")
        assert sub.get_open_frame("sess-a") is None
        assert sub.get_open_frame("sess-b") is not None


class TestThoughtSubstrateRetention:
    def test_enforce_retention_trim_oldest(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path, max_frames_per_session=2)
        for i in range(5):
            sub.open_frame("sess-1", observation_summary=f"frame {i}")
            sub.close_frame("sess-1")

        sub.enforce_retention_policy()
        remaining = sub.recent_frames()
        assert len(remaining) <= 2

    def test_retention_noop_when_under_limit(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path, max_frames_per_session=100)
        sub.open_frame("sess-1")
        sub.close_frame("sess-1")
        sub.enforce_retention_policy()
        assert len(sub.recent_frames()) == 1

    def test_retention_preserves_newest(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path, max_frames_per_session=1)
        for i in range(3):
            sub.open_frame("sess-1", observation_summary=f"frame {i}")
            sub.close_frame("sess-1")

        sub.enforce_retention_policy()
        remaining = sub.recent_frames()
        assert len(remaining) == 1
        # should keep the last frame ("frame 2")
        assert remaining[0]["observation_summary"] == "frame 2"


class TestThoughtSubstrateSummary:
    def test_summary_structure(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        sub.open_frame("sess-1")
        sub.close_frame("sess-1")
        summary = sub.summary()
        assert "open_frame_count" in summary
        assert "total_frame_count" in summary
        assert "total_journal_count" in summary
        assert summary["open_frame_count"] >= 0  # may have been cleared
        assert summary["total_frame_count"] >= 1

    def test_summary_shows_open_sessions(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        sub.open_frame("sess-open")
        summary = sub.summary()
        assert "sess-open" in summary["open_sessions"]


class TestThoughtSubstrateConcurrentAccess:
    def test_concurrent_open_close(self, tmp_path: Path) -> None:
        sub = ThoughtSubstrate(tmp_path)
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker(worker_id: int) -> None:
            try:
                session = f"sess-{worker_id}"
                for i in range(10):
                    sub.open_frame(session, observation_summary=f"worker {worker_id} frame {i}")
                    sub.close_frame(session)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"concurrent access raised: {errors}"
        # all frames persisted
        all_frames = sub.recent_frames(limit=200)
        assert len(all_frames) == 50  # 5 workers × 10 frames


class TestThoughtSubstrateWithAudit:
    def test_writes_to_audit_when_provided(self, tmp_path: Path) -> None:
        from OriginAgent.agent.meta_cognition_audit import JsonlMetaCognitionAuditLedger

        audit = JsonlMetaCognitionAuditLedger(tmp_path)
        sub = ThoughtSubstrate(tmp_path, audit=audit)
        sub.open_frame("sess-1")
        sub.close_frame("sess-1")

        # audit should have the journal
        journals = audit.recent_journals(limit=10)
        assert len(journals) >= 1
        assert journals[-1]["session_key"] == "sess-1"

    def test_audit_failure_does_not_crash(self, tmp_path: Path) -> None:
        class BrokenAudit:
            def append_journal(self, journal: object) -> None:
                raise RuntimeError("simulated audit failure")

        sub = ThoughtSubstrate(tmp_path, audit=BrokenAudit())  # type: ignore[arg-type]
        sub.open_frame("sess-1")
        # should not raise despite audit failure
        journal = sub.close_frame("sess-1")
        assert journal is not None
        assert journal.session_key == "sess-1"
