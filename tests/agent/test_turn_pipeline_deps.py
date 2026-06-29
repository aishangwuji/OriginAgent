"""Tests for TurnPipelineDeps construction."""
from typing import Callable
from unittest.mock import MagicMock

from OriginAgent.agent.agent_turn_pipeline import TurnPipelineDeps


def test_turn_pipeline_deps_accepts_callables():
    """TurnPipelineDeps fields accept plain callables, not just lambdas."""
    def my_getter() -> int:
        return 42

    deps = TurnPipelineDeps(
        auto_compact=MagicMock(),
        commands=MagicMock(),
        command_loop=MagicMock(),
        get_consolidator=my_getter,
        get_tools=lambda: MagicMock(),
        get_context=my_getter,
        sessions=MagicMock(),
        bus=MagicMock(),
        get_working_memory=my_getter,
        get_memory_governance=my_getter,
        get_rolling_episode_compaction=my_getter,
        workspace="/tmp",
        tools_config=MagicMock(),
        domain_runtime_contributions=[],
        domain_runtime_overrides={},
        archive_session_file_cap=lambda s: None,
        restore_runtime_checkpoint=lambda s: True,
        restore_pending_user_turn=lambda s: True,
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
        schedule_background=lambda c: None,
        schedule_nearline_memory=lambda c: None,
        schedule_background_review=lambda c: None,
        schedule_curator_review=lambda c: None,
        automation_enabled=lambda: False,
        action_planner=None,
        record_action_continuity_audit=lambda a: None,
        assemble_outbound=lambda *a, **kw: None,
        get_max_messages=lambda: 120,
    )
    assert deps.get_consolidator() == 42
    assert deps.get_context() == 42
    assert deps.get_max_messages() == 120
