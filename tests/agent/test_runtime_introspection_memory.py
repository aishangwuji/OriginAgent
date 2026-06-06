from __future__ import annotations

from OriginAgent.agent.introspection.service import RuntimeIntrospectionService


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
