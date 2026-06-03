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

def test_core_does_not_import_smart_home_domain_pack() -> None:
    offenders: dict[Path, list[str]] = {}
    for root in CORE_ROOTS:
        for path in root.rglob("*.py"):
            imports = _smart_home_imports(path)
            if imports:
                offenders[path] = imports

    assert offenders == {}


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
