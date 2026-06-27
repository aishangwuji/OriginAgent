"""Voice processing pipeline for OriginAgent."""
from OriginAgent.voice.pipeline import VoicePipeline
from OriginAgent.voice.stt import StreamSTT, VolcengineStreamSTT
from OriginAgent.voice.tts import StreamTTS, VolcengineStreamTTS
from OriginAgent.voice.capture import AudioCapture, HotkeyListener, available
from OriginAgent.voice.desktop_assistant import DesktopVoiceAssistant

__all__ = [
    "StreamSTT",
    "VolcengineStreamSTT",
    "StreamTTS",
    "VolcengineStreamTTS",
    "VoicePipeline",
    "AudioCapture",
    "HotkeyListener",
    "DesktopVoiceAssistant",
    "available",
]
