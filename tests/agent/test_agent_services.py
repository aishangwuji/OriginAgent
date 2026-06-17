from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from OriginAgent.agent.loop import AgentLoop


def test_agent_service_container_exposes_stable_services(tmp_path: Path) -> None:
    bus = MagicMock()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    with patch("OriginAgent.agent.loop.SessionManager"), \
         patch("OriginAgent.agent.loop.SubagentManager") as mock_subagents, \
         patch("OriginAgent.agent.loop.Dream"):
        mock_subagents.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)

    assert loop.services.sessions is loop.sessions
    assert loop.services.tools is loop.tools
    assert loop.services.context is loop.context
    assert loop.services.turn_pipeline is loop._turn_pipeline
    assert loop.services.cognitive_runtime is loop._cognitive_runtime
    assert not hasattr(loop.services, "_running")
    assert not hasattr(loop.services, "_pending_queues")
    assert not hasattr(loop.services, "_session_locks")
    assert not hasattr(loop.services, "_active_tasks")
