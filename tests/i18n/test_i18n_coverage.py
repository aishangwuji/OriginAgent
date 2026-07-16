"""Tests for i18n coverage of user-visible strings (Task B6).

Verifies that:
1. en.json and zh.json contain the new channel.*/cli.*/api.*/error.* keys
2. msteams.py uses t() instead of hardcoded mention response
3. api/server.py uses t() instead of hardcoded Chinese fallback
4. cli/stream.py uses t() instead of hardcoded "is thinking..."
5. BaseChannel._t helper resolves keys via the i18n system
"""

from __future__ import annotations

from pathlib import Path

import pytest

from OriginAgent.channels.base import BaseChannel
from OriginAgent.i18n import clear_cache, t

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHANNELS = _REPO_ROOT / "OriginAgent" / "channels"
_API = _REPO_ROOT / "OriginAgent" / "api"
_CLI = _REPO_ROOT / "OriginAgent" / "cli"

# Keys that must exist in both en.json and zh.json after B6.1.
REQUIRED_KEYS = [
    "channel.msteams.mention_only_response",
    "channel.msteams.auth_disabled_warning",
    "channel.msteams.auth_required_error",
    "cli.thinking",
    "cli.bot_name",
    "cli.onboard.static_models_notice",
    "cli.onboard.overwrite_prompt",
    "cli.onboard.overwrite_cancelled",
    "api.analyze_uploaded_file",
    "error.allow_from_empty",
    "error.channel.startup_failed",
]


@pytest.mark.parametrize("key", REQUIRED_KEYS)
@pytest.mark.parametrize("lang", ["en", "zh"])
def test_i18n_keys_exist_in_both_packs(key: str, lang: str) -> None:
    """Each required key must resolve to a real string (not the key itself)."""
    clear_cache()
    result = t(key, lang=lang)
    assert result != key, f"key {key!r} missing in {lang}.json (returned key itself)"
    assert isinstance(result, str) and result.strip(), (
        f"key {key!r} resolved to empty string in {lang}.json"
    )


def test_msteams_uses_i18n_for_mention_response() -> None:
    """msteams.py must source mention_only_response default from i18n, not hardcode."""
    src = (_CHANNELS / "msteams.py").read_text(encoding="utf-8")
    assert "from OriginAgent.i18n import t" in src, "msteams.py must import t from i18n"
    assert 't("channel.msteams.mention_only_response")' in src, (
        "msteams.py must call t('channel.msteams.mention_only_response')"
    )
    # The hardcoded English literal must no longer be the field default.
    assert 'mention_only_response: str = "Hi' not in src, (
        "msteams.py still hardcodes the English mention response"
    )


def test_api_server_uses_i18n_for_uploaded_file_prompt() -> None:
    """api/server.py must source the uploaded-file prompt from i18n."""
    src = (_API / "server.py").read_text(encoding="utf-8")
    assert "from OriginAgent.i18n import t" in src, (
        "api/server.py must import t from i18n"
    )
    assert 't("api.analyze_uploaded_file")' in src, (
        "api/server.py must call t('api.analyze_uploaded_file')"
    )
    assert '"请分析上传的文件"' not in src, (
        "api/server.py still hardcodes the Chinese uploaded-file prompt"
    )


def test_cli_stream_uses_i18n_for_thinking() -> None:
    """cli/stream.py must source the 'is thinking...' text from i18n."""
    src = (_CLI / "stream.py").read_text(encoding="utf-8")
    assert "from OriginAgent.i18n import t" in src, "cli/stream.py must import t from i18n"
    # Accept either quote style: f-strings nest single quotes inside double quotes.
    assert "t('cli.thinking')" in src or 't("cli.thinking")' in src, (
        "cli/stream.py must call t('cli.thinking')"
    )
    # The hardcoded spinner literal (with Rich dim closing tag) must be gone.
    assert "is thinking...[/dim]" not in src, (
        "cli/stream.py still hardcodes 'is thinking...' in the spinner"
    )


def test_base_channel_t_helper_resolves_keys() -> None:
    """BaseChannel._t must resolve i18n keys like the module-level t()."""

    class _DummyChannel(BaseChannel):
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        async def send(self, msg) -> None:  # type: ignore[override]
            return None

    ch = _DummyChannel(config={}, bus=None)  # type: ignore[arg-type]

    # _t should return the same value as the module-level t() for a known key.
    clear_cache()
    expected = t("cli.thinking", lang="en")
    assert ch._t("cli.thinking", lang="en") == expected

    # _t should also accept format kwargs and render placeholders.
    clear_cache()
    rendered = ch._t(
        "error.channel.startup_failed", lang="en", name="msteams", error="boom"
    )
    assert "msteams" in rendered and "boom" in rendered
