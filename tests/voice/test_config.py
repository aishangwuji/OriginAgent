"""Tests for voice pipeline configuration."""
import pytest
from OriginAgent.config.schema import ToolsConfig


def test_voice_pipeline_config_defaults():
    """VoicePipelineConfig is integrated into ToolsConfig with sensible defaults."""
    cfg = ToolsConfig()
    voice = cfg.voice
    assert voice.enabled is False
    assert voice.stt_provider == "volcengine"
    assert voice.tts_provider == "volcengine"
    assert voice.tts_sample_rate == 24000
    assert voice.auto_play_response is True
    assert voice.voice_activity_timeout_ms == 1500
    assert voice.max_record_seconds == 60
