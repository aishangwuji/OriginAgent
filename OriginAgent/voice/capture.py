"""Audio capture with Voice Activity Detection (VAD)."""
from __future__ import annotations

import tempfile
import uuid
import wave
from pathlib import Path

from loguru import logger

try:
    import sounddevice as sd
    import numpy as np
    import webrtcvad

    _HAS_SOUNDDEVICE = True
except ImportError:
    _HAS_SOUNDDEVICE = False
    webrtcvad = None
    logger.warning(
        "sounddevice/webrtcvad not available — desktop capture disabled"
    )


class AudioCapture:
    """Capture audio from the default microphone with VAD.

    Uses WebRTC VAD to detect speech segments, yielding complete
    utterances as WAV files.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        frame_ms: int = 30,
        vad_aggressiveness: int = 2,
        silence_ms: int = 800,
        max_record_ms: int = 30_000,
    ):
        if not _HAS_SOUNDDEVICE:
            raise RuntimeError("sounddevice not installed")
        if webrtcvad is None:
            raise RuntimeError("webrtcvad not installed")

        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.silence_ms = silence_ms
        self.max_record_ms = max_record_ms

        self._vad = webrtcvad.Vad(vad_aggressiveness)
        self._frame_samples = int(sample_rate * frame_ms / 1000)

    def record_utterance(self) -> Path | None:
        """Record one utterance (speech segment followed by silence).

        Returns a path to the WAV file, or None if nothing was captured.
        Blocks until the user stops speaking.
        """
        if not _HAS_SOUNDDEVICE:
            return None

        frames: list[bytes] = []
        silence_frames = 0
        max_frames = int(self.max_record_ms / self.frame_ms)
        silence_threshold = int(self.silence_ms / self.frame_ms)
        speech_detected = False
        recording = True

        def callback(indata: np.ndarray, _frames, _time, status):
            nonlocal recording, speech_detected, silence_frames
            if status:
                logger.warning("Audio capture status: {}", status)

            # Convert float32 to int16
            audio_int16 = (indata[:, 0] * 32767).astype(np.int16).tobytes()

            is_speech = self._vad.is_speech(audio_int16, self.sample_rate)
            if is_speech:
                speech_detected = True
                silence_frames = 0
                frames.append(audio_int16)
            elif speech_detected:
                silence_frames += 1
                frames.append(audio_int16)
                if silence_frames >= silence_threshold:
                    recording = False
            # If no speech yet and this is silence, skip the frame

            if len(frames) >= max_frames:
                recording = False

        logger.info("Listening... (speak now, will auto-stop after silence)")
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                callback=callback,
                blocksize=self._frame_samples,
            ):
                while recording:
                    sd.sleep(50)
        except Exception as exc:
            logger.exception("Audio capture error: {}", exc)
            return None

        if not speech_detected or len(frames) == 0:
            logger.info("No speech detected")
            return None

        # Save to WAV
        dest = Path(tempfile.gettempdir()) / f"voice_capture_{uuid.uuid4().hex[:8]}.wav"
        self._save_wav(dest, frames)
        logger.info("Captured {} frames → {}", len(frames), dest)
        return dest

    def _save_wav(self, path: Path, frames: list[bytes]) -> None:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(b"".join(frames))


class HotkeyListener:
    """Listen for a global hotkey and invoke a callback on each press."""

    def __init__(self, hotkey: str, callback, *, daemon: bool = True):
        self._hotkey = hotkey
        self._callback = callback
        self._listener = None

    def start(self) -> None:
        """Start listening for the hotkey (non-blocking)."""
        from pynput import keyboard

        current_keys: set = set()

        def on_press(key):
            current_keys.add(key)
            if self._normalize(key) == self._hotkey:
                self._callback()

        def on_release(key):
            current_keys.discard(key)

        # Parse hotkey string like "<ctrl>+<shift>+v"
        target = self._hotkey

        def _matches(target_key_str: str, key) -> bool:
            """Check if a key matches the target string."""
            from pynput.keyboard import Key

            parts = [p.strip() for p in target_key_str.lower().split("+")]
            for part in parts:
                if part == "ctrl" and Key.ctrl not in current_keys:
                    return False
                if part == "shift" and Key.shift not in current_keys:
                    return False
                if part == "alt" and Key.alt not in current_keys:
                    return False
            return self._normalize(key) == target

        self._listener = keyboard.Listener(
            on_press=on_press, on_release=on_release
        )
        self._listener.daemon = True
        self._listener.start()
        logger.info("Hotkey listener started: {}", self._hotkey)

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()

    @staticmethod
    def _normalize(key) -> str:
        """Normalize a key to a lowercase string."""
        try:
            return (key.char or "").lower() if hasattr(key, "char") else str(key)
        except Exception:
            return str(key)


def available() -> bool:
    """Check if desktop audio capture is available."""
    return _HAS_SOUNDDEVICE
