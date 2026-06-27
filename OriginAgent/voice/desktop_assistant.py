"""Desktop Voice Assistant — push-to-talk or continuous voice interaction.

Usage (standalone)::

    python -m OriginAgent.voice.desktop_assistant

The assistant connects to the OriginAgent gateway via WebSocket, captures
microphone input, transcribes via VoicePipeline, and plays TTS responses.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from loguru import logger

from OriginAgent.voice.capture import AudioCapture, HotkeyListener, available
from OriginAgent.voice.pipeline import VoicePipeline
from OriginAgent.voice.stt import VolcengineStreamSTT
from OriginAgent.voice.tts import VolcengineStreamTTS
from OriginAgent.voice.audio import AudioPlayback
from OriginAgent.bus.queue import MessageBus
from OriginAgent.bus.events import InboundMessage


class DesktopVoiceAssistant:
    """Desktop voice assistant using hotkey trigger + VAD.

    Press Ctrl+Shift+V to start a voice interaction. The assistant will:
    1. Listen for your speech (auto-stop on silence)
    2. Transcribe via Volcengine ASR
    3. Print the transcription
    4. Synthesize a response via TTS (mock — connects to agent loop)
    5. Play the response through speakers
    """

    def __init__(self, *, hotkey: str = "<ctrl>+<shift>+v"):
        self._hotkey = hotkey
        self._running = False
        self._pipeline: VoicePipeline | None = None
        self._playback = AudioPlayback()
        self._bus = MessageBus()

    def start(self, blocking: bool = True) -> None:
        """Start the voice assistant."""
        if not available():
            logger.error("Desktop audio capture not available")
            return

        stt = VolcengineStreamSTT()
        if not stt.api_key:
            logger.error(
                "VOLCENGINE_API_KEY not set — voice assistant requires it for STT"
            )
            return

        tts = VolcengineStreamTTS() if stt.api_key else None
        self._pipeline = VoicePipeline(stt=stt, tts=tts, bus=self._bus)

        logger.info("Desktop Voice Assistant ready (hotkey: {})", self._hotkey)
        print(f"\n🎤 Desktop Voice Assistant")
        print(f"   Press {self._hotkey} to speak")
        print(f"   Speak naturally — auto-stops after silence")
        print(f"   Press Ctrl+C to exit\n")

        self._running = True

        def on_trigger():
            asyncio.run(self._handle_voice_turn())

        listener = HotkeyListener(self._hotkey, on_trigger)
        listener.start()

        if blocking:
            try:
                while self._running:
                    time.sleep(0.25)
            except KeyboardInterrupt:
                pass
            finally:
                listener.stop()
                print("\n👋 Voice assistant stopped.")

    async def _handle_voice_turn(self) -> None:
        """Handle one voice turn: capture → STT → print → play."""
        if self._pipeline is None:
            return

        capture = AudioCapture()
        print("\n🎙️  Listening...")
        wav_path = capture.record_utterance()

        if wav_path is None:
            print("   (no speech detected)")
            return

        print(f"   Recorded: {wav_path}")

        # Transcribe
        text = await self._pipeline._stt.transcribe_file(wav_path)

        # Clean up temp file
        try:
            wav_path.unlink()
        except OSError:
            pass

        if not text:
            print("   (transcription empty)")
            return

        print(f"📝 You said: {text}")

        # Synthesize response (echo back for now — in production, goes through agent)
        if self._pipeline._tts:
            response_text = f"你说：{text}"
            tts_path = await self._pipeline._tts.synthesize(
                response_text, voice="zh_female_santong"
            )
            if tts_path and tts_path.exists():
                print(f"🔊 Playing response...")
                self._playback.play(tts_path)
                try:
                    tts_path.unlink()
                except OSError:
                    pass


def main():
    """Entry point for desktop voice assistant."""
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<level>{message}</level>")

    assistant = DesktopVoiceAssistant(hotkey="<ctrl>+<shift>+v")
    assistant.start(blocking=True)


if __name__ == "__main__":
    main()
