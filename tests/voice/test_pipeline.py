"""Tests for VoicePipeline orchestrator."""
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from OriginAgent.voice.pipeline import VoicePipeline, VoiceResult
from OriginAgent.voice.stt import StreamSTT
from OriginAgent.voice.tts import StreamTTS


class FakeSTT(StreamSTT):
    def __init__(self, canned_text: str = "测试语音输入"):
        super().__init__(api_key="fake")
        self.canned_text = canned_text
        self.calls: list[Path] = []

    async def transcribe_file(self, file_path):
        self.calls.append(Path(file_path))
        return self.canned_text


class FakeTTS(StreamTTS):
    def __init__(self):
        super().__init__(api_key="fake")
        self.calls: list[tuple[str, str | None]] = []

    async def synthesize(self, text, voice=None):
        self.calls.append((text, voice))
        return Path("/tmp/fake_output.wav")


class TestVoicePipeline:
    @pytest.fixture
    def bus(self):
        mock = MagicMock()
        mock.publish_inbound = AsyncMock()
        return mock

    @pytest.fixture
    def pipeline(self, bus):
        return VoicePipeline(stt=FakeSTT(), tts=FakeTTS(), bus=bus)

    @pytest.fixture
    def audio_file(self):
        """Temp WAV file that works on Windows with asyncio."""
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(b"fake audio")
        tmp.close()
        yield Path(tmp.name)
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    @pytest.mark.asyncio
    async def test_process_voice_message_transcribes(self, pipeline, audio_file):
        result = await pipeline.process_voice_message(
            audio_path=audio_file, chat_id="chat-1", channel_name="websocket",
        )
        assert result.transcribed_text == "测试语音输入"
        assert result.status == "transcribed"

    @pytest.mark.asyncio
    async def test_process_voice_message_missing_file(self, pipeline):
        result = await pipeline.process_voice_message(
            audio_path=Path("/nonexistent.wav"), chat_id="x", channel_name="w",
        )
        assert result.transcribed_text == ""
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_process_voice_message_publishes_to_bus(self, pipeline, audio_file, bus):
        await pipeline.process_voice_message(
            audio_path=audio_file, chat_id="chat-2", channel_name="websocket",
        )
        bus.publish_inbound.assert_called_once()
        msg = bus.publish_inbound.call_args[0][0]
        assert msg.content == "测试语音输入"
        assert msg.chat_id == "chat-2"
        assert msg.metadata["_voice_message"] is True

    @pytest.mark.asyncio
    async def test_synthesize_response_happy_path(self, pipeline):
        result = await pipeline.synthesize_response("你好", voice="test_voice")
        assert result == Path("/tmp/fake_output.wav")

    @pytest.mark.asyncio
    async def test_synthesize_response_no_tts(self, bus):
        pipeline = VoicePipeline(stt=FakeSTT(), tts=None, bus=bus)
        result = await pipeline.synthesize_response("hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_synthesize_response_empty_text(self, pipeline):
        result = await pipeline.synthesize_response("")
        assert result is None


class TestVoiceResult:
    def test_voice_result_defaults(self):
        result = VoiceResult()
        assert result.transcribed_text == ""
        assert result.tts_audio_path is None
        assert result.status == "transcribed"

    def test_voice_result_full(self):
        result = VoiceResult(
            transcribed_text="hello",
            tts_audio_path=Path("/tmp/out.wav"),
            audio_path=Path("/tmp/in.wav"),
            status="ok",
        )
        assert result.status == "ok"
        assert result.tts_audio_path == Path("/tmp/out.wav")
