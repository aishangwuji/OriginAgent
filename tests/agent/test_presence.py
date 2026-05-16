import json
from datetime import datetime, timezone

import pytest

from OpenHome.agent.presence import PresenceStore

NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
PAST = "2026-05-15T11:00:00+00:00"
FUTURE = "2026-05-15T13:00:00+00:00"


def test_upsert_creates_presence_json_and_updates_same_person(tmp_path):
    store = PresenceStore(tmp_path)

    first = store.upsert_presence(
        "alice",
        role="resident",
        status="home",
        source="manual",
        confidence=0.9,
        now=NOW,
    )
    second = store.upsert_presence(
        "alice",
        role="resident",
        status="away",
        source="phone_geofence",
        confidence=0.95,
        now=NOW,
    )

    assert first.person_id == second.person_id
    raw = json.loads(store.presence_file.read_text(encoding="utf-8"))
    assert list(raw["people"]) == ["alice"]
    assert raw["people"]["alice"]["status"] == "away"


def test_expired_person_presence_resolves_unknown(tmp_path):
    store = PresenceStore(tmp_path)
    store.upsert_presence(
        "alice",
        role="admin",
        status="home",
        source="manual",
        confidence=0.9,
        expires_at=PAST,
    )

    resolved = store.resolve_person("alice", now=NOW)

    assert resolved is not None
    assert resolved.status == "unknown"
    assert resolved.confidence == 0.0


def test_missing_and_no_known_admin_or_resident_resolves_unknown(tmp_path):
    store = PresenceStore(tmp_path)
    assert store.resolve_person("missing") is None

    store.upsert_presence(
        "guest",
        role="guest",
        status="away",
        source="manual",
        confidence=0.95,
    )

    assert store.resolve_occupancy(now=NOW).status == "unknown"


def test_confident_home_person_resolves_occupied(tmp_path):
    store = PresenceStore(tmp_path)
    store.upsert_presence(
        "alice",
        role="resident",
        status="home",
        source="wifi_presence",
        confidence=0.7,
    )

    occupancy = store.resolve_occupancy(now=NOW)

    assert occupancy.status == "occupied"
    assert occupancy.source == "person_presence"


def test_home_and_away_confidence_thresholds_are_separate(tmp_path):
    store = PresenceStore(tmp_path)
    store.upsert_presence(
        "alice",
        role="resident",
        status="home",
        source="manual",
        confidence=0.75,
    )
    assert store.resolve_person("alice", now=NOW).status == "home"
    assert store.resolve_occupancy(now=NOW).status == "occupied"

    store.upsert_presence(
        "alice",
        role="resident",
        status="away",
        source="manual",
        confidence=0.75,
    )
    assert store.resolve_person("alice", now=NOW).status == "unknown"
    assert store.resolve_occupancy(now=NOW).status == "unknown"


def test_all_known_admin_residents_confident_away_resolves_empty(tmp_path):
    store = PresenceStore(tmp_path)
    store.upsert_presence(
        "alice",
        role="admin",
        status="away",
        source="phone_geofence",
        confidence=0.9,
        now=NOW,
    )
    store.upsert_presence(
        "bob",
        role="resident",
        status="away",
        source="wifi_presence",
        confidence=0.85,
        now=NOW,
    )

    occupancy = store.resolve_occupancy(now=NOW)

    assert occupancy.status == "empty"
    assert occupancy.confidence == 0.85


def test_unknown_occupancy_signal_overrides_only_when_active_and_confident(tmp_path):
    store = PresenceStore(tmp_path)
    store.upsert_presence(
        "alice",
        role="resident",
        status="away",
        source="manual",
        confidence=0.95,
    )

    store.mark_unknown_occupancy(source="motion", confidence=0.7, expires_at=FUTURE)
    assert store.resolve_occupancy(now=NOW).status == "unknown"

    store.mark_unknown_occupancy(source="motion", confidence=0.49, expires_at=FUTURE)
    assert store.resolve_occupancy(now=NOW).status == "empty"

    store.mark_unknown_occupancy(source="door_sensor", confidence=0.9, expires_at=PAST)
    assert store.resolve_occupancy(now=NOW).status == "empty"


def test_invalid_role_status_source_rejected_and_confidence_clamped(tmp_path):
    store = PresenceStore(tmp_path)

    with pytest.raises(ValueError):
        store.upsert_presence("alice", role="owner", status="home", source="manual")
    with pytest.raises(ValueError):
        store.upsert_presence("alice", role="admin", status="nearby", source="manual")
    with pytest.raises(ValueError):
        store.upsert_presence("alice", role="admin", status="home", source="camera")

    low = store.upsert_presence(
        "alice",
        role="admin",
        status="home",
        source="manual",
        confidence=-2,
    )
    high = store.mark_unknown_occupancy(source="motion", confidence=2)

    assert low.confidence == 0.0
    assert high.confidence == 1.0


def test_child_and_elder_roles_are_valid_but_do_not_prove_empty(tmp_path):
    store = PresenceStore(tmp_path)
    child = store.upsert_presence(
        "kid",
        role="child",
        status="away",
        source="manual",
        confidence=0.95,
    )
    elder = store.upsert_presence(
        "grandparent",
        role="elder",
        status="away",
        source="manual",
        confidence=0.95,
    )

    assert child.role == "child"
    assert elder.role == "elder"
    assert store.resolve_occupancy(now=NOW).status == "unknown"


def test_motion_door_lock_schedule_and_system_cannot_bind_person_presence(tmp_path):
    store = PresenceStore(tmp_path)

    for source in ("motion", "door_lock", "door_sensor", "schedule", "system"):
        with pytest.raises(ValueError):
            store.upsert_presence(
                "alice",
                role="resident",
                status="home",
                source=source,
            )


def test_system_source_can_mark_unknown_occupancy_without_identity_binding(tmp_path):
    store = PresenceStore(tmp_path)

    signal = store.mark_unknown_occupancy(source="system", confidence=0.8)

    assert signal.source == "system"
    assert store.resolve_occupancy(now=NOW).status == "unknown"


def test_voice_source_is_session_self_report_not_voiceprint(tmp_path):
    store = PresenceStore(tmp_path)

    presence = store.upsert_presence(
        "alice",
        role="resident",
        status="home",
        source="voice",
        confidence=0.9,
    )

    assert presence.source == "voice"


def test_read_or_parse_failure_resolves_unknown(tmp_path):
    store = PresenceStore(tmp_path)
    store.presence_file.parent.mkdir(parents=True, exist_ok=True)
    store.presence_file.write_text("{bad json", encoding="utf-8")

    assert store.read_state() == {"people": {}}
    assert store.resolve_occupancy(now=NOW).status == "unknown"


def test_presence_writes_use_lock_and_atomic_writer(tmp_path, monkeypatch):
    store = PresenceStore(tmp_path)
    events = []

    class FakeLock:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, exc_type, exc, tb):
            events.append("exit")

    def fake_write(path, content):
        events.append(("write", path, json.loads(content)))

    monkeypatch.setattr(store, "_locked", lambda: FakeLock())
    monkeypatch.setattr("OpenHome.agent.presence._write_text_atomic", fake_write)

    store.upsert_presence(
        "alice",
        role="resident",
        status="home",
        source="manual",
        confidence=0.9,
    )

    assert events[0] == "enter"
    assert events[1][0] == "write"
    assert events[1][2]["people"]["alice"]["status"] == "home"
    assert events[2] == "exit"
