from __future__ import annotations

from OriginAgent.agent.introspection.service import RuntimeIntrospectionService
from OriginAgent.config.schema import NearlineMemoryConfig


class _Registry:
    tool_names = ["read_file", "session_search"]


class _Sessions:
    def list_sessions(self):
        return [{"key": "cli:direct"}]


def test_runtime_context_snapshot_includes_nearline_memory_summary(tmp_path) -> None:
    service = RuntimeIntrospectionService(
        workspace=tmp_path,
        registry=_Registry(),
        sessions=_Sessions(),
        pending_queues={},
    )

    snapshot = service.runtime_context_snapshot()

    assert snapshot.memory_summary["nearline"]["status"] == "disabled"
    assert snapshot.nearline_memory_summary["status"] == "disabled"
    assert snapshot.nearline_memory_summary["memcell_count"] == 0


def test_runtime_context_snapshot_uses_runtime_nearline_config(tmp_path) -> None:
    service = RuntimeIntrospectionService(
        workspace=tmp_path,
        registry=_Registry(),
        sessions=_Sessions(),
        pending_queues={},
        nearline_memory_config=NearlineMemoryConfig(
            enabled=True,
            pipeline_enabled=False,
            profile_shadow_write_enabled=True,
        ),
    )

    snapshot = service.runtime_context_snapshot()

    assert snapshot.memory_summary["nearline"]["status"] == "idle"
    assert snapshot.nearline_memory_summary["status"] == "idle"
    assert snapshot.nearline_memory_summary["nearline_enabled"] is True
    assert snapshot.nearline_memory_summary["pipeline_enabled"] is False
    assert snapshot.nearline_memory_summary["profile_shadow_write_enabled"] is True
    assert snapshot.nearline_memory_summary["memcell_count"] == 0


def test_runtime_context_snapshot_includes_workspace_memory_state(tmp_path) -> None:
    service = RuntimeIntrospectionService(
        workspace=tmp_path,
        registry=_Registry(),
        sessions=_Sessions(),
        pending_queues={},
    )

    snapshot = service.runtime_context_snapshot()

    assert snapshot.memory_summary["user_profile_file"]["status"] == "missing"
    assert snapshot.memory_summary["memory_candidate_queue"]["status"] == "lazy_not_created"
