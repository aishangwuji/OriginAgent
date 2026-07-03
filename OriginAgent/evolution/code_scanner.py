"""AST-based static analysis for evolution module capability enforcement.

Scans Python source files in an artifact directory for imports and calls
that require capabilities not declared in the module manifest.

This is NOT a security sandbox — it is a static lint pass that catches
obvious manifest-code mismatches.  Determined adversaries can bypass
AST scanning with dynamic imports, but this layer eliminates the
accidental/bypass class: a module that declares ``exec: false`` while
importing ``os`` will be rejected.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── Capability-to-import mapping ──────────────────────────────────────
# Top-level package name → required permission key.
# If a module imports any of these packages, the corresponding capability
# MUST be declared in the manifest.
DANGEROUS_IMPORTS: dict[str, str] = {
    "os": "exec",
    "subprocess": "exec",
    "ctypes": "exec",
    "signal": "exec",
    "socket": "device_domains",
    "shutil": "write_files",
}

# Built-in functions that always require the "exec" capability.
DANGEROUS_CALLS: frozenset[str] = frozenset(
    {"eval", "exec", "compile", "__import__"}
)

# Additional module-qualified calls that require specific capabilities.
# Key: (module, attr) tuple → required permission.
DANGEROUS_MODULE_CALLS: dict[tuple[str, str], str] = {
    ("pickle", "loads"): "exec",
    ("pickle", "load"): "exec",
    ("marshal", "loads"): "exec",
}


@dataclass(frozen=True)
class CapabilityScanResult:
    """Result of scanning an artifact directory for capability violations."""

    ok: bool
    """True if no violations found."""

    violations: tuple[str, ...]
    """Human-readable violation descriptions, one per mismatched file."""

    scanned_file_count: int = 0
    """Number of .py files scanned."""


def scan_artifact_for_violations(
    artifact_dir: Path,
    declared_permissions: dict[str, Any],
) -> CapabilityScanResult:
    """Scan all .py files in *artifact_dir* for capability violations.

    For each Python file, parses the AST and walks all nodes looking for:
    1. ``import X`` / ``from X import Y`` — checks X against DANGEROUS_IMPORTS
    2. ``eval(...)`` / ``exec(...)`` etc. — checks against DANGEROUS_CALLS
    3. ``pickle.loads(...)`` etc. — checks against DANGEROUS_MODULE_CALLS

    A violation is raised when an import or call requires a capability
    that the manifest does NOT declare (or explicitly denies).
    """
    violations: list[str] = []
    py_files = sorted(
        p for p in artifact_dir.rglob("*.py") if p.is_file()
    )
    scanned = 0

    for py_file in py_files:
        scanned += 1
        try:
            source = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            violations.append(
                f"{_rel(py_file, artifact_dir)}: cannot read source"
            )
            continue

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            violations.append(
                f"{_rel(py_file, artifact_dir)}: syntax error — {exc}"
            )
            continue

        file_violations = _scan_tree(
            tree,
            declared_permissions,
        )
        for v in file_violations:
            violations.append(f"{_rel(py_file, artifact_dir)}: {v}")

    return CapabilityScanResult(
        ok=len(violations) == 0,
        violations=tuple(violations),
        scanned_file_count=scanned,
    )


def _scan_tree(
    tree: ast.AST,
    permissions: dict[str, Any],
) -> list[str]:
    """Walk *tree* and return capability violations found."""
    violations: list[str] = []

    for node in ast.walk(tree):
        # ── Import statements ─────────────────────────────────────────
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in DANGEROUS_IMPORTS:
                    needed_perm = DANGEROUS_IMPORTS[root]
                    if not permissions.get(needed_perm):
                        violations.append(
                            f"imports '{alias.name}' but "
                            f"permission '{needed_perm}' is not declared"
                        )

        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            root = node.module.split(".")[0]
            if root in DANGEROUS_IMPORTS:
                needed_perm = DANGEROUS_IMPORTS[root]
                if not permissions.get(needed_perm):
                    violations.append(
                        f"imports from '{node.module}' but "
                        f"permission '{needed_perm}' is not declared"
                    )

        # ── Bare dangerous calls ──────────────────────────────────────
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in DANGEROUS_CALLS:
                    if not permissions.get("exec"):
                        violations.append(
                            f"calls {node.func.id}() but "
                            f"permission 'exec' is not declared"
                        )
            # ── Module-qualified calls (pickle.loads etc.) ────────────
            elif isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    key = (node.func.value.id, node.func.attr)
                    if key in DANGEROUS_MODULE_CALLS:
                        needed_perm = DANGEROUS_MODULE_CALLS[key]
                        if not permissions.get(needed_perm):
                            violations.append(
                                f"calls {node.func.value.id}.{node.func.attr}() "
                                f"but permission '{needed_perm}' is not declared"
                            )

    return violations


def _rel(path: Path, base: Path) -> str:
    """Return *path* relative to *base*, or just the filename."""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name
