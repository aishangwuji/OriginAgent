from datetime import datetime, timedelta, timezone

import pytest

from OriginAgent.domain_packs.smart_home.runtime.presence import PresenceStore
from OriginAgent.domain_packs.smart_home.runtime.presence_signals import (
    FORBIDDEN_METADATA_KEYS,
    PresenceSignal,
    PresenceSignalIngestor,
    new_presence_signal,
)

NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
PAST = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()


def signal(**kwargs):
    defaults = {
        "signal_id": "sig_test",
        "source": "manual",
        "observed_at": NOW.isoformat(),
        "status_hint": "home",
        "confidence": 0.95,
        "person_id": "alice",
        "role": "resident",
    }
    defaults.update(kwargs)
    return PresenceSignal(**defaults)


def test_presence_signal_clamps_confidence_and_validates_fields():
    high = signal(confidence=3)
    low = signal(signal_id="sig_low", confidence=-1)

    assert high.confidence == 1.0
    assert low.confidence == 0.0

    with pytest.raises(ValueError):
        signal(source="camera")
    with pytest.raises(ValueError):
        signal(status_hint="nearby")
    with pytest.raises(ValueError):
        signal(role="owner")


def test_default_confidence_and_ttl_are_applied_when_omitted(tmp_path):
    store = PresenceStore(tmp_path)
    ingestor = PresenceSignalIngestor(store)
    created = PresenceSignal(
        signal_id="sig_wifi",
        source="wifi_presence",
        observed_at=NOW.isoformat(),
        status_hint="home",
        person_id="alice",
        role="resident",
    )

    result = ingestor.ingest(created)

    assert result is not None
    assert result.confidence == 0.70
    assert result.expires_at == (NOW + timedelta(minutes=20)).isoformat()


def test_expired_signal_is_ignored(tmp_path):
    store = PresenceStore(tmp_path)
    ingestor = PresenceSignalIngestor(store)

    result = ingestor.ingest(
        signal(expires_at=PAST)
    )

    assert result is None
    assert store.read_state() == {"people": {}}


def test_metadata_sanitized_at_construction_and_ingest_entry(tmp_path):
    store = PresenceStore(tmp_path)
    ingestor = PresenceSignalIngestor(store)
    raw = {
        "mac": "aa:bb",
        "IP": "192.0.2.10",
        "raw_audio": "bytes",
        "note": "ok",
    }
    created = signal(metadata=raw)

    assert created.metadata == {"note": "ok"}

    created.metadata["raw_video"] = "late mutation"
    created.metadata["safe"] = "yes"
    ingestor.ingest(created)

    assert created.metadata == {"note": "ok", "safe": "yes"}
    assert not any(key.casefold() in FORBIDDEN_METADATA_KEYS for key in created.metadata)


def test_none_status_only_allowed_for_manual_and_ingests_noop(tmp_path):
    store = PresenceStore(tmp_path)
    ingestor = PresenceSignalIngestor(store)
    none_signal = signal(status_hint="none", person_id=None, role="unknown")

    assert ingestor.ingest(none_signal) is None
    assert store.read_state() == {"people": {}}

    with pytest.raises(ValueError):
        signal(source="wifi_presence", status_hint="none")


@pytest.mark.parametrize("source", ["motion", "door_sensor", "door_lock", "system", "schedule"])
def test_non_person_binding_sources_with_person_id_rejected_before_store_write(tmp_path, source):
    store = PresenceStore(tmp_path)
    ingestor = PresenceSignalIngestor(store)
    source_signal = PresenceSignal(
        signal_id=f"sig_{source}",
        source=source,
        observed_at=NOW.isoformat(),
        status_hint="activity",
        confidence=0.7,
        person_id="alice",
        role="resident",
    )

    with pytest.raises(ValueError):
        ingestor.ingest(source_signal)

    assert store.read_state() == {"people": {}}


@pytest.mark.parametrize("source", ["manual", "voice", "wifi_presence", "phone_geofence"])
def test_trusted_person_sources_update_presence(tmp_path, source):
    store = PresenceStore(tmp_path)
    ingestor = PresenceSignalIngestor(store)

    result = ingestor.ingest(
        signal(source=source, signal_id=f"sig_{source}")
    )

    assert result is not None
    assert result.person_id == "alice"
    assert store.resolve_person("alice", now=NOW).status == "home"


@pytest.mark.parametrize("source", ["door_sensor", "door_lock", "motion"])
def test_activity_sources_produce_unknown_occupancy(tmp_path, source):
    store = PresenceStore(tmp_path)
    ingestor = PresenceSignalIngestor(store)

    result = ingestor.ingest(
        PresenceSignal(
            signal_id=f"sig_{source}",
            source=source,
            observed_at=NOW.isoformat(),
            status_hint="activity",
            confidence=None,
        )
    )

    assert result is not None
    assert result.status == "unknown"
    assert store.resolve_occupancy(now=NOW).status == "unknown"


def test_schedule_signal_never_binds_person_or_writes_occupancy(tmp_path):
    store = PresenceStore(tmp_path)
    ingestor = PresenceSignalIngestor(store)
    schedule = PresenceSignal(
        signal_id="sig_schedule",
        source="schedule",
        observed_at=NOW.isoformat(),
        status_hint="home",
        confidence=0.9,
    )

    result = ingestor.ingest(schedule)

    assert result is None
    assert store.resolve_person("alice", now=NOW) is None
    assert store.resolve_occupancy(now=NOW).status == "unknown"
    assert store.read_state() == {"people": {}}


def test_low_confidence_unknown_signal_does_not_overwrite_active_unknown_occupancy(tmp_path):
    store = PresenceStore(tmp_path)
    ingestor = PresenceSignalIngestor(store)
    strong = PresenceSignal(
        signal_id="sig_motion_active",
        source="motion",
        observed_at=NOW.isoformat(),
        status_hint="activity",
        confidence=0.65,
    )
    weak = PresenceSignal(
        signal_id="sig_motion_inactive",
        source="motion",
        observed_at=NOW.isoformat(),
        status_hint="unknown",
        confidence=0.0,
    )

    ingestor.ingest(strong)
    before = store.read_state()["unknown_occupancy"]
    result = ingestor.ingest(weak)
    after = store.read_state()["unknown_occupancy"]

    assert result is None
    assert before == after


def test_schedule_hint_does_not_clear_existing_unknown_occupancy(tmp_path):
    store = PresenceStore(tmp_path)
    ingestor = PresenceSignalIngestor(store)
    ingestor.ingest(
        PresenceSignal(
            signal_id="sig_motion_active",
            source="motion",
            observed_at=NOW.isoformat(),
            status_hint="activity",
            confidence=0.65,
        )
    )
    before = store.read_state()["unknown_occupancy"]

    result = ingestor.ingest(
        PresenceSignal(
            signal_id="sig_schedule",
            source="schedule",
            observed_at=NOW.isoformat(),
            status_hint="home",
            confidence=0.9,
        )
    )

    assert result is None
    assert store.read_state()["unknown_occupancy"] == before


def test_new_presence_signal_generates_sanitized_signal_with_defaults():
    created = new_presence_signal(
        source="motion",
        status_hint="activity",
        metadata={"mac": "aa:bb", "zone_label": "living"},
    )

    assert created.signal_id.startswith("presence_signal_")
    assert created.confidence == 0.65
    assert created.expires_at is not None
    assert created.metadata == {"zone_label": "living"}
