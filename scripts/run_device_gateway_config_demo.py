"""Config wiring demo for the lighting-only device gateway."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from OpenHome.agent.loop import AgentLoop
from OpenHome.bus.queue import MessageBus
from OpenHome.config.schema import Config, DeviceToolsConfig


def _provider():
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return provider


def _lighting_names(loop: AgentLoop) -> list[str]:
    return [name for name in loop.tools.tool_names if name.startswith("openhome_device_lighting_")]


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="openhome-device-config-") as tmp:
        workspace = Path(tmp)
        cfg = Config()
        cfg.agents.defaults.workspace = str(workspace)
        default_loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())
        assert _lighting_names(default_loop) == []
        print("[PASS] default config has no lighting tools")

        cfg.tools.device = DeviceToolsConfig(
            enabled=True,
            lighting_enabled=True,
            mode="dry_run",
            backend="fake",
        )
        loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())
        names = _lighting_names(loop)
        assert len(names) == 3
        print("[PASS] dry-run config registers exactly 3 lighting tools")

        loop._set_tool_context("cli", "direct", actor_id="demo_user", trigger="user_initiated")
        result = await loop.tools.execute(
            "openhome_device_lighting_set_power",
            {"device_id": "demo_lamp", "power": "on"},
        )
        assert isinstance(result, dict)
        assert result["status"] == "success"
        print("[PASS] typed lighting tool call dry-run accepted")

        tool_audit = workspace / "memory" / "audit" / "tool_calls.jsonl"
        action_audit = workspace / "memory" / "audit" / "action_decisions.jsonl"
        assert tool_audit.exists()
        assert action_audit.exists()
        raw_audit = tool_audit.read_text(encoding="utf-8") + action_audit.read_text(encoding="utf-8")
        assert "demo_lamp" not in raw_audit
        print("[PASS] tool/action audit files exist without raw device id")
        print(json.dumps({"workspace": str(workspace), "tools": names}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

