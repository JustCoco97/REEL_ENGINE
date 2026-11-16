"""Real FFmpeg decoding and cache tests for audio waveform envelopes."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from reel_engine.waveform import WaveformCache, WaveformError

FFMPEG = shutil.which("ffmpeg")


def make_audio(path, duration, *, codec="pcm_s16le"):
    path = Path(path)
    subprocess.run([
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
        f"sine=frequency=880:duration={duration}", "-ac", "1", "-ar", "8000",
        "-c:a", codec, str(path),
    ], check=True, capture_output=True, timeout=60)
    return path


@unittest.skipUnless(FFMPEG, "FFmpeg non disponibile")
class WaveformCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reel-waveform-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = WaveformCache(self.root / "cache")

    def test_ffmpeg_waveform_is_cached_in_memory_and_on_disk(self):
        audio = make_audio(self.root / "tone.wav", 0.8)
        key, data = self.cache.load_or_generate(audio)
        self.assertEqual(data.bin_count, 80)
        self.assertAlmostEqual(data.duration, 0.8, delta=0.02)
        self.assertTrue(any(data.peaks))
        cache_file = self.root / "cache" / key[:2] / f"{key}.rwave"
        self.assertTrue(cache_file.is_file())

        with mock.patch("reel_engine.waveform.subprocess.Popen", side_effect=AssertionError("cache miss")):
            _, in_memory = self.cache.load_or_generate(audio)
        self.assertIs(in_memory, data)

        reopened_cache = WaveformCache(self.root / "cache")
        with mock.patch("reel_engine.waveform.subprocess.Popen", side_effect=AssertionError("disk cache miss")):
            reopened_key, from_disk = reopened_cache.load_or_generate(audio)
        self.assertEqual(reopened_key, key)
        self.assertEqual(from_disk.peaks, data.peaks)

    def test_file_change_invalidates_cache_and_long_audio_stays_compact(self):
        audio = make_audio(self.root / "changing.wav", 0.6)
        old_key, first = self.cache.load_or_generate(audio)
        replacement = make_audio(self.root / "replacement.wav", 1.4)
        replacement.replace(audio)
        new_key = self.cache.cache_key(audio)
        self.assertNotEqual(new_key, old_key)
        _, changed = self.cache.load_or_generate(audio)
        self.assertAlmostEqual(changed.duration, 1.4, delta=0.02)
        self.assertGreater(changed.bin_count, first.bin_count)

        long_audio = make_audio(self.root / "long.flac", 180, codec="flac")
        _, long_data = self.cache.load_or_generate(long_audio)
        self.assertAlmostEqual(long_data.duration, 180, delta=0.02)
        self.assertEqual(long_data.bin_count, 18000)
        self.assertLessEqual(len(long_data.peaks), 72000)

    def test_missing_or_undecodable_media_has_clear_error(self):
        self.assertIsNone(self.cache.cache_key(self.root / "missing.wav"))
        invalid = self.root / "broken.wav"
        invalid.write_bytes(b"not an audio file")
        with self.assertRaises(WaveformError):
            self.cache.load_or_generate(invalid)


if __name__ == "__main__":
    unittest.main()