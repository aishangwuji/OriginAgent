"""End-to-end voice pipeline tests against real Volcengine API.

These tests require a real VOLCENGINE_API_KEY environment variable and network
access. They are skipped automatically when the key is absent, so the test
suite remains green in CI without credentials.

Run explicitly:
    VOLCENGINE_API_KEY=xxx .venv\\Scripts\\python.exe -m pytest tests/voice/test_voice_e2e.py -v
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

from OriginAgent.voice.stt import VolcengineStreamSTT
from OriginAgent.voice.tts import VolcengineStreamTTS


def _volcengine_key() -> str | None:
    """Return the Volcengine API key from the environment, or None if absent."""
    key = os.environ.get("VOLCENGINE_API_KEY", "").strip()
    return key or None


@pytest.fixture
def skip_without_key() -> str:
    """Skip the test when VOLCENGINE_API_KEY is not set in the environment."""
    key = _volcengine_key()
    if not key:
        pytest.skip("VOLCENGINE_API_KEY not set in environment; skipping E2E test")
    return key


@pytest.fixture
def silence_wav(tmp_path: Path) -> Path:
    """Create a minimal 1-second silent WAV file (16kHz, mono, 16-bit PCM)."""
    sample_rate = 16000
    duration_sec = 1
    num_samples = sample_rate * duration_sec
    wav_data = bytearray()
    # RIFF header
    wav_data.extend(b"RIFF")
    wav_data.extend((36 + num_samples * 2).to_bytes(4, "little"))
    wav_data.extend(b"WAVE")
    # fmt chunk
    wav_data.extend(b"fmt ")
    wav_data.extend((16).to_bytes(4, "little"))
    wav_data.extend((1).to_bytes(2, "little"))  # PCM
    wav_data.extend((1).to_bytes(2, "little"))  # mono
    wav_data.extend(sample_rate.to_bytes(4, "little"))
    wav_data.extend((sample_rate * 2).to_bytes(4, "little"))  # byte rate
    wav_data.extend((2).to_bytes(2, "little"))  # block align
    wav_data.extend((16).to_bytes(2, "little"))  # bits per sample
    # data chunk
    wav_data.extend(b"data")
    wav_data.extend((num_samples * 2).to_bytes(4, "little"))
    # silence samples
    for _ in range(num_samples):
        wav_data.extend((0).to_bytes(2, "little", signed=True))

    wav_file = tmp_path / f"silence_{uuid.uuid4().hex[:8]}.wav"
    wav_file.write_bytes(bytes(wav_data))
    return wav_file


@pytest.mark.asyncio
async def test_stt_api_round_trip(skip_without_key: str, silence_wav: Path) -> None:
    """STT: a real API call against Volcengine ASR returns without raising.

    A silent WAV is expected to produce empty or whitespace-only text; the
    goal is to verify the API contract, not transcription accuracy.
    """
    stt = VolcengineStreamSTT()
    assert stt.api_key, "api_key must be populated from environment"

    result = await stt.transcribe_file(silence_wav)
    # Empty/whitespace is acceptable for a silence file; we only assert the
    # call completed and returned a string (possibly empty).
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_tts_api_round_trip(skip_without_key: str, tmp_path: Path) -> None:
    """TTS: a real API call against Volcengine Seed-TTS produces a WAV file."""
    tts = VolcengineStreamTTS(output_dir=str(tmp_path))
    assert tts.api_key, "api_key must be populated from environment"

    test_text = "你好,我是 OriginAgent,语音功能已成功启用。"
    result = await tts.synthesize(test_text, voice="zh_female_santong")

    assert result is not None, "TTS must return a path when API succeeds"
    assert result.exists(), f"TTS output file must exist: {result}"
    assert result.stat().st_size > 0, "TTS output file must not be empty"
