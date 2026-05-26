from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from OriginAgent.agent.facts import FactStore
from OriginAgent.session.cold_archive import SessionColdArchiveStore
from OriginAgent.session.search import SessionSearchService
from OriginAgent.session.search_index import SearchTextNormalizer, SessionSearchIndexService

RAW_SECRET = "supersecret12345"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_session(path: Path, content: str) -> None:
    _write_jsonl(
        path,
        [
            {"_type": "metadata", "key": "cli:direct"},
            {
                "role": "user",
                "content": content,
                "timestamp": "2026-05-20T10:00:00",
            },
        ],
    )


def test_normalizer_generates_multilingual_and_identifier_tokens() -> None:
    tokens = SearchTextNormalizer().tokens("我爱智能家居，HomeAssistant MCP foo_bar baz-qux")

    assert "智能家居" in tokens
    assert "我爱" in tokens
    assert "homeassistant" in tokens
    assert "home" in tokens
    assert "assistant" in tokens
    assert "foo_bar" in tokens
    assert "foo" in tokens
    assert "bar" in tokens
    assert "baz" in tokens
    assert "qux" in tokens


def test_index_time_redaction_keeps_raw_secret_out_of_sqlite(tmp_path: Path) -> None:
    _write_session(
        tmp_path / "sessions" / "cli_direct.jsonl",
        f"我爱智能家居，HomeAssistant MCP 调试 api_key={RAW_SECRET}",
    )
    index = SessionSearchIndexService(tmp_path, webui_dir=tmp_path / "webui")

    status = index.refresh_incremental(sources=["sessions"])

    assert status["session_search_indexed_doc_count"] == 1
    assert status["session_search_skipped_secret_risk_count"] == 0
    data = index.db_path.read_bytes()
    assert RAW_SECRET.encode() not in data
    assert b"REDACTED_SECRET" in data

    result = SessionSearchService(
        tmp_path,
        webui_dir=tmp_path / "webui",
        index_service=index,
    ).search(query="智能家居", sources=["sessions"], mode="semantic")

    assert result["total_matches"] == 1
    row = result["results"][0]
    assert row["match_type"] == "semantic"
    assert row["redacted"] is True
    assert "[REDACTED_SECRET]" in row["snippet"]
    assert RAW_SECRET not in row["snippet"]


def test_residual_secret_risk_skips_index_document(tmp_path: Path) -> None:
    _write_session(
        tmp_path / "sessions" / "cli_direct.jsonl",
        "AWS key leak AKIAIOSFODNN7EXAMPLE",
    )
    index = SessionSearchIndexService(tmp_path, webui_dir=tmp_path / "webui")
    index.refresh_incremental(sources=["sessions"])

    conn = sqlite3.connect(index.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 0
    finally:
        conn.close()
    assert index.runtime_status()["session_search_skipped_secret_risk_count"] == 1


def test_facts_are_indexed_only_when_explicit_source_requested(tmp_path: Path) -> None:
    fact = FactStore(tmp_path).upsert_fact(
        "用户喜欢智能家居故障排查清单。",
        category="preference",
        scope="core",
        owner="user",
        source_excerpt="用户提到智能家居排查。",
    )
    index = SessionSearchIndexService(tmp_path, webui_dir=tmp_path / "webui")
    index.refresh_incremental(sources=["facts"])
    service = SessionSearchService(tmp_path, webui_dir=tmp_path / "webui", index_service=index)

    default_result = service.search(query="故障排查", mode="semantic")
    explicit_result = service.search(query="故障排查", sources=["facts"], mode="semantic")

    assert default_result["total_matches"] == 0
    assert explicit_result["total_matches"] == 1
    row = explicit_result["results"][0]
    assert row["source"] == "facts"
    assert row["record_status"] == "active"
    assert row["locator"]["fact_id"] == fact.fact_id


def test_cold_archive_indexed_only_when_explicit_source_requested(tmp_path: Path) -> None:
    archive = SessionColdArchiveStore(tmp_path).archive(
        "cli:direct",
        [
            {
                "role": "user",
                "content": "cold indexed phrase for recall",
                "timestamp": "2026-05-20T11:00:00",
            },
            {
                "role": "assistant",
                "content": "assistant-only archived detail",
                "timestamp": "2026-05-20T11:01:00",
            },
        ],
        reason="session_file_cap",
    )
    assert archive is not None
    index = SessionSearchIndexService(tmp_path, webui_dir=tmp_path / "webui")
    status = index.refresh_incremental(sources=["cold"])
    service = SessionSearchService(tmp_path, webui_dir=tmp_path / "webui", index_service=index)

    default_result = service.search(query="cold indexed phrase", mode="semantic")
    explicit_result = service.search(
        query="cold indexed phrase",
        sources=["cold"],
        mode="semantic",
    )

    assert status["session_search_indexed_source_counts"]["cold"] == 2
    assert default_result["total_matches"] == 0
    assert explicit_result["total_matches"] == 1
    row = explicit_result["results"][0]
    assert row["source"] == "cold"
    assert row["record_status"] == "session_file_cap"
    assert row["locator"]["archive_id"] == archive.archive_id
    assert row["locator"]["message_index"] == 0


def test_deleted_cold_archive_file_removes_index_rows(tmp_path: Path) -> None:
    archive = SessionColdArchiveStore(tmp_path).archive(
        "cli:direct",
        [{"role": "user", "content": "temporary cold searchable"}],
        reason="auto_compact",
    )
    assert archive is not None
    index = SessionSearchIndexService(tmp_path, webui_dir=tmp_path / "webui")

    first = index.refresh_incremental(sources=["cold"])
    assert first["session_search_indexed_source_counts"]["cold"] == 1

    archive.path.unlink()
    second = index.refresh_incremental(sources=["cold"])

    assert second["session_search_indexed_doc_count"] == 0
    result = SessionSearchService(
        tmp_path,
        webui_dir=tmp_path / "webui",
        index_service=index,
    ).search(query="temporary cold searchable", sources=["cold"], mode="semantic")
    assert result["total_matches"] == 0


def test_semantic_disabled_falls_back_to_literal(tmp_path: Path) -> None:
    _write_session(tmp_path / "sessions" / "cli_direct.jsonl", "lighting fallback target")
    index = SessionSearchIndexService(
        tmp_path,
        webui_dir=tmp_path / "webui",
        semantic_enabled=False,
    )
    result = SessionSearchService(
        tmp_path,
        webui_dir=tmp_path / "webui",
        index_service=index,
        semantic_enabled=False,
    ).search(query="lighting fallback", sources=["sessions"], mode="hybrid")

    assert result["mode"] == "hybrid"
    assert result["total_matches"] == 1
    assert result["results"][0]["match_type"] == "literal"
    assert "falling back to literal" in result["performance_note"]


def test_single_cjk_character_uses_literal_fallback(tmp_path: Path) -> None:
    _write_session(tmp_path / "sessions" / "cli_direct.jsonl", "灯")
    index = SessionSearchIndexService(tmp_path, webui_dir=tmp_path / "webui")
    result = SessionSearchService(
        tmp_path,
        webui_dir=tmp_path / "webui",
        index_service=index,
    ).search(query="灯", sources=["sessions"], mode="semantic")

    assert result["total_matches"] == 1
    assert result["results"][0]["match_type"] == "literal"


def test_deleted_source_file_removes_index_rows(tmp_path: Path) -> None:
    session_path = tmp_path / "sessions" / "cli_direct.jsonl"
    _write_session(session_path, "temporary searchable phrase")
    index = SessionSearchIndexService(tmp_path, webui_dir=tmp_path / "webui")

    first = index.refresh_incremental(sources=["sessions"])
    assert first["session_search_indexed_doc_count"] == 1

    session_path.unlink()
    second = index.refresh_incremental(sources=["sessions"])

    assert second["session_search_indexed_doc_count"] == 0
    result = SessionSearchService(
        tmp_path,
        webui_dir=tmp_path / "webui",
        index_service=index,
    ).search(query="temporary searchable", sources=["sessions"], mode="semantic")
    assert result["total_matches"] == 0


def test_zero_refresh_budget_marks_stale_and_uses_literal_fallback(tmp_path: Path) -> None:
    _write_session(tmp_path / "sessions" / "cli_direct.jsonl", "智能家居预算回退")
    index = SessionSearchIndexService(tmp_path, webui_dir=tmp_path / "webui")
    service = SessionSearchService(
        tmp_path,
        webui_dir=tmp_path / "webui",
        index_service=index,
        max_tool_refresh_ms=0,
    )

    result = service.search(query="智能家居", sources=["sessions"], mode="semantic")

    assert result["index_stale"] is True
    assert result["total_matches"] == 1
    assert result["results"][0]["match_type"] == "literal"


def test_backend_literal_does_not_create_index_file(tmp_path: Path) -> None:
    _write_session(tmp_path / "sessions" / "cli_direct.jsonl", "literal backend only")
    service = SessionSearchService(
        tmp_path,
        webui_dir=tmp_path / "webui",
        index_backend="literal",
    )

    result = service.search(query="literal backend", sources=["sessions"], mode="hybrid")

    assert result["total_matches"] == 1
    assert result["results"][0]["match_type"] == "literal"
    assert not (tmp_path / "memory" / "session_search.sqlite3").exists()


def test_indexed_time_filter_runs_before_sql_limit(tmp_path: Path) -> None:
    base = datetime(2026, 1, 1, 10, 0, 0)
    rows = [{"_type": "metadata", "key": "cli:direct"}]
    for index in range(260):
        rows.append(
            {
                "role": "user",
                "content": f"needle outside range {index}",
                "timestamp": (base + timedelta(minutes=index)).isoformat(),
            }
        )
    rows.append(
        {
            "role": "user",
            "content": "needle inside requested range",
            "timestamp": "2026-05-20T10:00:00",
        }
    )
    _write_jsonl(tmp_path / "sessions" / "cli_direct.jsonl", rows)
    index = SessionSearchIndexService(tmp_path, webui_dir=tmp_path / "webui")
    index.refresh_incremental(sources=["sessions"])

    result = SessionSearchService(
        tmp_path,
        webui_dir=tmp_path / "webui",
        index_service=index,
    ).search(
        query="needle",
        sources=["sessions"],
        mode="semantic",
        since="2026-05-01",
        limit=1,
    )

    assert result["total_matches"] == 1
    assert "inside requested range" in result["results"][0]["snippet"]
