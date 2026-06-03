import dataclasses
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from OriginAgent.evolution.events import (
    EventType,
    EvolutionEvent,
    normalize_event_type,
)
from OriginAgent.evolution.ledger import EvolutionLedger, canonical_dump, compute_event_hash


def test_evolution_event_is_immutable_and_new_populates_defaults() -> None:
    event = EvolutionEvent.new(
        EventType.MODULE_PROPOSED,
        module_id="calendar-helper",
        module_version="1.0.0",
        module_type="skill",
    )

    assert event.event_id.startswith("evolution_")
    assert event.created_at
    assert event.schema_version == "originagent.evolution.event.v1"
    assert event.event_type == "module_proposed"
    assert event.actor == "user"
    assert event.previous_event_hash is None

    with pytest.raises(dataclasses.FrozenInstanceError):
        event.event_type = "module_failed"  # type: ignore[misc]


def test_evolution_event_can_record_explicit_unmapped_event() -> None:
    event = EvolutionEvent.new(EventType.UNMAPPED, result={"raw_action": "always_on"})

    assert event.event_type == "unmapped"
    assert event.result == {"raw_action": "always_on"}


def test_evolution_event_new_accepts_actor() -> None:
    event = EvolutionEvent.new(EventType.MODULE_PROPOSED, actor="reviewer")

    assert event.actor == "reviewer"


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("verify", EventType.MODULE_VERIFIED),
        ("activate", EventType.MODULE_ACTIVATED),
        ("reject", EventType.MODULE_FAILED),
        ("deprecate", EventType.MODULE_DEPRECATED),
        ("always_on", None),
        ("always_off", None),
    ],
)
def test_normalize_event_type_handles_skill_lifecycle_actions(
    action: str,
    expected: EventType | None,
) -> None:
    assert normalize_event_type("skill_lifecycle", {"action": action}) is expected


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("install", EventType.MODULE_INSTALLED_STAGING),
        ("upgrade", None),
        ("uninstall", None),
        ("enable", None),
        ("disable", None),
        ("activate", EventType.MODULE_ACTIVATED),
        ("deactivate", EventType.MODULE_DEPRECATED),
        ("eval", EventType.MODULE_VERIFIED),
        ("move_to_domain", None),
    ],
)
def test_normalize_event_type_handles_domain_pack_actions(
    action: str,
    expected: EventType | None,
) -> None:
    assert normalize_event_type("domain_pack", {"action": action}) is expected


def test_normalize_event_type_marks_unknown_values_unmapped() -> None:
    assert normalize_event_type("evolution", {"event_type": "missing"}) is None
    assert normalize_event_type("skill_lifecycle", {"action": "missing"}) is None
    assert normalize_event_type("unknown", {"action": "activate"}) is None


def test_ledger_appends_hash_chain(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path)
    first = ledger.append(EvolutionEvent.new(EventType.MODULE_PROPOSED, module_id="alpha"))
    second = ledger.append(
        EvolutionEvent.new(EventType.MODULE_MANIFEST_VALIDATED, module_id="alpha")
    )

    assert first.previous_event_hash is None
    assert first.event_hash
    assert second.previous_event_hash == first.event_hash
    assert second.event_hash
    assert second.event_hash != first.event_hash

    lines = (tmp_path / "memory" / "evolution_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event_id"] == first.event_id
    assert json.loads(lines[1])["event_id"] == second.event_id


def test_canonical_dump_is_stable_for_key_order() -> None:
    left = {"b": 2, "a": {"d": 4, "c": 3}}
    right = {"a": {"c": 3, "d": 4}, "b": 2}

    assert canonical_dump(left) == canonical_dump(right)
    assert compute_event_hash(left) == compute_event_hash(right)


def test_hash_changes_when_event_field_changes() -> None:
    event = EvolutionEvent.new(EventType.MODULE_PROPOSED, module_id="alpha").to_dict()
    changed = dict(event)
    changed["module_id"] = "beta"

    assert compute_event_hash(event) != compute_event_hash(changed)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_ledger_rejects_non_json_float_values(tmp_path, value: float) -> None:
    ledger = EvolutionLedger(tmp_path)
    event = EvolutionEvent.new(EventType.MODULE_FAILED, result={"score": value})

    with pytest.raises(ValueError):
        ledger.append(event)


def test_verify_chain_detects_tampering(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path)
    ledger.append(EvolutionEvent.new(EventType.MODULE_PROPOSED, module_id="alpha"))
    ledger.append(EvolutionEvent.new(EventType.MODULE_MANIFEST_VALIDATED, module_id="alpha"))

    event_path = tmp_path / "memory" / "evolution_events.jsonl"
    lines = event_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["module_id"] = "beta"
    lines[0] = json.dumps(tampered, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    event_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = ledger.verify_chain()

    assert result.chain_integrity == "broken"
    assert result.valid is False
    assert result.broken_at_index == 0
    assert result.broken_line == 1
    assert result.expected_hash
    assert result.actual_hash
    assert "event_hash" in result.error


def test_verify_chain_accepts_valid_ledger(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path)
    first = ledger.append(EvolutionEvent.new(EventType.MODULE_PROPOSED, module_id="alpha"))
    second = ledger.append(
        EvolutionEvent.new(EventType.MODULE_MANIFEST_VALIDATED, module_id="alpha")
    )

    result = ledger.verify_chain()

    assert result.ok
    assert result.event_count == 2
    assert result.terminal_event_hash == second.event_hash
    assert result.terminal_event_hash != first.event_hash


def test_concurrent_appends_keep_jsonl_chain_intact(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path)
    barrier = threading.Barrier(12)

    def append_one(index: int) -> str:
        barrier.wait(timeout=5)
        event = ledger.append(EvolutionEvent.new(EventType.MODULE_PROPOSED, module_id=f"module-{index}"))
        return event.event_hash

    with ThreadPoolExecutor(max_workers=12) as executor:
        event_hashes = list(executor.map(append_one, range(12)))

    result = ledger.verify_chain()
    lines = (tmp_path / "memory" / "evolution_events.jsonl").read_text(encoding="utf-8").splitlines()

    assert result.ok
    assert result.event_count == 12
    assert len(lines) == 12
    assert len(set(event_hashes)) == 12
    for line in lines:
        assert json.loads(line)["event_hash"]
