"""Voice channel — handles voice messages for all channels."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from OriginAgent.bus.events import OutboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.channels.base import BaseChannel


class VoiceChannel(BaseChannel):
    """Voice-dedicated channel for STT/TTS processing.

    Works alongside existing channels (WebSocket, Telegram, etc.) to
    provide voice message handling. Other channels delegate voice
    processing to VoiceChannel via :meth:`handle_voice_message`.
    """

    name = "voice"
    display_name = "Voice"

    def __init__(self, config: Any, bus: MessageBus):
        super().__init__(config, bus)
        self._pipeline = None

    def set_pipeline(self, pipeline) -> None:
        """Inject the VoicePipeline instance (set by ChannelManager)."""
        self._pipeline = pipeline

    async def start(self) -> None:
        self._running = True
        self.logger.info("VoiceChannel started")

    async def stop(self) -> None:
        self._running = False
        self.logger.info("VoiceChannel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        self.logger.debug(
            "VoiceChannel send (noop for {}): {}",
            msg.chat_id, msg.content[:50],
        )

    async def handle_voice_message(
        self,
        chat_id: str,
        audio_path: str | Path,
        sender_id: str = "user",
        metadata: dict | None = None,
    ):
        """Transcribe voice message and publish to agent bus.

        Called by other channels when they receive an audio attachment.
        Returns VoiceResult for the caller.
        """
        if self._pipeline is None:
            self.logger.warning("VoiceChannel: no pipeline configured")
            from OriginAgent.voice.pipeline import VoiceResult
            return VoiceResult(status="error")

        return await self._pipeline.process_voice_message(
            audio_path=audio_path,
            chat_id=chat_id,
            channel_name="voice",
            sender_id=sender_id,
            metadata=metadata,
        )

    async def synthesize_response(
        self,
        text: str,
        voice: str | None = None,
    ) -> Path | None:
        """Synthesize agent response as speech audio."""
        if self._pipeline is None:
            return None
        return await self._pipeline.synthesize_response(text, voice=voice)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return {"enabled": False}
