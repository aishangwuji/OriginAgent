"""Tests for `block_kinds` source-enrichment in ContextAssemblerV2.assemble.

验证 `context.assembled` 事件的 `block_kinds` 字段在 `_meta.source` 存在时
扩展为 `kind:source` 格式，source 缺失时保持纯 `kind`（无冒号）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from OriginAgent.agent.context_assembler import ContextAssemblerV2


def _build_minimal_builder() -> MagicMock:
    """构造最小 mock builder，使 merged 仅包含 build_runtime_context_block 返回的受测 block。

    其余 builder 返回空/None，确保 block_kinds 仅由受测 block 贡献，
    断言可精确到单一元素。
    """
    builder = MagicMock()
    builder.timezone = "UTC"
    builder.WORKING_MEMORY_CONTEXT_KIND = "working_memory"
    builder.WORLD_STATE_CONTEXT_KIND = "world_state"
    builder.world_state = None
    builder.session_store = None

    builder.build_user_content.return_value = []
    builder.prepare_prewarm_bundle.return_value = None
    builder.build_phase1_continuity_blocks.return_value = []
    builder.build_reference_context_blocks.return_value = []
    builder.build_internal_event_block.return_value = {
        "type": "text",
        "text": "",
        "_meta": {"kind": "internal_event"},
    }

    builder.collect_assembly_audit.return_value = {
        "media": {},
        "retrieval_fusion": {
            "enabled": True,
            "sources_used": [],
            "source_counts": {},
            "deduped_count": 0,
            "trimmed_count": 0,
            "hits": {},
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


def _assemble(assembler: ContextAssemblerV2) -> dict:
    """执行一次 assemble 并返回 context.assembled 事件的 kwargs。"""
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
    return assembled_call.kwargs


def test_block_kinds_includes_source_when_present():
    """_meta.source 存在时，block_kinds 应输出 `kind:source` 格式。

    背景: 日志中多个 reference_context block 无法区分来源（fact_store /
    nearline / semantic_search），扩展为 kind:source 以提升可观测性。
    """
    builder = _build_minimal_builder()
    builder.build_runtime_context_block.return_value = {
        "type": "text",
        "text": "ref from fact_store",
        "_meta": {"kind": "reference_context", "source": "fact_store"},
    }
    assembler = ContextAssemblerV2(builder)

    kwargs = _assemble(assembler)
    block_kinds = kwargs["block_kinds"]

    # merged 仅含受测 block，block_kinds 应为单一元素
    assert block_kinds == ["reference_context:fact_store"], (
        f"block_kinds 未按 kind:source 格式输出: {block_kinds}"
    )


def test_block_kinds_omits_source_when_absent():
    """_meta.source 缺失时，block_kinds 应保持纯 kind（无冒号后缀）。

    避免 source 缺失的 block 产生形如 `runtime_context:` 的尾随冒号。
    """
    builder = _build_minimal_builder()
    builder.build_runtime_context_block.return_value = {
        "type": "text",
        "text": "runtime",
        "_meta": {"kind": "runtime_context"},
    }
    assembler = ContextAssemblerV2(builder)

    kwargs = _assemble(assembler)
    block_kinds = kwargs["block_kinds"]

    assert block_kinds == ["runtime_context"], (
        f"source 缺失时 block_kinds 应为纯 kind 无冒号: {block_kinds}"
    )
    assert ":" not in block_kinds[0], (
        f"source 缺失却出现冒号: {block_kinds[0]}"
    )
