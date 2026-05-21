from __future__ import annotations

import ast
from pathlib import Path


CORE_ROOTS = (
    Path("OriginAgent/agent"),
    Path("OriginAgent/config"),
    Path("OriginAgent/session"),
    Path("OriginAgent/templates"),
)
FORBIDDEN_IMPORT_PREFIX = "OriginAgent.domain_packs.smart_home"

# Existing smart-home bridge debt scheduled for migration into
# OriginAgent/domain_packs/smart_home. This allowlist should only shrink.
SMART_HOME_BRIDGE_DEBT = {
    Path("OriginAgent/agent/action_safety.py"),
    Path("OriginAgent/agent/device_actions.py"),
    Path("OriginAgent/agent/device_backends.py"),
    Path("OriginAgent/agent/device_factory.py"),
    Path("OriginAgent/agent/device_integrations.py"),
    Path("OriginAgent/agent/devices.py"),
    Path("OriginAgent/agent/memory.py"),
    Path("OriginAgent/agent/permissions.py"),
    Path("OriginAgent/agent/presence.py"),
    Path("OriginAgent/agent/presence_adapters.py"),
    Path("OriginAgent/agent/presence_signals.py"),
    Path("OriginAgent/agent/tools/device.py"),
    Path("OriginAgent/agent/tools/device_messages.py"),
}


def test_core_smart_home_imports_are_limited_to_known_bridge_debt() -> None:
    offenders: dict[Path, list[str]] = {}
    for root in CORE_ROOTS:
        for path in root.rglob("*.py"):
            imports = _smart_home_imports(path)
            if imports:
                offenders[path] = imports

    unexpected = {
        path: imports
        for path, imports in offenders.items()
        if path not in SMART_HOME_BRIDGE_DEBT
    }
    assert unexpected == {}


def _smart_home_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(
                alias.name for alias in node.names if alias.name.startswith(FORBIDDEN_IMPORT_PREFIX)
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(FORBIDDEN_IMPORT_PREFIX):
                imports.append(module)
    return imports
