import json
import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
import zlib
from pathlib import Path

from scripts.reel_engine import VideoSettings, load_timeline


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "reel_engine.py"


def make_placeholder_assets(directory):
    """Create minimal files for validation-only tests."""
    directory = Path(directory)
    images = directory / "fixtures" / "images"
    images.mkdir(parents=True, exist_ok=True)
    audio = directory / "fixtures" / "test_audio.m4a"
    image_1 = images / "01.png"
    image_2 = images / "02.png"
    audio.write_bytes(b"audio-placeholder")
    image_1.write_bytes(b"image-placeholder")
    image_2.write_bytes(b"image-placeholder")
    return audio, image_1, image_2


def _write_synthetic_png(path, rgb):
    """Write a small, valid RGB PNG using only the Python standard library."""
    width, height = 96, 160

    def chunk(kind, data):
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    pixels = zlib.compress(row * height)
    image = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image)


def _write_synthetic_wav(path, *, duration=2.0, sample_rate=48000, frequency=440.0):
    """Write a non-silent mono PCM WAV using only the Python standard library."""
    frame_count = int(duration * sample_rate)
    frames = bytearray()
    for index in range(frame_count):
        sample = int(8192 * math.sin(2 * math.pi * frequency * index / sample_rate))
        frames.extend(struct.pack("<h", sample))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(frames)


def make_renderable_assets(directory):
    """Create valid images and audio for real CLI rendering tests."""
    directory = Path(directory)
    images = directory / "images"
    images.mkdir(parents=True, exist_ok=True)
    image_1 = images / "01.png"
    image_2 = images / "02.png"
    audio = directory / "test_audio.wav"
    _write_synthetic_png(image_1, (220, 60, 60))
    _write_synthetic_png(image_2, (60, 120, 220))
    _write_synthetic_wav(audio)
    return images, audio


def write_timeline(directory, *, scenes=None, canvas=None, audio=None, schema_version=1):
    directory = Path(directory)
    fixture_audio, image_1, _ = make_placeholder_assets(directory)
    payload = {
        "schema_version": schema_version,
        "canvas": canvas or {"width": 1080, "height": 1920, "fps": 30},
        "audio": str(audio or fixture_audio),
        "scenes": scenes or [
            {"id": "opening", "type": "image", "asset": str(image_1), "duration": 0.5}
        ],
    }
    path = directory / "timeline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TimelineLoadingTests(unittest.TestCase):
    def test_loads_and_normalizes_relative_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "images").mkdir()
            (folder / "audio.m4a").write_bytes(b"audio-placeholder")
            (folder / "images" / "frame.png").write_bytes(b"image-placeholder")
            scene = [{"id": "first", "type": "image", "asset": "images/frame.png", "duration": 2.5}]
            path = write_timeline(folder, scenes=scene, canvas=None, audio="audio.m4a")

            timeline = load_timeline(path)

            self.assertEqual(timeline.video, VideoSettings(1080, 1920, 30.0))
            self.assertEqual(timeline.scenes[0].id, "first")
            self.assertEqual(timeline.scenes[0].asset, (folder / "images" / "frame.png").resolve())
            self.assertEqual(timeline.scenes[0].duration, 2.5)
            self.assertEqual(timeline.audio_asset, (folder / "audio.m4a").resolve())

    def test_accepts_video_asset_in_model(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "audio.m4a").touch()
            (folder / "clip.mp4").touch()
            path = write_timeline(
                folder,
                scenes=[{"id": "clip", "type": "video", "asset": "clip.mp4", "duration": 3, "source_in": 1}],
                audio="audio.m4a",
            )
            timeline = load_timeline(path)
            self.assertEqual(timeline.scenes[0].media_type, "video")
            self.assertEqual(timeline.scenes[0].source_in, 1.0)

    def test_rejects_unsupported_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_timeline(temp, schema_version=2)
            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_timeline(path)

    def test_rejects_duplicate_scene_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            _, image_1, image_2 = make_placeholder_assets(folder)
            duplicate = [
                {"id": "same", "type": "image", "asset": str(image_1), "duration": 1},
                {"id": "same", "type": "image", "asset": str(image_2), "duration": 1},
            ]
            path = write_timeline(folder, scenes=duplicate)
            with self.assertRaisesRegex(ValueError, "duplicato"):
                load_timeline(path)

    def test_rejects_nonpositive_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            _, image_1, _ = make_placeholder_assets(folder)
            scenes = [{"id": "bad", "type": "image", "asset": str(image_1), "duration": 0}]
            path = write_timeline(folder, scenes=scenes)
            with self.assertRaisesRegex(ValueError, "duration"):
                load_timeline(path)

    def test_rejects_missing_asset(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            fixture_audio, _, _ = make_placeholder_assets(folder)
            scenes = [{"id": "missing", "type": "image", "asset": "absent.png", "duration": 1}]
            path = write_timeline(folder, scenes=scenes, audio=fixture_audio)
            with self.assertRaisesRegex(ValueError, "non trovato"):
                load_timeline(path)

    def test_rejects_invalid_scene_type(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            _, image_1, _ = make_placeholder_assets(folder)
            scenes = [{"id": "bad", "type": [], "asset": str(image_1), "duration": 1}]
            path = write_timeline(folder, scenes=scenes)
            with self.assertRaisesRegex(ValueError, "type"):
                load_timeline(path)

    def test_rejects_invalid_canvas_dimensions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_timeline(temp, canvas={"width": 1079, "height": 1920, "fps": 30})
            with self.assertRaisesRegex(ValueError, "canvas.width"):
                load_timeline(path)


class CliCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        missing_tools = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
        if missing_tools:
            raise RuntimeError(
                "Prerequisiti dei test CLI non disponibili nel PATH: " + ", ".join(missing_tools)
            )

        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        try:
            encoder_result = subprocess.run(
                [ffmpeg, "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                check=True,
            )
            subprocess.run([ffprobe, "-version"], capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"Impossibile verificare FFmpeg/ffprobe: {exc}") from exc

        encoder_text = encoder_result.stdout + encoder_result.stderr
        missing_encoders = [
            encoder
            for encoder in ("libx264", "aac")
            if re.search(rf"(?m)^\s*[A-Z.]{{6}}\s+{re.escape(encoder)}(?:\s|$)", encoder_text) is None
        ]
        if missing_encoders:
            raise RuntimeError(
                "Encoder richiesti dai test non disponibili in FFmpeg: "
                + ", ".join(missing_encoders)
            )

    def _run_cli(self, sandbox, *arguments):
        return subprocess.run(
            [sys.executable, str(sandbox / "scripts" / "reel_engine.py"), *map(str, arguments)],
            cwd=sandbox,
            capture_output=True,
            text=True,
            check=True,
        )

    def _make_isolated_engine(self, root):
        sandbox = Path(root) / "engine"
        (sandbox / "scripts").mkdir(parents=True)
        shutil.copy2(SCRIPT, sandbox / "scripts" / "reel_engine.py")
        return sandbox

    def test_legacy_images_audio_command_renders(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = self._make_isolated_engine(root)
            images, audio = make_renderable_assets(root / "media")
            output = root / "legacy.mp4"
            result = self._run_cli(sandbox, "--images", images, "--audio", audio, "--output", output)
            self.assertTrue(output.is_file())
            self.assertIn("VIDEO COMPLETATO", result.stdout)

    def test_timeline_json_command_loads_and_renders(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = self._make_isolated_engine(root)
            fixture = root / "timeline-inputs"
            fixture.mkdir()
            images, audio = make_renderable_assets(fixture / "media")
            scenes = [
                {"id": "first", "type": "image", "asset": str(images / "01.png"), "duration": 0.5},
                {"id": "second", "type": "image", "asset": str(images / "02.png"), "duration": 0.5},
            ]
            timeline_path = write_timeline(fixture, scenes=scenes, audio=audio)
            output = root / "timeline.mp4"

            result = self._run_cli(sandbox, "--timeline", timeline_path, "--output", output)

            self.assertTrue(output.is_file())
            self.assertIn("Scene: 2", result.stdout)
            self.assertIn("VIDEO COMPLETATO", result.stdout)


if __name__ == "__main__":
    unittest.main()
