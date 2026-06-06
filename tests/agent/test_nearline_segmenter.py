from __future__ import annotations

from OriginAgent.memory.segmenter import canonicalize_session_messages, segment_memcells


def test_canonicalize_session_messages_preserves_tool_metadata_and_text_blocks() -> None:
    canonical = canonicalize_session_messages(
        "websocket:chat1",
        [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Need help"}],
                "timestamp": "2026-06-05T10:00:00+08:00",
                "media": ["D:/tmp/a.png"],
            },
            {
                "role": "tool",
                "content": [{"type": "text", "text": "Found result"}],
                "timestamp": "2026-06-05T10:00:05+08:00",
                "name": "search_memory",
                "tool_call_id": "tool_1",
            },
        ],
        channel="websocket",
        chat_id="chat1",
    )

    assert len(canonical) == 2
    assert canonical[0].content == "Need help"
    assert canonical[0].metadata["media"] == ["D:/tmp/a.png"]
    assert canonical[1].tool_name == "search_memory"
    assert canonical[1].tool_call_id == "tool_1"
    assert canonical[1].content == "Found result"


def test_segment_memcells_splits_on_user_return_after_assistant_and_tool() -> None:
    canonical = canonicalize_session_messages(
        "websocket:chat1",
        [
            {"role": "user", "content": "book a reminder", "timestamp": "2026-06-05T10:00:00+08:00"},
            {
                "role": "assistant",
                "content": "",
                "timestamp": "2026-06-05T10:00:02+08:00",
                "tool_calls": [{"id": "call_1", "function": {"name": "create_reminder"}}],
            },
            {
                "role": "tool",
                "content": "created",
                "timestamp": "2026-06-05T10:00:03+08:00",
                "name": "create_reminder",
                "tool_call_id": "call_1",
            },
            {"role": "assistant", "content": "Scheduled.", "timestamp": "2026-06-05T10:00:05+08:00"},
            {"role": "user", "content": "also next monday", "timestamp": "2026-06-05T10:01:00+08:00"},
        ],
    )

    memcells = segment_memcells(canonical, idle_gap_seconds=900, max_messages_per_memcell=12)

    assert len(memcells) == 4
    assert [cell.kind for cell in memcells] == [
        "conversation_turn",
        "mixed_turn",
        "conversation_turn",
        "conversation_turn",
    ]
    assert memcells[1].message_ids == [canonical[1].message_id, canonical[2].message_id]
    assert memcells[1].metadata["contains_tool_call"] is True
    assert memcells[-1].messages[0].content == "also next monday"


def test_segment_memcells_splits_on_idle_gap_and_message_cap() -> None:
    canonical = canonicalize_session_messages(
        "websocket:chat1",
        [
            {"role": "user", "content": "one", "timestamp": "2026-06-05T10:00:00+08:00"},
            {"role": "assistant", "content": "two", "timestamp": "2026-06-05T10:00:10+08:00"},
            {"role": "assistant", "content": "three", "timestamp": "2026-06-05T10:30:00+08:00"},
            {"role": "assistant", "content": "four", "timestamp": "2026-06-05T10:30:10+08:00"},
        ],
    )

    memcells = segment_memcells(canonical, idle_gap_seconds=60, max_messages_per_memcell=2)

    assert len(memcells) == 2
    assert [len(cell.messages) for cell in memcells] == [2, 2]
    assert memcells[0].ended_at == "2026-06-05T10:00:10+08:00"
    assert memcells[1].started_at == "2026-06-05T10:30:00+08:00"
