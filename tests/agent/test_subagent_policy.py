from __future__ import annotations

from OriginAgent.agent.subagent_policy import SubagentPolicy
from OriginAgent.security.capabilities import CapabilitySnapshot


def test_default_subagent_policy_normal_mode_exposes_daily_tools() -> None:
    policy = SubagentPolicy.default_for_parent(CapabilitySnapshot.user_turn())

    snapshot = policy.capability_snapshot
    assert snapshot.can_read_files is True
    assert snapshot.can_write_files is False
    assert snapshot.can_exec is False
    assert snapshot.can_create_cron is False
    assert snapshot.can_spawn is False
    assert snapshot.can_send_cross_target is False
    assert policy.allow_web is True
    assert {"read_file", "write_file", "edit_file", "exec", "web_search", "web_fetch"}.issubset(
        policy.allowed_tool_names
    )


def test_restricted_subagent_policy_is_read_only_and_no_web() -> None:
    policy = SubagentPolicy.from_config(CapabilitySnapshot.user_turn(), mode="restricted")

    assert policy.allow_web is False
    assert policy.allowed_tool_names == frozenset({"read_file", "list_dir", "glob", "grep", "ask_user"})


def test_restricted_subagent_policy_without_parent_read_blocks_tools() -> None:
    policy = SubagentPolicy.from_config(CapabilitySnapshot.scheduled_default(), mode="restricted")

    assert policy.capability_snapshot.can_read_files is False
    assert policy.allowed_tool_names == frozenset()


def test_child_policy_inherits_parent_allowed_tools_when_not_overridden() -> None:
    parent = SubagentPolicy.from_config(CapabilitySnapshot.user_turn(), mode="restricted")

    child = parent.child_policy()

    assert child.allowed_tool_names == parent.allowed_tool_names
    assert child.allow_web is False


def test_child_policy_allows_nested_spawn_only_when_explicitly_enabled() -> None:
    parent = SubagentPolicy(
        capability_snapshot=CapabilitySnapshot.user_turn().derive_subagent(),
        allowed_tool_names=frozenset({"read_file", "list_dir", "glob", "grep", "spawn"}),
        allow_web=False,
        allow_nested_spawn=True,
        max_subagent_depth=2,
        max_children_per_subagent=2,
        child_allowed_tool_names=frozenset({"read_file", "grep"}),
    )

    child = parent.child_policy()

    assert child.allow_nested_spawn is True
    assert child.max_subagent_depth == 1
    assert child.max_children_per_subagent == 2
    assert child.allowed_tool_names == frozenset({"read_file", "grep"})