"""Tests for exec tool internal URL blocking."""

from __future__ import annotations

import socket
import sys
from unittest.mock import patch

import pytest

from OpenHome.agent.tools.shell import ExecTool
from OpenHome.agent.tools.sandbox import build_bwrap_argv


def _fake_resolve_private(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))]


def _fake_resolve_localhost(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]


def _fake_resolve_public(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


@pytest.mark.asyncio
async def test_exec_blocks_curl_metadata():
    tool = ExecTool()
    with patch("OpenHome.security.network.socket.getaddrinfo", _fake_resolve_private):
        result = await tool.execute(
            command='curl -s -H "Metadata-Flavor: Google" http://169.254.169.254/computeMetadata/v1/'
        )
    assert "Error" in result
    assert "internal" in result.lower() or "private" in result.lower()


@pytest.mark.asyncio
async def test_exec_blocks_wget_localhost():
    tool = ExecTool()
    with patch("OpenHome.security.network.socket.getaddrinfo", _fake_resolve_localhost):
        result = await tool.execute(command="wget http://localhost:8080/secret -O /tmp/out")
    assert "Error" in result


@pytest.mark.asyncio
async def test_exec_allows_normal_commands():
    tool = ExecTool(timeout=5)
    result = await tool.execute(command="echo hello")
    assert "hello" in result
    assert "Error" not in result.split("\n")[0]


@pytest.mark.asyncio
async def test_exec_allows_curl_to_public_url():
    """Commands with public URLs should not be blocked by the internal URL check."""
    tool = ExecTool()
    with patch("OpenHome.security.network.socket.getaddrinfo", _fake_resolve_public):
        guard_result = tool._guard_command("curl https://example.com/api", "/tmp")
    assert guard_result is None


@pytest.mark.asyncio
async def test_exec_blocks_chained_internal_url():
    """Internal URLs buried in chained commands should still be caught."""
    tool = ExecTool()
    with patch("OpenHome.security.network.socket.getaddrinfo", _fake_resolve_private):
        result = await tool.execute(
            command="echo start && curl http://169.254.169.254/latest/meta-data/ && echo done"
        )
    assert "Error" in result


# --- #2989: block writes to OpenHome internal state files -----------------


@pytest.mark.parametrize(
    "command",
    [
        "cat foo >> history.jsonl",
        "echo '{}' > history.jsonl",
        "echo '{}' > memory/history.jsonl",
        "echo '{}' > ./workspace/memory/history.jsonl",
        "tee -a history.jsonl < foo",
        "tee history.jsonl",
        "cp /tmp/fake.jsonl history.jsonl",
        "mv backup.jsonl memory/history.jsonl",
        "dd if=/dev/zero of=memory/history.jsonl",
        "sed -i 's/old/new/' history.jsonl",
        "echo x > .dream_cursor",
        "cp /tmp/x memory/.dream_cursor",
    ],
)
def test_exec_blocks_writes_to_history_jsonl(command):
    """Direct writes to history.jsonl / .dream_cursor must be blocked (#2989)."""
    tool = ExecTool()
    result = tool._guard_command(command, "/tmp")
    assert result is not None
    assert "deny pattern filter" in result.lower()


@pytest.mark.parametrize(
    "command",
    [
        "cat history.jsonl",
        "wc -l history.jsonl",
        "tail -n 5 history.jsonl",
        "grep foo history.jsonl",
        "cp history.jsonl /tmp/history.backup",
        "ls memory/",
        "echo history.jsonl",
    ],
)
def test_exec_allows_reads_of_history_jsonl(command):
    """Read-only access to history.jsonl must still be allowed."""
    tool = ExecTool()
    result = tool._guard_command(command, "/tmp")
    assert result is None


# --- #2826: working_dir must not escape the configured workspace ---------


@pytest.mark.asyncio
async def test_exec_blocks_working_dir_outside_workspace(tmp_path):
    """An LLM-supplied working_dir outside the workspace must be rejected."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True, sandbox="")
    result = await tool.execute(command="rm calendar.ics", working_dir="/etc")
    assert "outside the configured workspace" in result


@pytest.mark.asyncio
async def test_exec_blocks_absolute_rm_via_hijacked_working_dir(tmp_path):
    """Regression for #2826: `rm /abs/path` via working_dir hijack."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    victim_dir = tmp_path / "outside"
    victim_dir.mkdir()
    victim = victim_dir / "file.ics"
    victim.write_text("data")

    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True, sandbox="")
    result = await tool.execute(
        command=f"rm {victim}",
        working_dir=str(victim_dir),
    )
    assert "outside the configured workspace" in result
    assert victim.exists(), "victim file must not have been deleted"


@pytest.mark.asyncio
async def test_exec_allows_working_dir_within_workspace(tmp_path):
    """A working_dir that is a subdirectory of the workspace is fine."""
    workspace = tmp_path / "workspace"
    subdir = workspace / "project"
    subdir.mkdir(parents=True)
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True, timeout=5, sandbox="bwrap")
    with patch("OpenHome.agent.tools.shell._IS_WINDOWS", False):
        with patch("OpenHome.agent.tools.shell.shutil.which", lambda name: "/usr/bin/bwrap"):
            with patch.object(tool, "_spawn") as spawn:
                class FakeProcess:
                    returncode = 0
                    async def communicate(self):
                        return b"ok\n", b""
                spawn.return_value = FakeProcess()
                result = await tool.execute(command="echo ok", working_dir=str(subdir))
    assert "ok" in result
    assert "outside the configured workspace" not in result


@pytest.mark.asyncio
async def test_exec_allows_working_dir_equal_to_workspace(tmp_path):
    """Passing working_dir equal to the workspace root must be allowed."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True, timeout=5, sandbox="bwrap")
    with patch("OpenHome.agent.tools.shell._IS_WINDOWS", False):
        with patch("OpenHome.agent.tools.shell.shutil.which", lambda name: "/usr/bin/bwrap"):
            with patch.object(tool, "_spawn") as spawn:
                class FakeProcess:
                    returncode = 0
                    async def communicate(self):
                        return b"ok\n", b""
                spawn.return_value = FakeProcess()
                result = await tool.execute(command="echo ok", working_dir=str(workspace))
    assert "ok" in result
    assert "outside the configured workspace" not in result


@pytest.mark.asyncio
async def test_workspace_exec_without_restrict_to_workspace_fails_closed(tmp_path):
    """Workspace exec must not claim protected path safety without sandbox restriction."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=False, timeout=5)
    result = await tool.execute(command="echo ok", working_dir=str(other))
    assert (
        "requires restrict_to_workspace with a supported sandbox" in result
        or "outside the configured workspace" in result
    )


# --- #3599: stdio redirects to /dev/null must not trip the workspace guard ----


@pytest.mark.parametrize(
    "command",
    [
        # The exact command from the #3599 reporter.
        'rm test_print.txt 2>/dev/null; echo "done"',
        # Plain redirect of stdout / stderr.
        "find . -type f >/dev/null",
        "noisy_cmd 2>/dev/null",
        "noisy_cmd >/dev/null 2>&1",
        # Read from /dev/urandom is also a benign device read.
        "head -c 16 /dev/urandom | xxd",
        "echo done >/dev/stderr",
        "echo line </dev/stdin",
        # Per-process FD aliases never escape the workspace.
        "cat /dev/fd/3",
    ],
)
def test_exec_allows_benign_device_targets_inside_workspace(tmp_path, command):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True, sandbox="")
    assert tool._guard_command(command, str(workspace)) is None


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX rm and /dev/null syntax")
async def test_exec_3599_regression_rm_with_dev_null_redirect(tmp_path):
    """#3599: ``rm <ws-path> 2>/dev/null`` must succeed against the workspace guard."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "test_print.txt"
    target.write_text("scratch")
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True, timeout=5, sandbox="bwrap")
    with patch("OpenHome.agent.tools.shell._IS_WINDOWS", False):
        with patch("OpenHome.agent.tools.shell.shutil.which", lambda name: "/usr/bin/bwrap"):
            with patch.object(tool, "_spawn") as spawn:
                class FakeProcess:
                    returncode = 0
                    async def communicate(self):
                        return b"done\n", b""
                spawn.return_value = FakeProcess()
                result = await tool.execute(
                    command=f'rm {target} 2>/dev/null; echo "done"',
                    working_dir=str(workspace),
                )
    assert "done" in result
    assert "path outside working dir" not in result
    assert target.exists()


def test_exec_still_blocks_real_outside_path_via_redirect(tmp_path):
    """Redirect *targets* outside the workspace (not /dev/...) must still be blocked.

    We only whitelist kernel device files; arbitrary outside redirects such as
    ``> /etc/issue`` should remain caught by the workspace guard so a buggy
    LLM cannot exfiltrate data outside the workspace via stderr redirection.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True, sandbox="")
    blocked = tool._guard_command("echo pwn > /etc/issue", str(workspace))
    assert blocked is not None
    assert "path outside working dir" in blocked


@pytest.mark.asyncio
async def test_exec_restrict_to_workspace_requires_sandbox(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True, timeout=5)

    result = await tool.execute(command="echo ok", working_dir=str(workspace))

    assert "requires a supported sandbox" in result


def test_bwrap_profile_contains_network_off_and_ro_media(tmp_path, monkeypatch):
    from OpenHome.config import paths as config_paths
    from OpenHome.security import paths as security_paths
    from OpenHome.agent.tools import sandbox as sandbox_mod

    workspace = tmp_path / "workspace"
    media = tmp_path / "media"
    workspace.mkdir()
    media.mkdir()
    monkeypatch.setattr(config_paths, "get_media_dir", lambda channel=None: media)
    monkeypatch.setattr(security_paths, "get_media_dir", lambda channel=None: media)
    monkeypatch.setattr(sandbox_mod, "get_media_dir", lambda channel=None: media)

    argv = build_bwrap_argv("echo ok", str(workspace), str(workspace))

    assert "--unshare-net" in argv
    assert _has_pair(argv, "--bind", str(workspace), str(workspace))
    assert _has_pair(argv, "--ro-bind-try", str(media), str(media))


def _has_pair(argv, flag, source, dest):
    for idx, item in enumerate(argv):
        if item == flag and argv[idx + 1:idx + 3] == [source, dest]:
            return True
    return False
