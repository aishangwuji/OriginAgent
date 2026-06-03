from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from OriginAgent.session.cold_archive import SessionColdArchiveStore
from OriginAgent.session.search import SessionSearchService


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_searches_session_jsonl_roles_and_filters(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "sessions" / "websocket_chat1.jsonl",
        [
            {
                "_type": "metadata",
                "key": "websocket:chat1",
                "created_at": "2026-05-18T00:00:00",
                "updated_at": "2026-05-19T11:00:00",
                "metadata": {},
            },
            {
                "role": "user",
                "content": "Please review the lighting automation plan.",
                "timestamp": "2026-05-19T10:00:00",
            },
            {
                "role": "assistant",
                "content": "The lighting automation plan needs a safety fallback.",
                "timestamp": "2026-05-19T10:01:00",
            },
            {
                "role": "tool",
                "name": "read_file",
                "content": "lighting automation source note",
                "timestamp": "2026-05-19T10:02:00",
            },
        ],
    )

    service = SessionSearchService(tmp_path)
    result = service.search(
        query="lighting automation",
        roles=["assistant"],
        session_key="websocket:chat1",
        since="2026-05-19",
        until="2026-05-19T23:59:59",
    )

    assert result["searched_sources"] == ["sessions", "history", "webui"]
    assert result["total_matches"] == 1
    assert result["results"][0]["role"] == "assistant"
    assert result["results"][0]["session_key"] == "websocket:chat1"
    assert result["results"][0]["locator"]["message_index"] == 1


def test_searches_history_with_cursor_long_content_and_redaction(tmp_path: Path) -> None:
    long_secret = "archive summary " + ("x" * 520) + " api_key=supersecret12345"
    _write_jsonl(
        tmp_path / "memory" / "history.jsonl",
        [
            {
                "cursor": 7,
                "timestamp": "2026-05-18T12:30:00",
                "content": long_secret,
            }
        ],
    )

    result = SessionSearchService(tmp_path).search(query="api_key", sources=["history"])

    assert result["total_matches"] == 1
    row = result["results"][0]
    assert row["source"] == "history"
    assert row["session_key"] == "memory:history"
    assert row["locator"]["cursor"] == 7
    assert row["locator"]["has_full_content"] is True
    assert "[REDACTED_SECRET]" in row["snippet"]
    assert "supersecret12345" not in row["snippet"]


def test_searches_cold_archive_only_when_explicitly_requested(tmp_path: Path) -> None:
    result = SessionColdArchiveStore(tmp_path).archive(
        "cli:direct",
        [
            {
                "role": "user",
                "content": "cold archive exact phrase",
                "timestamp": "2026-05-20T10:00:00",
            },
            {
                "role": "assistant",
                "content": "cold archive assistant reply",
                "timestamp": "2026-05-20T10:01:00",
            },
        ],
        reason="auto_compact",
    )
    assert result is not None

    service = SessionSearchService(tmp_path)
    default = service.search(query="cold archive exact phrase")
    explicit = service.search(query="cold archive exact phrase", sources=["cold"])

    assert default["searched_sources"] == ["sessions", "history", "webui"]
    assert default["total_matches"] == 0
    assert explicit["searched_sources"] == ["cold"]
    assert explicit["total_matches"] == 1
    row = explicit["results"][0]
    assert row["source"] == "cold"
    assert row["session_key"] == "cli:direct"
    assert row["role"] == "user"
    assert row["record_status"] == "auto_compact"
    assert row["locator"]["archive_id"] == result.archive_id
    assert row["locator"]["batch_line"] == result.line
    assert row["locator"]["message_index"] == 0
    assert row["locator"]["session_key"] == "cli:direct"
    assert row["locator"]["has_full_content"] is True


def test_searches_webui_transcript_and_maps_websocket_stem(tmp_path: Path) -> None:
    webui_dir = tmp_path / "webui"
    _write_jsonl(
        webui_dir / "websocket_chat1.jsonl",
        [
            {
                "event": "user",
                "text": "Find last week's lighting automation.",
                "timestamp": "2026-05-19T08:00:00",
            },
            {
                "event": "message",
                "text": "Recovered the lighting automation summary.",
                "timestamp": "2026-05-19T08:01:00",
            },
        ],
    )
    _write_jsonl(
        webui_dir / "odd-name.jsonl",
        [{"event": "user", "text": "lighting automation fallback"}],
    )

    mapped = SessionSearchService(tmp_path, webui_dir=webui_dir).search(
        query="lighting automation",
        sources=["webui"],
        session_key="websocket:chat1",
    )
    unknown = SessionSearchService(tmp_path, webui_dir=webui_dir).search(
        query="fallback",
        sources=["webui"],
    )

    assert mapped["total_matches"] == 2
    assert {row["session_key"] for row in mapped["results"]} == {"websocket:chat1"}
    assert unknown["results"][0]["session_key"] == "unknown"
    assert unknown["results"][0]["locator"]["stem"] == "odd-name"


def test_corrupt_jsonl_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "cli_direct.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                json.dumps({"_type": "metadata", "key": "cli:direct"}),
                "{bad json",
                json.dumps(
                    {
                        "role": "user",
                        "content": "literal target",
                        "timestamp": "2026-05-19T10:00:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = SessionSearchService(tmp_path).search(query="literal target", sources=["sessions"])

    assert result["total_matches"] == 1
    assert result["skipped_records"] == 1


def test_filters_limit_clamp_and_sorting_are_stable(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "sessions" / "websocket_chat1.jsonl",
        [
            {"_type": "metadata", "key": "websocket:chat1"},
            {
                "role": "user",
                "content": "alpha",
                "timestamp": "2026-05-18T09:00:00",
            },
            {
                "role": "assistant",
                "content": "alpha alpha",
                "timestamp": "2026-05-18T09:00:00",
            },
            {
                "role": "assistant",
                "content": "alpha newest",
                "timestamp": "2026-05-19T09:00:00",
            },
        ],
    )
    _write_jsonl(
        tmp_path / "memory" / "history.jsonl",
        [
            {
                "cursor": 1,
                "timestamp": "2026-05-18T09:00:00",
                "content": "alpha alpha alpha",
            }
        ],
    )

    result = SessionSearchService(tmp_path).search(
        query="alpha",
        channel="websocket",
        chat_id="chat1",
        limit=100,
    )

    assert len(result["results"]) == 3
    assert result["results"][0]["snippet"] == "alpha newest"
    assert result["results"][1]["match_count"] == 2
    assert result["results"][2]["match_count"] == 1


def test_date_only_until_includes_entire_day(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "sessions" / "cli_direct.jsonl",
        [
            {"_type": "metadata", "key": "cli:direct"},
            {
                "role": "user",
                "content": "deadline note",
                "timestamp": "2026-05-19T23:59:58",
            },
        ],
    )

    result = SessionSearchService(tmp_path).search(
        query="deadline",
        sources=["sessions"],
        until="2026-05-19",
    )

    assert result["total_matches"] == 1


def test_cache_reuse_and_invalidation_on_file_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "cli_direct.jsonl"
    _write_jsonl(
        path,
        [
            {"_type": "metadata", "key": "cli:direct"},
            {"role": "user", "content": "first cached phrase", "timestamp": "2026-05-19T10:00:00"},
        ],
    )
    service = SessionSearchService(tmp_path)

    first = service.search(query="first cached phrase", sources=["sessions"])
    path.write_text("not json\n", encoding="utf-8")
    second = service.search(query="first cached phrase", sources=["sessions"])
    _write_jsonl(
        path,
        [
            {"_type": "metadata", "key": "cli:direct"},
            {"role": "user", "content": "second cached phrase", "timestamp": "2026-05-19T10:01:00"},
        ],
    )
    third = service.search(query="second cached phrase", sources=["sessions"])

    assert first["total_matches"] == 1
    assert second["total_matches"] == 0
    assert second["skipped_records"] == 1
    assert third["total_matches"] == 1


def test_large_range_returns_performance_note(tmp_path: Path) -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=45)).date().isoformat()
    _write_jsonl(
        tmp_path / "sessions" / "cli_direct.jsonl",
        [
            {"_type": "metadata", "key": "cli:direct"},
            {"role": "user", "content": "ancient marker", "timestamp": f"{old}T10:00:00"},
        ],
    )

    result = SessionSearchService(tmp_path).search(
        query="ancient marker",
        sources=["sessions"],
        since=old,
    )

    assert result["total_matches"] == 1
    assert result["performance_note"] is not None
    assert "Large time range" in result["performance_note"]


def test_large_session_file_search_performance_smoke(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "cli_direct.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().replace(microsecond=0).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"_type": "metadata", "key": "cli:direct"}) + "\n")
        for index in range(100_000):
            content = "performance needle" if index == 99_999 else f"ordinary line {index}"
            f.write(
                json.dumps(
                    {"role": "user", "content": content, "timestamp": now},
                    ensure_ascii=False,
                )
                + "\n"
            )

    start = time.perf_counter()
    result = SessionSearchService(tmp_path).search(query="performance needle", sources=["sessions"])
    elapsed = time.perf_counter() - start

    assert result["total_matches"] == 1
    assert elapsed < 2.0
