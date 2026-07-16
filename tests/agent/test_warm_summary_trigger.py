"""Phase 3 Task 6 集成测试：温区满 50 轮触发异步总结。

覆盖 SubTask 6.5 要求的五个场景：
- 满 50 轮触发总结（mock WarmSummarizer）
- 异步执行（验证不阻塞主流程）
- 温区清空
- 归档文件生成（warm_summaries.jsonl + warm_archive/{session_key}.jsonl）
- working_memory 同步（commitments → open_loops，open_questions → priority_facts）

设计要点：
- 使用真实 workspace（tmp_path）验证归档文件落盘；
- 使用真实 WorkingMemoryManager 验证 open_loops/priority_facts 同步；
- mock WarmSummarizer 避免实际 LLM 调用；
- schedule_background 捕获 coro 到列表，随后 await 验证后台副作用。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.agent_turn_pipeline import (
    AgentTurnPipeline,
    TurnContext,
    TurnEvent,
    TurnPipelineDeps,
    TurnState,
)
from OriginAgent.agent.warm_store import WarmStore
from OriginAgent.agent.working_memory import WorkingMemoryManager
from OriginAgent.bus.events import InboundMessage
from OriginAgent.session.manager import Session, SessionManager


# ── 辅助构造 ──────────────────────────────────────────────────────────

def _user_msg(text: str, idx: int) -> dict:
    """构造一条 user 消息，附带稳定 timestamp 便于排序。"""
    return {
        "role": "user",
        "content": f"{text}-{idx}",
        "timestamp": f"2026-07-07T13:{idx:02d}:00Z",
    }


def _assistant_msg(text: str, idx: int) -> dict:
    """构造一条 assistant 消息。"""
    return {
        "role": "assistant",
        "content": f"{text}-a{idx}",
        "timestamp": f"2026-07-07T13:{idx:02d}:30Z",
    }


def _populate_warm_to_full(session: Session, turn_count: int = 50) -> None:
    """直接通过 WarmStore.append 把温区填满指定轮数（绕过 spill）。"""
    warm_store = WarmStore()
    for i in range(turn_count):
        warm_store.append(
            session,
            _user_msg("u", i),
            [_assistant_msg("a", i)],
        )


def _build_save_ctx(session: Session) -> TurnContext:
    """构造一个直接进入 state_save 的 TurnContext。"""
    return TurnContext(
        msg=InboundMessage(
            channel="cli",
            sender_id="user-1",
            chat_id="warm-summary",
            content="continue",
            metadata={},
        ),
        session_key=session.key,
        state=TurnState.SAVE,
        turn_id="turn:warm-summary-save",
        session=session,
        history=[],
        all_messages=[{"role": "assistant", "content": "done"}],
        final_content="done",
    )


def _make_mock_summarizer(
    *,
    delay: float = 0.0,
    summary: dict | None = None,
) -> MagicMock:
    """构造 mock WarmSummarizer，summarize 为可延迟的 async 函数。

    使用普通 async 函数而非 AsyncMock，以便支持 delay 模拟 LLM 耗时。
    """
    summarizer = MagicMock()
    effective_summary = summary if summary is not None else {
        "summary": "温区总结内容",
        "commitments": ["承诺事项A"],
        "decisions": [],
        "open_questions": ["待解决问题B"],
        "key_entities": [],
        "timestamp_range": {
            "start": "2026-07-07T13:00:00Z",
            "end": "2026-07-07T13:49:30Z",
        },
    }

    async def _summarize(warm_messages, hot_messages, *, turn_range, session_key):
        if delay > 0:
            await asyncio.sleep(delay)
        return effective_summary

    summarizer.summarize = _summarize
    return summarizer


async def _drain_scheduled_coros(scheduled_coros: list) -> None:
    """Await 所有已调度的 coro，确保后台任务（含温区总结）完成。

    state_save 会调度多个后台任务（consolidator、温区总结等），
    它们都被追加到 scheduled_coros。为避免遗漏温区总结 coro，
    这里统一 await 全部，随后清空列表。
    """
    for coro in scheduled_coros:
        try:
            await coro
        except Exception:
            # 后台任务的异常已在 _run_warm_summary 内部捕获，
            # 此处防御性吞掉以避免测试被无关异常干扰。
            pass
    scheduled_coros.clear()


def _build_minimal_deps(
    session: Session,
    workspace: Path,
    *,
    scheduled_coros: list,
    summarizer: MagicMock | None = None,
    get_warm_summarizer: object | None = None,
) -> TurnPipelineDeps:
    """构造可触发温区总结的 TurnPipelineDeps。

    Args:
        workspace: 真实 workspace 路径（用于归档文件写入）。
        scheduled_coros: 列表引用，schedule_background 会把 coro 追加到此列表。
        summarizer: mock summarizer；提供时通过 get_warm_summarizer 返回。
        get_warm_summarizer: 显式覆盖 getter（优先于 summarizer）；
            传 None 表示不配置 summarizer（跳过温区总结）。
    """
    sessions = SessionManager(workspace)
    working_memory = WorkingMemoryManager(sessions)

    consolidator = MagicMock()
    consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)

    rolling_episode_compaction = MagicMock()
    rolling_episode_compaction.maybe_compact = MagicMock(return_value=None)

    context = MagicMock()
    context._context_config = MagicMock(governance_enabled=False)

    tools = {"message": MagicMock()}

    # 决定 get_warm_summarizer 的有效值：显式参数优先于 summarizer mock
    if get_warm_summarizer is not None:
        effective_getter = get_warm_summarizer
    elif summarizer is not None:
        def effective_getter():
            return summarizer
    else:
        effective_getter = None

    return TurnPipelineDeps(
        auto_compact=MagicMock(),
        commands=MagicMock(),
        command_loop=MagicMock(),
        get_consolidator=lambda: consolidator,
        get_tools=lambda: tools,
        get_context=lambda: context,
        sessions=sessions,
        bus=MagicMock(),
        get_working_memory=lambda: working_memory,
        get_memory_governance=lambda: MagicMock(),
        get_rolling_episode_compaction=lambda: rolling_episode_compaction,
        workspace=workspace,
        tools_config=MagicMock(),
        domain_runtime_contributions=[],
        domain_runtime_overrides={},
        archive_session_file_cap=lambda s: None,
        restore_runtime_checkpoint=lambda s: False,
        restore_pending_user_turn=lambda s: False,
        load_continuity_checkpoint=lambda s: None,
        record_recovered_continuity_checkpoint=lambda c: None,
        mark_webui_session=lambda s, m: None,
        persist_shortcut_command_turn=lambda m, s, r: None,
        is_webui_message=lambda m: False,
        resolve_runtime_context=lambda *a, **kw: MagicMock(),
        record_runtime_context=lambda s, r: None,
        write_continuity_runtime_identity=lambda s, r: None,
        snapshot_for_trigger=lambda t: MagicMock(),
        update_working_memory_from_turn=lambda *a, **kw: None,
        set_tool_context=lambda *a, **kw: None,
        replay_token_budget=lambda: 4096,
        build_initial_messages=lambda *a, **kw: [],
        persist_user_message_early=lambda *a, **kw: False,
        schedule_session_search_refresh=lambda *a, **kw: None,
        build_progress_callback=lambda m: MagicMock(),
        build_retry_wait_callback=lambda m: MagicMock(),
        pending_ask_user_id=lambda m: None,
        consume_tool_approval_reply=lambda *a, **kw: (None, False),
        build_recovered_continuity_context=lambda c: {},
        run_agent_loop=lambda *a, **kw: (None, [], [], "completed", False),
        clear_pending_user_turn=lambda s: None,
        clear_runtime_checkpoint=lambda s: None,
        save_turn=lambda s, m, skip: None,
        record_governance_audit=lambda a: None,
        save_continuity_checkpoint=lambda *a, **kw: {},
        schedule_background=lambda c: scheduled_coros.append(c),
        schedule_nearline_memory=lambda c: None,
        schedule_background_review=lambda c: None,
        schedule_curator_review=lambda c: None,
        automation_enabled=lambda: False,
        action_planner=None,
        record_action_continuity_audit=lambda a: None,
        assemble_outbound=lambda *a, **kw: None,
        get_max_messages=lambda: 120,
        get_warm_summarizer=effective_getter,
    )


# ── 测试 1: 满 50 轮触发总结 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_state_save_triggers_warm_summary_when_warm_full(tmp_path: Path) -> None:
    """温区满 50 轮时，state_save 调度后台温区总结任务。"""
    session = Session(key="cli:warm-trigger")
    _populate_warm_to_full(session, turn_count=50)
    assert WarmStore().is_full(session) is True

    scheduled_coros: list = []
    summarizer = _make_mock_summarizer()
    deps = _build_minimal_deps(
        session, tmp_path,
        scheduled_coros=scheduled_coros,
        summarizer=summarizer,
    )
    pipeline = AgentTurnPipeline(deps)
    ctx = _build_save_ctx(session)

    result = await pipeline.state_save(ctx)

    assert result == TurnEvent.OK
    # schedule_background 至少被调用一次（consolidator + 温区总结）
    assert len(scheduled_coros) >= 1
    # await 所有调度的 coro（含 _run_warm_summary），确保后台任务完成
    await _drain_scheduled_coros(scheduled_coros)
    # 间接验证：温区被 drain 清空（说明 _run_warm_summary 执行了）
    buffer = WarmStore().load(session)
    assert buffer["turn_count"] == 0


# ── 测试 2: 异步执行不阻塞主流程 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_warm_summary_does_not_block_state_save(tmp_path: Path) -> None:
    """state_save 调度温区总结后立即返回，不等待 LLM summarize 完成。"""
    session = Session(key="cli:warm-async")
    _populate_warm_to_full(session, turn_count=50)

    scheduled_coros: list = []
    # summarize 模拟 LLM 耗时 0.3s
    summarizer = _make_mock_summarizer(delay=0.3)
    deps = _build_minimal_deps(
        session, tmp_path,
        scheduled_coros=scheduled_coros,
        summarizer=summarizer,
    )
    pipeline = AgentTurnPipeline(deps)
    ctx = _build_save_ctx(session)

    start = time.monotonic()
    result = await pipeline.state_save(ctx)
    elapsed = time.monotonic() - start

    assert result == TurnEvent.OK
    # state_save 应在远小于 0.3s 内返回（异步调度，不等待 summarize）
    assert elapsed < 0.2, f"state_save 阻塞了 {elapsed:.3f}s，预期异步非阻塞"

    # 清理未 await 的 coro，避免 RuntimeWarning
    for coro in scheduled_coros:
        coro.close()


# ── 测试 3: 温区清空 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_warm_summary_drains_warm_store(tmp_path: Path) -> None:
    """后台温区总结任务执行后，温区被 drain 清空。"""
    session = Session(key="cli:warm-drain")
    _populate_warm_to_full(session, turn_count=50)
    assert WarmStore().is_full(session) is True

    scheduled_coros: list = []
    summarizer = _make_mock_summarizer()
    deps = _build_minimal_deps(
        session, tmp_path,
        scheduled_coros=scheduled_coros,
        summarizer=summarizer,
    )
    pipeline = AgentTurnPipeline(deps)
    ctx = _build_save_ctx(session)

    await pipeline.state_save(ctx)
    # await 所有调度的 coro（含 _run_warm_summary），确保后台任务完成
    await _drain_scheduled_coros(scheduled_coros)

    buffer = WarmStore().load(session)
    assert buffer["turn_count"] == 0
    assert buffer["messages"] == []


# ── 测试 4: 归档文件生成 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_warm_summary_writes_archive_files(tmp_path: Path) -> None:
    """后台任务写入 warm_summaries.jsonl 与 warm_archive/{safe_key}.jsonl。"""
    session_key = "cli:warm-archive"
    session = Session(key=session_key)
    _populate_warm_to_full(session, turn_count=50)

    scheduled_coros: list = []
    summarizer = _make_mock_summarizer()
    deps = _build_minimal_deps(
        session, tmp_path,
        scheduled_coros=scheduled_coros,
        summarizer=summarizer,
    )
    pipeline = AgentTurnPipeline(deps)
    ctx = _build_save_ctx(session)

    await pipeline.state_save(ctx)
    await _drain_scheduled_coros(scheduled_coros)

    # warm_archive/{safe_key}.jsonl 应存在，包含 50 轮 = 100 条消息
    safe_key = SessionManager.safe_key(session_key)
    archive_path = tmp_path / "warm_archive" / f"{safe_key}.jsonl"
    assert archive_path.exists(), f"归档文件未生成: {archive_path}"
    archive_lines = archive_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(archive_lines) == 100  # 50 user + 50 assistant
    # 每行应为合法 JSON 且包含 role/content
    for line in archive_lines:
        msg = json.loads(line)
        assert "role" in msg
        assert "content" in msg

    # warm_summaries.jsonl 应存在，包含 1 条总结索引
    summaries_path = tmp_path / "warm_summaries.jsonl"
    assert summaries_path.exists(), f"总结索引未生成: {summaries_path}"
    summary_lines = summaries_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(summary_lines) == 1
    entry = json.loads(summary_lines[0])
    assert entry["session_key"] == session_key
    assert entry["turn_range"] == "1-50"
    assert entry["summary"] == "温区总结内容"
    assert entry["commitments"] == ["承诺事项A"]
    assert entry["open_questions"] == ["待解决问题B"]
    assert entry["locator"].startswith(f"warm_archive/{safe_key}.jsonl:")
    assert entry["locator"].endswith(":100")


# ── 测试 5: working_memory 同步 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_run_warm_summary_syncs_to_working_memory(tmp_path: Path) -> None:
    """commitments → open_loops，open_questions → priority_facts 同步。"""
    session = Session(key="cli:warm-wm-sync")
    _populate_warm_to_full(session, turn_count=50)

    scheduled_coros: list = []
    custom_summary = {
        "summary": "本轮涉及多项承诺与待决问题",
        "commitments": ["明天发报告", "联系客户"],
        "decisions": [],
        "open_questions": ["是否启动方案B?", "预算上限多少?"],
        "key_entities": [],
        "timestamp_range": {"start": "", "end": ""},
    }
    summarizer = _make_mock_summarizer(summary=custom_summary)
    deps = _build_minimal_deps(
        session, tmp_path,
        scheduled_coros=scheduled_coros,
        summarizer=summarizer,
    )
    pipeline = AgentTurnPipeline(deps)
    ctx = _build_save_ctx(session)

    await pipeline.state_save(ctx)
    await _drain_scheduled_coros(scheduled_coros)

    working_memory = deps.get_working_memory()
    snapshot = working_memory.load(session)
    # commitments → open_loops（追加去重）
    assert "明天发报告" in snapshot.open_loops
    assert "联系客户" in snapshot.open_loops
    # open_questions → priority_facts（追加去重）
    assert "是否启动方案B?" in snapshot.priority_facts
    assert "预算上限多少?" in snapshot.priority_facts


# ── 测试 6: 未配置 summarizer 时不触发 ───────────────────────────────

@pytest.mark.asyncio
async def test_state_save_skips_when_summarizer_not_configured(tmp_path: Path) -> None:
    """get_warm_summarizer=None 时，即使温区满也不触发总结。"""
    session = Session(key="cli:warm-no-summarizer")
    _populate_warm_to_full(session, turn_count=50)

    scheduled_coros: list = []
    deps = _build_minimal_deps(
        session, tmp_path,
        scheduled_coros=scheduled_coros,
        get_warm_summarizer=None,  # 显式未配置
    )
    pipeline = AgentTurnPipeline(deps)
    ctx = _build_save_ctx(session)

    result = await pipeline.state_save(ctx)

    assert result == TurnEvent.OK
    # 温区仍然满（未被 drain）
    assert WarmStore().is_full(session) is True
    # 没有生成归档文件
    assert not (tmp_path / "warm_summaries.jsonl").exists()
    assert not (tmp_path / "warm_archive").exists()
    # 关闭 consolidator 等未 await 的 coro，避免 RuntimeWarning
    for coro in scheduled_coros:
        coro.close()


# ── 测试 7: 多批总结累积归档 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_warm_summary_accumulates_across_batches(tmp_path: Path) -> None:
    """同一 session 多批总结累积写入 warm_summaries.jsonl，turn_range 递增。"""
    session_key = "cli:warm-batches"
    session = Session(key=session_key)

    scheduled_coros: list = []
    summarizer = _make_mock_summarizer()
    deps = _build_minimal_deps(
        session, tmp_path,
        scheduled_coros=scheduled_coros,
        summarizer=summarizer,
    )
    pipeline = AgentTurnPipeline(deps)

    # 第 1 批：填充 50 轮并触发总结
    _populate_warm_to_full(session, turn_count=50)
    ctx1 = _build_save_ctx(session)
    await pipeline.state_save(ctx1)
    await _drain_scheduled_coros(scheduled_coros)

    # 第 2 批：再次填充 50 轮并触发总结
    _populate_warm_to_full(session, turn_count=50)
    ctx2 = _build_save_ctx(session)
    await pipeline.state_save(ctx2)
    await _drain_scheduled_coros(scheduled_coros)

    # warm_summaries.jsonl 应有 2 条总结，turn_range 递增
    summaries_path = tmp_path / "warm_summaries.jsonl"
    summary_lines = summaries_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(summary_lines) == 2
    entries = [json.loads(line) for line in summary_lines]
    assert entries[0]["turn_range"] == "1-50"
    assert entries[1]["turn_range"] == "51-100"

    # warm_archive 应累积 200 条消息（两批 × 100）
    safe_key = SessionManager.safe_key(session_key)
    archive_path = tmp_path / "warm_archive" / f"{safe_key}.jsonl"
    archive_lines = archive_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(archive_lines) == 200
