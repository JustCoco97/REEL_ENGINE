import json
import math
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import wave
import zlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from reel_engine.jobs import REGISTRY_FILENAME, REGISTRY_FORMAT, JobManager, JobRegistryError, JobStatus
from reel_engine.project import create_project, open_project, save_project
from reel_engine.rendering import RenderCancelledError, run


def _write_png(path):
    width, height = 96, 160

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes((60, 140, 220)) * width
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) +
                     chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))


def _write_wav(path, duration=1.5):
    rate = 48000
    frames = bytearray()
    for index in range(int(rate * duration)):
        frames.extend(struct.pack("<h", int(5000 * math.sin(2 * math.pi * 440 * index / rate))))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(frames)


class JobManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reel-jobs-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work_root = self.root / "isolated jobs"
        self.project_dir = self.root / "project"
        self.project_dir.mkdir()
        self.image = self.project_dir / "media" / "frame.png"
        self.audio = self.project_dir / "media" / "voice.wav"
        _write_png(self.image)
        _write_wav(self.audio)
        project = create_project("Job fixture", self.project_dir)
        project.timeline.update({
            "canvas": {"width": 160, "height": 96, "fps": 10},
            "audio": str(self.audio),
            "scenes": [{"id": "opening", "type": "image", "asset": str(self.image), "duration": 1.0}],
        })
        save_project(project)
        self.project_path = project.path

    def _manager(self, **kwargs):
        manager = JobManager(work_root=self.work_root, **kwargs)
        self.addCleanup(manager.shutdown)
        return manager

    def test_job_manager_uses_saved_crossfade_style_from_editor_settings(self):
        project = open_project(self.project_path)
        project.settings["editor"] = {"crossfade_style": "wipeleft"}
        save_project(project)
        received = []

        def renderer(timeline, output, **kwargs):
            received.append(kwargs.get("crossfade_style"))
            output.write_bytes(b"video")

        manager = self._manager(renderer=renderer, quality_check=False)
        job = manager.submit(self.project_path, self.root / "style.mp4")
        self.assertTrue(manager.wait(job.id, 10))
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(received, ["wipeleft"])

    def test_old_project_job_manager_defaults_to_fade_crossfade_style(self):
        received = []

        def renderer(timeline, output, **kwargs):
            received.append(kwargs.get("crossfade_style"))
            output.write_bytes(b"video")

        manager = self._manager(renderer=renderer, quality_check=False)
        job = manager.submit(self.project_path, self.root / "old-project.mp4")
        self.assertTrue(manager.wait(job.id, 10))
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(received, ["fade"])

    def test_real_ffmpeg_render_is_checked_and_published(self):
        updates = []
        manager = self._manager(on_update=lambda job: updates.append((job.phase, job.progress)))
        output = self.root / "final video with spaces.mp4"
        job = manager.submit(self.project_path, output)
        self.assertTrue(manager.wait(job.id, 60))
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(job.progress, 1.0)
        self.assertTrue(output.is_file())
        self.assertGreater(output.stat().st_size, 0)
        self.assertIn("verificato e pubblicato", job.log_path.read_text(encoding="utf-8"))
        self.assertFalse((job.workspace / "render.mp4").exists())
        self.assertTrue(any(phase.startswith("Rendering scena") and percent > 0.01 for phase, percent in updates))
        self.assertTrue(any(phase == "Assemblaggio finale" for phase, _ in updates))

    def test_silent_project_passes_quality_control_and_publishes_video_only(self):
        project = open_project(self.project_path)
        project.timeline["audio"] = None
        save_project(project)
        manager = self._manager()
        output = self.root / "silent-project.mp4"

        job = manager.submit(self.project_path, output)
        self.assertTrue(manager.wait(job.id, 60))
        self.assertEqual(job.status, JobStatus.COMPLETED, job.error)
        self.assertTrue(job.published)
        probe = json.loads(subprocess.check_output([
            shutil.which("ffprobe"), "-v", "error", "-show_entries",
            "stream=codec_type", "-of", "json", str(output),
        ], text=True))
        self.assertEqual([item["codec_type"] for item in probe["streams"]], ["video"])

    def test_completed_history_is_restored_after_manager_restart(self):
        def renderer(timeline, output, **kwargs):
            output.write_bytes(b"completed result")

        first_manager = self._manager(renderer=renderer, quality_check=False)
        original = first_manager.submit(self.project_path, self.root / "saved.mp4")
        self.assertTrue(first_manager.wait(original.id, 5))
        first_manager.shutdown()

        second_manager = self._manager(renderer=renderer, quality_check=False)
        restored = second_manager.get_job(original.id)
        self.assertEqual(restored.status, JobStatus.COMPLETED)
        self.assertEqual(restored.progress, 1.0)
        self.assertEqual(restored.output_path, original.output_path)
        self.assertTrue(restored._done.is_set())
        self.assertTrue(restored.log_path.is_file())

    def _write_registry(self, record):
        document = {"format": REGISTRY_FORMAT, "version": 1, "jobs": [record]}
        (self.work_root / REGISTRY_FILENAME).write_text(json.dumps(document), encoding="utf-8")

    def _registry_record(self, job_id, status, output, *, published=False):
        workspace = self.work_root / job_id
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "job.log").write_text("persisted log\n", encoding="utf-8")
        return {
            "id": job_id, "project_path": str(self.project_path), "output_path": str(output),
            "status": status, "progress": 0.45, "phase": "Rendering scena 1/2", "error": None,
            "created_at": "2026-11-11T10:00:00+00:00", "started_at": "2026-11-11T10:00:01+00:00",
            "finished_at": None, "published": published,
        }

    def test_queued_work_is_restored_and_runs_after_restart(self):
        job_id = "a" * 32
        output = self.root / "resumed.mp4"
        self._write_registry(self._registry_record(job_id, "queued", output))

        def renderer(timeline, staged, **kwargs):
            staged.write_bytes(b"resumed result")

        manager = self._manager(renderer=renderer, quality_check=False)
        job = manager.get_job(job_id)
        self.assertTrue(manager.wait(job_id, 5))
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(output.read_bytes(), b"resumed result")

    def test_running_job_is_marked_interrupted_and_not_retried(self):
        job_id = "b" * 32
        output = self.root / "interrupted.mp4"
        self._write_registry(self._registry_record(job_id, "running", output))
        called = []

        def renderer(*args, **kwargs):
            called.append(True)

        manager = self._manager(renderer=renderer, quality_check=False)
        job = manager.get_job(job_id)
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertEqual(job.phase, "Interrotto dal riavvio")
        self.assertIn("non è stato ripreso", job.error)
        self.assertTrue(job._done.is_set())
        self.assertFalse(called)

    def test_published_result_is_reconciled_if_shutdown_preceded_terminal_record(self):
        job_id = "c" * 32
        output = self.root / "already-published.mp4"
        output.write_bytes(b"verified output")
        self._write_registry(self._registry_record(job_id, "running", output, published=True))
        manager = self._manager(renderer=lambda *args, **kwargs: self.fail("non deve ripartire"))
        job = manager.get_job(job_id)
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(job.progress, 1.0)

    def test_corrupt_persistent_registry_is_reported(self):
        self.work_root.mkdir(parents=True)
        (self.work_root / REGISTRY_FILENAME).write_text("{broken", encoding="utf-8")
        with self.assertRaises(JobRegistryError):
            JobManager(work_root=self.work_root)

    def test_fifo_queue_states_progress_and_isolated_workspaces(self):
        entered = threading.Event()
        release = threading.Event()
        order = []

        def renderer(timeline, output, *, progress_callback, cancel_event, work_root, line_callback):
            order.append(timeline.scenes[0].id)
            if len(order) == 1:
                entered.set()
                progress_callback("Rendering fixture", 0.4)
                release.wait(5)
            output.write_bytes(b"fake-video")

        # Disable external-file QC for this deterministic queue/status test.
        manager = self._manager(renderer=renderer, quality_check=False)
        first = manager.submit(self.project_path, self.root / "one.mp4")
        self.assertTrue(entered.wait(5))
        second_project = create_project("Second fixture", self.root / "second project")
        second_project.timeline.update({
            "canvas": {"width": 160, "height": 96, "fps": 10},
            "audio": str(self.audio),
            "scenes": [{"id": "second", "type": "image", "asset": str(self.image), "duration": 1.0}],
        })
        save_project(second_project)
        second = manager.submit(second_project.path, self.root / "two.mp4")
        self.assertEqual(first.status, JobStatus.RUNNING)
        self.assertEqual(first.progress, 0.4)
        self.assertEqual(first.phase, "Rendering fixture")
        self.assertEqual(second.status, JobStatus.QUEUED)
        self.assertNotEqual(first.workspace, second.workspace)
        self.assertNotEqual(first.log_path, second.log_path)
        release.set()
        self.assertTrue(manager.wait(first.id, 10))
        self.assertTrue(manager.wait(second.id, 10))
        self.assertEqual(order, ["opening", "second"])
        self.assertEqual((first.status, second.status), (JobStatus.COMPLETED, JobStatus.COMPLETED))

    def test_queued_job_can_be_cancelled_without_starting(self):
        entered = threading.Event()
        release = threading.Event()

        def renderer(timeline, output, **kwargs):
            entered.set()
            release.wait(5)
            output.write_bytes(b"video")

        manager = self._manager(renderer=renderer, quality_check=False)
        first = manager.submit(self.project_path, self.root / "first.mp4")
        self.assertTrue(entered.wait(5))
        second = manager.submit(self.project_path, self.root / "second.mp4")
        self.assertTrue(manager.cancel(second.id))
        self.assertTrue(manager.wait(second.id, 1))
        release.set()
        self.assertTrue(manager.wait(first.id, 10))
        self.assertEqual(second.status, JobStatus.CANCELLED)
        self.assertFalse((self.root / "second.mp4").exists())

    def test_running_job_cancel_api_interrupts_ffmpeg_without_publishing(self):
        executable = shutil.which("ffmpeg")
        self.assertIsNotNone(executable, "FFmpeg è richiesto per questo test")
        entered = threading.Event()

        def renderer(timeline, output, *, cancel_event, **kwargs):
            entered.set()
            run([executable, "-re", "-f", "lavfi", "-i",
                 "testsrc2=size=160x96:rate=10", "-t", "30", "-f", "null", "-"],
                cancel_event=cancel_event)

        manager = self._manager(renderer=renderer, quality_check=False)
        output = self.root / "cancelled.mp4"
        job = manager.submit(self.project_path, output)
        self.assertTrue(entered.wait(5))
        self.assertTrue(manager.cancel(job.id))
        self.assertTrue(manager.wait(job.id, 5))
        self.assertEqual(job.status, JobStatus.CANCELLED)
        self.assertFalse(output.exists())

    def test_running_job_cancellation_interrupts_ffmpeg_process(self):
        executable = shutil.which("ffmpeg")
        self.assertIsNotNone(executable, "FFmpeg è richiesto per questo test")
        cancellation = threading.Event()
        result = []

        def render_slow_test_source():
            try:
                run([executable, "-re", "-f", "lavfi", "-i",
                     "testsrc2=size=160x96:rate=10", "-t", "30", "-f", "null", "-"],
                    cancel_event=cancellation)
            except Exception as exc:
                result.append(exc)

        worker = threading.Thread(target=render_slow_test_source)
        worker.start()
        time.sleep(0.6)
        cancellation.set()
        worker.join(8)
        self.assertFalse(worker.is_alive(), "Il processo FFmpeg non si è arrestato")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], RenderCancelledError)

    def test_failure_keeps_existing_destination_and_reports_error(self):
        def renderer(timeline, output, **kwargs):
            output.write_bytes(b"incomplete")

        manager = self._manager(renderer=renderer)
        output = self.root / "already there.mp4"
        output.write_bytes(b"original publication")
        job = manager.submit(self.project_path, output)
        self.assertTrue(manager.wait(job.id, 20))
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("FFprobe", job.error)
        self.assertEqual(output.read_bytes(), b"original publication")
        self.assertFalse((job.workspace / "render.mp4").exists())
        self.assertIn("Lavoro fallito", job.log_path.read_text(encoding="utf-8"))

    def test_missing_media_fails_before_renderer_and_original_media_is_protected(self):
        called = []

        def renderer(*args, **kwargs):
            called.append(True)

        manager = self._manager(renderer=renderer, quality_check=False)
        project = create_project("Lost", self.root / "lost-project")
        project.timeline["audio"] = str(self.root / "absent.wav")
        save_project(project)
        missing_job = manager.submit(project.path, self.root / "missing.mp4")
        self.assertTrue(manager.wait(missing_job.id, 5))
        self.assertEqual(missing_job.status, JobStatus.FAILED)
        self.assertIn("mancanti", missing_job.error)
        self.assertFalse(called)

        protected_job = manager.submit(self.project_path, self.audio)
        self.assertTrue(manager.wait(protected_job.id, 5))
        self.assertEqual(protected_job.status, JobStatus.FAILED)
        self.assertIn("risorsa originale", protected_job.error)
        self.assertGreater(self.audio.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
