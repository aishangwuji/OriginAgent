"""Streaming Text-to-Speech providers."""
from __future__ import annotations

import base64
import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

import httpx
from loguru import logger


class StreamTTS(ABC):
    """Abstract base for text-to-speech providers."""

    def __init__(self, *, api_key: str | None = None):
        self.api_key = api_key

    @abstractmethod
    async def synthesize(self, text: str, voice: str | None = None) -> Path | None:
        """Synthesize text to audio file. Returns path or None on failure."""
        ...


class VolcengineStreamTTS(StreamTTS):
    """Volcengine Seed-TTS 2.0 — unidirectional text-to-speech."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        resource_id: str | None = None,
        api_base: str | None = None,
        sample_rate: int = 24000,
        output_dir: str | None = None,
    ):
        self.api_key = (api_key or os.environ.get("VOLCENGINE_API_KEY") or "").strip()
        self.resource_id = (
            resource_id
            or os.environ.get("VOLCENGINE_TTS_RESOURCE_ID")
            or "seed-tts-2.0"
        ).strip() or "seed-tts-2.0"
        self.api_url = (
            api_base
            or os.environ.get("VOLCENGINE_TTS_BASE_URL")
            or "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
        ).strip()
        self.sample_rate = sample_rate
        self.output_dir = Path(
            output_dir
            or os.environ.get("ORIGINAGENT_TTS_OUTPUT_DIR")
            or Path.cwd() / "uploads" / "perception"
        )
        super().__init__(api_key=self.api_key)

    async def synthesize(self, text: str, voice: str | None = None) -> Path | None:
        if not self.api_key:
            logger.warning("Volcengine TTS: API key not configured")
            return None

        text = text.strip()
        if not text:
            return None

        speaker = (voice or "zh_female_santong").strip()
        request_id = str(uuid.uuid4())
        payload = {
            "req_params": {"text": text},
            "speaker": speaker,
            "audio_params": {
                "format": "wav",
                "sample_rate": self.sample_rate,
            },
        }
        headers = {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": request_id,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.api_url, headers=headers, json=payload, timeout=120.0
                )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:
            logger.exception("Volcengine TTS request failed: {}", exc)
            return None

        if not isinstance(body, dict):
            logger.error("Volcengine TTS: unexpected response shape")
            return None

        if int(body.get("code") or 0) != 0:
            logger.error(
                "Volcengine TTS error: {}", body.get("message") or "unknown"
            )
            return None

        audio_b64 = body.get("data")
        if not isinstance(audio_b64, str) or not audio_b64:
            logger.error("Volcengine TTS: no audio data in response")
            return None

        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception as exc:
            logger.exception("Volcengine TTS base64 decode failed: {}", exc)
            return None

        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = (
            f"tts_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
            f"_{uuid.uuid4().hex[:8]}.wav"
        )
        dest = self.output_dir / filename
        dest.write_bytes(audio_bytes)
        logger.info(
            "Volcengine TTS: {} bytes written to {}", len(audio_bytes), dest
        )
        return dest
