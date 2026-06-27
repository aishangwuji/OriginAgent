"""
End-to-end voice pipeline test.
Run with: .venv\Scripts\python.exe tests/voice/manual_e2e_test.py
"""
import asyncio
import os
import uuid
from pathlib import Path

# Ensure VOLCENGINE_API_KEY is available
os.environ["VOLCENGINE_API_KEY"] = os.environ.get(
    "VOLCENGINE_API_KEY", "8iof722efrNG2ppBJLhIglpYMxhiO6Vs"
)

from OriginAgent.voice.stt import VolcengineStreamSTT
from OriginAgent.voice.tts import VolcengineStreamTTS


async def test_stt():
    """Test STT with a real Volcengine ASR API call."""
    print("=" * 60)
    print("Test 1: Volcengine Speech-to-Text (STT)")
    print("=" * 60)

    stt = VolcengineStreamSTT()
    print(f"API URL: {stt.api_url}")
    print(f"Resource ID: {stt.resource_id}")
    print(f"API Key: {'***configured***' if stt.api_key else 'MISSING!'}")

    if not stt.api_key:
        print("❌ VOLCENGINE_API_KEY not set — skipping STT test")
        return False

    # Create a minimal WAV file for testing (0.5s of silence)
    # This won't produce meaningful text, but validates the API works
    sample_rate = 16000
    duration_sec = 1
    num_samples = sample_rate * duration_sec
    wav_data = bytearray()
    # RIFF header
    wav_data.extend(b"RIFF")
    wav_data.extend((36 + num_samples * 2).to_bytes(4, "little"))
    wav_data.extend(b"WAVE")
    # fmt chunk
    wav_data.extend(b"fmt ")
    wav_data.extend((16).to_bytes(4, "little"))  # chunk size
    wav_data.extend((1).to_bytes(2, "little"))  # PCM
    wav_data.extend((1).to_bytes(2, "little"))  # mono
    wav_data.extend(sample_rate.to_bytes(4, "little"))
    wav_data.extend((sample_rate * 2).to_bytes(4, "little"))  # byte rate
    wav_data.extend((2).to_bytes(2, "little"))  # block align
    wav_data.extend((16).to_bytes(2, "little"))  # bits per sample
    # data chunk
    wav_data.extend(b"data")
    wav_data.extend((num_samples * 2).to_bytes(4, "little"))
    # silence samples
    for _ in range(num_samples):
        wav_data.extend((0).to_bytes(2, "little", signed=True))

    test_file = Path(f"tests/voice/test_audio_{uuid.uuid4().hex[:8]}.wav")
    test_file.write_bytes(bytes(wav_data))
    print(f"Created test WAV: {test_file} ({test_file.stat().st_size} bytes)")

    try:
        result = await stt.transcribe_file(test_file)
        print(f"STT result: '{result}'")
        if result:
            print("✅ STT API call succeeded!")
            success = True
        else:
            print("⚠️  STT returned empty (silence file — expected)")
            success = True  # API worked, just got empty text for silence
    except Exception as e:
        print(f"❌ STT failed: {e}")
        success = False
    finally:
        try:
            test_file.unlink()
        except OSError:
            pass

    return success


async def test_tts():
    """Test TTS with a real Volcengine Seed-TTS API call."""
    print()
    print("=" * 60)
    print("Test 2: Volcengine Text-to-Speech (TTS)")
    print("=" * 60)

    tts = VolcengineStreamTTS(output_dir="uploads/perception")
    print(f"API URL: {tts.api_url}")
    print(f"Resource ID: {tts.resource_id}")
    print(f"API Key: {'***configured***' if tts.api_key else 'MISSING!'}")

    if not tts.api_key:
        print("❌ VOLCENGINE_API_KEY not set — skipping TTS test")
        return False

    test_text = "你好，我是OriginAgent，语音功能已成功启用。"
    print(f"Input text: '{test_text}'")

    try:
        result = await tts.synthesize(test_text, voice="zh_female_santong")
        if result and result.exists():
            print(f"✅ TTS succeeded! Output: {result} ({result.stat().st_size} bytes)")
            try:
                result.unlink()
            except OSError:
                pass
            return True
        else:
            print(f"❌ TTS returned None or file doesn't exist")
            return False
    except Exception as e:
        print(f"❌ TTS failed: {e}")
        return False


async def main():
    print("OriginAgent Voice Pipeline — E2E Test")
    print(f"Python: {os.sys.version}")
    print()

    stt_ok = await test_stt()
    tts_ok = await test_tts()

    print()
    print("=" * 60)
    print("Summary:")
    print(f"  STT: {'✅ PASS' if stt_ok else '❌ FAIL'}")
    print(f"  TTS: {'✅ PASS' if tts_ok else '❌ FAIL'}")
    print("=" * 60)

    if stt_ok and tts_ok:
        print("🎉 Voice pipeline is fully operational!")
    else:
        print("⚠️  Some tests failed — check VOLCENGINE_API_KEY and network")

    return 0 if (stt_ok and tts_ok) else 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
