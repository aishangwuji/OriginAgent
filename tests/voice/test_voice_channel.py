"""Tests for VoiceChannel."""
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from OriginAgent.channels.voice import VoiceChannel
from OriginAgent.bus.queue import MessageBus


class TestVoiceChannel:
    @pytest.fixture
    def bus(self):
        return MagicMock(spec=MessageBus)

    @pytest.fixture
    def channel(self, bus):
        return VoiceChannel(config={"enabled": True}, bus=bus)

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

    def test_name_and_display(self, channel):
        assert channel.name == "voice"
        assert channel.display_name == "Voice"

    def test_default_config(self):
        cfg = VoiceChannel.default_config()
        assert cfg == {"enabled": False}

    @pytest.mark.asyncio
    async def test_start_stop(self, channel):
        await channel.start()
        assert channel.is_running
        await channel.stop()
        assert not channel.is_running

    @pytest.mark.asyncio
    async def test_send_is_noop(self, channel):
        from OriginAgent.bus.events import OutboundMessage
        msg = OutboundMessage(channel="voice", chat_id="x", content="hello")
        await channel.send(msg)

    @pytest.mark.asyncio
    async def test_handle_voice_message_uses_pipeline(self, channel, audio_file):
        fake_stt = MagicMock()
        fake_stt.transcribe_file = AsyncMock(return_value="transcribed text")

        from OriginAgent.voice.pipeline import VoicePipeline
        pipeline = VoicePipeline(stt=fake_stt, tts=None, bus=channel.bus)
        channel.set_pipeline(pipeline)

        result = await channel.handle_voice_message("test-chat", audio_file)
        assert result.transcribed_text == "transcribed text"
        fake_stt.transcribe_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_pipeline_returns_error(self, channel, audio_file):
        result = await channel.handle_voice_message("test-chat", audio_file)
        assert result.status == "error"
