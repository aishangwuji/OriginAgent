"""Desktop Voice Assistant — push-to-talk via local OriginAgent gateway.

Connects to the running OriginAgent gateway WebSocket, captures microphone
input on hotkey, transcribes via the gateway's pipeline, and plays back
TTS responses automatically.

Usage::

    # Start the gateway first (separate terminal):
    originagent gateway

    # Then run the assistant:
    python -m OriginAgent.voice.desktop_assistant
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

from loguru import logger

from OriginAgent.voice.capture import AudioCapture, available
from OriginAgent.voice.stt import VolcengineStreamSTT
from OriginAgent.voice.audio import AudioPlayback

try:
    import websockets

    _HAS_WEBSOCKETS = True
except ImportError:
    _HAS_WEBSOCKETS = False


class DesktopVoiceAssistant:
    """Desktop voice assistant connecting to the local gateway.

    Connects to ``ws://127.0.0.1:8765/``, creates a chat session, and
    listens for a hotkey. On each press it captures microphone audio,
    transcribes via Volcengine ASR, sends the text to the agent, and
    plays back the TTS response.
    """

    def __init__(self, *, ws_url: str = "ws://127.0.0.1:8765/"):
        self._ws_url = ws_url
        self._ws = None
        self._chat_id: str | None = None
        self._running = False
        self._playback = AudioPlayback()
        self._stt = VolcengineStreamSTT()
        self._pending_tts: list[str] = []  # audio URLs to play

    async def connect(self) -> bool:
        """Connect to the gateway WebSocket and create a new chat."""
        if not _HAS_WEBSOCKETS:
            logger.error("websockets library not installed (pip install websockets)")
            return False

        if not self._stt.api_key:
            logger.error("VOLCENGINE_API_KEY not set")
            return False

        try:
            self._ws = await websockets.connect(self._ws_url, max_size=4 * 1024 * 1024)
            logger.info("Connected to gateway at {}", self._ws_url)

            # Create a new chat
            self._chat_id = f"desktop_{uuid.uuid4().hex[:8]}"
            await self._ws.send(json.dumps({"type": "attach", "chat_id": self._chat_id}))
            logger.info("Created chat session: {}", self._chat_id)
            return True
        except Exception as exc:
            logger.exception("Failed to connect to gateway: {}", exc)
            return False

    async def _listen_loop(self):
        """Background task that listens for incoming WebSocket messages."""
        while self._running and self._ws is not None:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

            try:
                data = json.loads(raw) if isinstance(raw, str) else None
                if data is None:
                    continue

                event = data.get("event")

                if event == "message":
                    text = data.get("text", "")
                    if text:
                        print(f"\n🤖 {text[:200]}")

                elif event == "voice_audio":
                    audio_url = data.get("audio_url", "")
                    if audio_url:
                        self._pending_tts.append(audio_url)

                elif event == "turn_end":
                    pass  # Turn complete

                elif event == "error":
                    detail = data.get("detail") or data.get("message", "")
                    print(f"\n⚠️  Gateway error: {detail}")

            except json.JSONDecodeError:
                continue

    async def _play_tts(self):
        """Background task that plays TTS audio URLs as they arrive."""
        while self._running:
            while self._pending_tts:
                audio_url = self._pending_tts.pop(0)
                # For now, skip audio URL playback in CLI since we need
                # HTTP fetch → save → play. The URL is available for
                # future web-based clients.
                print(f"🔊 TTS audio ready: {audio_url}")
            await asyncio.sleep(0.25)

    async def _handle_voice_turn(self):
        """Record, transcribe, and send a voice message."""
        if self._ws is None or self._chat_id is None:
            print("   (not connected to gateway)")
            return

        capture = AudioCapture()
        print("\n🎙️  Recording... (speak now, auto-stops on silence)")
        wav_path = capture.record_utterance()

        if wav_path is None:
            print("   (no speech detected)")
            return

        # Transcribe
        text = await self._stt.transcribe_file(wav_path)
        try:
            wav_path.unlink()
        except OSError:
            pass

        if not text:
            print("   (transcription empty)")
            return

        print(f"📝 You: {text}")

        # Send as voice_message via WebSocket
        # Read the WAV back and encode as data URL for the voice_message envelope
        # (The gateway handles auto-TTS of the response)
        await self._ws.send(json.dumps({
            "type": "voice_message",
            "chat_id": self._chat_id,
            "audio_data_url": "",  # No audio data URL — send text directly
            "text": text,
            "desktop": True,
        }))
        # Also send as regular text message as fallback
        await self._ws.send(json.dumps({
            "type": "message",
            "chat_id": self._chat_id,
            "content": text,
            "desktop": True,
        }))
        print("⏳ Waiting for response...")

    async def run(self):
        """Run the voice assistant (hotkey loop)."""
        if not available() and not self._stt.api_key:
            logger.error("Desktop audio capture unavailable and STT not configured")
            return

        connected = await self.connect()
        if not connected:
            return

        self._running = True

        # Start listener tasks
        listener_task = asyncio.create_task(self._listen_loop())
        tts_task = asyncio.create_task(self._play_tts())

        print(f"\n🎤 Desktop Voice Assistant")
        print(f"   Connected to {self._ws_url}")
        print(f"   Chat ID: {self._chat_id}")
        print(f"   Press Enter to speak (or type 'q' to quit)")
        print()

        try:
            while self._running:
                line = input("> ").strip().lower()
                if line == "q" or line == "quit" or line == "exit":
                    break
                if line:  # typed text — send directly
                    await self._ws.send(json.dumps({
                        "type": "message",
                        "chat_id": self._chat_id,
                        "content": line,
                    }))
                    print("⏳ Waiting for response...")
                else:  # Enter pressed — voice capture
                    await self._handle_voice_turn()
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            self._running = False
            listener_task.cancel()
            tts_task.cancel()
            if self._ws:
                await self._ws.close()
            print("\n👋 Voice assistant stopped.")


def main():
    """Entry point for desktop voice assistant."""
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<level>{message}</level>")

    assistant = DesktopVoiceAssistant()
    asyncio.run(assistant.run())


if __name__ == "__main__":
    main()
