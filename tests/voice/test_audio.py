"""Tests for audio utilities."""
import os
import tempfile
from pathlib import Path

import pytest
from OriginAgent.voice.audio import AudioPlayback, save_audio_data_url


class TestSaveAudioDataURL:
    @pytest.fixture
    def tmp_dir(self):
        tmp = tempfile.mkdtemp()
        yield Path(tmp)
        try:
            os.rmdir(tmp)
        except OSError:
            pass

    def test_saves_wav_from_data_url(self, tmp_dir):
        url = (
            "data:audio/wav;base64,"
            "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEA+AAACABAAZGF0YQAAAAA="
        )
        path = save_audio_data_url(url, tmp_dir)
        assert path is not None
        assert path.exists()
        assert path.suffix == ".wav"
        try:
            path.unlink()
        except OSError:
            pass

    def test_returns_none_for_invalid_url(self, tmp_dir):
        assert save_audio_data_url("not-a-data-url", tmp_dir) is None

    def test_returns_none_for_empty_string(self, tmp_dir):
        assert save_audio_data_url("", tmp_dir) is None

    def test_saves_ogg_from_data_url(self, tmp_dir):
        url = "data:audio/ogg;base64,dGVzdA=="
        path = save_audio_data_url(url, tmp_dir)
        assert path is not None
        assert path.suffix == ".ogg"
        try:
            path.unlink()
        except OSError:
            pass


class TestAudioPlayback:
    def test_play_nonexistent_file(self):
        player = AudioPlayback()
        result = player.play(Path("/nonexistent/audio.wav"))
        assert result is False

    def test_play_windows(self):
        if os.name != "nt":
            pytest.skip("Windows only")
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(
            b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
            b"\x01\x00\x01\x00\x40\x1f\x00\x00\x80>\x00\x00"
            b"\x02\x00\x10\x00data\x00\x00\x00\x00"
        )
        tmp.close()
        try:
            player = AudioPlayback()
            result = player.play(Path(tmp.name))
            assert isinstance(result, bool)
        except Exception:
            pytest.skip("winsound unavailable")
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
