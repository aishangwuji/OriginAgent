from __future__ import annotations

import json

import pytest

from OpenHome.agent.action_runtime import ActionExecutionResult
from OpenHome.agent.tools.device import LightingSetPowerTool
from OpenHome.agent.tools.device_messages import (
    BACKEND_FAILED,
    CONFIRMATION_REQUIRED,
    DRY_RUN_ACCEPTED,
    POLICY_DENIED,
    VALIDATION_FAILED,
    device_human_message,
)


def test_device_human_messages_are_stable() -> None:
    assert device_human_message("dry_run") == DRY_RUN_ACCEPTED
    assert device_human_message("denied") == POLICY_DENIED
    assert device_human_message("failed") == VALIDATION_FAILED
    assert device_human_message("pending_confirmation") == CONFIRMATION_REQUIRED
    assert device_human_message("backend_failed") == BACKEND_FAILED


class _Executor:
    def submit_typed(self, action):
        return ActionExecutionResult(
            status="dry_run",
            action_id="action_123",
            reason="raw backend said ok",
            confirmation_id=None,
            backend_result={
                "device_id": action.device_id,
                "token": "secret-token",
                "host": "private-host",
                "Authorization": "Bearer secret",
            },
            backend_called=True,
            permission_status="allow",
        )


@pytest.mark.asyncio
async def test_device_tool_result_uses_stable_message_without_raw_payload() -> None:
    tool = LightingSetPowerTool(_Executor())  # type: ignore[arg-type]
    tool.set_context("admin_user", "user_initiated")

    result = await tool.execute(device_id="private_device_7f3a9c", power="on")
    text = json.dumps(result, ensure_ascii=False)

    assert result["human_message"] == DRY_RUN_ACCEPTED
    assert "backend_result" not in result
    assert "private_device_7f3a9c" not in text
    assert "secret-token" not in text
    assert "private-host" not in text
    assert "Bearer" not in text
    assert "raw backend said ok" not in text
