"""Voice processing pipeline for OriginAgent."""
from OriginAgent.voice.audio import AudioPlayback
from OriginAgent.voice.capture import AudioCapture
from OriginAgent.voice.desktop_assistant import DesktopVoiceAssistant
from OriginAgent.voice.pipeline import VoicePipeline
from OriginAgent.voice.stt import StreamSTT, VolcengineStreamSTT
from OriginAgent.voice.tts import StreamTTS, VolcengineStreamTTS

__all__ = [
    "StreamSTT",
    "VolcengineStreamSTT",
    "StreamTTS",
    "VolcengineStreamTTS",
    "VoicePipeline",
    "DesktopVoiceAssistant",
    "AudioCapture",
    "AudioPlayback",
]
