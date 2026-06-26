"""Tests for StreamSTT providers."""
import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OriginAgent.voice.stt import VolcengineStreamSTT


class TestVolcengineStreamSTT:
    @pytest.fixture
    def stt(self):
        return VolcengineStreamSTT(api_key="test-volc-key")

    @pytest.fixture
    def audio_file(self):
        """Create a temp WAV file that works cross-platform."""
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(b"fake WAV data for testing")
        tmp.close()
        yield Path(tmp.name)
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    def test_default_values(self, stt):
        assert stt.api_key == "test-volc-key"
        assert "openspeech.bytedance.com" in stt.api_url
        assert stt.resource_id == "volc.bigasr.auc_turbo"
        assert stt.model_name == "bigmodel"

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("VOLCENGINE_API_KEY", "env-key")
        stt = VolcengineStreamSTT()
        assert stt.api_key == "env-key"

    def test_no_api_key_returns_empty(self):
        stt = VolcengineStreamSTT(api_key="")

        async def _test():
            result = await stt.transcribe_file(Path("/tmp/fake.wav"))
            assert result == ""

        asyncio.run(_test())

    @pytest.mark.asyncio
    async def test_transcribe_happy_path_mock(self, stt, audio_file):
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.headers = {"X-Api-Status-Code": "20000000"}
        resp_mock.json.return_value = {"result": {"text": "你好世界"}}
        resp_mock.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=resp_mock)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await stt.transcribe_file(audio_file)
            assert "你好" in result

    @pytest.mark.asyncio
    async def test_retry_on_502(self, stt, audio_file):
        fail_resp = MagicMock()
        fail_resp.status_code = 502
        fail_resp.headers = {}
        fail_resp.raise_for_status.side_effect = Exception("502")
        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.headers = {"X-Api-Status-Code": "20000000"}
        success_resp.json.return_value = {"result": {"text": "retry success"}}
        success_resp.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[fail_resp, success_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await stt.transcribe_file(audio_file)
            assert result == "retry success"

    @pytest.mark.asyncio
    async def test_missing_file_returns_empty(self, stt):
        result = await stt.transcribe_file(Path("/nonexistent/missing.wav"))
        assert result == ""
