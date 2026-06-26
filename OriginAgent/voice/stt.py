"""Streaming Speech-to-Text providers."""
from __future__ import annotations

import asyncio
import base64
import os
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

_MAX_RETRIES = 3
_BACKOFF_S = (1.0, 2.0, 4.0)
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
_RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)


class StreamSTT(ABC):
    """Abstract base for speech-to-text providers."""

    def __init__(self, *, api_key: str | None = None):
        self.api_key = api_key

    @abstractmethod
    async def transcribe_file(self, file_path: str | Path) -> str:
        """Transcribe an audio file. Returns empty string on any failure."""
        ...


class VolcengineStreamSTT(StreamSTT):
    """Volcengine openspeech ASR — file-based transcription via 'bigmodel'."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        resource_id: str | None = None,
        user_id: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("VOLCENGINE_API_KEY")
        self.api_url = (
            api_base
            or os.environ.get("VOLCENGINE_TRANSCRIPTION_BASE_URL")
            or "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
        )
        self.resource_id = (
            resource_id
            or os.environ.get("VOLCENGINE_TRANSCRIPTION_RESOURCE_ID")
            or "volc.bigasr.auc_turbo"
        )
        self.user_id = (
            (user_id or os.environ.get("VOLCENGINE_TRANSCRIPTION_USER_ID") or "").strip()
            or "originagent"
        )
        self.model_name = "bigmodel"
        super().__init__(api_key=self.api_key)

    async def transcribe_file(self, file_path: str | Path) -> str:
        if not self.api_key:
            logger.warning("Volcengine API key not configured for transcription")
            return ""

        path = Path(file_path)
        if not path.exists() or path.stat().st_size == 0:
            logger.error("Audio file not found or empty: {}", file_path)
            return ""

        try:
            data = path.read_bytes()
        except OSError as exc:
            logger.exception("Volcengine STT: cannot read audio file: {}", exc)
            return ""

        audio_format = self._guess_format(path)
        request_id = str(uuid.uuid4())
        headers = {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": request_id,
            "X-Api-Sequence": "-1",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "user": {"uid": self.user_id},
            "audio": {
                "format": audio_format,
                "data": base64.b64encode(data).decode("ascii"),
            },
            "request": {
                "model_name": self.model_name,
                "enable_itn": True,
            },
        }

        async with httpx.AsyncClient() as client:
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    response = await client.post(
                        self.api_url, headers=headers, json=body, timeout=120.0
                    )
                except _RETRYABLE_EXCEPTIONS as exc:
                    if attempt < _MAX_RETRIES:
                        logger.warning(
                            "Volcengine STT transient (attempt {}/{}): {}",
                            attempt + 1, _MAX_RETRIES + 1, exc,
                        )
                        await asyncio.sleep(_BACKOFF_S[attempt])
                        continue
                    logger.exception("Volcengine STT retries exhausted: {}", exc)
                    return ""
                except Exception as exc:
                    logger.exception("Volcengine STT unexpected error: {}", exc)
                    return ""

                status_code = response.status_code
                header_code = str(response.headers.get("X-Api-Status-Code") or "").strip()
                if (
                    status_code in _RETRYABLE_STATUS or header_code in {"55000031"}
                ) and attempt < _MAX_RETRIES:
                    logger.warning(
                        "Volcengine STT transient HTTP {} (attempt {}/{})",
                        status_code, attempt + 1, _MAX_RETRIES + 1,
                    )
                    await asyncio.sleep(_BACKOFF_S[attempt])
                    continue

                try:
                    response.raise_for_status()
                except Exception as exc:
                    logger.exception("Volcengine STT HTTP error: {}", exc)
                    return ""

                if header_code and header_code != "20000000":
                    logger.error(
                        "Volcengine STT business error {}: {}",
                        header_code,
                        response.headers.get("X-Api-Message") or "",
                    )
                    return ""

                try:
                    payload = response.json()
                except Exception as exc:
                    logger.exception("Volcengine STT malformed response: {}", exc)
                    return ""
                if not isinstance(payload, dict):
                    return ""
                result = payload.get("result")
                if not isinstance(result, dict):
                    return ""
                text = result.get("text")
                return text if isinstance(text, str) else ""
        return ""

    @staticmethod
    def _guess_format(path: Path) -> str:
        suffix = path.suffix.lower().lstrip(".")
        if suffix == "wave":
            return "wav"
        if suffix == "mpeg":
            return "mp3"
        if suffix in {"wav", "mp3", "ogg"}:
            return suffix
        if suffix == "opus":
            return "ogg"
        return "wav"
