"""Voice transcription providers."""

import asyncio
import base64
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

# Up to 3 retries (4 attempts total) with exponential backoff on transient
# failures. Whisper endpoints occasionally return 502/503 under load, and
# mobile-network transcription callers hit sporadic connect/read errors.
# Without this, a voice message silently becomes the empty string.
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


async def _post_transcription_with_retry(
    url: str,
    *,
    api_key: str | None,
    path: Path,
    model: str,
    provider_label: str,
    language: str | None = None,
) -> str:
    """POST an audio file for transcription, retrying on transient errors.

    Retries on connect/read/timeout failures and on 408/429/5xx responses.
    Other errors (including 4xx such as 401/403) return "" immediately — the
    caller's config is wrong and retrying only wastes quota.

    When ``language`` is provided, it is forwarded as the ``language``
    multipart field on every attempt (the dict is rebuilt per attempt so the
    same field is present on retries).
    """
    try:
        data = path.read_bytes()
    except OSError as e:
        logger.exception("{} transcription error: cannot read audio file: {}", provider_label, e)
        return ""
    headers = {"Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient() as client:
        for attempt in range(_MAX_RETRIES + 1):
            files = {
                "file": (path.name, data),
                "model": (None, model),
            }
            if language:
                files["language"] = (None, language)
            try:
                response = await client.post(url, headers=headers, files=files, timeout=60.0)
            except _RETRYABLE_EXCEPTIONS as e:
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "{} transcription transient error (attempt {}/{}): {}",
                        provider_label,
                        attempt + 1,
                        _MAX_RETRIES + 1,
                        e,
                    )
                    await asyncio.sleep(_BACKOFF_S[attempt])
                    continue
                logger.exception(
                    "{} transcription error after {} attempts: {}",
                    provider_label,
                    _MAX_RETRIES + 1,
                    e,
                )
                return ""
            except Exception as e:
                logger.exception("{} transcription error: {}", provider_label, e)
                return ""

            if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                logger.warning(
                    "{} transcription transient HTTP {} (attempt {}/{})",
                    provider_label,
                    response.status_code,
                    attempt + 1,
                    _MAX_RETRIES + 1,
                )
                await asyncio.sleep(_BACKOFF_S[attempt])
                continue

            try:
                response.raise_for_status()
            except Exception as e:
                logger.exception("{} transcription error: {}", provider_label, e)
                return ""

            try:
                payload = response.json()
            except Exception as e:
                logger.exception(
                    "{} transcription error: malformed response body: {}",
                    provider_label,
                    e,
                )
                return ""
            if not isinstance(payload, dict):
                logger.error(
                    "{} transcription error: unexpected response shape: {!r}",
                    provider_label,
                    type(payload).__name__,
                )
                return ""
            return payload.get("text", "")


class OpenAITranscriptionProvider:
    """Voice transcription provider using OpenAI's Whisper API."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        language: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.api_url = (
            api_base
            or os.environ.get("OPENAI_TRANSCRIPTION_BASE_URL")
            or "https://api.openai.com/v1/audio/transcriptions"
        )
        self.language = language or None

    async def transcribe(self, file_path: str | Path) -> str:
        if not self.api_key:
            logger.warning("OpenAI API key not configured for transcription")
            return ""
        path = Path(file_path)
        if not path.exists():
            logger.error("Audio file not found: {}", file_path)
            return ""
        return await _post_transcription_with_retry(
            self.api_url,
            api_key=self.api_key,
            path=path,
            model="whisper-1",
            provider_label="OpenAI",
            language=self.language,
        )


class GroqTranscriptionProvider:
    """
    Voice transcription provider using Groq's Whisper API.

    Groq offers extremely fast transcription with a generous free tier.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        language: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        self.api_url = (
            api_base
            or os.environ.get("GROQ_BASE_URL")
            or "https://api.groq.com/openai/v1/audio/transcriptions"
        )
        self.language = language or None

    async def transcribe(self, file_path: str | Path) -> str:
        """
        Transcribe an audio file using Groq.

        Args:
            file_path: Path to the audio file.

        Returns:
            Transcribed text.
        """
        if not self.api_key:
            logger.warning("Groq API key not configured for transcription")
            return ""

        path = Path(file_path)
        if not path.exists():
            logger.error("Audio file not found: {}", file_path)
            return ""

        return await _post_transcription_with_retry(
            self.api_url,
            api_key=self.api_key,
            path=path,
            model="whisper-large-v3",
            provider_label="Groq",
            language=self.language,
        )


class VolcengineTranscriptionProvider:
    """Voice transcription provider using Volcengine Doubao Voice ASR."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        language: str | None = None,
        resource_id: str | None = None,
        user_id: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("VOLCENGINE_API_KEY")
        self.api_url = (
            api_base
            or os.environ.get("VOLCENGINE_TRANSCRIPTION_BASE_URL")
            or "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
        )
        self.language = (language or "").strip() or None
        self.resource_id = (
            resource_id
            or os.environ.get("VOLCENGINE_TRANSCRIPTION_RESOURCE_ID")
            or "volc.bigasr.auc_turbo"
        )
        self.user_id = (user_id or os.environ.get("VOLCENGINE_TRANSCRIPTION_USER_ID") or "").strip() or "originagent"

    async def transcribe(self, file_path: str | Path) -> str:
        if not self.api_key:
            logger.warning("Volcengine API key not configured for transcription")
            return ""

        path = Path(file_path)
        if not path.exists():
            logger.error("Audio file not found: {}", file_path)
            return ""

        try:
            data = path.read_bytes()
        except OSError as e:
            logger.exception("Volcengine transcription error: cannot read audio file: {}", e)
            return ""

        audio_format = _guess_volcengine_audio_format(path)
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
                "model_name": "bigmodel",
                "enable_itn": True,
            },
        }
        async with httpx.AsyncClient() as client:
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    response = await client.post(
                        self.api_url,
                        headers=headers,
                        json=body,
                        timeout=120.0,
                    )
                except _RETRYABLE_EXCEPTIONS as e:
                    if attempt < _MAX_RETRIES:
                        logger.warning(
                            "Volcengine transcription transient error (attempt {}/{}): {}",
                            attempt + 1,
                            _MAX_RETRIES + 1,
                            e,
                        )
                        await asyncio.sleep(_BACKOFF_S[attempt])
                        continue
                    logger.exception(
                        "Volcengine transcription error after {} attempts: {}",
                        _MAX_RETRIES + 1,
                        e,
                    )
                    return ""
                except Exception as e:
                    logger.exception("Volcengine transcription error: {}", e)
                    return ""

                status_code = response.status_code
                header_code = str(response.headers.get("X-Api-Status-Code") or "").strip()
                if (
                    status_code in _RETRYABLE_STATUS
                    or header_code in {"55000031"}
                ) and attempt < _MAX_RETRIES:
                    logger.warning(
                        "Volcengine transcription transient failure HTTP {} / API {} (attempt {}/{})",
                        status_code,
                        header_code or "unknown",
                        attempt + 1,
                        _MAX_RETRIES + 1,
                    )
                    await asyncio.sleep(_BACKOFF_S[attempt])
                    continue

                try:
                    response.raise_for_status()
                except Exception as e:
                    logger.exception("Volcengine transcription HTTP error: {}", e)
                    return ""

                if header_code and header_code != "20000000":
                    logger.error(
                        "Volcengine transcription business error {}: {}",
                        header_code,
                        response.headers.get("X-Api-Message") or "",
                    )
                    return ""

                try:
                    payload = response.json()
                except Exception as e:
                    logger.exception(
                        "Volcengine transcription error: malformed response body: {}",
                        e,
                    )
                    return ""
                if not isinstance(payload, dict):
                    logger.error(
                        "Volcengine transcription error: unexpected response shape: {!r}",
                        type(payload).__name__,
                    )
                    return ""
                result = payload.get("result")
                if not isinstance(result, dict):
                    return ""
                text = result.get("text")
                return text if isinstance(text, str) else ""
        return ""


def _guess_volcengine_audio_format(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "wave":
        return "wav"
    if suffix == "mpeg":
        return "mp3"
    if suffix in {"wav", "mp3", "ogg"}:
        return suffix
    if suffix in {"opus"}:
        return "ogg"
    return "wav"
