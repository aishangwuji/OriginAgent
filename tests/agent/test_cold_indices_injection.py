"""Phase 4 Task 5 — 冷区索引注入测试。

验证 warm_summaries.jsonl 中的最近 5 条索引会被写入 continuity checkpoint 的
``cold_indices`` 字段，并在 ``recovered_continuity`` 块中按
``[turn_range] summary (key_entities)`` 格式渲染。

设计要点（渐进式暴露）：
- checkpoint 只保留索引视图字段（turn_range/summary/key_entities），不含完整
  commitments/decisions，避免上下文膨胀；
- 按 session_key 过滤，确保多 session 共用同一 jsonl 时互不污染；
- 最近 5 条上限，超出部分需通过 locator 回查 warm_archive。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from OriginAgent.agent.agent_runtime import AgentRuntime, RuntimeDependencies
from OriginAgent.agent.context import ContextBuilder


def _build_runtime(workspace: Path) -> AgentRuntime:
    """构建带 workspace 的 AgentRuntime，用于 cold_indices 测试。"""
    working = MagicMock()
    working.current_goal = "test goal"
    working.current_plan = ["step 1"]
    working.open_loops = []
    working.active_constraints = []

    working_memory = MagicMock()
    working_memory.load.return_value = working

    deps = RuntimeDependencies(
        working_memory=working_memory,
        nearline_memory=None,
        workspace=workspace,
    )
    return AgentRuntime(deps)


def _make_session(session_key: str = "cli:test") -> SimpleNamespace:
    return SimpleNamespace(key=session_key, metadata={}, messages=[])


def _write_warm_summary(
    workspace: Path,
    *,
    session_key: str,
    turn_range: str,
    summary: str,
    key_entities: list[str] | None = None,
    commitments: list[str] | None = None,
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> None:
    """向 warm_summaries.jsonl 追加一条总结索引（模拟 Task 6 的归档输出）。"""
    entry = {
        "session_key": session_key,
        "turn_range": turn_range,
        "summary": summary,
        "commitments": commitments or [],
        "decisions": [],
        "open_questions": [],
        "key_entities": key_entities or [],
        "timestamp_range": {"start": "", "end": ""},
        "locator": f"warm_archive/{session_key}.jsonl:0:50",
        "created_at": created_at,
    }
    summaries_path = workspace / "warm_summaries.jsonl"
    with summaries_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── SubTask 5.2: 索引写入 checkpoint ──────────────────────────────


def test_save_checkpoint_includes_cold_indices(tmp_path: Path):
    """_save_continuity_checkpoint 应包含 cold_indices 字段且只保留索引视图字段。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_warm_summary(
        workspace,
        session_key="cli:test",
        turn_range="51-100",
        summary="讨论了背题提醒系统",
        key_entities=["背题", "cron", "MetaCognitionReflector"],
        commitments=["实现 cron 调度"],  # 应被过滤掉，不进入索引视图
    )

    runtime = _build_runtime(workspace)
    session = _make_session("cli:test")

    checkpoint = runtime._save_continuity_checkpoint(session)

    assert "cold_indices" in checkpoint
    cold = checkpoint["cold_indices"]
    assert len(cold) == 1
    # 索引视图只含 turn_range/summary/key_entities，不含 commitments/decisions
    assert set(cold[0].keys()) == {"turn_range", "summary", "key_entities"}
    assert cold[0]["turn_range"] == "51-100"
    assert cold[0]["summary"] == "讨论了背题提醒系统"
    assert cold[0]["key_entities"] == ["背题", "cron", "MetaCognitionReflector"]


def test_cold_indices_missing_file_returns_empty(tmp_path: Path):
    """warm_summaries.jsonl 不存在时，cold_indices 应为空列表（不抛异常）。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    runtime = _build_runtime(workspace)
    session = _make_session("cli:test")

    checkpoint = runtime._save_continuity_checkpoint(session)

    assert checkpoint["cold_indices"] == []


def test_cold_indices_missing_workspace_returns_empty():
    """workspace 未配置时，cold_indices 应安全返回空列表。"""
    working = MagicMock()
    working.current_goal = "g"
    working.current_plan = []
    working.open_loops = []
    working.active_constraints = []
    working_memory = MagicMock()
    working_memory.load.return_value = working
    deps = RuntimeDependencies(working_memory=working_memory, nearline_memory=None, workspace=None)
    runtime = AgentRuntime(deps)

    cold = runtime._collect_cold_indices("cli:test")

    assert cold == []


# ── SubTask 5.2: 最近 5 条上限 ────────────────────────────────────


def test_cold_indices_respects_five_limit(tmp_path: Path):
    """超过 5 条时只保留最近 5 条（按文件顺序倒序，末尾即最新）。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # 写入 7 条（turn_range 1-50 .. 301-350），最后写入的是最新的
    for i in range(7):
        start = i * 50 + 1
        end = (i + 1) * 50
        _write_warm_summary(
            workspace,
            session_key="cli:test",
            turn_range=f"{start}-{end}",
            summary=f"summary-{i}",
            key_entities=[f"entity-{i}"],
        )

    runtime = _build_runtime(workspace)
    session = _make_session("cli:test")

    checkpoint = runtime._save_continuity_checkpoint(session)
    cold = checkpoint["cold_indices"]

    assert len(cold) == 5
    # 倒序：最新（第 7 条，301-350）在前
    assert cold[0]["turn_range"] == "301-350"
    assert cold[0]["summary"] == "summary-6"
    assert cold[-1]["turn_range"] == "101-150"


# ── SubTask 5.2: session_key 过滤 ─────────────────────────────────


def test_cold_indices_filters_by_session_key(tmp_path: Path):
    """多 session 共用同一 jsonl 时，只返回当前 session_key 的索引。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_warm_summary(workspace, session_key="cli:a", turn_range="1-50", summary="a-1", key_entities=["a1"])
    _write_warm_summary(workspace, session_key="cli:b", turn_range="1-50", summary="b-1", key_entities=["b1"])
    _write_warm_summary(workspace, session_key="cli:a", turn_range="51-100", summary="a-2", key_entities=["a2"])
    _write_warm_summary(workspace, session_key="cli:b", turn_range="51-100", summary="b-2", key_entities=["b2"])

    runtime = _build_runtime(workspace)
    session = _make_session("cli:a")

    checkpoint = runtime._save_continuity_checkpoint(session)
    cold = checkpoint["cold_indices"]

    assert len(cold) == 2
    # 只含 session a 的条目，倒序（最新在前）
    assert cold[0]["turn_range"] == "51-100"
    assert cold[0]["summary"] == "a-2"
    assert cold[1]["turn_range"] == "1-50"
    assert cold[1]["summary"] == "a-1"
    # 不应混入 session b 的内容
    assert all("b-" not in item["summary"] for item in cold)


def test_cold_indices_ignores_malformed_lines(tmp_path: Path):
    """损坏的 JSON 行应被跳过，不影响其他合法条目。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    summaries_path = workspace / "warm_summaries.jsonl"
    # 手动写入：一条合法 + 一条损坏 + 一条合法
    summaries_path.write_text(
        json.dumps(
            {"session_key": "cli:test", "turn_range": "1-50", "summary": "ok-1", "key_entities": []},
            ensure_ascii=False,
        )
        + "\n"
        + "this is not json\n"
        + json.dumps(
            {"session_key": "cli:test", "turn_range": "51-100", "summary": "ok-2", "key_entities": []},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    runtime = _build_runtime(workspace)
    cold = runtime._collect_cold_indices("cli:test")

    assert len(cold) == 2
    assert cold[0]["turn_range"] == "51-100"  # 倒序，最新在前
    assert cold[1]["turn_range"] == "1-50"


# ── SubTask 5.3: _load_continuity_checkpoint 加载 cold_indices ─────


def test_load_checkpoint_returns_cold_indices():
    """_load_continuity_checkpoint 应返回 cold_indices 字段并归一化。"""
    session = SimpleNamespace(
        key="cli:test",
        metadata={
            "continuity_checkpoint_v1": {
                "session_key": "cli:test",
                "current_goal": "goal",
                "current_plan": [],
                "open_loops": [],
                "active_constraints": [],
                "pending_confirmation_refs": [],
                "recent_turns_summary": [],
                "cold_indices": [
                    {
                        "turn_range": "51-100",
                        "summary": "讨论了背题提醒系统",
                        "key_entities": ["背题", "cron"],
                    }
                ],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        },
    )

    result = AgentRuntime._load_continuity_checkpoint(session)

    assert result is not None
    assert "cold_indices" in result
    assert len(result["cold_indices"]) == 1
    assert result["cold_indices"][0]["turn_range"] == "51-100"
    assert result["cold_indices"][0]["key_entities"] == ["背题", "cron"]


def test_load_checkpoint_old_checkpoint_without_cold_indices_is_compatible():
    """旧 checkpoint（无 cold_indices 字段）加载时应返回空列表，保持向后兼容。"""
    session = SimpleNamespace(
        key="cli:test",
        metadata={
            "continuity_checkpoint_v1": {
                "session_key": "cli:test",
                "current_goal": "goal",
                "current_plan": [],
                "open_loops": [],
                "active_constraints": [],
                "pending_confirmation_refs": [],
                "recent_turns_summary": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        },
    )

    result = AgentRuntime._load_continuity_checkpoint(session)

    assert result is not None
    assert result["cold_indices"] == []


def test_load_checkpoint_caps_cold_indices_at_five():
    """_load_continuity_checkpoint 应将 cold_indices 截断到 5 条。"""
    raw_indices = [
        {"turn_range": f"{i * 50 + 1}-{(i + 1) * 50}", "summary": f"s-{i}", "key_entities": []}
        for i in range(8)
    ]
    session = SimpleNamespace(
        key="cli:test",
        metadata={
            "continuity_checkpoint_v1": {
                "session_key": "cli:test",
                "current_goal": "goal",
                "current_plan": [],
                "open_loops": [],
                "active_constraints": [],
                "pending_confirmation_refs": [],
                "recent_turns_summary": [],
                "cold_indices": raw_indices,
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        },
    )

    result = AgentRuntime._load_continuity_checkpoint(session)

    assert len(result["cold_indices"]) == 5


# ── SubTask 5.3: build_recovered_continuity_context 渲染 ──────────


def test_build_recovered_continuity_renders_cold_indices():
    """recovered_continuity 块应按 [turn_range] summary (key_entities) 格式渲染冷区索引。"""
    snapshot = {
        "session_key": "cli:test",
        "current_goal": "goal",
        "cold_indices": [
            {
                "turn_range": "51-100",
                "summary": "讨论了背题提醒系统",
                "key_entities": ["背题", "cron", "MetaCognitionReflector"],
            },
            {
                "turn_range": "101-150",
                "summary": "修复了 MetaCognitionReflector 崩溃",
                "key_entities": ["MetaCognitionReflector", "call_llm"],
            },
        ],
    }

    block = ContextBuilder.build_recovered_continuity_context(snapshot)

    text = block["text"]
    assert "Cold Indices" in text
    assert "[51-100] 讨论了背题提醒系统 (背题, cron, MetaCognitionReflector)" in text
    assert "[101-150] 修复了 MetaCognitionReflector 崩溃 (MetaCognitionReflector, call_llm)" in text
    assert block["_meta"]["kind"] == ContextBuilder.RECOVERED_CONTINUITY_CONTEXT_KIND


def test_build_recovered_continuity_renders_cold_indices_without_entities():
    """key_entities 为空时，渲染应为 [turn_range] summary（不带括号）。"""
    snapshot = {
        "session_key": "cli:test",
        "current_goal": "goal",
        "cold_indices": [
            {"turn_range": "1-50", "summary": "初始对话", "key_entities": []},
        ],
    }

    block = ContextBuilder.build_recovered_continuity_context(snapshot)

    text = block["text"]
    assert "[1-50] 初始对话" in text
    # 不应出现空括号
    assert "初始对话 ()" not in text


def test_build_recovered_continuity_without_cold_indices():
    """snapshot 无 cold_indices 时不应渲染 Cold Indices 段。"""
    snapshot = {
        "session_key": "cli:test",
        "current_goal": "goal",
        "recent_turns_summary": [{"role": "user", "content": "Hi"}],
    }

    block = ContextBuilder.build_recovered_continuity_context(snapshot)

    text = block["text"]
    assert "Cold Indices" not in text
    assert "Recent Turns Summary" in text


# ── 端到端：save → load → render ──────────────────────────────────


def test_end_to_end_save_load_render_cold_indices(tmp_path: Path):
    """端到端：写入 warm_summaries → save checkpoint → load → 渲染格式正确。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_warm_summary(
        workspace,
        session_key="cli:e2e",
        turn_range="51-100",
        summary="讨论了背题提醒系统",
        key_entities=["背题", "cron", "MetaCognitionReflector"],
        commitments=["实现 cron 调度"],
    )

    runtime = _build_runtime(workspace)
    session = _make_session("cli:e2e")

    # 1. save
    runtime._save_continuity_checkpoint(session)
    # 2. load
    loaded = AgentRuntime._load_continuity_checkpoint(session)
    assert loaded is not None
    assert len(loaded["cold_indices"]) == 1
    # 3. render
    block = ContextBuilder.build_recovered_continuity_context(loaded)
    assert "[51-100] 讨论了背题提醒系统 (背题, cron, MetaCognitionReflector)" in block["text"]
