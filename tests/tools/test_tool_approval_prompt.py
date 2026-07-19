"""方案 C2: tool_approval prompt 模板四段式测试。

Agent 申请授权时应主动说明：操作、收益、风险、是否授权。
"""
from __future__ import annotations

from OriginAgent.agent.tools.registry import _tool_approval_prompt


def test_tool_approval_prompt_includes_four_sections() -> None:
    """prompt 应包含操作/收益/风险/是否授权四段式提示。"""
    prompt = _tool_approval_prompt("exec", {"command": "ls -la"})

    # 必须包含四段式关键词
    assert "操作" in prompt or "action" in prompt.lower(), f"prompt 缺少'操作'段: {prompt!r}"
    assert "收益" in prompt or "benefit" in prompt.lower(), f"prompt 缺少'收益'段: {prompt!r}"
    assert "风险" in prompt or "risk" in prompt.lower(), f"prompt 缺少'风险'段: {prompt!r}"
    assert "授权" in prompt or "approve" in prompt.lower(), f"prompt 缺少'是否授权'段: {prompt!r}"


def test_tool_approval_prompt_includes_tool_name_and_params() -> None:
    """prompt 仍需包含工具名和参数摘要（保留可审计性）。"""
    prompt = _tool_approval_prompt("read_file", {"path": "/tmp/secret.txt"})

    assert "read_file" in prompt
    assert "/tmp/secret.txt" in prompt or "secret.txt" in prompt


def test_tool_approval_prompt_instructs_agent_to_ask_in_natural_language() -> None:
    """prompt 应指示 Agent 用自然语言向用户申请，而非透传英文模板。"""
    prompt = _tool_approval_prompt("exec", {"command": "rm -rf /tmp/cache"})

    # 应有明确的"用自然语言申请"指示
    assert (
        "自然语言" in prompt
        or "natural language" in prompt.lower()
        or "用大白话" in prompt
        or "向用户申请" in prompt
    ), f"prompt 缺少自然语言申请指示: {prompt!r}"
