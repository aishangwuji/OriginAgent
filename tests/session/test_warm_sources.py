"""Phase 4 Task 6 单元测试：warm_archive / warm_summaries source 检索。

覆盖 SubTask 6.5 要求的三个场景：
- warm_archive 检索命中（含 session_key 精确读文件、未指定时全局扫描、role 过滤）
- warm_summaries 检索命中（含 summary / commitments / key_entities 多字段匹配、结构化字段完整性）
- 与现有 source 不冲突（默认 source 不含 warm、显式 warm 不影响标准 source、混合检索正常）

数据格式与 agent_turn_pipeline._archive_warm_messages 写入格式保持一致。
"""

from __future__ import annotations

import json
from pathlib import Path

from OriginAgent.session.search import SessionSearchService


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _warm_summary_entry(
    session_key: str,
    *,
    turn_range: str = "1-50",
    summary: str = "讨论了照明自动化方案",
    commitments: list[str] | None = None,
    decisions: list[str] | None = None,
    open_questions: list[str] | None = None,
    key_entities: list[str] | None = None,
    created_at: str = "2026-07-07T13:50:00Z",
) -> dict:
    """构造与 agent_turn_pipeline._archive_warm_messages 一致的结构化索引条目。"""
    return {
        "session_key": session_key,
        "turn_range": turn_range,
        "summary": summary,
        "commitments": commitments or [],
        "decisions": decisions or [],
        "open_questions": open_questions or [],
        "key_entities": key_entities or [],
        "timestamp_range": {"start": "2026-07-07T13:00:00Z", "end": "2026-07-07T13:49:30Z"},
        "locator": f"warm_archive/{session_key.replace(':', '_')}.jsonl:0:100",
        "created_at": created_at,
    }


# ── warm_archive 检索 ────────────────────────────────────────────────


def test_warm_archive_search_hits_with_session_key(tmp_path: Path) -> None:
    """指定 session_key 时精确读取对应归档文件并命中 content 子串。"""
    _write_jsonl(
        tmp_path / "warm_archive" / "cli_warm-hit.jsonl",
        [
            {"role": "user", "content": "请确认照明自动化方案", "timestamp": "2026-07-07T13:00:00Z"},
            {"role": "assistant", "content": "照明自动化方案已就绪", "timestamp": "2026-07-07T13:00:30Z"},
            {"role": "user", "content": "无关消息不命中", "timestamp": "2026-07-07T13:01:00Z"},
        ],
    )

    result = SessionSearchService(tmp_path).search(
        query="照明自动化",
        sources=["warm_archive"],
        session_key="cli:warm-hit",
    )

    assert result["searched_sources"] == ["warm_archive"]
    assert result["total_matches"] == 2
    sources = {row["source"] for row in result["results"]}
    assert sources == {"warm_archive"}
    for row in result["results"]:
        assert row["session_key"] == "cli:warm-hit"
        assert row["locator"]["has_full_content"] is True
        assert row["match_count"] >= 1
        assert row["match_type"] == "literal"


def test_warm_archive_search_case_insensitive(tmp_path: Path) -> None:
    """literal 模式大小写不敏感子串匹配。"""
    _write_jsonl(
        tmp_path / "warm_archive" / "cli_case.jsonl",
        [
            {"role": "user", "content": "Deploy the API Gateway", "timestamp": "2026-07-07T14:00:00Z"},
        ],
    )

    result = SessionSearchService(tmp_path).search(
        query="api gateway",
        sources=["warm_archive"],
        session_key="cli:case",
    )

    assert result["total_matches"] == 1
    assert result["results"][0]["role"] == "user"


def test_warm_archive_search_scans_all_files_without_session_key(tmp_path: Path) -> None:
    """未指定 session_key 时扫描 warm_archive 目录下全部文件。"""
    _write_jsonl(
        tmp_path / "warm_archive" / "cli_alpha.jsonl",
        [{"role": "user", "content": "alpha target phrase", "timestamp": "2026-07-07T10:00:00Z"}],
    )
    _write_jsonl(
        tmp_path / "warm_archive" / "cli_beta.jsonl",
        [{"role": "assistant", "content": "beta target phrase", "timestamp": "2026-07-07T11:00:00Z"}],
    )

    result = SessionSearchService(tmp_path).search(
        query="target phrase",
        sources=["warm_archive"],
    )

    assert result["total_matches"] == 2
    session_keys = {row["session_key"] for row in result["results"]}
    assert session_keys == {"cli:alpha", "cli:beta"}


def test_warm_archive_search_role_filter(tmp_path: Path) -> None:
    """role 过滤仅返回指定角色的命中。"""
    _write_jsonl(
        tmp_path / "warm_archive" / "cli_role.jsonl",
        [
            {"role": "user", "content": "role filter target", "timestamp": "2026-07-07T09:00:00Z"},
            {"role": "assistant", "content": "role filter target", "timestamp": "2026-07-07T09:00:30Z"},
        ],
    )

    result = SessionSearchService(tmp_path).search(
        query="role filter target",
        sources=["warm_archive"],
        session_key="cli:role",
        roles=["assistant"],
    )

    assert result["total_matches"] == 1
    assert result["results"][0]["role"] == "assistant"


def test_warm_archive_missing_file_returns_empty(tmp_path: Path) -> None:
    """文件不存在时返回空结果，不抛异常。"""
    result = SessionSearchService(tmp_path).search(
        query="anything",
        sources=["warm_archive"],
        session_key="cli:nonexistent",
    )
    assert result["total_matches"] == 0
    assert result["results"] == []


def test_warm_archive_no_match_returns_empty(tmp_path: Path) -> None:
    """无命中时返回空结果。"""
    _write_jsonl(
        tmp_path / "warm_archive" / "cli_nomatch.jsonl",
        [{"role": "user", "content": "完全不相关的内容", "timestamp": "2026-07-07T09:00:00Z"}],
    )
    result = SessionSearchService(tmp_path).search(
        query="特定关键词",
        sources=["warm_archive"],
        session_key="cli:nomatch",
    )
    assert result["total_matches"] == 0


# ── warm_summaries 检索 ──────────────────────────────────────────────


def test_warm_summaries_search_hits_summary_field(tmp_path: Path) -> None:
    """匹配 summary 字段命中。"""
    _write_jsonl(
        tmp_path / "warm_summaries.jsonl",
        [_warm_summary_entry("cli:warm-sum", summary="本轮讨论了数据库迁移方案")],
    )

    result = SessionSearchService(tmp_path).search(
        query="数据库迁移",
        sources=["warm_summaries"],
    )

    assert result["searched_sources"] == ["warm_summaries"]
    assert result["total_matches"] == 1
    row = result["results"][0]
    assert row["source"] == "warm_summaries"
    assert row["session_key"] == "cli:warm-sum"
    assert row["role"] == "archive"
    assert row["locator"]["turn_range"] == "1-50"
    assert row["locator"]["summary"] == "本轮讨论了数据库迁移方案"


def test_warm_summaries_search_hits_commitments_field(tmp_path: Path) -> None:
    """匹配 commitments 字段命中。"""
    _write_jsonl(
        tmp_path / "warm_summaries.jsonl",
        [_warm_summary_entry(
            "cli:warm-commit",
            summary="无关摘要内容",
            commitments=["明天交付迁移报告"],
        )],
    )

    result = SessionSearchService(tmp_path).search(
        query="迁移报告",
        sources=["warm_summaries"],
    )

    assert result["total_matches"] == 1
    row = result["results"][0]
    assert row["locator"]["commitments"] == ["明天交付迁移报告"]


def test_warm_summaries_search_hits_key_entities_and_decisions(tmp_path: Path) -> None:
    """匹配 key_entities / decisions 字段命中。"""
    _write_jsonl(
        tmp_path / "warm_summaries.jsonl",
        [_warm_summary_entry(
            "cli:warm-entities",
            summary="无关摘要",
            decisions=["采用微服务架构"],
            key_entities=["订单服务", "支付服务"],
        )],
    )

    result_entity = SessionSearchService(tmp_path).search(
        query="订单服务",
        sources=["warm_summaries"],
    )
    assert result_entity["total_matches"] == 1
    assert result_entity["results"][0]["locator"]["key_entities"] == ["订单服务", "支付服务"]

    result_decision = SessionSearchService(tmp_path).search(
        query="微服务架构",
        sources=["warm_summaries"],
    )
    assert result_decision["total_matches"] == 1
    assert result_decision["results"][0]["locator"]["decisions"] == ["采用微服务架构"]


def test_warm_summaries_returns_full_structured_fields(tmp_path: Path) -> None:
    """命中结果含完整结构化字段（commitments/decisions/open_questions/key_entities/timestamp_range）。"""
    entry = _warm_summary_entry(
        "cli:warm-full",
        summary="完整结构化字段测试",
        commitments=["承诺事项A"],
        decisions=["决策事项B"],
        open_questions=["待解决问题C"],
        key_entities=["实体D"],
    )
    _write_jsonl(tmp_path / "warm_summaries.jsonl", [entry])

    result = SessionSearchService(tmp_path).search(
        query="完整结构化",
        sources=["warm_summaries"],
    )

    assert result["total_matches"] == 1
    locator = result["results"][0]["locator"]
    assert locator["commitments"] == ["承诺事项A"]
    assert locator["decisions"] == ["决策事项B"]
    assert locator["open_questions"] == ["待解决问题C"]
    assert locator["key_entities"] == ["实体D"]
    assert locator["timestamp_range"] == {"start": "2026-07-07T13:00:00Z", "end": "2026-07-07T13:49:30Z"}
    assert locator["turn_range"] == "1-50"
    assert locator["archive_locator"].startswith("warm_archive/cli_warm-full.jsonl:")


def test_warm_summaries_missing_file_returns_empty(tmp_path: Path) -> None:
    """warm_summaries.jsonl 不存在时返回空结果，不抛异常。"""
    result = SessionSearchService(tmp_path).search(
        query="anything",
        sources=["warm_summaries"],
    )
    assert result["total_matches"] == 0
    assert result["results"] == []


def test_warm_summaries_session_key_filter(tmp_path: Path) -> None:
    """session_key 过滤仅返回指定 session 的索引条目。"""
    _write_jsonl(
        tmp_path / "warm_summaries.jsonl",
        [
            _warm_summary_entry("cli:session-a", summary="会话A的部署方案"),
            _warm_summary_entry("cli:session-b", summary="会话B的部署方案"),
        ],
    )

    result = SessionSearchService(tmp_path).search(
        query="部署方案",
        sources=["warm_summaries"],
        session_key="cli:session-a",
    )

    assert result["total_matches"] == 1
    assert result["results"][0]["session_key"] == "cli:session-a"


# ── 与现有 source 不冲突 ─────────────────────────────────────────────


def test_default_sources_do_not_include_warm(tmp_path: Path) -> None:
    """默认 source 不包含 warm_archive / warm_summaries，避免意外扫描。"""
    _write_jsonl(
        tmp_path / "warm_archive" / "cli_default.jsonl",
        [{"role": "user", "content": "warm default probe", "timestamp": "2026-07-07T09:00:00Z"}],
    )
    _write_jsonl(
        tmp_path / "warm_summaries.jsonl",
        [_warm_summary_entry("cli:default", summary="warm default probe")],
    )

    result = SessionSearchService(tmp_path).search(query="warm default probe")

    assert result["searched_sources"] == ["sessions", "history", "webui"]
    assert result["total_matches"] == 0


def test_warm_sources_do_not_pollute_standard_sources(tmp_path: Path) -> None:
    """显式请求标准 source 时不返回 warm 命中，反之亦然。"""
    _write_jsonl(
        tmp_path / "sessions" / "cli_mix.jsonl",
        [
            {"_type": "metadata", "key": "cli:mix"},
            {"role": "user", "content": "shared keyword hit", "timestamp": "2026-07-07T09:00:00Z"},
        ],
    )
    _write_jsonl(
        tmp_path / "warm_archive" / "cli_mix.jsonl",
        [{"role": "user", "content": "shared keyword hit", "timestamp": "2026-07-07T10:00:00Z"}],
    )

    # 只查 sessions：不应包含 warm_archive 命中
    only_sessions = SessionSearchService(tmp_path).search(
        query="shared keyword hit",
        sources=["sessions"],
    )
    assert only_sessions["total_matches"] == 1
    assert only_sessions["results"][0]["source"] == "sessions"

    # 只查 warm_archive：不应包含 sessions 命中
    only_warm = SessionSearchService(tmp_path).search(
        query="shared keyword hit",
        sources=["warm_archive"],
    )
    assert only_warm["total_matches"] == 1
    assert only_warm["results"][0]["source"] == "warm_archive"


def test_mixed_sources_combine_results(tmp_path: Path) -> None:
    """同时请求标准 source 与 warm source 时结果合并、统一排序。"""
    _write_jsonl(
        tmp_path / "sessions" / "cli_combined.jsonl",
        [
            {"_type": "metadata", "key": "cli:combined"},
            {"role": "user", "content": "combined query term", "timestamp": "2026-07-07T08:00:00Z"},
        ],
    )
    _write_jsonl(
        tmp_path / "warm_archive" / "cli_combined.jsonl",
        [{"role": "assistant", "content": "combined query term", "timestamp": "2026-07-07T12:00:00Z"}],
    )
    _write_jsonl(
        tmp_path / "warm_summaries.jsonl",
        [_warm_summary_entry("cli:combined", summary="combined query term")],
    )

    result = SessionSearchService(tmp_path).search(
        query="combined query term",
        sources=["sessions", "warm_archive", "warm_summaries"],
    )

    assert result["searched_sources"] == ["sessions", "warm_archive", "warm_summaries"]
    assert result["total_matches"] == 3
    result_sources = {row["source"] for row in result["results"]}
    assert result_sources == {"sessions", "warm_archive", "warm_summaries"}


def test_warm_sources_with_time_range_filter(tmp_path: Path) -> None:
    """warm source 支持 since/until 时间范围过滤。"""
    _write_jsonl(
        tmp_path / "warm_archive" / "cli_time.jsonl",
        [
            {"role": "user", "content": "time range probe", "timestamp": "2026-07-01T10:00:00Z"},
            {"role": "assistant", "content": "time range probe", "timestamp": "2026-07-07T10:00:00Z"},
        ],
    )

    result = SessionSearchService(tmp_path).search(
        query="time range probe",
        sources=["warm_archive"],
        session_key="cli:time",
        since="2026-07-07",
        until="2026-07-07T23:59:59",
    )

    assert result["total_matches"] == 1
    assert result["results"][0]["role"] == "assistant"
