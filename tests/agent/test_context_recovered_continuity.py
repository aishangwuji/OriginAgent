"""build_recovered_continuity_context 结构化渲染测试（P0 修复）。

背景：原实现把整个 checkpoint 通过 ``json.dumps(dict(snapshot))`` 灌入上下文，
导致历史归档 summary 与结构化字段无差别暴露（"突然聊起很久之前的事"），
且与已结构化渲染的 recent_turns_text/cold_indices_text 重复。

本测试验证修复后的输出满足以下契约：
1. 不再包含 ``json.dumps`` 生成的完整 JSON（断言不出现 ``"session_key":`` 这类 JSON 键值对格式）；
2. 非空的结构化字段以 ``## {Field Name}`` 标题渲染；
3. 已有的 Recent Turns Summary / Cold Indices 渲染逻辑保持不变；
4. 空字段不渲染对应标题；
5. 输出包含 updated_at 的渲染，让 Agent 感知 checkpoint 新鲜度。
"""
from __future__ import annotations

from OriginAgent.agent.context import ContextBuilder


# ── 1. 不再注入完整 JSON ──────────────────────────────────────────


def test_output_does_not_contain_raw_json_dump():
    """输出文本不应包含 json.dumps 生成的 JSON 键值对格式。"""
    snapshot = {
        "session_key": "cli:test",
        "current_goal": "goal",
        "current_plan": ["step 1"],
        "open_loops": [],
        "active_constraints": [],
        "pending_confirmation_refs": [],
        "recent_turns_summary": [],
        "cold_indices": [],
        "updated_at": "2026-07-17T00:00:00+00:00",
    }

    block = ContextBuilder.build_recovered_continuity_context(snapshot)
    text = block["text"]

    # 不应出现 JSON 键值对格式
    assert '"session_key":' not in text
    assert '"current_goal":' not in text
    assert '"recent_turns_summary":' not in text
    assert '"cold_indices":' not in text
    # 也不应出现 JSON 数组格式
    assert '"step 1"' not in text


# ── 2. 非空结构化字段渲染为 ## 标题 ────────────────────────────────


def test_non_empty_structured_fields_rendered_with_headings():
    """非空的 current_goal/current_plan/open_loops/active_constraints/pending_confirmation_refs
    应在输出中渲染对应的结构化标题。"""
    snapshot = {
        "session_key": "cli:test",
        "current_goal": "完成 P0 修复",
        "current_plan": ["步骤一", "步骤二"],
        "open_loops": ["待回填的循环节点"],
        "active_constraints": ["不得破坏既有契约"],
        "pending_confirmation_refs": ["confirm#1"],
        "recent_turns_summary": [],
        "cold_indices": [],
        "updated_at": "2026-07-17T00:00:00+00:00",
    }

    block = ContextBuilder.build_recovered_continuity_context(snapshot)
    text = block["text"]

    assert "## Current Goal" in text
    assert "完成 P0 修复" in text

    assert "## Current Plan" in text
    assert "- 步骤一" in text
    assert "- 步骤二" in text

    assert "## Open Loops" in text
    assert "- 待回填的循环节点" in text

    assert "## Active Constraints" in text
    assert "- 不得破坏既有契约" in text

    assert "## Pending Confirmations" in text
    assert "- confirm#1" in text


# ── 3. 已有 recent_turns_text / cold_indices_text 渲染逻辑保持不变 ──


def test_recent_turns_and_cold_indices_still_rendered():
    """当对应数据存在时，Recent Turns Summary 与 Cold Indices 应照常渲染。"""
    snapshot = {
        "session_key": "cli:test",
        "current_goal": "goal",
        "current_plan": [],
        "open_loops": [],
        "active_constraints": [],
        "pending_confirmation_refs": [],
        "recent_turns_summary": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ],
        "cold_indices": [
            {
                "turn_range": "51-100",
                "summary": "讨论了背题提醒系统",
                "key_entities": ["背题", "cron"],
            }
        ],
        "updated_at": "2026-07-17T00:00:00+00:00",
    }

    block = ContextBuilder.build_recovered_continuity_context(snapshot)
    text = block["text"]

    assert "## Recent Turns Summary" in text
    assert "[user] Hi" in text
    assert "[assistant] Hello" in text

    assert "## Cold Indices" in text
    assert "[51-100] 讨论了背题提醒系统 (背题, cron)" in text


# ── 4. 空字段不渲染对应标题 ────────────────────────────────────────


def test_empty_fields_do_not_render_headings():
    """部分字段为空时，对应标题不应出现在输出中。"""
    snapshot = {
        "session_key": "cli:test",
        "current_goal": "",  # 空
        "current_plan": [],  # 空列表
        "open_loops": [],  # 空列表
        "active_constraints": [],  # 空列表
        "pending_confirmation_refs": [],  # 空列表
        "recent_turns_summary": [],  # 空
        "cold_indices": [],  # 空
        "updated_at": "2026-07-17T00:00:00+00:00",
    }

    block = ContextBuilder.build_recovered_continuity_context(snapshot)
    text = block["text"]

    assert "## Current Goal" not in text
    assert "## Current Plan" not in text
    assert "## Open Loops" not in text
    assert "## Active Constraints" not in text
    assert "## Pending Confirmations" not in text
    assert "## Recent Turns Summary" not in text
    assert "## Cold Indices" not in text


def test_partial_empty_fields_rendered_correctly():
    """部分字段为空、部分非空时，仅非空字段渲染标题。"""
    snapshot = {
        "session_key": "cli:test",
        "current_goal": "仅目标非空",
        "current_plan": [],  # 空
        "open_loops": ["未闭环 A"],
        "active_constraints": [],  # 空
        "pending_confirmation_refs": [],  # 空
        "recent_turns_summary": [],
        "cold_indices": [],
        "updated_at": "2026-07-17T00:00:00+00:00",
    }

    block = ContextBuilder.build_recovered_continuity_context(snapshot)
    text = block["text"]

    assert "## Current Goal" in text
    assert "仅目标非空" in text
    assert "## Open Loops" in text
    assert "- 未闭环 A" in text

    # 空字段对应标题不应出现
    assert "## Current Plan" not in text
    assert "## Active Constraints" not in text
    assert "## Pending Confirmations" not in text


# ── 5. updated_at 渲染（checkpoint 新鲜度感知）──────────────────────


def test_updated_at_is_rendered():
    """updated_at 字段非空时应渲染为 Checkpoint Updated At 标题，让 Agent 感知新鲜度。"""
    snapshot = {
        "session_key": "cli:test",
        "current_goal": "goal",
        "current_plan": [],
        "open_loops": [],
        "active_constraints": [],
        "pending_confirmation_refs": [],
        "recent_turns_summary": [],
        "cold_indices": [],
        "updated_at": "2026-07-17T12:34:56+00:00",
    }

    block = ContextBuilder.build_recovered_continuity_context(snapshot)
    text = block["text"]

    assert "## Checkpoint Updated At" in text
    assert "2026-07-17T12:34:56+00:00" in text


def test_updated_at_missing_does_not_render_heading():
    """updated_at 缺失或为空时，不应渲染 Checkpoint Updated At 标题。"""
    snapshot = {
        "session_key": "cli:test",
        "current_goal": "goal",
        "current_plan": [],
        "open_loops": [],
        "active_constraints": [],
        "pending_confirmation_refs": [],
        "recent_turns_summary": [],
        "cold_indices": [],
        # 无 updated_at
    }

    block = ContextBuilder.build_recovered_continuity_context(snapshot)
    text = block["text"]

    assert "## Checkpoint Updated At" not in text


# ── 元信息保持不变 ──────────────────────────────────────────────────


def test_meta_kind_unchanged():
    """修复后 _meta.kind 应保持为 RECOVERED_CONTINUITY_CONTEXT_KIND。"""
    snapshot = {
        "session_key": "cli:test",
        "current_goal": "goal",
    }

    block = ContextBuilder.build_recovered_continuity_context(snapshot)

    assert block["type"] == "text"
    assert block["_meta"]["kind"] == ContextBuilder.RECOVERED_CONTINUITY_CONTEXT_KIND
    assert block["_meta"]["trust"] == "internal"
