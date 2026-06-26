"""Tests for StreamTTS providers."""
import base64
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OriginAgent.voice.tts import VolcengineStreamTTS


class TestVolcengineStreamTTS:
    @pytest.fixture
    def tts(self):
        return VolcengineStreamTTS(api_key="test-volc-key")

    @pytest.fixture
    def tmp_dir(self):
        """Temp dir that works on Windows with asyncio."""
        tmp = tempfile.mkdtemp()
        yield Path(tmp)
        try:
            os.rmdir(tmp)
        except OSError:
            pass

    def test_default_values(self, tts):
        assert tts.api_key == "test-volc-key"
        assert "openspeech.bytedance.com" in tts.api_url
        assert tts.resource_id == "seed-tts-2.0"
        assert tts.sample_rate == 24000

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("VOLCENGINE_API_KEY", "env-tts-key")
        tts = VolcengineStreamTTS()
        assert tts.api_key == "env-tts-key"

    @pytest.mark.asyncio
    async def test_synthesize_happy_path(self, tts, tmp_dir):
        tts.output_dir = tmp_dir
        wav_header = (
            b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
            b"\x01\x00\x01\x00\x40\x1f\x00\x00\x80>\x00\x00"
            b"\x02\x00\x10\x00data\x00\x00\x00\x00"
        )
        audio_b64 = base64.b64encode(wav_header).decode("ascii")
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.json.return_value = {"code": 0, "message": "success", "data": audio_b64}
        resp_mock.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=resp_mock)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tts.synthesize("你好世界", voice="zh_female_santong")
        assert result is not None
        assert result.suffix == ".wav"
        assert result.exists()

    @pytest.mark.asyncio
    async def test_no_api_key_returns_none(self):
        tts = VolcengineStreamTTS(api_key="")
        result = await tts.synthesize("hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_text_returns_none(self, tts):
        result = await tts.synthesize("")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self, tts):
        resp_mock = MagicMock()
        resp_mock.status_code = 400
        resp_mock.json.return_value = {"code": 3000, "message": "invalid parameter"}
        resp_mock.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=resp_mock)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tts.synthesize("test")
        assert result is None
