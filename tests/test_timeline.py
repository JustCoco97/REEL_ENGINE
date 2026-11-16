import json
import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
import wave
import zlib
from pathlib import Path

from scripts.reel_engine import VideoSettings, load_timeline
from reel_engine import rendering
from reel_engine.model import Scene, Timeline, Transition, TransitionSettings
from reel_engine.timing import align_seconds, seconds_to_frames


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


def _write_synthetic_wav(path, *, duration=2.0, sample_rate=48000, frequency=440.0, amplitude=8192, start_after=0.0):
    """Write a non-silent mono PCM WAV using only the Python standard library."""
    frame_count = int(duration * sample_rate)
    frames = bytearray()
    start_frame = int(start_after * sample_rate)
    for index in range(frame_count):
        sample = 0 if index < start_frame else int(
            amplitude * math.sin(2 * math.pi * frequency * (index - start_frame) / sample_rate)
        )
        frames.extend(struct.pack("<h", sample))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(frames)


def _make_synthetic_video(path, *, duration=2.0):
    """Generate a tiny moving test clip using the installed FFmpeg executable."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i",
        f"testsrc2=size=160x96:rate=20:duration={duration}", "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], capture_output=True, check=True)
    return path


def _make_segmented_video(path, *, segment_duration=1.5):
    """Create blue then red video segments so a wrong source seek is visible."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        shutil.which("ffmpeg"), "-v", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=blue:s=160x96:r=20:d={segment_duration}",
        "-f", "lavfi", "-i", f"color=c=red:s=160x96:r=20:d={segment_duration}",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
        "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], capture_output=True, check=True)
    return path


def _probe_video(path):
    result = subprocess.check_output([
        shutil.which("ffprobe"), "-v", "error", "-count_frames", "-select_streams", "v:0",
        "-show_entries", "stream=nb_read_frames,r_frame_rate", "-show_entries", "format=duration",
        "-of", "json", str(path),
    ], text=True)
    return json.loads(result)


def _probe_duration(path):
    result = subprocess.check_output([
        shutil.which("ffprobe"), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], text=True)
    return float(result.strip())


def _extract_rgb_frame(path, time_seconds):
    result = subprocess.run([
        shutil.which("ffmpeg"), "-v", "error", "-ss", str(time_seconds), "-i", str(path),
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ], capture_output=True, check=True)
    return result.stdout


def _probe_tone_amplitude(path, frequency, start, duration, sample_rate=48000):
    decoded = subprocess.run([
        shutil.which("ffmpeg"), "-v", "error", "-i", str(path), "-map", "0:a:0",
        "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(sample_rate), "pipe:1",
    ], capture_output=True, check=True).stdout
    sample_count = len(decoded) // 2
    samples = struct.unpack(f"<{sample_count}h", decoded)
    first = int(start * sample_rate)
    last = min(sample_count, int((start + duration) * sample_rate))
    count = last - first
    real = sum(samples[index] * math.cos(2 * math.pi * frequency * index / sample_rate)
               for index in range(first, last))
    imaginary = sum(samples[index] * math.sin(2 * math.pi * frequency * index / sample_rate)
                    for index in range(first, last))
    return 2 * math.hypot(real, imaginary) / count / 32768


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


def write_timeline(directory, *, scenes=None, canvas=None, audio=None, background_audio=None, texts=None, subtitles=None, transitions=None, schema_version=1):
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
    if background_audio is not None:
        payload["background_audio"] = background_audio
    if texts is not None:
        payload["texts"] = texts
    if subtitles is not None:
        payload["subtitles"] = subtitles
    if transitions is not None:
        payload["transitions"] = transitions
    path = directory / "timeline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_timeline_without_audio(directory, **options):
    """Write a v1 timeline whose primary audio is explicitly null."""
    path = write_timeline(directory, **options)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["audio"] = None
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class FrameTimingTests(unittest.TestCase):
    def test_scene_times_align_to_nearest_frame_using_timeline_fps(self):
        self.assertEqual(seconds_to_frames(0.101, 25), 3)
        self.assertAlmostEqual(align_seconds(0.101, 25), 0.12)
        self.assertAlmostEqual(align_seconds(0.101, 25, minimum_frames=1), 0.12)
        self.assertEqual(seconds_to_frames(0.02, 25), 1)


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
            _make_synthetic_video(folder / "clip.mp4")
            path = write_timeline(
                folder,
                scenes=[{"id": "clip", "type": "video", "asset": "clip.mp4", "duration": 0.8, "source_in": 1}],
                audio="audio.m4a",
            )
            timeline = load_timeline(path)
            self.assertEqual(timeline.scenes[0].media_type, "video")
            self.assertEqual(timeline.scenes[0].source_in, 1.0)

    def test_rejects_video_extension_with_invalid_media_file(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "audio.m4a").touch()
            (folder / "clip.mp4").write_bytes(b"not a video")
            path = write_timeline(
                folder,
                scenes=[{"id": "clip", "type": "video", "asset": "clip.mp4", "duration": 1}],
                audio="audio.m4a",
            )
            with self.assertRaisesRegex(ValueError, "sorgente video non leggibile"):
                load_timeline(path)

    def test_quantizes_image_and_video_scene_times_to_canvas_fps(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            _, image, _ = make_placeholder_assets(folder)
            _make_synthetic_video(folder / "clip.mp4", duration=1.0)
            path = write_timeline(
                folder,
                canvas={"width": 160, "height": 96, "fps": 25},
                scenes=[
                    {"id": "still", "type": "image", "asset": str(image), "duration": 0.101},
                    {"id": "clip", "type": "video", "asset": "clip.mp4",
                     "duration": 0.101, "source_in": 0.101},
                ],
            )
            timeline = load_timeline(path)
            self.assertEqual([seconds_to_frames(scene.duration, 25) for scene in timeline.scenes], [3, 3])
            self.assertEqual(seconds_to_frames(timeline.scenes[1].source_in, 25), 3)

    def test_rejects_trim_beyond_video_source_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "audio.m4a").touch()
            _make_synthetic_video(folder / "clip.mp4", duration=1.0)
            path = write_timeline(
                folder,
                scenes=[{"id": "clip", "type": "video", "asset": "clip.mp4",
                         "duration": 0.6, "source_in": 0.6}],
                audio="audio.m4a",
            )
            with self.assertRaisesRegex(ValueError, "supera la durata"):
                load_timeline(path)

    def test_loads_background_audio_options(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            background = folder / "music.wav"
            background.touch()
            path = write_timeline(
                folder,
                scenes=[{"id": "long", "type": "image", "asset": "fixtures/images/01.png", "duration": 10}],
                background_audio={
                    "asset": "music.wav", "source_in": 2.5, "duration": 8,
                    "volume": 0.3, "fade_in": 1, "fade_out": 1.5,
                },
            )
            timeline = load_timeline(path)
            self.assertEqual(timeline.background_audio.asset, background.resolve())
            self.assertEqual(timeline.background_audio.source_in, 2.5)
            self.assertEqual(timeline.background_audio.duration, 8)
            self.assertEqual(timeline.background_audio.volume, 0.3)
            self.assertEqual(timeline.background_audio.fade_in, 1)
            self.assertEqual(timeline.background_audio.fade_out, 1.5)

    def test_rejects_invalid_background_audio_options(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            background = folder / "music.wav"
            background.touch()
            invalid_options = [
                {"asset": "music.wav", "volume": 1.1},
                {"asset": "music.wav", "duration": 0.5, "fade_in": 0.4, "fade_out": 0.2},
            ]
            for options in invalid_options:
                with self.subTest(options=options):
                    path = write_timeline(folder, background_audio=options)
                    with self.assertRaisesRegex(ValueError, "background_audio"):
                        load_timeline(path)

    def test_loads_timed_text_and_subtitle_styles(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = write_timeline(
                folder,
                scenes=[{"id": "long", "type": "image", "asset": "fixtures/images/01.png", "duration": 2}],
                texts=[{
                    "start": 0.1, "end": 1.2, "text": "Caffè — già!", "font": "Arial",
                    "size": 32, "color": "#FFFFFF", "position": "top_right",
                    "outline_color": "#101010", "outline_width": 3,
                    "background_color": "#000000@0.5", "background_padding": 6,
                }],
                subtitles=[{"start": 0.5, "end": 2, "text": "L’azione è qui?", "position": "bottom_center"}],
            )
            timeline = load_timeline(path)
            self.assertEqual(timeline.texts[0].text, "Caffè — già!")
            self.assertTrue(timeline.texts[0].font.is_file())
            self.assertEqual(timeline.texts[0].position, "top_right")
            self.assertEqual(timeline.texts[0].background_color, "#000000@0.5")
            self.assertEqual(timeline.subtitles[0].text, "L’azione è qui?")
            self.assertEqual(timeline.subtitles[0].position, "bottom_center")

    def test_rejects_invalid_timed_text_position_and_interval(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            invalid = [
                [{"start": 0, "end": 0.5, "text": "Titolo", "position": "somewhere"}],
                [{"start": 0.25, "end": 0.55, "text": "Fuori tempo"}],
            ]
            for texts in invalid:
                with self.subTest(texts=texts):
                    path = write_timeline(folder, texts=texts)
                    with self.assertRaisesRegex(ValueError, "texts"):
                        load_timeline(path)

    def test_loads_and_frame_aligns_transitions(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = write_timeline(
                folder,
                canvas={"width": 160, "height": 96, "fps": 25},
                scenes=[
                    {"id": "first", "type": "image", "asset": "fixtures/images/01.png", "duration": 1},
                    {"id": "second", "type": "image", "asset": "fixtures/images/02.png", "duration": 1},
                    {"id": "third", "type": "image", "asset": "fixtures/images/01.png", "duration": 1},
                ],
                transitions={
                    "start": {"type": "fade_from_black", "duration": 0.101},
                    "between": [
                        {"after": "first", "type": "crossfade", "duration": 0.126},
                        {"after": "second", "type": "cut"},
                    ],
                    "end": {"type": "fade_to_black", "duration": 0.201},
                },
            )
            timeline = load_timeline(path)
            self.assertEqual(timeline.transitions.start.duration, 0.12)
            self.assertEqual(timeline.transitions.between[0].duration, 0.12)
            self.assertEqual(timeline.transitions.between[1].type, "cut")
            self.assertEqual(timeline.transitions.end.duration, 0.2)
            self.assertAlmostEqual(3 - timeline.transitions.between[0].duration, 2.88)

    def test_rejects_crossfade_overlap_longer_than_a_scene(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = write_timeline(
                folder,
                canvas={"width": 160, "height": 96, "fps": 20},
                scenes=[
                    {"id": "first", "type": "image", "asset": "fixtures/images/01.png", "duration": 0.2},
                    {"id": "second", "type": "image", "asset": "fixtures/images/02.png", "duration": 0.2},
                ],
                transitions={"between": [{"after": "first", "type": "crossfade", "duration": 0.3}]},
            )
            with self.assertRaisesRegex(ValueError, "dissolvenze incrociate"):
                load_timeline(path)


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


class CrossfadeStyleTests(unittest.TestCase):
    def _timeline(self):
        image = Path("frame.png")
        return Timeline(
            (Scene("first", image, "image", 1.0), Scene("second", image, "image", 1.0)),
            None,
            transitions=TransitionSettings(between=(Transition("crossfade", 0.4, "first"),)),
        )

    def test_transition_graph_keeps_fade_default_and_accepts_selected_style(self):
        timeline = self._timeline()
        default = rendering._build_transition_graph(timeline, [], 1.6)
        selected = rendering._build_transition_graph(timeline, [], 1.6, crossfade_style="wipeleft")
        self.assertTrue(any("xfade=transition=fade:" in item for item in default))
        self.assertTrue(any("xfade=transition=wipeleft:" in item for item in selected))

    def test_renderer_rejects_unsupported_crossfade_style_before_work(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "Stile crossfade non supportato"):
                rendering.render_timeline(self._timeline(), Path(temp) / "out.mp4",
                                          work_root=Path(temp), crossfade_style="not-a-filter")
            self.assertEqual(list(Path(temp).iterdir()), [])


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
        shutil.copytree(PROJECT / "src" / "reel_engine", sandbox / "src" / "reel_engine")
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

    def test_concurrent_renderings_use_separate_work_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = self._make_isolated_engine(root)
            media = root / "media"
            media.mkdir()
            image = media / "frame.png"
            audio = media / "audio.wav"
            image.touch()
            audio.touch()
            timelines = [
                Timeline((Scene(f"scene_{index}", image, "image", 0.5),), audio)
                for index in (1, 2)
            ]
            barrier = threading.Barrier(2)
            workdirs = []
            lock = threading.Lock()

            def fake_run(command, cwd=None):
                if "-f" not in command:
                    segment = Path(command[-1])
                    segment.write_bytes(b"segment")
                    barrier.wait(timeout=10)
                    with lock:
                        workdirs.append(segment.parent)
                    return

                workdir = Path(cwd)
                concat_file = Path(command[command.index("-i") + 1])
                if not concat_file.is_absolute():
                    concat_file = workdir / concat_file
                contents = concat_file.read_text(encoding="utf-8")
                if "file 'scene_001.mp4'\n" not in contents:
                    raise AssertionError("La lista concat non fa riferimento al segmento locale")
                Path(command[-1]).write_bytes(b"render")

            sandbox_rendering = sandbox / "src" / "reel_engine" / "rendering.py"
            outputs = [root / "render_1.mp4", root / "render_2.mp4"]
            with mock.patch.object(rendering, "__file__", str(sandbox_rendering)), mock.patch.object(
                rendering, "run", side_effect=fake_run
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    jobs = [executor.submit(rendering.render_timeline, timeline, output)
                            for timeline, output in zip(timelines, outputs)]
                    for job in jobs:
                        job.result(timeout=20)

            self.assertEqual(len(workdirs), 2)
            self.assertNotEqual(workdirs[0], workdirs[1])
            self.assertTrue(all(output.is_file() for output in outputs))
            self.assertEqual(list((sandbox / "temp" / "renders").iterdir()), [])

    def test_video_scene_trims_source_in_and_duration_and_renders(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = self._make_isolated_engine(root)
            fixture = root / "video-inputs"
            fixture.mkdir()
            video = _make_synthetic_video(fixture / "clip.mp4", duration=2.0)
            audio = fixture / "audio.wav"
            _write_synthetic_wav(audio, duration=2.0)
            timeline_path = write_timeline(
                fixture,
                scenes=[{"id": "trimmed", "type": "video", "asset": str(video),
                         "source_in": 0.7, "duration": 0.6}],
                audio=audio,
                canvas={"width": 160, "height": 96, "fps": 20},
            )
            timeline = load_timeline(timeline_path)
            output = root / "video-scene.mp4"

            result = self._run_cli(sandbox, "--timeline", timeline_path, "--output", output)

            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)
            probe = _probe_video(output)
            stream = probe["streams"][0]
            self.assertEqual(int(stream["nb_read_frames"]), 12)
            self.assertAlmostEqual(_probe_duration(output), 0.6, delta=1 / 20)
            self.assertIn("VIDEO COMPLETATO", result.stdout)

    def test_split_video_source_in_renders_continuously_from_second_half(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = self._make_isolated_engine(root)
            video = _make_segmented_video(root / "split-video.mp4")
            timeline_path = write_timeline_without_audio(
                root,
                canvas={"width": 160, "height": 96, "fps": 20},
                scenes=[
                    {"id": "first", "type": "video", "asset": str(video),
                     "source_in": 0, "duration": 1.5},
                    {"id": "second", "type": "video", "asset": str(video),
                     "source_in": 1.5, "duration": 1.5},
                ],
            )
            timeline = load_timeline(timeline_path)
            self.assertEqual([scene.source_in for scene in timeline.scenes], [0.0, 1.5])
            self.assertEqual([scene.duration for scene in timeline.scenes], [1.5, 1.5])
            output = root / "split-video-render.mp4"
            try:
                self._run_cli(sandbox, "--timeline", timeline_path, "--output", output)
            except subprocess.CalledProcessError as exc:
                self.fail(exc.stderr or exc.stdout or str(exc))

            self.assertAlmostEqual(_probe_duration(output), 3.0, delta=1 / 20)
            blue_frame = _extract_rgb_frame(output, 1.0)
            red_frame = _extract_rgb_frame(output, 2.0)
            blue_pixel = tuple(blue_frame[(48 * 160 + 80) * 3:(48 * 160 + 80) * 3 + 3])
            red_pixel = tuple(red_frame[(48 * 160 + 80) * 3:(48 * 160 + 80) * 3 + 3])
            self.assertGreater(blue_pixel[2], blue_pixel[0] * 2)
            self.assertGreater(red_pixel[0], red_pixel[2] * 2)

    def test_mixed_image_and_video_scenes_render_together(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = self._make_isolated_engine(root)
            media = root / "media"
            images, audio = make_renderable_assets(media)
            video = _make_synthetic_video(media / "clip.mp4", duration=1.0)
            timeline_path = write_timeline(
                root,
                scenes=[
                    {"id": "still", "type": "image", "asset": str(images / "01.png"), "duration": 0.4},
                    {"id": "motion", "type": "video", "asset": str(video), "duration": 0.5, "source_in": 0.2},
                ],
                audio=audio,
                canvas={"width": 160, "height": 96, "fps": 20},
            )
            output = root / "mixed.mp4"
            self._run_cli(sandbox, "--timeline", timeline_path, "--output", output)
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)
            probe = _probe_video(output)
            self.assertEqual(int(probe["streams"][0]["nb_read_frames"]), 18)
            self.assertAlmostEqual(_probe_duration(output), 0.9, delta=1 / 20)

    def test_background_audio_mixes_with_source_trim_volume_fades_and_video_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = self._make_isolated_engine(root)
            fixture = root / "audio-inputs"
            fixture.mkdir()
            image = fixture / "frame.png"
            _write_synthetic_png(image, (90, 120, 180))
            voice = fixture / "voice.wav"
            music = fixture / "music.wav"
            _write_synthetic_wav(voice, duration=2.0, frequency=440, amplitude=3000)
            _write_synthetic_wav(
                music, duration=2.0, frequency=880, amplitude=12000, start_after=0.5
            )
            timeline_path = write_timeline(
                fixture,
                scenes=[{"id": "only", "type": "image", "asset": str(image), "duration": 1.0}],
                audio=voice,
                background_audio={
                    "asset": str(music), "source_in": 0.5, "duration": 0.6,
                    "volume": 0.25, "fade_in": 0.2, "fade_out": 0.2,
                },
                canvas={"width": 160, "height": 96, "fps": 20},
            )
            output = root / "background-audio.mp4"

            self._run_cli(sandbox, "--timeline", timeline_path, "--output", output)

            self.assertTrue(output.is_file())
            self.assertAlmostEqual(_probe_duration(output), 1.0, delta=1 / 20)
            mid = _probe_tone_amplitude(output, 880, 0.25, 0.1)
            fade_in = _probe_tone_amplitude(output, 880, 0.05, 0.1)
            fade_out = _probe_tone_amplitude(output, 880, 0.45, 0.1)
            after_track = _probe_tone_amplitude(output, 880, 0.75, 0.1)
            self.assertGreater(mid, 0.04)
            self.assertLess(mid, 0.15)
            self.assertGreater(fade_in, mid * 0.25)
            self.assertLess(fade_in, mid * 0.8)
            self.assertGreater(fade_out, mid * 0.25)
            self.assertLess(fade_out, mid * 0.8)
            self.assertLess(after_track, mid * 0.15)

    def test_long_background_music_extends_video_with_black_tail_and_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = self._make_isolated_engine(root)
            fixture = root / "music-tail-inputs"
            images, _placeholder_audio = make_renderable_assets(fixture)
            voice = fixture / "short_voice.wav"
            music = fixture / "long_music.wav"
            _write_synthetic_wav(voice, duration=0.35, frequency=440, amplitude=2500)
            _write_synthetic_wav(music, duration=2.0, frequency=880, amplitude=7000)
            timeline_path = write_timeline(
                fixture,
                scenes=[
                    {"id": "first", "type": "image", "asset": str(images / "01.png"), "duration": 0.5},
                    {"id": "second", "type": "image", "asset": str(images / "02.png"), "duration": 0.5},
                ], audio=voice,
                background_audio={"asset": str(music), "volume": 0.5},
                canvas={"width": 160, "height": 96, "fps": 20},
            )
            output = root / "music-tail.mp4"
            self._run_cli(sandbox, "--timeline", timeline_path, "--output", output)

            self.assertAlmostEqual(_probe_duration(output), 2.0, delta=0.06)
            probe = subprocess.check_output([
                shutil.which("ffprobe"), "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                str(output),
            ], text=True)
            self.assertGreaterEqual(float(probe.strip()), 1.95)
            self.assertGreater(_probe_tone_amplitude(output, 880, 1.7, 0.15), 0.04)
            tail = _extract_rgb_frame(output, 1.7)
            self.assertLess(max(tail), 12, "la coda senza scene deve essere nera")

    def test_longer_images_than_music_keep_visual_duration_and_audio_stream(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = self._make_isolated_engine(root)
            fixture = root / "short-music-inputs"
            images, _placeholder_audio = make_renderable_assets(fixture)
            voice = fixture / "short_voice.wav"
            music = fixture / "short_music.wav"
            _write_synthetic_wav(voice, duration=0.3, frequency=440, amplitude=2000)
            _write_synthetic_wav(music, duration=0.45, frequency=880, amplitude=4000)
            timeline_path = write_timeline(
                fixture,
                scenes=[
                    {"id": "first", "type": "image", "asset": str(images / "01.png"), "duration": 0.5},
                    {"id": "second", "type": "image", "asset": str(images / "02.png"), "duration": 0.5},
                ], audio=voice,
                canvas={"width": 160, "height": 96, "fps": 20},
            )
            output = root / "images-longer.mp4"
            self._run_cli(sandbox, "--timeline", timeline_path, "--output", output)

            self.assertAlmostEqual(_probe_duration(output), 1.0, delta=0.06)
            probe = subprocess.check_output([
                shutil.which("ffprobe"), "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                str(output),
            ], text=True)
            self.assertGreaterEqual(float(probe.strip()), 0.95)
            final_image = _extract_rgb_frame(output, 0.9)
            self.assertGreater(sum(final_image) // len(final_image), 20)

    def test_background_music_only_renders_and_extends_video_without_primary_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = self._make_isolated_engine(root)
            fixture = root / "music-only-inputs"
            images, _placeholder_audio = make_renderable_assets(fixture)
            music = fixture / "music.wav"
            _write_synthetic_wav(music, duration=2.0, frequency=880, amplitude=7000)
            timeline_path = write_timeline_without_audio(
                fixture,
                scenes=[
                    {"id": "first", "type": "image", "asset": str(images / "01.png"), "duration": 0.5},
                    {"id": "second", "type": "image", "asset": str(images / "02.png"), "duration": 0.5},
                ],
                background_audio={"asset": str(music), "volume": 0.5},
                canvas={"width": 160, "height": 96, "fps": 20},
            )
            payload = json.loads(timeline_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertIsNone(payload["audio"])
            output = root / "music-only.mp4"
            self._run_cli(sandbox, "--timeline", timeline_path, "--output", output)

            probe = _probe_video(output)
            self.assertEqual(int(probe["streams"][0]["nb_read_frames"]), 40)
            self.assertAlmostEqual(_probe_duration(output), 2.0, delta=0.06)
            stream_info = json.loads(subprocess.check_output([
                shutil.which("ffprobe"), "-v", "error", "-show_entries",
                "stream=codec_type", "-of", "json", str(output),
            ], text=True))
            self.assertEqual({item["codec_type"] for item in stream_info["streams"]}, {"video", "audio"})
            self.assertGreater(_probe_tone_amplitude(output, 880, 1.65, 0.15), 0.04)
            self.assertLess(max(_extract_rgb_frame(output, 1.65)), 12)

    def test_no_audio_v1_timeline_exports_video_without_audio_stream(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = self._make_isolated_engine(root)
            fixture = root / "silent-inputs"
            images, _placeholder_audio = make_renderable_assets(fixture)
            timeline_path = write_timeline_without_audio(
                fixture,
                scenes=[{"id": "still", "type": "image", "asset": str(images / "01.png"), "duration": 1.0}],
                canvas={"width": 160, "height": 96, "fps": 20},
            )
            output = root / "silent.mp4"
            self._run_cli(sandbox, "--timeline", timeline_path, "--output", output)

            self.assertAlmostEqual(_probe_duration(output), 1.0, delta=0.06)
            stream_info = json.loads(subprocess.check_output([
                shutil.which("ffprobe"), "-v", "error", "-show_entries",
                "stream=codec_type", "-of", "json", str(output),
            ], text=True))
            self.assertEqual([item["codec_type"] for item in stream_info["streams"]], ["video"])

    def test_timed_text_and_unicode_subtitle_render_with_windows_font(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = self._make_isolated_engine(root)
            fixture = root / "text-inputs"
            images, audio = make_renderable_assets(fixture)
            timeline_path = write_timeline(
                fixture,
                scenes=[{"id": "only", "type": "image", "asset": str(images / "01.png"), "duration": 1.0}],
                audio=audio,
                canvas={"width": 160, "height": 96, "fps": 20},
                texts=[{
                    "start": 0.2, "end": 0.8, "text": "Caffè, déjà vu — 50%!",
                    "font": "Arial", "size": 22, "color": "white", "position": "top_center",
                    "outline_color": "black", "outline_width": 2,
                }],
                subtitles=[{
                    "start": 0.2, "end": 0.8, "text": "L’azione è qui?",
                    "font": "Arial", "size": 18, "color": "#FFFF00", "position": "bottom_center",
                    "background_color": "black@0.45", "background_padding": 4,
                }],
            )
            output = root / "text-overlay.mp4"

            self._run_cli(sandbox, "--timeline", timeline_path, "--output", output)

            self.assertTrue(output.is_file())
            self.assertAlmostEqual(_probe_duration(output), 1.0, delta=1 / 20)
            before = _extract_rgb_frame(output, 0.05)
            active = _extract_rgb_frame(output, 0.5)
            white_before = sum(
                1 for red, green, blue in zip(before[0::3], before[1::3], before[2::3])
                if red > 220 and green > 220 and blue > 220
            )
            white_active = sum(
                1 for red, green, blue in zip(active[0::3], active[1::3], active[2::3])
                if red > 220 and green > 220 and blue > 220
            )
            self.assertGreater(white_active, white_before + 20)

    def test_crossfade_black_fades_and_audio_text_stay_in_sync(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = self._make_isolated_engine(root)
            fixture = root / "transition-inputs"
            images, _ = make_renderable_assets(fixture)
            video = _make_synthetic_video(fixture / "motion.mp4", duration=1.0)
            voice = fixture / "voice.wav"
            music = fixture / "music.wav"
            _write_synthetic_wav(voice, duration=2.5, frequency=440)
            _write_synthetic_wav(music, duration=2.5, frequency=880, amplitude=5000)
            timeline_path = write_timeline(
                fixture,
                canvas={"width": 160, "height": 96, "fps": 20},
                audio=voice,
                background_audio={"asset": str(music), "volume": 0.15},
                scenes=[
                    {"id": "opener", "type": "image", "asset": str(images / "01.png"), "duration": 0.8},
                    {"id": "motion", "type": "video", "asset": str(video), "source_in": 0.1, "duration": 0.8},
                    {"id": "closer", "type": "image", "asset": str(images / "02.png"), "duration": 0.8},
                ],
                transitions={
                    "start": {"type": "fade_from_black", "duration": 0.2},
                    "between": [
                        {"after": "opener", "type": "crossfade", "duration": 0.2},
                        {"after": "motion", "type": "crossfade", "duration": 0.2},
                    ],
                    "end": {"type": "fade_to_black", "duration": 0.2},
                },
                texts=[{"start": 0.2, "end": 1.9, "text": "Passaggio — Caffè", "size": 20}],
                subtitles=[{"start": 1.3, "end": 1.9, "text": "Scena finale", "size": 16}],
            )
            output = root / "transitions.mp4"

            self._run_cli(sandbox, "--timeline", timeline_path, "--output", output,
                          "--crossfade-style", "wipeleft")

            self.assertTrue(output.is_file())
            probe = _probe_video(output)
            self.assertEqual(int(probe["streams"][0]["nb_read_frames"]), 50)
            self.assertAlmostEqual(_probe_duration(output), 2.5, delta=1 / 20)
            stream_info = json.loads(subprocess.check_output([
                shutil.which("ffprobe"), "-v", "error", "-show_entries",
                "stream=codec_type", "-of", "json", str(output),
            ], text=True))
            self.assertIn("audio", [item["codec_type"] for item in stream_info["streams"]])

            early = _extract_rgb_frame(output, 0.05)
            middle = _extract_rgb_frame(output, 0.4)
            before_end = _extract_rgb_frame(output, 1.6)
            end_fade = _extract_rgb_frame(output, 1.95)
            mean_rgb = lambda frame: sum(frame) / len(frame)
            self.assertLess(mean_rgb(early), mean_rgb(middle) * 0.6)
            self.assertLess(mean_rgb(end_fade), mean_rgb(before_end) * 0.6)
            active_subtitle = _extract_rgb_frame(output, 1.5)
            white_pixels = sum(
                1 for red, green, blue in zip(
                    active_subtitle[0::3], active_subtitle[1::3], active_subtitle[2::3]
                ) if red > 220 and green > 220 and blue > 220
            )
            self.assertGreater(white_pixels, 20)

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
