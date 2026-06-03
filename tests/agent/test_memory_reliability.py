"""Reliability tests for memory history and Dream file writes."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.memory import Dream, MemoryStore
from OriginAgent.agent.runner import AgentRunResult


EMPTY_FACT_PROPOSALS = json.dumps({
    "facts_to_upsert": [],
    "facts_to_deprecate": [],
    "memory_render_hints": [],
})


def _run_result(stop_reason: str = "completed") -> AgentRunResult:
    return AgentRunResult(
        final_content=stop_reason,
        stop_reason=stop_reason,
        messages=[],
        tools_used=[],
        usage={},
        tool_events=[],
    )


def _history_lines(store: MemoryStore) -> list[str]:
    return store.history_file.read_text(encoding="utf-8").splitlines()


def test_concurrent_append_history_has_unique_monotonic_cursors(tmp_path):
    store = MemoryStore(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        cursors = list(pool.map(lambda i: store.append_history(f"event {i}"), range(50)))

    assert sorted(cursors) == list(range(1, 51))
    entries = store.read_unprocessed_history(since_cursor=0)
    assert len(entries) == 50
    assert sorted(entry["cursor"] for entry in entries) == list(range(1, 51))


def test_append_recovers_when_cursor_file_lags_and_tail_is_corrupt(tmp_path):
    store = MemoryStore(tmp_path, max_history_entries=1)
    store.history_file.write_text(
        json.dumps({"cursor": 42, "timestamp": "2026-01-01 00:00", "content": "old"}) + "\n"
        '{"cursor":',
        encoding="utf-8",
    )
    store._cursor_file.write_text("10", encoding="utf-8")

    cursor = store.append_history("new")

    assert cursor == 43
    store.compact_history(processed_through=42)
    text = store.history_file.read_text(encoding="utf-8")
    assert '{"cursor":' in text
    assert '"cursor": 43' in text
    assert '"cursor": 42' not in text


def test_append_recovers_with_valid_json_non_object_history_lines(tmp_path):
    store = MemoryStore(tmp_path)
    store.history_file.write_text(
        json.dumps({"cursor": 42, "timestamp": "2026-01-01 00:00", "content": "old"}) + "\n"
        "[]\n"
        '"not a record"\n'
        "123\n",
        encoding="utf-8",
    )
    store._cursor_file.write_text("10", encoding="utf-8")

    cursor = store.append_history("new")

    assert cursor == 43
    text = store.history_file.read_text(encoding="utf-8")
    assert "[]\n" in text
    assert '"not a record"\n' in text
    assert "123\n" in text


def test_compact_history_preserves_invalid_and_unprocessed_lines(tmp_path):
    store = MemoryStore(tmp_path, max_history_entries=3)
    lines = [
        json.dumps({"cursor": 1, "timestamp": "t", "content": "processed-1"}),
        "{broken json",
        "[]",
        '"not a record"',
        "123",
        json.dumps({"timestamp": "t", "content": "no cursor"}),
        json.dumps({"cursor": True, "timestamp": "t", "content": "bool cursor"}),
        json.dumps({"cursor": 5, "timestamp": "t", "content": "unprocessed"}),
        json.dumps({"cursor": 2, "timestamp": "t", "content": "processed-2"}),
    ]
    store.history_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    store.compact_history(processed_through=2)

    text = store.history_file.read_text(encoding="utf-8")
    assert "processed-1" not in text
    assert "processed-2" not in text
    assert "{broken json" in text
    assert "[]" in text
    assert '"not a record"' in text
    assert "123" in text
    assert "no cursor" in text
    assert "bool cursor" in text
    assert "unprocessed" in text
    assert len(_history_lines(store)) == 7


def test_mark_dream_processed_keeps_cursor_when_compact_fails(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path)

    def fail_compact(_cursor):
        raise RuntimeError("compact failed")

    monkeypatch.setattr(store, "_compact_history_unlocked", fail_compact)

    store.mark_dream_processed(7)

    assert store.get_last_dream_cursor() == 7


@pytest.mark.asyncio
async def test_dream_phase2_failure_restores_memory_workspace(tmp_path):
    store = MemoryStore(tmp_path)
    store.write_soul("# Soul\n- original\n")
    store.write_user("# User\n- original\n")
    store.write_memory("# Memory\n- original\n")
    existing_skill = tmp_path / "skills" / "existing" / "SKILL.md"
    existing_skill.parent.mkdir(parents=True)
    existing_skill.write_text("original skill", encoding="utf-8")
    store.append_history("remember this")

    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=MagicMock(content=EMPTY_FACT_PROPOSALS))
    dream = Dream(store=store, provider=provider, model="test-model")

    async def dirty_phase2(_spec):
        store.write_soul("# Soul\n- dirty\n")
        store.user_file.unlink()
        store.write_memory("# Memory\n- dirty\n")
        (tmp_path / "skills" / "new-skill").mkdir(parents=True)
        (tmp_path / "skills" / "new-skill" / "SKILL.md").write_text(
            "dirty skill",
            encoding="utf-8",
        )
        existing_skill.write_text("dirty existing skill", encoding="utf-8")
        return _run_result("max_iterations")

    dream._runner.run = dirty_phase2  # type: ignore[method-assign]

    result = await dream.run()

    assert result is False
    assert store.get_last_dream_cursor() == 0
    assert store.read_soul() == "# Soul\n- original\n"
    assert store.read_user() == "# User\n- original\n"
    assert store.read_memory() == "# Memory\n- original\n"
    assert existing_skill.read_text(encoding="utf-8") == "original skill"
    assert not (tmp_path / "skills" / "new-skill").exists()
    assert store.read_unprocessed_history(since_cursor=0)[0]["content"] == "remember this"


@pytest.mark.asyncio
async def test_dream_success_marks_processed_and_compacts_safely(tmp_path):
    store = MemoryStore(tmp_path, max_history_entries=2)
    store.write_soul("# Soul\n")
    store.write_user("# User\n")
    store.write_memory("# Memory\n")
    store.append_history("event 1")
    store.append_history("event 2")
    store.append_history("event 3")

    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=MagicMock(content=EMPTY_FACT_PROPOSALS))
    dream = Dream(store=store, provider=provider, model="test-model")
    dream._runner.run = AsyncMock(return_value=_run_result("completed"))  # type: ignore[method-assign]

    result = await dream.run()

    assert result is True
    assert store.get_last_dream_cursor() == 3
    entries = store.read_unprocessed_history(since_cursor=0)
    assert [entry["cursor"] for entry in entries] == [2, 3]
