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
