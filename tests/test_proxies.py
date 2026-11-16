"""Project-local proxy cache tests."""

import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from reel_engine.gui.proxies import ProxyCache, ProxyCancelled


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg/FFprobe non installati")
class ProxyCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reel-proxy-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.source = self.root / "source.mp4"
        self._make_video(self.source, "blue")
        self.cache = ProxyCache(self.project)

    def _make_video(self, path, color):
        subprocess.run([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
            f"color=c={color}:s=1280x720:r=10:d=0.5", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(path),
        ], check=True, capture_output=True)

    def test_generates_light_video_only_proxy_and_reuses_it(self):
        proxy = self.cache.generate(self.source)
        self.assertTrue(proxy.is_file())
        self.assertEqual(proxy.parent, self.project / ".reel_engine" / "proxies")
        probe = subprocess.run([
            FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,width,height",
            "-of", "default=noprint_wrappers=1", str(proxy),
        ], check=True, capture_output=True, text=True).stdout
        self.assertIn("codec_type=video", probe)
        self.assertNotIn("codec_type=audio", probe)
        self.assertLessEqual(max(
            int(line.split("=", 1)[1]) for line in probe.splitlines()
            if line.startswith(("width=", "height="))
        ), 960)
        with patch("reel_engine.gui.proxies.subprocess.Popen", side_effect=AssertionError("proxy should be reused")):
            self.assertEqual(self.cache.generate(self.source), proxy)

    def test_modified_or_replaced_source_gets_a_new_cache_entry(self):
        first_proxy = self.cache.generate(self.source)
        replacement = self.root / "replacement.mp4"
        self._make_video(replacement, "red")
        replacement.replace(self.source)
        self.assertNotEqual(self.cache.proxy_path(self.source), first_proxy)
        self.assertIsNone(self.cache.proxy_for(self.source))
        second_proxy = self.cache.generate(self.source)
        self.assertTrue(second_proxy.is_file())
        self.assertNotEqual(second_proxy, first_proxy)

    def test_cancellation_does_not_publish_partial_proxy(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(ProxyCancelled):
            self.cache.generate(self.source, cancel_event=cancelled)
        self.assertIsNone(self.cache.proxy_for(self.source))
        self.assertEqual(list(self.cache.root.glob("*.tmp.mp4")), [])

    def test_missing_encoder_fails_without_creating_cache_artifacts(self):
        with self.assertRaises(FileNotFoundError):
            self.cache.generate(self.source, ffmpeg="missing-ffmpeg-executable")
        self.assertEqual(list(self.cache.root.glob("*.mp4")), [])


if __name__ == "__main__":
    unittest.main()
