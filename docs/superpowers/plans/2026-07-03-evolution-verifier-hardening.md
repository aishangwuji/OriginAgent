# Evolution Verifier Security Hardening & Technical Debt Remediation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the structural security gap in Evolution Module Verifier where code-semantic verification is absent (manifest permissions are trusted without checking actual code behavior), add adversarial regression tests, and lay groundwork for RuntimeDependencies decomposition and meta-cognition facade.

**Architecture:** Four-phase plan ordered by risk priority. Phase 1-A (AST scanner + adversarial test) is the critical path — it replaces the hardcoded `ok=True` in `_append_python_file_check` with real AST-based capability violation detection. Phase 1-B adds characterization tests for the evolution activation pipeline. Phase 2 (Strangler Fig) and Phase 3 (MetaCognitionFacade) are maintenance improvements that can proceed independently and incrementally.

**Tech Stack:** Python 3.11+, `ast` stdlib module, pytest, dataclasses

## Global Constraints

- Python >= 3.11
- Follow PEP 8 via ruff (select E, F, I, N, W; ignore E501)
- Line length: 100
- Never run `ruff format` — destroys git blame
- Tests use pytest with `asyncio_mode = "auto"`
- All new code must have type annotations on function signatures
- Existing verifier tests must continue to pass
- `EvolutionVerificationReport` is `frozen=True` — new fields need defaults

---

## ⚠️ Priority Order

```
Phase 1-A (地基) → Phase 1-B (地基) → Phase 2 (维护) → Phase 3 (维护)
     ↑                                      ↑
  DO FIRST                              Can be done in any order,
                                        incremental Strangler Fig
```

**If you only have time for one thing, do Phase 1-A Tasks 1-3 (adversarial test + AST scanner).** The red test is the most persuasive piece of engineering debt documentation in the entire codebase.

---

## File Structure

| File | Role | Phase |
|------|------|-------|
| `OriginAgent/evolution/code_scanner.py` | **NEW** — AST-based capability violation scanner | 1-A |
| `OriginAgent/evolution/verifier.py` | **MODIFY** — replace `_append_python_file_check`, integrate scanner | 1-A |
| `tests/evolution/test_code_scanner.py` | **NEW** — unit tests for scanner | 1-A |
| `tests/evolution/test_verifier.py` | **MODIFY** — add adversarial tests | 1-A, 1-B |
| `tests/evolution/test_activation_pipeline.py` | **NEW** — characterization tests for activation | 1-B |
| `OriginAgent/agent/agent_runtime.py` | **MODIFY** — add sub-container dataclasses | 2 |
| `OriginAgent/agent/loop.py` | **MODIFY** — wire sub-containers in `__init__` | 2 |
| `OriginAgent/agent/meta_cognition/__init__.py` | **NEW** — MetaCognitionFacade module | 3 |
| `docs/adr/2026-07-03-meta-cognition-architecture.md` | **NEW** — ADR for facade pattern | 3 |

---

## Phase 1-A: AST Scanner + Adversarial Test (地基 — Priority 1)

### Task 1: Write the adversarial test (RED — must fail on current code)

**Files:**
- Modify: `tests/evolution/test_verifier.py` (append new test functions)

**Interfaces:**
- Consumes: `EvolutionModuleVerifier`, `EvolutionModuleManager`, `_write_package`, `_stage_package`, `_check` (existing test helpers in `test_verifier.py`)
- Produces: Two failing tests that will pass in Task 3

- [ ] **Step 1: Add the adversarial test — manifest says `exec: false`, code imports `os`**

```python
def test_adversarial_exec_false_but_code_imports_os_fails(tmp_path: Path) -> None:
    """A module declaring exec:false but importing os must FAIL verification."""
    source = _write_package(
        tmp_path / "source",
        {"permissions": {"exec": False, "read_files": True}},
    )
    # Add a Python file that imports os — violates exec:false declaration
    (source / "helpers.py").write_text(
        "import os\nimport subprocess\n\ndef do_work():\n    pass\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    staged = EvolutionModuleManager(workspace).stage(source)
    assert staged.ok

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    # This MUST fail — the code imports os/subprocess but manifest denies exec
    assert report.ok is False, (
        f"Verifier should reject module with os import when exec:false. "
        f"Violations: {getattr(report, 'code_semantic_violations', 'N/A')}"
    )
    assert "permission_code_semantic_mismatch" in {
        check["code"] for check in report.checks
    }
    semantic_check = _check(report, "permission_code_semantic_mismatch")
    assert semantic_check["ok"] is False
```

- [ ] **Step 2: Add a second adversarial test — manifest says `write_files: false`, code imports `shutil`**

```python
def test_adversarial_write_files_false_but_code_imports_shutil_fails(tmp_path: Path) -> None:
    """A module declaring write_files:false but importing shutil must FAIL."""
    source = _write_package(
        tmp_path / "source",
        {"permissions": {"write_files": False, "read_files": True}},
    )
    (source / "file_ops.py").write_text(
        "import shutil\nimport pathlib\n\ndef backup():\n    pass\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    staged = EvolutionModuleManager(workspace).stage(source)
    assert staged.ok

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is False
    semantic_check = _check(report, "permission_code_semantic_mismatch")
    assert semantic_check["ok"] is False
```

- [ ] **Step 3: Add a positive test — clean module with matching permissions passes**

```python
def test_clean_module_with_no_dangerous_imports_passes_semantic_check(tmp_path: Path) -> None:
    """A module with no dangerous imports and correct manifest must pass."""
    source = _write_package(
        tmp_path / "source",
        {"permissions": {"read_files": True}},
    )
    (source / "clean_helpers.py").write_text(
        "from pathlib import Path\nfrom typing import Any\n\ndef read_config(p: Path) -> dict[str, Any]:\n    return {}\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    staged = EvolutionModuleManager(workspace).stage(source)
    assert staged.ok

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is True
    semantic_check = _check(report, "permission_code_semantic_mismatch")
    assert semantic_check["ok"] is True
```

- [ ] **Step 4: Run tests to verify they FAIL**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evolution/test_verifier.py::test_adversarial_exec_false_but_code_imports_os_fails -v`

Expected: **FAIL** — `report.ok` is `True` because `_append_python_file_check` hardcodes `ok=True` and no code scanning exists.

- [ ] **Step 5: Commit the red tests**

```bash
git add tests/evolution/test_verifier.py
git commit -m "test: add adversarial evolution verifier tests (currently RED)

These tests expose the structural gap in EvolutionModuleVerifier:
manifest permissions are trusted without code-semantic verification.
Currently FAIL because _append_python_file_check has hardcoded ok=True.

Acceptance criteria: these tests turn GREEN when AST-based scanning
detects os/subprocess imports violating exec:false declarations."
```

### Task 2: Create the AST code scanner module

**Files:**
- Create: `OriginAgent/evolution/code_scanner.py`

**Interfaces:**
- Consumes: `ast` (stdlib), `pathlib.Path`
- Produces: `scan_artifact_for_violations(artifact_dir: Path, declared_permissions: dict[str, Any]) -> CapabilityScanResult`

- [ ] **Step 1: Create `OriginAgent/evolution/code_scanner.py`**

```python
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
```

- [ ] **Step 2: Create unit tests for the scanner**

Create `tests/evolution/test_code_scanner.py`:

```python
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from OriginAgent.evolution.code_scanner import (
    DANGEROUS_CALLS,
    DANGEROUS_IMPORTS,
    DANGEROUS_MODULE_CALLS,
    CapabilityScanResult,
    _scan_tree,
    scan_artifact_for_violations,
)


class TestScanTreeImports:
    def test_import_os_without_exec_fails(self) -> None:
        tree = ast.parse("import os")
        violations = _scan_tree(tree, {"exec": False})
        assert len(violations) == 1
        assert "imports 'os'" in violations[0]
        assert "permission 'exec'" in violations[0]

    def test_import_os_with_exec_passes(self) -> None:
        tree = ast.parse("import os")
        violations = _scan_tree(tree, {"exec": True})
        assert len(violations) == 0

    def test_import_subprocess_without_exec_fails(self) -> None:
        tree = ast.parse("import subprocess")
        violations = _scan_tree(tree, {"exec": False})
        assert len(violations) == 1
        assert "subprocess" in violations[0]

    def test_import_from_os_path_without_exec_fails(self) -> None:
        tree = ast.parse("from os.path import join")
        violations = _scan_tree(tree, {"exec": False})
        assert len(violations) == 1
        assert "os.path" in violations[0]

    def test_import_shutil_without_write_files_fails(self) -> None:
        tree = ast.parse("import shutil")
        violations = _scan_tree(tree, {"write_files": False})
        assert len(violations) == 1
        assert "permission 'write_files'" in violations[0]

    def test_import_shutil_with_write_files_passes(self) -> None:
        tree = ast.parse("import shutil")
        violations = _scan_tree(tree, {"write_files": True})
        assert len(violations) == 0

    def test_import_socket_without_device_domains_fails(self) -> None:
        tree = ast.parse("import socket")
        violations = _scan_tree(tree, {"device_domains": ()})
        assert len(violations) == 1
        assert "permission 'device_domains'" in violations[0]

    def test_import_socket_with_device_domains_passes(self) -> None:
        tree = ast.parse("import socket")
        violations = _scan_tree(tree, {"device_domains": ("*",)})
        assert len(violations) == 0

    def test_safe_imports_pass(self) -> None:
        tree = ast.parse("from pathlib import Path\nimport json\nfrom typing import Any")
        violations = _scan_tree(tree, {})
        assert len(violations) == 0

    def test_nested_import_from_ctypes_fails(self) -> None:
        tree = ast.parse("from ctypes import CDLL")
        violations = _scan_tree(tree, {"exec": False})
        assert len(violations) == 1
        assert "ctypes" in violations[0]


class TestScanTreeDangerousCalls:
    def test_eval_call_without_exec_fails(self) -> None:
        tree = ast.parse("eval('1+1')")
        violations = _scan_tree(tree, {"exec": False})
        assert len(violations) == 1
        assert "eval()" in violations[0]

    def test_exec_call_without_exec_fails(self) -> None:
        tree = ast.parse("exec('x=1')")
        violations = _scan_tree(tree, {"exec": False})
        assert len(violations) == 1
        assert "exec()" in violations[0]

    def test_compile_call_without_exec_fails(self) -> None:
        tree = ast.parse("compile('x', '', 'exec')")
        violations = _scan_tree(tree, {"exec": False})
        assert len(violations) == 1
        assert "compile()" in violations[0]

    def test_pickle_loads_without_exec_fails(self) -> None:
        tree = ast.parse("import pickle\npickle.loads(data)")
        violations = _scan_tree(tree, {"exec": False})
        assert len(violations) == 1
        assert "pickle.loads()" in violations[0]

    def test_dangerous_call_with_exec_passes(self) -> None:
        tree = ast.parse("eval('1+1')")
        violations = _scan_tree(tree, {"exec": True})
        assert len(violations) == 0


class TestScanArtifactDirectory:
    def test_clean_directory_passes(self, tmp_path: Path) -> None:
        (tmp_path / "mod.py").write_text(
            "from pathlib import Path\ndef hello():\n    pass\n",
            encoding="utf-8",
        )
        result = scan_artifact_for_violations(tmp_path, {"exec": False})
        assert result.ok is True
        assert len(result.violations) == 0
        assert result.scanned_file_count == 1

    def test_directory_with_os_import_fails(self, tmp_path: Path) -> None:
        (tmp_path / "mod.py").write_text("import os\n", encoding="utf-8")
        result = scan_artifact_for_violations(tmp_path, {"exec": False})
        assert result.ok is False
        assert len(result.violations) == 1
        assert "imports 'os'" in result.violations[0]

    def test_multiple_violations_in_multiple_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("import os\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("import subprocess\n", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "c.py").write_text("import ctypes\n", encoding="utf-8")
        result = scan_artifact_for_violations(tmp_path, {"exec": False})
        assert result.ok is False
        assert len(result.violations) == 3
        assert result.scanned_file_count == 3

    def test_syntax_error_reported_as_violation(self, tmp_path: Path) -> None:
        (tmp_path / "broken.py").write_text("def foo(\n", encoding="utf-8")
        result = scan_artifact_for_violations(tmp_path, {"exec": False})
        assert result.ok is False
        assert any("syntax error" in v for v in result.violations)

    def test_no_python_files_returns_clean(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# hello\n", encoding="utf-8")
        result = scan_artifact_for_violations(tmp_path, {"exec": False})
        assert result.ok is True
        assert result.scanned_file_count == 0
```

- [ ] **Step 3: Run scanner unit tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evolution/test_code_scanner.py -v`

Expected: **All 15 tests PASS** (scanner module is self-contained, no integration needed)

- [ ] **Step 4: Commit the scanner module**

```bash
git add OriginAgent/evolution/code_scanner.py tests/evolution/test_code_scanner.py
git commit -m "feat: add AST-based capability scanner for evolution modules

Introduces OriginAgent.evolution.code_scanner with:
- DANGEROUS_IMPORTS mapping (os→exec, subprocess→exec, socket→device_domains, etc.)
- DANGEROUS_CALLS check for eval/exec/compile/__import__
- DANGEROUS_MODULE_CALLS check for pickle.loads etc.
- scan_artifact_for_violations() — walks all .py files in an artifact dir

Unit tested across imports, calls, multi-file scans, and error handling."
```

### Task 3: Integrate scanner into EvolutionModuleVerifier

**Files:**
- Modify: `OriginAgent/evolution/verifier.py`
- Modify: `tests/evolution/test_verifier.py` (update `test_successful_report_includes_phase_1c_check_codes`)

**Interfaces:**
- Consumes: `OriginAgent.evolution.code_scanner.scan_artifact_for_violations`
- Produces: Updated `EvolutionVerificationReport` with `code_semantic_violations` field

- [ ] **Step 1: Add `code_semantic_violations` field to `EvolutionVerificationReport`**

In `OriginAgent/evolution/verifier.py`, modify the dataclass (line 47-60):

```python
@dataclass(frozen=True)
class EvolutionVerificationReport:
    ok: bool
    status: VerificationStatus
    checks: tuple[dict[str, Any], ...]
    module_id: str = ""
    module_type: str = ""
    module_version: str = ""
    artifact_digest: str = ""
    staging_path: str = ""
    error: str = ""
    permissions_evaluated: dict[str, Any] | None = None
    permissions_denied: tuple[str, ...] = ()
    unknown_keys_rejected: tuple[str, ...] = ()
    code_semantic_violations: tuple[str, ...] = ()
    """AST-detected code-to-manifest mismatches.  Empty when clean."""
```

- [ ] **Step 2: Replace `_append_python_file_check` with `_append_code_semantic_check`**

In `OriginAgent/evolution/verifier.py`, replace the function at line 359-368:

```python
# REMOVE the old function:
# def _append_python_file_check(artifact_dir: Path, checks: list[dict[str, Any]]) -> None:
#     python_count = sum(1 for path in artifact_dir.rglob("*.py") if path.is_file())
#     checks.append(
#         _check(
#             "Python 文件存在性",
#             True,
#             "contains_python_files",
#             f"artifact contains {python_count} Python file(s)",
#         )
#     )

# ADD the new function:
def _append_code_semantic_check(
    artifact_dir: Path,
    permissions: dict[str, Any],
    checks: list[dict[str, Any]],
) -> tuple[str, ...]:
    """Scan Python files for capability violations vs declared permissions.

    Replaces the old file-counting stub with actual AST-based analysis.
    Returns the tuple of violation strings for inclusion in the report.
    """
    from OriginAgent.evolution.code_scanner import scan_artifact_for_violations

    scan_result = scan_artifact_for_violations(artifact_dir, permissions)
    python_count = scan_result.scanned_file_count

    if scan_result.ok and python_count == 0:
        message = "no Python files to scan"
    elif scan_result.ok:
        message = f"scanned {python_count} Python file(s) — no capability violations"
    else:
        violation_summary = "; ".join(scan_result.violations)
        message = (
            f"scanned {python_count} Python file(s) — "
            f"{len(scan_result.violations)} violation(s): {violation_summary}"
        )

    checks.append(
        _check(
            "代码语义-权限声明一致性",
            scan_result.ok,
            "permission_code_semantic_mismatch",
            message,
        )
    )
    return scan_result.violations
```

- [ ] **Step 3: Update `verify()` method to call new scanner and capture violations**

In `OriginAgent/evolution/verifier.py`, modify lines 167-177 of the `verify` method:

```python
        # OLD:
        # _append_python_file_check(artifact_dir, checks)
        #
        # return _report(
        #     checks=checks,
        #     metadata=metadata,
        #     artifact_digest=artifact_digest,
        #     staging_path=staging_path,
        #     permissions_evaluated=permissions_evaluated,
        #     permissions_denied=tuple(permissions_denied),
        #     unknown_keys_rejected=tuple(unknown_keys),
        # )

        # NEW:
        code_semantic_violations = _append_code_semantic_check(
            artifact_dir,
            manifest.permissions,
            checks,
        )

        return _report(
            checks=checks,
            metadata=metadata,
            artifact_digest=artifact_digest,
            staging_path=staging_path,
            permissions_evaluated=permissions_evaluated,
            permissions_denied=tuple(permissions_denied),
            unknown_keys_rejected=tuple(unknown_keys),
            code_semantic_violations=code_semantic_violations,
        )
```

- [ ] **Step 4: Update `_report()` to accept and forward `code_semantic_violations`**

In `OriginAgent/evolution/verifier.py`, modify the `_report` function signature (line 384-408):

```python
def _report(
    *,
    checks: list[dict[str, Any]],
    artifact_digest: str,
    staging_path: str,
    metadata: dict[str, Any] | None = None,
    permissions_evaluated: dict[str, Any] | None = None,
    permissions_denied: tuple[str, ...] = (),
    unknown_keys_rejected: tuple[str, ...] = (),
    code_semantic_violations: tuple[str, ...] = (),
) -> EvolutionVerificationReport:
    failed = [check for check in checks if not check["ok"]]
    return EvolutionVerificationReport(
        ok=not failed,
        status="verified" if not failed else "failed",
        checks=tuple(checks),
        module_id=str((metadata or {}).get("module_id") or ""),
        module_type=str((metadata or {}).get("module_type") or ""),
        module_version=str((metadata or {}).get("version") or ""),
        artifact_digest=artifact_digest,
        staging_path=staging_path,
        error=str(failed[0]["message"]) if failed else "",
        permissions_evaluated=permissions_evaluated or {},
        permissions_denied=permissions_denied,
        unknown_keys_rejected=unknown_keys_rejected,
        code_semantic_violations=code_semantic_violations,
    )
```

- [ ] **Step 5: Update existing test that checks for check codes**

In `tests/evolution/test_verifier.py`, update `test_successful_report_includes_phase_1c_check_codes` (line 83-105) to expect the new check code:

```python
def test_successful_report_includes_phase_1c_check_codes(tmp_path: Path) -> None:
    workspace, staged = _stage_package(tmp_path, {"permissions": {"read_files": True}})

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert {check["code"] for check in report.checks} >= {
        "staging_metadata_integrity",
        "digest_match",
        "manifest_staging_alignment",
        "permission_read_files",
        "permission_write_files",
        "permission_exec",
        "permission_send_cross_target",
        "permission_create_cron",
        "permission_spawn",
        "permission_device_domains",
        "permission_mcp_scopes",
        "permission_unknown_keys",
        "external_endpoints",
        "external_writes_state",
        "context_token_budget",
        "permission_code_semantic_mismatch",  # ← NEW: replaces contains_python_files
    }
```

- [ ] **Step 6: Update `test_python_files_are_not_imported_and_are_reported`**

This test (line 297-314) was testing the old file-counting behavior. Replace it with a test that validates the new scanner finds no violations for a harmless `side_effect.py` that only imports `pathlib`:

```python
def test_python_files_scanned_for_semantic_violations(tmp_path: Path) -> None:
    """Verifier scans .py files and reports no violations for safe imports."""
    source = _write_package(tmp_path / "source")
    (source / "side_effect.py").write_text(
        "from pathlib import Path\n\ndef helper():\n    return Path('/tmp')\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    staged = EvolutionModuleManager(workspace).stage(source)
    assert staged.ok

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is True
    semantic_check = _check(report, "permission_code_semantic_mismatch")
    assert semantic_check["ok"] is True
    assert len(report.code_semantic_violations) == 0
```

- [ ] **Step 7: Run all verifier tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evolution/test_verifier.py tests/evolution/test_code_scanner.py -v`

Expected:
- All existing tests PASS (updated for new check code)
- The three adversarial tests from Task 1 now **PASS** (GREEN)
- All scanner unit tests PASS

- [ ] **Step 8: Run full evolution test suite to check for regressions**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evolution/ -v`

Expected: All tests PASS. No existing tests broken.

- [ ] **Step 9: Commit**

```bash
git add OriginAgent/evolution/verifier.py OriginAgent/evolution/code_scanner.py tests/evolution/
git commit -m "feat: integrate AST code scanner into EvolutionModuleVerifier

Replace _append_python_file_check (hardcoded ok=True, file-count only)
with _append_code_semantic_check that:
- Parses AST of every .py file in the artifact
- Cross-references imports (os,subprocess,socket,ctypes,shutil)
  and calls (eval,exec,compile,pickle.loads) against declared manifest
  permissions
- Produces 'permission_code_semantic_mismatch' check — fails when
  code requires capabilities not declared in manifest

Add adversarial tests that now PASS:
- exec:false + import os → rejected
- write_files:false + import shutil → rejected

Closes the manifest-code trust gap: static linting catches accidental
capability bypasses. This is Layer 1 defense — not a security sandbox,
but eliminates the 'honor system' where a module's self-declared
permissions were trusted without any code-level verification."
```

---

## Phase 1-B: Characterization Tests for Evolution Activation Pipeline

### Task 4: Characterization tests for staging → verify → activate flow

**Files:**
- Create: `tests/evolution/test_activation_pipeline.py`

**Interfaces:**
- Consumes: `EvolutionModuleManager`, `EvolutionModuleVerifier`, `EvolutionCapabilityGate`, `EvolutionLedger`
- Produces: End-to-end characterization tests for the full evolution pipeline

- [ ] **Step 1: Create characterization tests**

```python
"""Characterization tests for the evolution activation pipeline.

These tests document the CURRENT behavior of the staging → verify →
activate → capability snapshot pipeline.  They serve as regression
protection when the verifier or capability gate is modified.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from OriginAgent.evolution import (
    EvolutionCapabilityGate,
    EvolutionLedger,
    EvolutionModuleManager,
    EvolutionModuleVerifier,
)
from OriginAgent.evolution.manifest import MODULE_SCHEMA_VERSION


def _write_skill_package(root: Path, **manifest_overrides: Any) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": MODULE_SCHEMA_VERSION,
        "module_id": "test-skill",
        "module_type": "skill",
        "version": "1.0.0",
        "permissions": {"read_files": True},
    }
    manifest.update(manifest_overrides)
    (root / "evolution_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text("# Test Skill\n", encoding="utf-8")
    return root


class TestStagingToVerification:
    """Characterization: staging produces a verifiable artifact."""

    def test_staged_skill_passes_verification(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(tmp_path / "source")
        staged = EvolutionModuleManager(workspace).stage(source)
        assert staged.ok, f"staging failed: {staged.error}"

        report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
        assert report.ok, f"verification failed: {report.error}"
        assert report.status == "verified"

    def test_staging_metadata_matches_manifest(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(tmp_path / "source")
        staged = EvolutionModuleManager(workspace).stage(source)
        assert staged.ok

        report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
        assert report.module_id == "test-skill"
        assert report.module_type == "skill"
        assert report.module_version == "1.0.0"

    def test_digest_changes_when_artifact_content_changes(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(tmp_path / "source")
        staged1 = EvolutionModuleManager(workspace).stage(source)

        # Modify content
        (source / "SKILL.md").write_text("# Updated\n", encoding="utf-8")
        staged2 = EvolutionModuleManager(workspace).stage(source)

        assert staged1.artifact_digest != staged2.artifact_digest


class TestCapabilityGateIntegration:
    """Characterization: capability gate builds correct snapshots from verification."""

    def test_gate_rejects_unverified_artifact(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        gate = EvolutionCapabilityGate(workspace)
        result = gate.snapshot_for_artifact("nonexistent-digest")
        assert result.ok is False
        assert result.status == "not_active"

    def test_gate_requires_ledger_verified_event(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(tmp_path / "source")
        staged = EvolutionModuleManager(workspace).stage(source)
        assert staged.ok

        # Verify but do NOT activate (no ledger event recorded)
        report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
        assert report.ok

        # Gate should reject because no MODULE_VERIFIED event in ledger
        gate = EvolutionCapabilityGate(workspace)
        result = gate.snapshot_for_artifact(staged.artifact_digest)
        assert result.ok is False
        assert result.status == "unverified"


class TestCodeSemanticEnforcement:
    """Characterization: AST scanner blocks modules with code-manifest mismatches."""

    def test_import_os_with_exec_false_rejected_by_verifier(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(
            tmp_path / "source",
            permissions={"exec": False, "read_files": True},
        )
        (source / "dangerous.py").write_text("import os\n", encoding="utf-8")
        staged = EvolutionModuleManager(workspace).stage(source)
        assert staged.ok

        report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
        assert report.ok is False
        assert len(report.code_semantic_violations) > 0
        assert any("imports 'os'" in v for v in report.code_semantic_violations)

    def test_import_shutil_with_write_files_false_rejected(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(
            tmp_path / "source",
            permissions={"write_files": False, "read_files": True},
        )
        (source / "file_ops.py").write_text("import shutil\n", encoding="utf-8")
        staged = EvolutionModuleManager(workspace).stage(source)
        assert staged.ok

        report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
        assert report.ok is False
        assert len(report.code_semantic_violations) > 0
        assert any("shutil" in v for v in report.code_semantic_violations)

    def test_clean_module_passes_full_pipeline(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(
            tmp_path / "source",
            permissions={"read_files": True},
        )
        (source / "helpers.py").write_text(
            "from pathlib import Path\n\ndef read(p: Path) -> str:\n    return p.read_text()\n",
            encoding="utf-8",
        )
        staged = EvolutionModuleManager(workspace).stage(source)
        assert staged.ok

        report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
        assert report.ok is True
        assert len(report.code_semantic_violations) == 0
```

- [ ] **Step 2: Run characterization tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evolution/test_activation_pipeline.py -v`

Expected: All 8 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/evolution/test_activation_pipeline.py
git commit -m "test: add characterization tests for evolution activation pipeline

Covers staging→verify→capability gate integration flow:
- Staging produces verifiable artifacts
- Digest changes on content modification
- Gate rejects unverified and unactivated artifacts
- AST scanner blocks code-manifest mismatches (full pipeline)
- Clean modules pass full pipeline end-to-end"
```

---

## Phase 2: RuntimeDependencies Strangler Fig Decomposition (维护)

### Task 5: Introduce sub-container dataclasses

**Files:**
- Modify: `OriginAgent/agent/agent_runtime.py`

**Interfaces:**
- Consumes: existing `Any`-typed fields
- Produces: `CoreServices`, `MetaCognitionServices`, `MemoryServices`, `BackgroundServices`, `StoreServices`, `RuntimeConfig` — typed sub-containers

- [ ] **Step 1: Add sub-container dataclasses before `RuntimeDependencies`**

In `OriginAgent/agent/agent_runtime.py`, insert after line 27 and before `RuntimeDependencies`:

```python
# ── Sub-container dataclasses (Strangler Fig — incremental migration) ──
# Each group bundles related services with real type annotations.
# As call sites are migrated, Any → concrete type.


@dataclass(frozen=True)
class CoreServices:
    """Primary runtime services."""
    tools: Any = None  # TODO(migrate): ToolRegistry
    provider: Any = None  # TODO(migrate): LLMProvider
    runner: Any = None  # TODO(migrate): AgentRunner
    context: Any = None  # TODO(migrate): ContextBuilder
    sessions: Any = None  # TODO(migrate): SessionManager
    bus: Any = None  # TODO(migrate): MessageBus
    workspace: Any = None  # TODO(migrate): Path
    subagents: Any = None


@dataclass(frozen=True)
class MetaCognitionServices:
    """Meta-cognition runtime components."""
    runtime: Any = None  # TODO(migrate): MetaCognitionRuntime
    reflector: Any = None  # TODO(migrate): MetaCognitionReflector
    regulator: Any = None  # TODO(migrate): MetaCognitionRegulator
    config: Any = None
    coordinator: Any = None  # TODO(migrate): MetaCognitionCoordinator
    perception_fusion: Any = None


@dataclass(frozen=True)
class MemoryServices:
    """Session memory and persistence."""
    working_memory: Any = None
    nearline_memory: Any = None
    session_search_index: Any = None
    consolidator: Any = None
    dream: Any = None
    session_cold_archive: Any = None
    rolling_episode_compaction: Any = None
    memory_governance: Any = None
    auto_compact: Any = None
    state_holder: Any = None  # TODO(migrate): SessionStateHolder


@dataclass(frozen=True)
class BackgroundServices:
    """Background processing services."""
    background_review: Any = None
    curator: Any = None
    cognitive_loop: Any = None
    cognitive_scheduler: Any = None
    cognitive_audit: Any = None
    cron_service: Any = None


@dataclass(frozen=True)
class StoreServices:
    """Persistent stores."""
    file_state_store: Any = None
    confirmation_store: Any = None
    confirmation_manager: Any = None
    grant_store: Any = None


@dataclass(frozen=True)
class RuntimeConfig:
    """Scalar runtime configuration (already typed)."""
    model: str | None = None
    max_iterations: int = 10
    context_window_tokens: int = 0
    context_block_limit: int = 0
    max_tool_result_chars: int = 0
    provider_retry_mode: str = "standard"
    tool_hint_max_length: int | None = None
    restrict_to_workspace: bool = False
    unified_session: bool = False
    runtime_profile: str = "default"
    consolidation_ratio: float = 0.5
    max_messages: int = 120
```

- [ ] **Step 2: Add grouped fields to `RuntimeDependencies` with compat aliases**

In `OriginAgent/agent/agent_runtime.py`, add new grouped fields to `RuntimeDependencies` while KEEPING all existing flat fields:

```python
@dataclass(frozen=True)
class RuntimeDependencies:
    """Immutable dependency bundle for AgentRuntime.

    All dependencies are injected at construction time.  AgentRuntime
    never reaches back to AgentLoop — every method receives context
    explicitly.

    Fields are being migrated from flat Any-typed entries into typed
    sub-containers via Strangler Fig pattern.  During migration:
    - New code accesses ``deps.core.tools`` instead of ``deps.tools``
    - Old flat fields remain as compat aliases
    - Sub-containers are populated in AgentLoop.__init__ alongside
      flat fields (same values, two access paths)
    """

    # ── New: grouped sub-containers (migration target) ──────────────
    core: CoreServices = CoreServices()
    meta: MetaCognitionServices = MetaCognitionServices()
    memory: MemoryServices = MemoryServices()
    background: BackgroundServices = BackgroundServices()
    stores: StoreServices = StoreServices()
    config: RuntimeConfig = RuntimeConfig()

    # ── Existing flat fields (keep for backward compat) ────────────
    # Infrastructure
    state_holder: Any = None  # TODO(migrate): → memory.state_holder
    host: Any = None  # AgentHost
    ...
    # (ALL existing 72 fields REMAIN — no deletions in this phase)
```

**CRITICAL:** Do NOT delete any existing flat fields. This is pure addition — the Strangler Fig pattern means old code continues to work via flat fields while new code can use sub-containers.

- [ ] **Step 3: Populate sub-containers in loop.py alongside flat fields**

In `OriginAgent/agent/loop.py`, in the `RuntimeDependencies(...)` constructor call (line 424), add the grouped arguments:

```python
self._runtime = AgentRuntime(RuntimeDependencies(
    # ── New: grouped sub-containers ────────────────────────────
    core=CoreServices(
        tools=self.tools,
        provider=self.provider,
        runner=self.runner,
        context=self.context,
        sessions=self.sessions,
        bus=self.bus,
        workspace=self.workspace,
        subagents=self.subagents,
    ),
    meta=MetaCognitionServices(
        runtime=getattr(self, "_meta_cognition_runtime", None),
        reflector=getattr(self, "_meta_cognition_reflector", None),
        regulator=getattr(self, "_meta_cognition_regulator", None),
        config=getattr(self, "_meta_cognition_config", None),
        coordinator=self._meta_coordinator,
        perception_fusion=getattr(self, "_perception_fusion", None),
    ),
    memory=MemoryServices(
        state_holder=self._state_holder,
        working_memory=self.working_memory,
        nearline_memory=self.nearline_memory,
        session_search_index=self.session_search_index,
        consolidator=self.consolidator,
        dream=self.dream,
        session_cold_archive=self.session_cold_archive,
        rolling_episode_compaction=self.rolling_episode_compaction,
        memory_governance=self.memory_governance,
        auto_compact=self.auto_compact,
    ),
    background=BackgroundServices(
        background_review=self.background_review,
        curator=self.curator,
        cognitive_loop=self.cognitive_loop,
        cognitive_scheduler=self.cognitive_scheduler,
        cognitive_audit=self._cognitive_audit,
        cron_service=self.cron_service,
    ),
    stores=StoreServices(
        file_state_store=self._file_state_store,
        confirmation_store=self._confirmation_store,
        confirmation_manager=self._confirmation_manager,
        grant_store=self._grant_store,
    ),
    config=RuntimeConfig(
        model=self.model,
        max_iterations=self.max_iterations,
        context_window_tokens=self.context_window_tokens,
        context_block_limit=self.context_block_limit,
        max_tool_result_chars=self.max_tool_result_chars,
        provider_retry_mode=self.provider_retry_mode,
        tool_hint_max_length=self.tool_hint_max_length,
        restrict_to_workspace=self.restrict_to_workspace,
        unified_session=self._unified_session,
        runtime_profile=self._runtime_profile,
        consolidation_ratio=0.5,
        max_messages=self._max_messages,
    ),

    # ── Existing flat fields (keep ALL, unchanged) ──────────────
    state_holder=self._state_holder,
    host=self._host,
    tools=self.tools,
    provider=self.provider,
    ...  # ← EVERY existing flat field stays exactly as-is
))
```

- [ ] **Step 4: Verify existing tests still pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_action_runtime.py tests/agent/test_action_safety.py -v --timeout=30`

Expected: All tests PASS (sub-containers are additive, flat fields unchanged).

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
git commit -m "refactor: introduce sub-container dataclasses for RuntimeDependencies

Strangler Fig step 1 — additive only, no deletions.

Add six typed sub-containers (CoreServices, MetaCognitionServices,
MemoryServices, BackgroundServices, StoreServices, RuntimeConfig)
alongside existing flat fields. AgentLoop.__init__ populates both.

This enables incremental migration: future changes to a subsystem's
code can switch from deps.tools → deps.core.tools, gaining type
safety one accessor at a time. Existing flat fields remain as
compat aliases until all call sites are migrated."
```

---

## Phase 3: MetaCognitionFacade (维护)

### Task 6: Create MetaCognitionFacade module and ADR

**Files:**
- Create: `OriginAgent/agent/meta_cognition/__init__.py`
- Create: `docs/adr/2026-07-03-meta-cognition-architecture.md`
- Modify: `OriginAgent/agent/meta_cognition_coordinator.py` (add note referencing facade)

**Interfaces:**
- Consumes: `MetaCognitionRuntime`, `MetaCognitionReflector`, `MetaCognitionRegulator`, `MetaCognitionCoordinator`
- Produces: `MetaCognitionFacade` with `observe()`, `reflect()`, `regulate()`, `start_turn()`, `end_turn()`, `status()` methods

- [ ] **Step 1: Create the facade module**

Create `OriginAgent/agent/meta_cognition/__init__.py`:

```python
"""Meta-cognition subsystem unified facade.

Internal structure (10 files):
  runtime   — trigger collection bounded by turn lifecycle
  reflector — turn-end structured artifact generation
  regulator — online monitoring, depth control, budget scheduling
  coordinator — lifecycle owner (turn start/end, scan scheduling)
  models    — structured contracts (MetaTrigger, ReflectionRecord, etc.)
  patterns  — rule-based consolidation into ErrorPattern
  triggers  — builder functions for standard trigger types
  audit     — append-only sidecar audit ledger
  redact    — shared redaction/trimming helpers
  evolution_bridge — patterns → governed evolution signals

External consumers should ONLY import from this facade.
Internal files may import each other directly.
"""

from __future__ import annotations

from typing import Any

from OriginAgent.agent.meta_cognition_audit import JsonlMetaCognitionAuditLedger
from OriginAgent.agent.meta_cognition_coordinator import MetaCognitionCoordinator
from OriginAgent.agent.meta_cognition_models import MetaTrigger
from OriginAgent.agent.meta_cognition_reflector import MetaCognitionReflector
from OriginAgent.agent.meta_cognition_regulator import MetaCognitionRegulator
from OriginAgent.agent.meta_cognition_runtime import MetaCognitionRuntime


class MetaCognitionFacade:
    """Unified entry point for the meta-cognition subsystem.

    Wraps the 10-file internal decomposition behind a stable API.
    External callers (AgentLoop, AgentRuntime) should depend on this
    facade rather than importing individual meta_cognition_* modules.

    API surface:
      observe()   — record a meta-cognition trigger
      reflect()   — run end-of-turn structured reflection
      regulate()  — check depth/budget and recommend throttle action
      start_turn() / end_turn() — turn lifecycle hooks
      status()    — runtime health and summary
    """

    def __init__(
        self,
        *,
        runtime: MetaCognitionRuntime | None = None,
        reflector: MetaCognitionReflector | None = None,
        regulator: MetaCognitionRegulator | None = None,
        coordinator: MetaCognitionCoordinator | None = None,
        config: Any = None,
    ):
        self._coordinator = coordinator or MetaCognitionCoordinator(
            runtime=runtime,
            reflector=reflector,
            regulator=regulator,
            config=config,
        )

    # ── Turn lifecycle ──────────────────────────────────────────────

    def start_turn(self, turn_id: str) -> None:
        self._coordinator.start_turn(turn_id)

    def end_turn(self, turn_id: str) -> None:
        self._coordinator.end_turn(turn_id)

    # ── Observation ─────────────────────────────────────────────────

    def observe(self, trigger: MetaTrigger, *, turn_id: str | None = None) -> Any:
        """Record a meta-cognition trigger and update internal state."""
        return self._coordinator.record_trigger(trigger, turn_id=turn_id)

    # ── Reflection ──────────────────────────────────────────────────

    def reflect(self, *, turn_id: str | None = None) -> Any:
        """Run end-of-turn structured reflection via the coordinator."""
        return self._coordinator.perform_reflection(turn_id=turn_id)

    # ── Regulation ──────────────────────────────────────────────────

    def regulate(self) -> Any:
        """Check depth/budget and return a throttle recommendation."""
        return self._coordinator.regulate()

    # ── Status ──────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Return runtime health and summary information."""
        return self._coordinator.status()


__all__ = [
    "JsonlMetaCognitionAuditLedger",
    "MetaCognitionCoordinator",
    "MetaCognitionFacade",
    "MetaCognitionReflector",
    "MetaCognitionRegulator",
    "MetaCognitionRuntime",
    "MetaTrigger",
]
```

- [ ] **Step 2: Create ADR document**

Create `docs/adr/2026-07-03-meta-cognition-architecture.md`:

```markdown
# ADR: Meta-Cognition Subsystem Architecture

**Date:** 2026-07-03
**Status:** Accepted
**Author:** System (via expert review remediation)

## Context

The meta-cognition subsystem is decomposed across 10 files
(runtime, reflector, regulator, coordinator, models, patterns,
triggers, audit, redact, evolution_bridge) totaling ~3,354 lines.
An external expert review flagged this as over-fragmentation:
understanding "meta-cognition" requires jumping across many files.

## Decision

Keep the 10-file decomposition but expose a single Facade
(`OriginAgent.agent.meta_cognition.MetaCognitionFacade`) as
the ONLY import target for external consumers.

### Dependency Graph

```
MetaCognitionFacade (public API)
├── MetaCognitionCoordinator (lifecycle owner)
│   ├── MetaCognitionRuntime (trigger collection)
│   ├── MetaCognitionReflector (turn-end artifacts)
│   └── MetaCognitionRegulator (throttle/depth)
├── meta_cognition_triggers (builders)
├── meta_cognition_models (contracts)
├── meta_cognition_patterns (consolidation)
├── meta_cognition_evolution_bridge (evolution signals)
├── meta_cognition_audit (append-only ledger)
└── meta_cognition_redact (shared helpers)
```

External consumers ONLY import `MetaCognitionFacade`.
Internal files may import each other directly.

## Consequences

- **Positive:** New contributors start from one file instead of 10.
  The facade documents what the subsystem DOES, not how it's split.
- **Positive:** Refactoring internal files has zero blast radius on
  external consumers (they only see the facade).
- **Negative:** Facade adds ~60 lines of delegation code.
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/ -k "meta" -v --timeout=30`

Expected: Any existing meta-cognition tests still PASS (facade is additive, no existing imports changed).

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/meta_cognition/__init__.py docs/adr/2026-07-03-meta-cognition-architecture.md
git commit -m "feat: add MetaCognitionFacade as unified entry point for meta-cognition subsystem

Wraps the 10-file meta-cognition decomposition behind a stable public
API. External consumers now import from OriginAgent.agent.meta_cognition
instead of reaching into individual meta_cognition_* modules.

Includes ADR documenting the facade pattern and dependency graph."
```

---

## Verification Checklist

After all phases are complete, run:

```bash
# Full evolution test suite
.\.venv\Scripts\python.exe -m pytest tests/evolution/ -v

# Full agent test suite
.\.venv\Scripts\python.exe -m pytest tests/agent/ -v --timeout=60

# Lint
.\.venv\Scripts\python.exe -m ruff check OriginAgent/evolution/code_scanner.py OriginAgent/evolution/verifier.py OriginAgent/agent/agent_runtime.py
```

Expected: All tests green. No ruff errors. The adversarial tests that were RED in Task 1 are now GREEN.

---

## Follow-up Work (not in this plan)

1. **Runtime enforcement (Layer 2 sandbox):** `sys.addaudithook` + subprocess isolation for evolution modules — see expert recommendation for design sketch
2. **mypy baseline:** Add `mypy` to CI with `--no-error-summary` and a baseline config to block new type errors incrementally
3. **Turn pipeline state-transition tests:** Focused tests for error/timeout recovery paths in AgentRuntime
4. **Complete Strangler Fig migration:** Incrementally move all AgentRuntime methods from `self._deps.tools` to `self._deps.core.tools`, then remove flat fields
