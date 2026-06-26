"""VoicePipeline — orchestrates STT → Agent → TTS flow."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from OriginAgent.bus.events import InboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.voice.stt import StreamSTT
from OriginAgent.voice.tts import StreamTTS


@dataclass
class VoiceResult:
    """Result of a voice processing turn."""
    transcribed_text: str = ""
    tts_audio_path: Path | None = None
    audio_path: Path | None = None
    status: str = "transcribed"  # "transcribed" | "ok" | "error"


class VoicePipeline:
    """Orchestrates voice message flow: STT → Agent → TTS.

    Usage::

        pipeline = VoicePipeline(
            stt=VolcengineStreamSTT(),
            tts=VolcengineStreamTTS(),
            bus=bus,
        )
        result = await pipeline.process_voice_message(
            audio_path, chat_id, "websocket",
        )
        # result.transcribed_text → agent processes via the bus.
        # After agent responds:
        tts_audio = await pipeline.synthesize_response(reply_text)
    """

    def __init__(
        self,
        *,
        stt: StreamSTT,
        tts: StreamTTS | None,
        bus: MessageBus,
    ):
        self._stt = stt
        self._tts = tts
        self._bus = bus

    async def process_voice_message(
        self,
        audio_path: str | Path,
        chat_id: str,
        channel_name: str,
        sender_id: str = "user",
        metadata: dict | None = None,
    ) -> VoiceResult:
        """Transcribe audio and publish as InboundMessage.

        Returns VoiceResult with transcription. The agent loop picks up
        the published message and responds normally.
        """
        path = Path(audio_path)
        if not path.exists():
            logger.error(
                "VoicePipeline: audio file not found: {}", audio_path
            )
            return VoiceResult(audio_path=path, status="error")

        text = await self._stt.transcribe_file(path)
        if not text:
            logger.warning(
                "VoicePipeline: STT returned empty text for {}", audio_path
            )
            return VoiceResult(audio_path=path, status="error")

        meta = dict(metadata or {})
        meta["_voice_message"] = True
        meta["_audio_source"] = str(audio_path)

        msg = InboundMessage(
            channel=channel_name,
            sender_id=sender_id,
            chat_id=chat_id,
            content=text,
            media=[],
            metadata=meta,
        )
        await self._bus.publish_inbound(msg)

        logger.info(
            "VoicePipeline: transcribed {} → '{}...' via {}",
            audio_path, text[:80], channel_name,
        )
        return VoiceResult(
            transcribed_text=text,
            audio_path=path,
            status="transcribed",
        )

    async def synthesize_response(
        self,
        text: str,
        voice: str | None = None,
    ) -> Path | None:
        """Synthesize text to speech. Returns audio file path or None."""
        if self._tts is None:
            return None
        if not text or not text.strip():
            return None
        return await self._tts.synthesize(text, voice=voice)
