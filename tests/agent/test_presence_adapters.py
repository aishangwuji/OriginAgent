from OpenHome.agent.presence import PresenceStore
from OpenHome.agent.presence_adapters import (
    DoorEvent,
    DoorSensorAdapter,
    ManualPresenceAdapter,
    MotionAdapter,
    MotionEvent,
    PhoneGeofenceAdapter,
    PhoneGeofenceEvent,
    ScheduleHint,
    ScheduleHintAdapter,
    VoiceSelfReportAdapter,
    WifiDeviceEvent,
    WifiPresenceAdapter,
)
from OpenHome.agent.presence_signals import PresenceSignalIngestor


def test_manual_presence_adapter_home_and_away_emit_trusted_person_signals():
    home = ManualPresenceAdapter.home(person_id="alice", role="admin")
    away = ManualPresenceAdapter.away(person_id="alice", role="admin")

    assert home.source == "manual"
    assert home.status_hint == "home"
    assert home.person_id == "alice"
    assert home.role == "admin"
    assert away.status_hint == "away"


def test_manual_nobody_home_is_noop_not_direct_empty(tmp_path):
    store = PresenceStore(tmp_path)
    ingestor = PresenceSignalIngestor(store)
    nobody = ManualPresenceAdapter.nobody_home()

    assert nobody.source == "manual"
    assert nobody.status_hint == "none"
    assert ingestor.ingest(nobody) is None
    assert store.resolve_occupancy().status == "unknown"


def test_manual_someone_home_emits_unknown_occupancy_signal():
    signal = ManualPresenceAdapter.someone_home()

    assert signal.source == "manual"
    assert signal.status_hint == "activity"
    assert signal.person_id is None


def test_voice_self_report_requires_supplied_current_session_person():
    signal = VoiceSelfReportAdapter.home(person_id="alice", role="resident")

    assert signal.source == "voice"
    assert signal.person_id == "alice"
    assert signal.status_hint == "home"


def test_wifi_adapter_maps_registered_device_without_preserving_mac_or_ip():
    signal = WifiPresenceAdapter.from_event(
        WifiDeviceEvent(
            registered_device_id="device_alice_phone",
            person_id="alice",
            online=True,
            metadata={"mac": "aa:bb", "ip": "192.0.2.10", "room": "entry"},
        )
    )

    assert signal.source == "wifi_presence"
    assert signal.status_hint == "home"
    assert signal.person_id == "alice"
    assert signal.metadata == {
        "registered_device_id": "device_alice_phone",
        "room": "entry",
    }


def test_phone_geofence_adapter_maps_inside_and_outside_home():
    inside = PhoneGeofenceAdapter.from_event(
        PhoneGeofenceEvent(person_id="alice", state="inside_home")
    )
    outside = PhoneGeofenceAdapter.from_event(
        PhoneGeofenceEvent(person_id="alice", state="outside_home")
    )

    assert inside.source == "phone_geofence"
    assert inside.status_hint == "home"
    assert outside.status_hint == "away"


def test_door_and_motion_adapters_emit_unknown_occupancy_only():
    door = DoorSensorAdapter.from_event(
        DoorEvent(scope="home.entry", event="opened", metadata={"door_log": "raw"})
    )
    motion = MotionAdapter.from_event(
        MotionEvent(zone="home.living_room", active=True, metadata={"note": "pir"})
    )

    assert door.source == "door_sensor"
    assert door.status_hint == "activity"
    assert door.person_id is None
    assert door.metadata == {}
    assert motion.source == "motion"
    assert motion.status_hint == "activity"
    assert motion.person_id is None
    assert motion.metadata == {"note": "pir"}


def test_schedule_hint_adapter_omits_person_binding_and_low_confidence():
    signal = ScheduleHintAdapter.from_hint(
        ScheduleHint(person_id="alice", expected="home", zone="home")
    )

    assert signal.source == "schedule"
    assert signal.status_hint == "home"
    assert signal.person_id is None
    assert signal.confidence == 0.30
