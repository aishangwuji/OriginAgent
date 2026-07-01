"""Tests for CS-003: RuntimeEvent model + PerceptionEventFusion + bridge."""

from OriginAgent.agent.cognitive_events import CognitiveEvent
from OriginAgent.agent.meta_cognition_models import MetaTrigger, MetaTriggerType
from OriginAgent.agent.meta_cognition_triggers import bridge_runtime_event_to_trigger
from OriginAgent.agent.perception_event_fusion import PerceptionEventFusion
from OriginAgent.agent.perception_event_models import RuntimeEvent


# ── RuntimeEvent model tests ─────────────────────────────────────────────────


class TestRuntimeEventModel:
    def test_full_fields(self) -> None:
        e = RuntimeEvent(
            event_id="re-1",
            session_key="sess-a",
            event_type="cognitive_nudge",
            source="cognitive_loop",
            scope="scope://chat",
            owner_id="user-1",
            summary="test event",
            confidence=0.8,
            payload={"key": "val"},
            created_at="2026-07-01T00:00:00+00:00",
        )
        assert e.event_id == "re-1"
        assert e.confidence == 0.8
        assert e.payload == {"key": "val"}

    def test_defaults(self) -> None:
        e = RuntimeEvent(event_id="re-2", session_key="sess-b", event_type="tool_result", source="executor")
        assert e.scope == ""
        assert e.confidence == 0.0
        assert e.payload == {}
        assert e.created_at != ""

    def test_normalization_clamps_confidence(self) -> None:
        e = RuntimeEvent(event_id="re-3", session_key="sess-c", event_type="x", source="y", confidence=99.9)
        assert e.confidence == 1.0

    def test_normalization_negative_confidence(self) -> None:
        e = RuntimeEvent(event_id="re-4", session_key="sess-d", event_type="x", source="y", confidence=-1.0)
        assert e.confidence == 0.0

    def test_to_json_roundtrip(self) -> None:
        e = RuntimeEvent(
            event_id="re-5", session_key="sess-e", event_type="world_change", source="world_state",
            summary="something changed",
        )
        data = e.to_json()
        restored = RuntimeEvent.from_json(data)
        assert restored == e

    def test_from_json_extra_keys_filtered(self) -> None:
        raw = {"event_id": "re-6", "session_key": "sess-f", "event_type": "x", "source": "y", "unknown": "z"}
        e = RuntimeEvent.from_json(raw)
        assert e.event_id == "re-6"
        assert not hasattr(e, "unknown")

    def test_from_json_missing_keys(self) -> None:
        raw = {"event_id": "re-7"}
        e = RuntimeEvent.from_json(raw)
        assert e.session_key == ""
        assert e.source == ""

    def test_payload_not_dict_coerced(self) -> None:
        e = RuntimeEvent(event_id="re-8", session_key="s", event_type="x", source="y", payload=None)  # type: ignore
        assert e.payload == {}


# ── Source adapter tests ─────────────────────────────────────────────────────


class TestBridgeCognitiveEvent:
    def test_bridges_all_event_types(self) -> None:
        for etype in ("goal_nudge", "pending_confirmation_nudge", "scheduled_reminder", "foresight_nudge"):
            ce = CognitiveEvent(
                event_id=f"ce-{etype}",
                session_key="sess-a",
                event_type=etype,
                source_type="cognitive_loop",
                source_reference="ref",
                summary=f"test {etype}",
            )
            re = PerceptionEventFusion.bridge_cognitive_event(ce)
            assert re is not None
            assert re.event_type == "cognitive_nudge"
            assert re.source == "cognitive_loop"

    def test_priority_maps_to_confidence(self) -> None:
        ce = CognitiveEvent(
            event_id="ce-p1", session_key="sess-a", event_type="goal_nudge",
            source_type="cognitive_loop", source_reference="ref", priority="high",
        )
        re = PerceptionEventFusion.bridge_cognitive_event(ce)
        assert re is not None
        assert re.confidence == 0.9

    def test_low_priority(self) -> None:
        ce = CognitiveEvent(
            event_id="ce-p2", session_key="sess-a", event_type="goal_nudge",
            source_type="cognitive_loop", source_reference="ref", priority="low",
        )
        re = PerceptionEventFusion.bridge_cognitive_event(ce)
        assert re is not None
        assert re.confidence == 0.3


class TestBridgeToolResult:
    def test_failure_event_type(self) -> None:
        re = PerceptionEventFusion.bridge_tool_result(
            session_key="sess-a", tool_name="read_file", status="error", error_kind="not_found",
        )
        assert re is not None
        assert re.event_type == "tool_failure"
        assert re.confidence == 0.3

    def test_success_event_type(self) -> None:
        re = PerceptionEventFusion.bridge_tool_result(
            session_key="sess-a", tool_name="read_file", status="success",
        )
        assert re is not None
        assert re.event_type == "tool_result"
        assert re.confidence == 0.8

    def test_none_for_empty_session(self) -> None:
        re = PerceptionEventFusion.bridge_tool_result(session_key="", tool_name="x", status="error")
        assert re is None


class TestBridgeWorldChange:
    def test_basic_change(self) -> None:
        re = PerceptionEventFusion.bridge_world_change(
            session_key="sess-a", change_summary="light turned on", confidence=0.7,
        )
        assert re is not None
        assert re.event_type == "world_change"
        assert re.source == "world_state"
        assert re.confidence == 0.7

    def test_none_for_empty_summary(self) -> None:
        re = PerceptionEventFusion.bridge_world_change(session_key="sess-a", change_summary="")
        assert re is None

    def test_with_diff(self) -> None:
        re = PerceptionEventFusion.bridge_world_change(
            session_key="sess-a", change_summary="temp changed",
            snapshot_diff={"temperature": 22},
        )
        assert re is not None
        assert re.payload["snapshot_diff"] == {"temperature": 22}


class TestBridgeUserMessage:
    def test_basic_message(self) -> None:
        re = PerceptionEventFusion.bridge_user_message(session_key="sess-a", text="hello")
        assert re is not None
        assert re.event_type == "user_message"

    def test_correction_detected(self) -> None:
        re = PerceptionEventFusion.bridge_user_message(session_key="sess-a", text="不是这样的，你理解错了")
        assert re is not None
        assert re.payload["matched_correction"] is not None

    def test_excluded_returns_none(self) -> None:
        re = PerceptionEventFusion.bridge_user_message(session_key="sess-a", text="继续")
        assert re is None

    def test_none_for_empty_text(self) -> None:
        re = PerceptionEventFusion.bridge_user_message(session_key="sess-a", text="")
        assert re is None


class TestBridgeDeviceEvent:
    def test_recognised_event(self) -> None:
        re = PerceptionEventFusion.bridge_device_event(
            session_key="sess-a", device_id="camera-1", event_type="sensor_triggered",
        )
        assert re is not None
        assert re.event_type == "device_event"
        assert re.scope == "camera-1"

    def test_unrecognised_event_type(self) -> None:
        re = PerceptionEventFusion.bridge_device_event(
            session_key="sess-a", device_id="camera-1", event_type="unknown_event",
        )
        assert re is None

    def test_none_without_device_id(self) -> None:
        re = PerceptionEventFusion.bridge_device_event(session_key="sess-a", device_id="", event_type="device_online")
        assert re is None


# ── RuntimeEvent → MetaTrigger bridge tests ──────────────────────────────────


class TestBridgeRuntimeEventToTrigger:
    def test_tool_failure_mapping(self) -> None:
        re = RuntimeEvent(
            event_id="re-1", session_key="sess-a", event_type="tool_failure",
            source="tool_executor", summary="error reading file", confidence=0.3,
        )
        mt = bridge_runtime_event_to_trigger(re)
        assert mt is not None
        assert mt.trigger_type == "tool_failure"
        assert mt.session_key == "sess-a"

    def test_cognitive_nudge_mapping(self) -> None:
        re = RuntimeEvent(
            event_id="re-2", session_key="sess-b", event_type="cognitive_nudge",
            source="cognitive_loop", summary="nudge", confidence=0.6,
        )
        mt = bridge_runtime_event_to_trigger(re)
        assert mt is not None
        assert mt.trigger_type == "cognitive_nudge"

    def test_world_change_mapping(self) -> None:
        re = RuntimeEvent(
            event_id="re-3", session_key="sess-c", event_type="world_change",
            source="world_state", summary="change detected",
        )
        mt = bridge_runtime_event_to_trigger(re)
        assert mt is not None
        assert mt.trigger_type == "world_change"

    def test_device_event_mapping(self) -> None:
        re = RuntimeEvent(
            event_id="re-4", session_key="sess-d", event_type="device_event",
            source="device_orchestrator", summary="device online",
        )
        mt = bridge_runtime_event_to_trigger(re)
        assert mt is not None
        assert mt.trigger_type == "device_event"

    def test_user_message_with_correction(self) -> None:
        re = RuntimeEvent(
            event_id="re-5", session_key="sess-e", event_type="user_message",
            source="user_input", summary="correction",
            payload={"matched_correction": "不对"},
        )
        mt = bridge_runtime_event_to_trigger(re)
        assert mt is not None
        assert mt.trigger_type == "user_correction"

    def test_user_message_without_correction(self) -> None:
        re = RuntimeEvent(
            event_id="re-6", session_key="sess-f", event_type="user_message",
            source="user_input", summary="normal query",
            payload={},
        )
        mt = bridge_runtime_event_to_trigger(re)
        assert mt is None

    def test_unmappable_type(self) -> None:
        re = RuntimeEvent(
            event_id="re-7", session_key="sess-g", event_type="unsupported_type",
            source="unknown",
        )
        mt = bridge_runtime_event_to_trigger(re)
        assert mt is None

    def test_not_runtime_event_returns_none(self) -> None:
        mt = bridge_runtime_event_to_trigger("not an event")  # type: ignore[arg-type]
        assert mt is None

    def test_severity_from_confidence(self) -> None:
        re = RuntimeEvent(
            event_id="re-8", session_key="sess-h", event_type="tool_failure",
            source="executor", confidence=0.1,
        )
        mt = bridge_runtime_event_to_trigger(re)
        assert mt is not None
        assert mt.severity == "high"  # low confidence → high severity


# ── PerceptionEventFusion.process() tests ────────────────────────────────────


class TestFusionProcess:
    def test_accepts_cognitive_event(self) -> None:
        fusion = PerceptionEventFusion()
        ce = CognitiveEvent(
            event_id="ce-p1", session_key="sess-a", event_type="goal_nudge",
            source_type="cognitive_loop", source_reference="ref",
        )
        re = fusion.process(source=ce)
        assert re is not None
        assert isinstance(re, RuntimeEvent)
        assert re.event_type == "cognitive_nudge"

    def test_accepts_string_hint(self) -> None:
        fusion = PerceptionEventFusion()
        re = fusion.process(
            "tool_result",
            session_key="sess-a",
            tool_name="test", status="success",
        )
        assert re is not None
        assert re.event_type == "tool_result"

    def test_returns_none_when_disabled(self) -> None:
        from types import SimpleNamespace
        fusion = PerceptionEventFusion(config=SimpleNamespace(enabled=False))
        ce = CognitiveEvent(
            event_id="ce-p2", session_key="sess-a", event_type="goal_nudge",
            source_type="cognitive_loop", source_reference="ref",
        )
        re = fusion.process(source=ce)
        assert re is None

    def test_returns_none_for_unknown_type(self) -> None:
        fusion = PerceptionEventFusion()
        re = fusion.process(source=42)  # type: ignore[arg-type]
        assert re is None

    def test_full_pipeline(self) -> None:
        """CognitiveEvent → process() → RuntimeEvent → bridge → MetaTrigger."""
        fusion = PerceptionEventFusion()
        ce = CognitiveEvent(
            event_id="ce-full", session_key="sess-a", event_type="goal_nudge",
            source_type="cognitive_loop", source_reference="ref",
            summary="full pipeline test", priority="medium",
        )
        re = fusion.process(source=ce)
        assert re is not None
        mt = bridge_runtime_event_to_trigger(re)
        assert mt is not None
        assert mt.trigger_type == "cognitive_nudge"
        assert mt.session_key == "sess-a"

    def test_device_event_full_pipeline(self) -> None:
        fusion = PerceptionEventFusion()
        re = fusion.process(
            "device_event",
            session_key="sess-a",
            device_id="cam-1",
            event_type="sensor_triggered",
            payload={"value": 42},
        )
        assert re is not None
        mt = bridge_runtime_event_to_trigger(re)
        assert mt is not None
        assert mt.trigger_type == "device_event"
        assert "cam-1" in mt.payload.get("event_summary", "")


# ── MetaTriggerType validation tests ─────────────────────────────────────────


class TestExtendedTriggerTypes:
    def test_new_types_accepted(self) -> None:
        for ttype in ("cognitive_nudge", "world_change", "device_event"):
            t = MetaTrigger(
                trigger_id=f"t-{ttype}",
                session_key="sess-a",
                trigger_type=ttype,  # type: ignore[arg-type]
                source_type="test",
                source_reference="ref",
            )
            assert t.trigger_type == ttype

    def test_new_types_serialize_and_deserialize(self) -> None:
        t = MetaTrigger(
            trigger_id="t-serial", session_key="sess-a",
            trigger_type="cognitive_nudge",  # type: ignore[arg-type]
            source_type="test", source_reference="ref",
        )
        data = t.to_json()
        restored = MetaTrigger.from_json(data)
        assert restored.trigger_type == "cognitive_nudge"
