"""Tests for log_event attrs enrichment in ContextAssemblerV2.assemble.

验证 `context.assembled` 事件包含以下 attrs：
- block_count
- block_kinds
- retrieval_sources
- retrieved_total
- trimmed_count
- recovered_continuity_included
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from OriginAgent.agent.context import ContextBuilder
from OriginAgent.agent.context_assembler import ContextAssemblerV2


def _build_mock_builder() -> MagicMock:
    """构造一个最小可用的 ContextBuilder mock，覆盖 assemble 调用到的接口。"""
    builder = MagicMock()
    builder.timezone = "UTC"
    builder.WORKING_MEMORY_CONTEXT_KIND = "working_memory"
    builder.WORLD_STATE_CONTEXT_KIND = "world_state"
    builder.world_state = None
    builder.session_store = None

    builder.build_user_content.return_value = [
        {"type": "text", "text": "user msg", "_meta": {"kind": "user_message"}},
    ]
    builder.build_runtime_context_block.return_value = {
        "type": "text",
        "text": "runtime",
        "_meta": {"kind": "runtime"},
    }
    builder.build_internal_event_block.return_value = {
        "type": "text",
        "text": "internal event",
        "_meta": {"kind": "internal_event"},
    }
    builder.prepare_prewarm_bundle.return_value = None
    builder.build_phase1_continuity_blocks.return_value = [
        {"type": "text", "text": "continuity A", "_meta": {"kind": "working_memory"}},
        {"type": "text", "text": "continuity B", "_meta": {"kind": "world_state"}},
    ]
    builder.build_reference_context_blocks.return_value = [
        {"type": "text", "text": "ref A", "_meta": {"kind": "reference", "source": "warm"}},
        {"type": "text", "text": "ref B", "_meta": {"kind": "reference", "source": "nearline"}},
    ]

    # 关键：collect_assembly_audit 返回包含 retrieval_fusion 的审计字典
    builder.collect_assembly_audit.return_value = {
        "media": {},
        "retrieval_fusion": {
            "enabled": True,
            "sources_used": ["warm", "nearline", "session_search"],
            "source_counts": {"warm": 2, "nearline": 1, "session_search": 3},
            "deduped_count": 1,
            "trimmed_count": 2,
            "hits": {"warm": [], "nearline": [], "session_search": []},
        },
        "governance": {},
        "prewarm": {"prewarm_empty": True},
        "governance_enabled": False,
        "prewarm_enabled": False,
    }
    return builder


def _find_log_call(mock_log_event, event_name):
    """从 mock_log_event.call_args_list 中找出指定事件名的首次调用。"""
    for call in mock_log_event.call_args_list:
        if call.args and call.args[0] == event_name:
            return call
    return None


def test_context_assembled_logs_block_count_and_retrieval():
    """context.assembled 事件应包含 block_count / block_kinds / retrieval_sources /
    retrieved_total / trimmed_count / recovered_continuity_included。"""
    builder = _build_mock_builder()
    assembler = ContextAssemblerV2(builder)

    recovered_block = {
        "type": "text",
        "text": "recovered continuity",
        "_meta": {"kind": "recovered_continuity"},
    }

    with patch("OriginAgent.agent.context_assembler.log_event") as mock_log_event:
        result = assembler.assemble(
            current_message="hello",
            media=None,
            channel="cli",
            chat_id="chat-1",
            sender_id="user-1",
            session_summary=None,
            session_metadata=None,
            internal_event=("cron", "fired"),
            runtime_context=None,
            session_key="cli:test-session",
            recovered_continuity_block=recovered_block,
            include_current_message=True,
        )

    # 验证 assemble 仍正常返回（不破坏既有契约）
    assert result is not None
    assert result.blocks is not None
    assert len(result.blocks) > 0

    assembled_call = _find_log_call(mock_log_event, "context.assembled")
    assert assembled_call is not None, "context.assembled 未被记录"
    kwargs = assembled_call.kwargs

    # 必需 attrs 存在性断言
    assert "block_count" in kwargs, "context.assembled 缺少 block_count"
    assert "block_kinds" in kwargs, "context.assembled 缺少 block_kinds"
    assert "retrieval_sources" in kwargs, "context.assembled 缺少 retrieval_sources"
    assert "retrieved_total" in kwargs, "context.assembled 缺少 retrieved_total"
    assert "trimmed_count" in kwargs, "context.assembled 缺少 trimmed_count"
    assert (
        "recovered_continuity_included" in kwargs
    ), "context.assembled 缺少 recovered_continuity_included"

    # 值的正确性断言
    # blocks 构成: runtime + recovered_continuity + 2 continuity + 2 reference + 1 internal_event + 1 user = 8
    assert kwargs["block_count"] == len(result.blocks)
    assert kwargs["block_count"] >= 1
    assert isinstance(kwargs["block_kinds"], list)
    # recovered_continuity_block 已传入，对应 kind 应出现在 block_kinds 中
    assert "recovered_continuity" in kwargs["block_kinds"]

    # retrieval 字段
    assert kwargs["retrieval_sources"] == ["warm", "nearline", "session_search"]
    # retrieved_total = sum(source_counts.values()) = 2 + 1 + 3 = 6
    assert kwargs["retrieved_total"] == 6
    assert kwargs["trimmed_count"] == 2
    # recovered_continuity_block 非空
    assert kwargs["recovered_continuity_included"] is True

    # session_key 应透传
    assert kwargs.get("session_key") == "cli:test-session"


def test_context_assembled_logs_recovered_continuity_false_when_absent():
    """当 recovered_continuity_block 为 None 时，recovered_continuity_included 应为 False。"""
    builder = _build_mock_builder()
    assembler = ContextAssemblerV2(builder)

    with patch("OriginAgent.agent.context_assembler.log_event") as mock_log_event:
        assembler.assemble(
            current_message="hi",
            media=None,
            channel="cli",
            chat_id="chat-1",
            sender_id="user-1",
            session_summary=None,
            session_metadata=None,
            internal_event=None,
            runtime_context=None,
            session_key="cli:test-session",
            recovered_continuity_block=None,
            include_current_message=True,
        )

    assembled_call = _find_log_call(mock_log_event, "context.assembled")
    assert assembled_call is not None
    assert assembled_call.kwargs["recovered_continuity_included"] is False


def test_user_text_block_has_meta_kind(tmp_path: Path) -> None:
    """_build_user_content 生成的 text block 必须携带 _meta.kind=user_text。

    背景: ContextAssemblerV2.assemble 的 block_kinds 从 _meta.kind 提取，
    若 user text block 缺少该字段，block_kinds 会出现 None，破坏审计完整性。
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    builder = ContextBuilder(workspace)

    blocks = builder.build_user_content("hello", None)

    assert any(
        block.get("type") == "text"
        and block.get("text") == "hello"
        and block.get("_meta", {}).get("kind") == "user_text"
        for block in blocks
    ), f"user text block 缺少 _meta.kind=user_text: {blocks}"


def test_context_assembled_block_kinds_no_none_for_user_text(tmp_path: Path) -> None:
    """context.assembled 事件的 block_kinds 不应包含 None，且应包含 user_text。

    验证 _build_user_content 补 _meta.kind 后，assemble 的审计字段完整：
    user text block 贡献 "user_text" 而非 None。
    mock builder 控制其余 block 均带 _meta.kind，build_user_content 委托真实实现。
    """
    real_builder = ContextBuilder(tmp_path)
    builder = _build_mock_builder()
    # 关键: build_user_content 委托真实实现，验证 _build_user_content 的修复
    builder.build_user_content.side_effect = real_builder.build_user_content

    assembler = ContextAssemblerV2(builder)

    with patch("OriginAgent.agent.context_assembler.log_event") as mock_log_event:
        assembler.assemble(
            current_message="hello",
            media=None,
            channel="cli",
            chat_id="chat-1",
            sender_id="user-1",
            session_summary=None,
            session_metadata=None,
            internal_event=None,
            runtime_context=None,
            session_key="cli:test-session",
            recovered_continuity_block=None,
            include_current_message=True,
        )

    assembled_call = _find_log_call(mock_log_event, "context.assembled")
    assert assembled_call is not None, "context.assembled 未被记录"
    block_kinds = assembled_call.kwargs["block_kinds"]
    assert None not in block_kinds, f"block_kinds 包含 None: {block_kinds}"
    assert "user_text" in block_kinds, f"block_kinds 缺少 user_text: {block_kinds}"
