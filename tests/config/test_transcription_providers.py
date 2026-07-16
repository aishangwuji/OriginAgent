"""Tests for unified TRANSCRIPTION_PROVIDERS single source of truth (spec 3.6)."""
from __future__ import annotations

from OriginAgent.config import doctor
from OriginAgent.config.schema import TRANSCRIPTION_PROVIDERS
from OriginAgent.gateway import rest_api


def test_transcription_providers_contains_all_three():
    """The canonical constant must include groq, openai, and volcengine."""
    assert "groq" in TRANSCRIPTION_PROVIDERS
    assert "openai" in TRANSCRIPTION_PROVIDERS
    assert "volcengine" in TRANSCRIPTION_PROVIDERS
    assert TRANSCRIPTION_PROVIDERS == {"groq", "openai", "volcengine"}


def test_doctor_uses_canonical_providers():
    """config.doctor must source its provider set from schema (rule 6)."""
    assert doctor._TRANSCRIPTION_PROVIDERS is TRANSCRIPTION_PROVIDERS


def test_rest_api_uses_canonical_providers():
    """gateway.rest_api must source its provider options from schema (rule 6)."""
    assert rest_api._TRANSCRIPTION_PROVIDER_OPTIONS is TRANSCRIPTION_PROVIDERS
