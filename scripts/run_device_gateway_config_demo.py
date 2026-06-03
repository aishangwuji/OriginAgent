from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from OriginAgent.agent.loop import AgentLoop
from OriginAgent.agent.tools.device_messages import DRY_RUN_ACCEPTED
from OriginAgent.bus.queue import MessageBus
from OriginAgent.config.schema import Config, DeviceToolsConfig
from OriginAgent.security.capabilities import CapabilitySnapshot


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    return provider


def _config(workspace: Path, device: DeviceToolsConfig | None = None) -> Config:
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    if device is not None:
        config.tools.device = device
    return config


def _lighting_tool_names(loop: AgentLoop) -> list[str]:
    return [name for name in loop.tools.tool_names if name.startswith("originagent_device_lighting_")]


async def _run_dry_run_tool(loop: AgentLoop) -> dict:
    loop.tools.set_capability_snapshot(CapabilitySnapshot.user_turn())
    tool = loop.tools.get("originagent_device_lighting_set_power")
    assert tool is not None
    if hasattr(tool, "set_context"):
        tool.set_context("admin_user", "user_initiated")
    return await tool.execute(device_id="private_device_7f3a9c", power="on")


def _audit_text(workspace: Path) -> str:
    audit_dir = workspace / "memory" / "audit"
    if not audit_dir.exists():
        return ""
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(audit_dir.glob("*.jsonl"))
    )


def main() -> None:
    import asyncio

    root = Path(tempfile.mkdtemp(prefix="originagent-device-config-demo-"))
    default_loop = AgentLoop.from_config(
        _config(root / "default"),
        bus=MessageBus(),
        provider=_provider(),
    )
    assert _lighting_tool_names(default_loop) == []
    print("[PASS] default config registers no device tools")

    dry_run_workspace = root / "dry-run"
    dry_run_loop = AgentLoop.from_config(
        _config(
            dry_run_workspace,
            DeviceToolsConfig(
                enabled=True,
                lighting_enabled=True,
                mode="dry_run",
                backend="fake",
            ),
        ),
        bus=MessageBus(),
        provider=_provider(),
    )
    names = _lighting_tool_names(dry_run_loop)
    assert names == [
        "originagent_device_lighting_set_power",
        "originagent_device_lighting_set_brightness",
        "originagent_device_lighting_set_color_temperature",
    ]
    print("[PASS] dry-run fake config registers exactly 3 lighting tools")

    result = asyncio.run(_run_dry_run_tool(dry_run_loop))
    result_text = json.dumps(result, ensure_ascii=False)
    assert result["human_message"] == DRY_RUN_ACCEPTED
    assert "private_device_7f3a9c" not in result_text
    assert "backend_result" not in result
    print("[PASS] dry-run tool returns stable redacted message")

    audit = _audit_text(dry_run_workspace)
    assert "private_device_7f3a9c" not in audit
    print("[PASS] action audit does not contain raw device id")

    real_loop = AgentLoop.from_config(
        _config(
            root / "real",
            DeviceToolsConfig(
                enabled=True,
                lighting_enabled=True,
                mode="real",
                backend="lighting_client",
            ),
        ),
        bus=MessageBus(),
        provider=_provider(),
    )
    assert _lighting_tool_names(real_loop) == []
    print("[PASS] real mode config registers no device tools")
    print(f"demo_workspace={root}")


if __name__ == "__main__":
    main()
