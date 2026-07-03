"""Tests for SharedSpace — cross-tenant facts, devices, calendar."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from OriginAgent.identity.shared_space import SharedSpace


class TestSharedSpace:
    """Unit tests for the SharedSpace dataclass."""

    def test_facts_dir_is_under_shared_facts(self) -> None:
        space = SharedSpace(workspace=Path("/tmp/test"))
        assert space.facts_dir == Path("/tmp/test/shared/facts")

    def test_device_domains_defaults_to_smart_home(self) -> None:
        space = SharedSpace(workspace=Path("/tmp/test"))
        assert space.device_domains == ["smart_home"]

    def test_calendar_path_is_under_shared(self) -> None:
        space = SharedSpace(workspace=Path("/tmp/test"))
        assert space.calendar_path == Path("/tmp/test/shared/calendar.jsonl")

    def test_shared_facts_returns_empty_when_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            space = SharedSpace(workspace=Path(td))
            assert space.shared_facts() == []

    def test_add_shared_fact_creates_dir_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            space = SharedSpace(workspace=Path(td))
            space.add_shared_fact({"content": "House alarm armed at 10pm"})

            facts_file = space.facts_dir / "shared_facts.jsonl"
            assert facts_file.exists()
            content = facts_file.read_text(encoding="utf-8").strip()
            assert content, "File should not be empty"

    def test_add_then_shared_facts_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            space = SharedSpace(workspace=Path(td))
            space.add_shared_fact({"content": "Garbage pickup is Tuesday"})

            facts = space.shared_facts()
            assert len(facts) == 1
            assert facts[0]["content"] == "Garbage pickup is Tuesday"

    def test_shared_facts_adds_timestamp_and_actor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            space = SharedSpace(workspace=Path(td))
            space.add_shared_fact({"content": "AC set to 24C"}, added_by="dad")

            facts = space.shared_facts()
            assert len(facts) == 1
            assert facts[0]["added_by"] == "dad"
            assert "added_at" in facts[0]

    def test_shared_facts_respects_limit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            space = SharedSpace(workspace=Path(td))
            for i in range(10):
                space.add_shared_fact({"content": f"fact_{i}"})

            all_facts = space.shared_facts(limit=100)
            assert len(all_facts) == 10

            limited = space.shared_facts(limit=3)
            assert len(limited) == 3
            # Most recent 3 facts
            assert limited[0]["content"] == "fact_7"
            assert limited[1]["content"] == "fact_8"
            assert limited[2]["content"] == "fact_9"

    def test_shared_facts_skips_empty_lines(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            facts_file = Path(td) / "shared" / "facts" / "shared_facts.jsonl"
            facts_file.parent.mkdir(parents=True)
            facts_file.write_text(
                json.dumps({"content": "one"}) + "\n"
                + "\n"  # empty line — should be skipped
                + json.dumps({"content": "two"}) + "\n"
                + "   \n"  # whitespace-only line — should be skipped
                + json.dumps({"content": "three"}) + "\n",
                encoding="utf-8",
            )

            space = SharedSpace(workspace=Path(td))
            facts = space.shared_facts()
            assert len(facts) == 3

    def test_multiple_facts_ordered_oldest_first(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            space = SharedSpace(workspace=Path(td))
            space.add_shared_fact({"content": "first"})
            space.add_shared_fact({"content": "second"})
            space.add_shared_fact({"content": "third"})

            facts = space.shared_facts()
            assert [f["content"] for f in facts] == ["first", "second", "third"]
