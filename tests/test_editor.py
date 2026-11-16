"""Desktop editor tests, including project persistence and an FFmpeg job render."""

import importlib.util
import copy
import json
import time
import os
import shutil
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from reel_engine.project import create_project, open_project
from reel_engine.validation import load_timeline


PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
if PYSIDE_AVAILABLE:
    from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRectF, QSize, QUrl, Qt
    from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QStyleOptionGraphicsItem

    from reel_engine.gui.editor import TimelineEditorPage, TimelineWidget, timeline_duration, timeline_visual_duration
    from reel_engine.jobs import JobManager, JobStatus
    from reel_engine.waveform import WaveformCache
    from reel_engine.gui.editor import _ClipItem, _WaveformGraphicsItem, _LANE_Y, _LEFT, _SCALE
    from reel_engine.gui.proxies import ProxyCache
    from reel_engine.waveform import WaveformData


def _make_assets(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    image = folder / "caffè_immagine.png"
    video = folder / "scena_video.mp4"
    audio = folder / "voce.wav"
    subprocess.run([
        shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=0x3c78dc:s=160x96", "-frames:v", "1", str(image),
    ], check=True, capture_output=True)
    subprocess.run([
        shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=size=160x96:rate=10:duration=0.6", "-an", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(video),
    ], check=True, capture_output=True)
    subprocess.run([
        shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=4", "-ac", "1", "-ar", "48000", str(audio),
    ], check=True, capture_output=True)
    return image, video, audio


def _make_solid_image(path, color):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
        f"color=c={color}:s=160x96", "-frames:v", "1", str(path),
    ], check=True, capture_output=True)
    return path


def _make_segmented_video(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        shutil.which("ffmpeg"), "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=160x96:r=20:d=1.5",
        "-f", "lavfi", "-i", "color=c=red:s=160x96:r=20:d=1.5",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
        "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True, capture_output=True)
    return path


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 non installato nell'ambiente di test")
class TimelineEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["REEL ENGINE editor tests"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reel-editor-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = create_project("Editor test", self.root / "project", canvas={"width": 160, "height": 96, "fps": 10})
        self.editor = TimelineEditorPage()
        self.editor.set_project(self.project)
        self._request_proxy = self.editor._request_video_proxy
        self.editor._request_video_proxy = lambda _source: None
        self.addCleanup(self.editor.close)
        self.addCleanup(self._release_editor_media)

    def _release_editor_media(self):
        self.editor.stop_playback()
        for player in (self.editor.video_player, *self.editor._crossfade_players,
                       self.editor.voice_player, self.editor.music_player):
            player.stop()
            player.setSource(QUrl())
        self.app.processEvents()
        QTest.qWait(150)

    def test_shutdown_disconnects_history_callbacks_and_stops_timers(self):
        timer = self.editor._trim_preview_timer
        timer.start()
        self.editor.shutdown_for_teardown()
        self.assertFalse(timer.isActive())
        self.assertTrue(self.editor._shutting_down)
        # A late stack notification must not call back into the editor.
        self.editor.undo_stack.canUndoChanged.emit(True)
        self.editor.undo_stack.canRedoChanged.emit(True)
        self.editor.undo_stack.cleanChanged.emit(False)
        self.assertFalse(self.editor._trim_preview_timer.isActive())

    def test_repeated_editor_creation_and_teardown_is_safe(self):
        for _ in range(3):
            editor = TimelineEditorPage()
            editor.set_project(create_project("Teardown", self.root / f"teardown-{_}"))
            editor.shutdown_for_teardown()
            editor.deleteLater()
            QCoreApplication.sendPostedEvents(None)
            self.app.processEvents()

    def test_media_library_import_filter_select_insert_and_persistence(self):
        image, video, _audio = _make_assets(self.root / "library")
        original_scenes = copy.deepcopy(self.editor.timeline.get("scenes", []))
        added = self.editor.add_media_library_paths([image, video])
        self.assertEqual(len(added), 2)
        self.assertEqual(self.editor.timeline.get("scenes", []), original_scenes,
                         "importare nella libreria non deve modificare la timeline")
        self.assertEqual(self.editor.media_list.count(), 2)
        video_key = str(video.resolve()).casefold()
        deadline = time.monotonic() + 15
        while video_key not in self.editor._media_video_thumbnails and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(25)
        self.assertIn(video_key, self.editor._media_video_thumbnails)
        self.assertFalse(self.editor._media_video_thumbnails[video_key].isNull())

        self.editor.media_filter.setCurrentIndex(self.editor.media_filter.findData("video"))
        self.assertEqual(self.editor.media_list.count(), 1)
        self.editor.media_search.setText(video.name)
        self.assertEqual(self.editor.media_list.count(), 1)
        item = self.editor.media_list.item(0)
        self.assertIn("VIDEO", item.text())
        item.setSelected(True)
        self.editor.add_selected_media_to_timeline()
        self.assertEqual(len(self.editor.timeline["scenes"]), 1)
        self.assertEqual(self.editor.timeline["scenes"][0]["asset"], str(video.resolve()))
        self.assertTrue(self.editor.save_current_project())
        reopened = open_project(self.project.path)
        self.assertEqual(set(reopened.settings["editor"]["media_library"]),
                         {str(image.resolve()), str(video.resolve())})

    def test_timeline_fixed_headers_zoom_fit_scroll_and_alignment(self):
        image_a = _make_solid_image(self.root / "zoom" / "a.png", "blue")
        image_b = _make_solid_image(self.root / "zoom" / "b.png", "red")
        self.editor.add_media_paths([image_a, image_b])
        self.editor.timeline["scenes"][0]["duration"] = 60.0
        self.editor._rebuild_timeline()
        self.editor.resize(1100, 760)
        self.editor.show()
        self.app.processEvents()
        view = self.editor.timeline_widget
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().maximum())
        fixed_geometry = self.editor.track_name_column.geometry()
        self.assertEqual([label.text().split(" ·")[0] for label in self.editor.track_name_labels],
                         ["VIDEO", "VOCE / AUDIO", "MUSICA", "TESTI", "SOTTOTITOLI"])
        self.assertEqual(self.editor.track_name_column.geometry(), fixed_geometry)

        initial_scale = view._scale
        QTest.mouseClick(self.editor.zoom_in_button, Qt.MouseButton.LeftButton)
        self.assertGreater(view._scale, initial_scale)
        second = view._scene_clip_items[1]
        self.assertAlmostEqual(second.pos().x(), 60.0 * view._scale)
        QTest.mouseClick(self.editor.zoom_out_button, Qt.MouseButton.LeftButton)
        self.assertAlmostEqual(view._scale, initial_scale)
        QTest.mouseClick(self.editor.zoom_fit_button, Qt.MouseButton.LeftButton)
        self.assertLess(view._scale, initial_scale)
        self.assertEqual(view.horizontalScrollBar().value(), 0)

    def test_export_panel_shows_progress_cancel_history_and_log(self):
        from reel_engine.jobs import JobManager, JobStatus
        from reel_engine.rendering import RenderCancelledError

        image = _make_solid_image(self.root / "job_ui" / "frame.png", "green")
        self.editor.add_media_paths([image])
        started = threading.Event()

        def renderer(_timeline, _output, *, progress_callback=None, cancel_event=None, **_kwargs):
            progress_callback("Codifica video", 0.42)
            started.set()
            while not cancel_event.wait(0.01):
                pass
            raise RenderCancelledError("Annullamento richiesto")

        manager = JobManager(work_root=self.root / "job-work", renderer=renderer, quality_check=False)
        self.addCleanup(manager.shutdown)
        self.editor._job_manager = manager
        job = self.editor.start_render(self.root / "job-output.mp4")
        self.assertTrue(started.wait(5))
        self.app.processEvents()
        self.editor._refresh_job()
        self.assertEqual(self.editor.job_phase_label.text(), "Codifica video")
        self.assertEqual(self.editor.job_progress.value(), 42)
        self.assertTrue(self.editor.cancel_job_button.isEnabled())
        QTest.mouseClick(self.editor.cancel_job_button, Qt.MouseButton.LeftButton)
        self.assertTrue(manager.wait(job.id, 10))
        self.app.processEvents()
        self.editor._refresh_job()
        self.assertEqual(job.status, JobStatus.CANCELLED)
        self.assertIn("annullata", self.editor.job_status_label.text().casefold())
        self.assertGreater(self.editor.job_history.count(), 0)
        self.assertTrue(self.editor.open_job_log_button.isEnabled())
        self.assertIn("annullamento", self.editor.job_log_view.toPlainText().casefold())
        QTest.mouseClick(self.editor.open_job_log_button, Qt.MouseButton.LeftButton)
        self.assertIs(self.editor.inspector_tabs.currentWidget(), self.editor.export_panel)
        job.status = JobStatus.FAILED
        job.error = "FFmpeg non ha completato il file"
        job.phase = "Controllo video"
        self.editor._refresh_job()
        self.assertIn("FFmpeg non ha completato", self.editor.job_status_label.text())

    def test_copy_paste_image_uses_playhead_cut_and_undo_redo_round_trip(self):
        image, video, _ = _make_assets(self.root / "copy-image-media")
        self.editor.show()
        self.app.processEvents()
        self.editor.add_media_paths([image, video])
        original = copy.deepcopy(self.editor.timeline["scenes"][0])
        view = self.editor.timeline_widget
        self.editor.select_item("scene", 0)
        view.setFocus()
        QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.editor._scene_clipboard, original)

        # The JSON v1 model has no gaps or arbitrary clip positions. A playhead
        # inside a clip therefore maps to the next legal scene boundary.
        self.editor.seek_to(1.0)
        undo_count = self.editor.undo_stack.count()
        before_paste = copy.deepcopy(self.editor.timeline["scenes"])
        QTest.keyClick(view, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        scenes = self.editor.timeline["scenes"]
        self.assertEqual([item["type"] for item in scenes], ["image", "image", "video"])
        pasted = scenes[1]
        self.assertNotEqual(pasted["id"], original["id"])
        self.assertEqual({key: value for key, value in pasted.items() if key != "id"},
                         {key: value for key, value in original.items() if key != "id"})
        self.assertEqual(self.editor._selected, ("scene", 1))
        self.assertAlmostEqual(self.editor._playhead, 3.0)
        self.assertIn("taglio successivo", self.editor.status_label.text())
        self.assertEqual(self.editor.undo_stack.count(), undo_count + 1)

        self.editor.undo()
        self.assertEqual(self.editor.timeline["scenes"], before_paste)
        self.assertEqual(self.editor._selected, ("scene", 0))
        self.editor.redo()
        self.assertEqual(self.editor.timeline["scenes"][1], pasted)
        self.assertTrue(self.editor.save_current_project())
        reopened = open_project(self.project.path)
        self.assertEqual(reopened.timeline["scenes"], self.editor.timeline["scenes"])

    def test_duplicate_video_preserves_properties_transitions_and_other_tracks(self):
        _, video, audio = _make_assets(self.root / "copy-video-media")
        self.editor.add_media_paths([video])
        self.editor.add_audio_path(audio)
        self.editor.add_audio_path(audio, background=True)
        self.editor.add_overlay("text")
        self.editor.add_overlay("subtitle")
        timeline = copy.deepcopy(self.editor.timeline)
        timeline["scenes"][0].update(duration=0.4, source_in=0.1)
        timeline["transitions"] = {"end": {"type": "fade_to_black", "duration": 0.1}}
        self.editor.timeline = timeline
        self.project.timeline = timeline
        self.editor._rebuild_timeline()
        self.editor.select_item("scene", 0)
        original = copy.deepcopy(self.editor.timeline["scenes"][0])
        tracks = {key: copy.deepcopy(self.editor.timeline.get(key)) for key in
                  ("audio", "background_audio", "texts", "subtitles", "transitions")}
        undo_count = self.editor.undo_stack.count()
        self.editor.show()
        self.app.processEvents()
        self.editor.timeline_widget.setFocus()
        QTest.keyClick(self.editor.timeline_widget, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(self.editor.timeline_widget, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self.editor.timeline["scenes"]), 2)
        self.assertEqual(self.editor._selected, ("scene", 0))
        self.editor.timeline_widget.setFocus()
        QTest.keyClick(self.editor.timeline_widget, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)

        scenes = self.editor.timeline["scenes"]
        self.assertEqual(len(scenes), 3)
        self.assertNotEqual(scenes[0]["id"], original["id"])
        pasted = scenes[0]
        duplicate = scenes[1]
        self.assertNotEqual(duplicate["id"], original["id"])
        self.assertEqual({key: value for key, value in pasted.items() if key != "id"},
                         {key: value for key, value in original.items() if key != "id"})
        self.assertEqual({key: value for key, value in duplicate.items() if key != "id"},
                         {key: value for key, value in original.items() if key != "id"})
        self.assertEqual(scenes[2], original)
        self.assertEqual(self.editor._selected, ("scene", 1))
        self.assertAlmostEqual(self.editor._playhead, 0.4)
        self.assertEqual({key: self.editor.timeline.get(key) for key in tracks}, tracks)
        self.assertEqual(self.editor.undo_stack.count(), undo_count + 2)
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline["scenes"], [pasted, original])
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline["scenes"], [original])
        self.assertTrue(self.editor.redo())
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline["scenes"][1], duplicate)
        self.assertTrue(self.editor.save_current_project())
        reopened = open_project(self.project.path)
        self.assertEqual(reopened.timeline["scenes"], self.editor.timeline["scenes"])
        self.assertEqual(reopened.timeline["transitions"], tracks["transitions"])

    def test_clip_copy_operations_remain_compatible_with_trim_split_and_snap(self):
        image, _, _ = _make_assets(self.root / "copy-compat-media")
        self.editor.add_media_paths([image])
        self.editor.select_item("scene", 0)
        self.assertTrue(self.editor.copy_selected_scene())
        self.assertTrue(self.editor.duplicate_selected_scene())
        self.assertTrue(self.editor.timeline_widget.snap_enabled)
        self.assertTrue(self.editor.trim_scene(1, "right", 1))
        self.assertAlmostEqual(self.editor.timeline["scenes"][1]["duration"], 3.1)
        self.editor.seek_to(self.editor._scene_starts[1] + 0.1)
        self.assertTrue(self.editor.split_selected())
        scenes = self.editor.timeline["scenes"]
        self.assertEqual(len(scenes), 3)
        self.assertEqual(Path(scenes[1]["asset"]).resolve(), image.resolve())
        self.assertEqual(Path(scenes[2]["asset"]).resolve(), image.resolve())
        self.assertAlmostEqual(scenes[1]["duration"] + scenes[2]["duration"], 3.1)
        self.assertTrue(self.editor.undo())
        self.assertTrue(self.editor.undo())
        self.assertTrue(self.editor.redo())
        self.assertTrue(self.editor.redo())
        self.assertEqual(len(self.editor.timeline["scenes"]), 3)

    def _add_three_scene_ripple_fixture(self, folder):
        images = [
            _make_solid_image(self.root / folder / f"ripple_{index}.png", color)
            for index, color in enumerate(("0x3c78dc", "0xdc3c3c", "0x3cdc78"), start=1)
        ]
        audio = _make_assets(self.root / folder / "audio-assets")[2]
        self.editor.add_media_paths(images)
        self.editor.add_audio_path(audio)
        self.editor.add_audio_path(audio, background=True)
        return images, audio

    def test_ripple_delete_first_scene_shortcut_and_round_trip(self):
        images, _ = self._add_three_scene_ripple_fixture("ripple-first")
        timeline = copy.deepcopy(self.editor.timeline)
        timeline["transitions"] = {
            "start": {"type": "fade_from_black", "duration": 0.5},
            "between": [{"after": "scene_001", "type": "crossfade", "duration": 0.2}],
        }
        timeline["texts"] = [{
            "start": 3.2, "end": 4.0, "text": "dopo il taglio", "font": "Arial",
            "size": 32, "color": "#FFFFFF", "position": "center",
            "outline_color": "black", "outline_width": 2,
        }]
        self.editor.timeline = timeline
        self.project.timeline = timeline
        self.editor._rebuild_timeline()
        self.editor.select_item("scene", 0)
        original = copy.deepcopy(self.editor.timeline)
        self.editor.show()
        self.app.processEvents()
        self.editor.timeline_widget.setFocus()
        count = self.editor.undo_stack.count()
        QTest.keyClick(self.editor.timeline_widget, Qt.Key.Key_Delete,
                       Qt.KeyboardModifier.ShiftModifier)

        self.assertEqual([Path(scene["asset"]).resolve() for scene in self.editor.timeline["scenes"]],
                         [images[1].resolve(), images[2].resolve()])
        self.assertAlmostEqual(self.editor.timeline["texts"][0]["start"], 0.4)
        self.assertAlmostEqual(self.editor.timeline["texts"][0]["end"], 1.2)
        self.assertEqual(self.editor.timeline["transitions"]["between"], [])
        self.assertEqual(self.editor.timeline["transitions"]["start"]["duration"], 0.5)
        self.assertEqual(self.editor.timeline["audio"], original["audio"])
        self.assertEqual(self.editor.timeline["background_audio"], original["background_audio"])
        self.assertEqual(self.editor._selected, ("scene", 0))
        self.assertAlmostEqual(self.editor._playhead, 0.0)
        self.assertEqual(self.editor.undo_stack.count(), count + 1)
        result = copy.deepcopy(self.editor.timeline)
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline, original)
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline, result)
        self.assertTrue(self.editor.save_current_project())
        self.assertEqual(open_project(self.project.path).timeline, result)

    def test_ripple_delete_middle_shifts_timed_tracks_and_preserves_global_audio(self):
        _images, audio = self._add_three_scene_ripple_fixture("ripple-middle")
        timeline = copy.deepcopy(self.editor.timeline)
        timeline["transitions"] = {"between": [
            {"after": "scene_001", "type": "crossfade", "duration": 0.2},
            {"after": "scene_002", "type": "crossfade", "duration": 0.2},
        ]}
        template = {
            "text": "cue", "font": "Arial", "size": 32, "color": "#FFFFFF",
            "position": "center", "outline_color": "black", "outline_width": 2,
        }
        timeline["texts"] = [
            {**template, "start": 1.0, "end": 2.0},
            {**template, "text": "cross incoming", "start": 2.8, "end": 3.2},
            {**template, "text": "inside removed", "start": 3.5, "end": 4.5},
            {**template, "text": "cross outgoing", "start": 5.4, "end": 5.8},
            {**template, "text": "after", "start": 7.0, "end": 8.0},
        ]
        timeline["subtitles"] = [
            {**template, "text": "subtitle after", "start": 6.2, "end": 6.8},
        ]
        self.editor.timeline = timeline
        self.project.timeline = timeline
        self.editor._rebuild_timeline()
        self.editor.select_item("scene", 1)
        original = copy.deepcopy(self.editor.timeline)
        undo_count = self.editor.undo_stack.count()

        self.assertTrue(self.editor.ripple_delete_selected())
        result = copy.deepcopy(self.editor.timeline)
        self.assertEqual([scene["id"] for scene in result["scenes"]], ["scene_001", "scene_003"])
        self.assertAlmostEqual(result["texts"][0]["start"], 1.0)
        self.assertAlmostEqual(result["texts"][0]["end"], 2.0)
        self.assertEqual([item["text"] for item in result["texts"]],
                         ["cue", "cross incoming", "cross outgoing", "after"])
        self.assertAlmostEqual(result["texts"][1]["start"], 2.8)
        self.assertAlmostEqual(result["texts"][1]["end"], 3.0)
        self.assertAlmostEqual(result["texts"][2]["start"], 3.0)
        self.assertAlmostEqual(result["texts"][2]["end"], 3.2)
        self.assertAlmostEqual(result["texts"][3]["start"], 4.4)
        self.assertAlmostEqual(result["texts"][3]["end"], 5.4)
        self.assertEqual(len(result["subtitles"]), 1)
        self.assertAlmostEqual(result["subtitles"][0]["start"], 3.6)
        self.assertAlmostEqual(result["subtitles"][0]["end"], 4.2)
        self.assertEqual(result["transitions"]["between"], [])
        self.assertEqual(result["audio"], str(audio.resolve()))
        self.assertEqual(result["background_audio"], original["background_audio"])
        self.assertEqual(self.editor._selected, ("scene", 1))
        self.assertAlmostEqual(self.editor._playhead, 3.0)
        self.assertEqual(self.editor.undo_stack.count(), undo_count + 1)
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline, original)
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline, result)
        self.assertTrue(self.editor.save_current_project())
        self.assertEqual(open_project(self.project.path).timeline, result)

        view = self.editor.timeline_widget
        waveform_key = view.waveform_cache.cache_key(audio)
        self.assertEqual(len(view._waveform_items_by_key[waveform_key]), 2)
        self.assertTrue(view.snap_enabled)
        self.assertTrue(self.editor.copy_selected_scene())
        self.assertTrue(self.editor.duplicate_selected_scene())
        self.assertTrue(self.editor.undo())
        self.assertTrue(self.editor.move_scene(0, 1))
        self.assertTrue(self.editor.undo())
        self.assertTrue(self.editor.trim_scene(1, "right", 1))
        self.assertTrue(self.editor.undo())
        self.editor.seek_to(self.editor._scene_starts[1] + 0.1)
        self.assertTrue(self.editor.split_selected())
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline, result)

    def test_ripple_delete_only_scene_leaves_valid_empty_project_and_undoes(self):
        image = _make_solid_image(self.root / "ripple-only" / "only.png", "0x3c78dc")
        self.editor.add_media_paths([image])
        self.editor.timeline["transitions"] = {
            "start": {"type": "fade_from_black", "duration": 0.5},
            "end": {"type": "fade_to_black", "duration": 0.5},
        }
        self.project.timeline = self.editor.timeline
        self.editor._rebuild_timeline()
        self.editor.select_item("scene", 0)
        original = copy.deepcopy(self.editor.timeline)

        self.assertTrue(self.editor.ripple_delete_selected())
        self.assertEqual(self.editor.timeline["scenes"], [])
        self.assertEqual(self.editor.timeline["transitions"], {})
        self.assertIsNone(self.editor._selected)
        self.assertAlmostEqual(self.editor._playhead, 0.0)
        self.assertEqual(self.editor.image_label.text(), "Importa un'immagine o un video per iniziare")
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline, original)
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline["scenes"], [])
        self.assertTrue(self.editor.save_current_project())
        self.assertEqual(open_project(self.project.path).timeline["scenes"], [])

    def test_ripple_delete_last_scene_preserves_end_fade_and_handles_empty_selection(self):
        _images, _audio = self._add_three_scene_ripple_fixture("ripple-last")
        timeline = copy.deepcopy(self.editor.timeline)
        timeline["transitions"] = {
            "between": [{"after": "scene_002", "type": "crossfade", "duration": 0.2}],
            "end": {"type": "fade_to_black", "duration": 0.6},
        }
        self.editor.timeline = timeline
        self.project.timeline = timeline
        self.editor._rebuild_timeline()
        self.editor.select_item("scene", 2)
        original = copy.deepcopy(self.editor.timeline)

        self.assertTrue(self.editor.ripple_delete_selected())
        result = copy.deepcopy(self.editor.timeline)
        self.assertEqual([scene["id"] for scene in result["scenes"]], ["scene_001", "scene_002"])
        self.assertEqual(result["transitions"]["between"], [])
        self.assertEqual(result["transitions"]["end"], original["transitions"]["end"])
        self.assertEqual(self.editor._selected, ("scene", 1))
        self.assertAlmostEqual(self.editor._playhead, 3.0)
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline, original)
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline, result)
        self.assertTrue(self.editor.save_current_project())
        self.assertEqual(open_project(self.project.path).timeline, result)

    @unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg/FFprobe non disponibili")
    def test_audio_waveform_async_cache_undo_reopen_and_render(self):
        image = _make_solid_image(self.root / "media" / "waveform_frame.png", "0x3c78dc")
        audio = self.root / "media" / "waveform_music.wav"
        subprocess.run([
            shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
            "sine=frequency=660:duration=1.6", "-ac", "1", "-ar", "44100", str(audio),
        ], check=True, capture_output=True, timeout=30)
        self.editor.show()
        self.app.processEvents()
        self.editor.add_media_paths([image])
        view = self.editor.timeline_widget
        view.waveform_cache = WaveformCache(self.root / "waveform-cache")
        self.editor.add_audio_path(audio, background=True)
        key = view.waveform_cache.cache_key(audio)
        self.assertIsNotNone(key)
        waveform_items = view._waveform_items_by_key[key]
        self.assertEqual(len(waveform_items), 1)
        item = waveform_items[0]
        deadline = time.monotonic() + 10
        while item.data is None and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(20)
        self.assertIsNotNone(item.data, item.toolTip())
        cached_data = item.data
        self.assertAlmostEqual(cached_data.duration, 1.6, delta=0.03)
        self.assertGreater(cached_data.bin_count, 150)
        self.assertIn(str(audio.resolve()), view._audio_file_watcher.files())
        bounds = item.boundingRect()
        waveform_image = QImage(max(1, int(bounds.width())), max(1, int(bounds.height())),
                                QImage.Format.Format_ARGB32)
        waveform_image.fill(Qt.GlobalColor.transparent)
        option = QStyleOptionGraphicsItem()
        option.exposedRect = bounds
        painter = QPainter(waveform_image)
        item.paint(painter, option)
        painter.end()
        ink = sum(
            1 for x in range(waveform_image.width()) for y in range(waveform_image.height())
            if waveform_image.pixelColor(x, y).green() > 180
        )
        self.assertGreater(ink, 0, "la waveform deve produrre pixel visibili nella traccia")

        history = self.editor.undo_stack.count()
        self.editor.select_item("background_audio", 0)
        self.editor._property_widgets["source_in"].setValue(0.4)
        self.editor._property_widgets["volume"].setValue(0.45)
        self.editor.apply_properties()
        self.assertEqual(self.editor.undo_stack.count(), history + 1)
        updated = view._waveform_items_by_key[key][0]
        self.assertAlmostEqual(updated.source_in, 0.4)
        self.assertAlmostEqual(updated.volume, 0.45)
        self.assertIs(updated.data, cached_data, "volume/source_in aggiornano la vista, non ricalcolano l'asset")
        self.assertTrue(self.editor.undo())
        undone = view._waveform_items_by_key[key][0]
        self.assertAlmostEqual(undone.source_in, 0.0)
        self.assertAlmostEqual(undone.volume, 0.2)
        self.assertIs(undone.data, cached_data)
        self.assertTrue(self.editor.redo())
        self.assertAlmostEqual(view._waveform_items_by_key[key][0].source_in, 0.4)
        self.assertTrue(self.editor.save_current_project())

        reopened = open_project(self.project.path)
        reopened_editor = TimelineEditorPage()
        self.addCleanup(reopened_editor.close)
        reopened_editor.timeline_widget.waveform_cache = view.waveform_cache
        reopened_editor.set_project(reopened)
        reopened_item = reopened_editor.timeline_widget._waveform_items_by_key[key][0]
        self.assertIs(reopened_item.data, cached_data)
        self.assertAlmostEqual(reopened_item.source_in, 0.4)
        self.assertAlmostEqual(reopened_item.volume, 0.45)

        reopened_editor._job_manager = JobManager(work_root=self.root / "waveform-jobs")
        self.addCleanup(lambda: reopened_editor._job_manager.shutdown(wait=True))
        output = self.root / "waveform_audio_render.mp4"
        job = reopened_editor.start_render(output)
        self.assertTrue(reopened_editor._job_manager.wait(job.id, timeout=90))
        self.assertEqual(job.status, JobStatus.COMPLETED, job.error)
        streams = subprocess.run([
            shutil.which("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type",
            "-of", "csv=p=0", str(output),
        ], capture_output=True, text=True, check=True, timeout=30)
        self.assertIn("audio", streams.stdout.splitlines())
        duration = subprocess.run([
            shutil.which("ffprobe"), "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(output),
        ], capture_output=True, text=True, check=True, timeout=30)
        self.assertAlmostEqual(float(duration.stdout.strip()), 3.0, delta=0.25)

        # External media replacement changes the cache identity and refreshes the open track.
        old_key = key
        replacement = self.root / "media" / "replacement.wav"
        subprocess.run([
            shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
            "sine=frequency=330:duration=0.9", "-ac", "1", "-ar", "44100", str(replacement),
        ], check=True, capture_output=True, timeout=30)
        os.replace(replacement, audio)
        view._audio_file_watcher.fileChanged.emit(str(audio.resolve()))
        deadline = time.monotonic() + 10
        new_key = view.waveform_cache.cache_key(audio)
        while (old_key in view._waveform_items_by_key or new_key not in view._waveform_items_by_key
               or view._waveform_items_by_key[new_key][0].data is None) and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(30)
            new_key = view.waveform_cache.cache_key(audio)
        self.assertNotEqual(new_key, old_key)
        self.assertIn(new_key, view._waveform_items_by_key)
        self.assertAlmostEqual(view._waveform_items_by_key[new_key][0].data.duration, 0.9, delta=0.03)

    def test_waveform_paint_tracks_scroll_zoom_source_offset_and_volume(self):
        encoded = bytearray()
        for _ in range(100):
            encoded.extend(struct.pack("<hh", -4000, 4000))
        for _ in range(100):
            encoded.extend(struct.pack("<hh", -20000, 20000))
        data = WaveformData(2.0, 100, bytes(encoded))
        item = _WaveformGraphicsItem(2 * _SCALE, 22, data=data)

        def render(exposed, zoom=1.0):
            width = max(1, round(exposed.width() * zoom))
            height = max(1, round(exposed.height() * zoom))
            image = QImage(width, height, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.scale(zoom, zoom)
            painter.translate(-exposed.left(), -exposed.top())
            option = QStyleOptionGraphicsItem()
            option.exposedRect = exposed
            item.paint(painter, option)
            painter.end()
            ys = [y for y in range(image.height()) for x in range(image.width())
                  if image.pixelColor(x, y).alpha() > 0]
            return image, (min(ys), max(ys)) if ys else None

        first_view, first_span = render(QRectF(0, 0, _SCALE, 22))
        scrolled_view, scrolled_span = render(QRectF(_SCALE, 0, _SCALE, 22))
        self.assertIsNotNone(first_span)
        self.assertIsNotNone(scrolled_span)
        self.assertGreater(scrolled_span[1] - scrolled_span[0], first_span[1] - first_span[0])

        item.source_in = 1.0
        offset_view, offset_span = render(QRectF(0, 0, _SCALE, 22))
        self.assertEqual(offset_span, scrolled_span, "source_in deve scorrere il segnale, non allungarlo")
        zoomed_view, zoomed_span = render(QRectF(0, 0, _SCALE, 22), zoom=2.0)
        self.assertGreaterEqual(zoomed_view.width(), first_view.width() * 2)
        self.assertGreaterEqual(zoomed_span[1] - zoomed_span[0], offset_span[1] - offset_span[0])
        item.volume = 0.25
        _, quiet_span = render(QRectF(0, 0, _SCALE, 22), zoom=2.0)
        self.assertLess(quiet_span[1] - quiet_span[0], zoomed_span[1] - zoomed_span[0])

    def test_undo_redo_import_delete_buttons_shortcuts_and_dirty_state(self):
        image = _make_solid_image(self.root / "media" / "cronologia.png", "0x3c78dc")
        self.assertFalse(self.editor.undo_button.isEnabled())
        self.assertFalse(self.editor.redo_button.isEnabled())

        self.editor.add_media_paths([image])
        self.assertEqual(self.editor.undo_stack.count(), 1)
        self.assertTrue(self.editor.undo_stack.isClean(), "l'importazione conserva il salvataggio immediato")
        self.assertTrue(self.editor.undo_button.isEnabled())
        QTest.mouseClick(self.editor.undo_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.editor.timeline["scenes"], [])
        self.assertFalse(self.editor.undo_stack.isClean())
        self.assertIn("*", self.editor.project_name.text())
        self.assertEqual(len(open_project(self.project.path).timeline["scenes"]), 1,
                         "Undo non deve salvare automaticamente")

        self.editor.show()
        self.app.processEvents()
        self.editor.timeline_widget.setFocus()
        QTest.keyClick(self.editor.timeline_widget, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self.editor.timeline["scenes"]), 1)
        self.assertTrue(self.editor.undo_stack.isClean())
        self.assertFalse(self.editor.redo_button.isEnabled())
        self.assertEqual(self.editor._selected, ("scene", 0))
        QTest.keyClick(self.editor.timeline_widget, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.editor.timeline["scenes"], [])
        self.assertTrue(self.editor.redo_button.isEnabled())
        QTest.mouseClick(self.editor.redo_button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.editor.timeline["scenes"]), 1)
        self.assertTrue(self.editor.undo_stack.isClean())

        self.editor.delete_selected()
        self.assertEqual(self.editor.timeline["scenes"], [])
        self.assertEqual(self.editor.undo_stack.command(self.editor.undo_stack.index() - 1).text(),
                         "Elimina selezione")
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline["scenes"][0]["asset"], str(image.resolve()))
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline["scenes"], [])

    def test_undo_redo_move_split_trim_and_transition_mapping(self):
        image_a = _make_solid_image(self.root / "media" / "uno.png", "0x3c78dc")
        image_b = _make_solid_image(self.root / "media" / "due.png", "0xdc3c3c")
        image_c = _make_solid_image(self.root / "media" / "tre.png", "0x3cdc78")
        self.editor.add_media_paths([image_a, image_b, image_c])
        self.editor.timeline["transitions"] = {
            "between": [{"after": "scene_001", "type": "crossfade", "duration": 0.2}]
        }
        self.editor.undo_stack.clear()
        self.editor.undo_stack.setClean()
        original = copy.deepcopy(self.editor.timeline)

        self.assertTrue(self.editor.move_scene(0, 1))
        self.assertEqual(self.editor.undo_stack.count(), 1)
        moved = copy.deepcopy(self.editor.timeline)
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline, original)
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline, moved)

        self.editor.select_item("scene", 1)
        self.editor.seek_to(self.editor._scene_starts[1] + 1.5)
        self.assertTrue(self.editor.split_selected())
        self.assertEqual(self.editor.undo_stack.count(), 2)
        split = copy.deepcopy(self.editor.timeline)
        self.assertEqual(split["transitions"]["between"][0]["after"], "scene_split_001")
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline["transitions"], moved["transitions"])
        self.assertEqual(self.editor.timeline["scenes"], moved["scenes"])
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline, split)

        self.assertTrue(self.editor.trim_scene(2, "right", 2))
        self.assertEqual(self.editor.undo_stack.count(), 3)
        trimmed = copy.deepcopy(self.editor.timeline)
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline, split)
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline, trimmed)
        self.assertEqual(self.editor._selected, ("scene", 2))
        self.assertEqual(self.editor._current_scene, 2)

        self.editor.delete_selected()
        self.assertEqual(self.editor.timeline.get("transitions", {}).get("between", []), [])
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline, trimmed)
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline.get("transitions", {}).get("between", []), [])

    def test_undo_redo_audio_text_subtitle_and_property_changes(self):
        image, _video, audio = _make_assets(self.root / "media")
        self.editor.add_media_paths([image])
        baseline_count = self.editor.undo_stack.count()

        self.editor.add_audio_path(audio)
        self.assertEqual(self.editor.undo_stack.count(), baseline_count + 1)
        self.assertEqual(self.editor.timeline["audio"], str(audio.resolve()))
        self.assertTrue(self.editor.undo())
        self.assertIsNone(self.editor.timeline["audio"])
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline["audio"], str(audio.resolve()))

        self.editor.add_audio_path(audio, background=True)
        self.editor._property_widgets["volume"].setValue(0.45)
        self.editor.apply_properties()
        self.assertAlmostEqual(self.editor.timeline["background_audio"]["volume"], 0.45)
        self.assertTrue(self.editor.undo())
        self.assertAlmostEqual(self.editor.timeline["background_audio"]["volume"], 0.2)
        self.assertTrue(self.editor.redo())
        self.assertAlmostEqual(self.editor.timeline["background_audio"]["volume"], 0.45)

        self.editor.add_overlay("text")
        self.editor._property_widgets["text"].setPlainText("Titolo aggiornato")
        self.editor._property_widgets["start"].setValue(0.2)
        self.editor._property_widgets["end"].setValue(2.5)
        self.editor.apply_properties()
        self.assertEqual(self.editor.timeline["texts"][0]["text"], "Titolo aggiornato")
        self.assertEqual((self.editor.timeline["texts"][0]["start"],
                          self.editor.timeline["texts"][0]["end"]), (0.2, 2.5))
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline["texts"][0]["text"], "Nuovo titolo")
        self.assertEqual((self.editor.timeline["texts"][0]["start"],
                          self.editor.timeline["texts"][0]["end"]), (0.0, 3.0))
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline["texts"][0]["text"], "Titolo aggiornato")

        self.editor.add_overlay("subtitle")
        self.editor._property_widgets["text"].setPlainText("Sottotitolo aggiornato")
        self.editor._property_widgets["start"].setValue(0.5)
        self.editor._property_widgets["end"].setValue(2.5)
        self.editor.apply_properties()
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline["subtitles"][0]["text"], "Nuovo sottotitolo")
        self.assertEqual((self.editor.timeline["subtitles"][0]["start"],
                          self.editor.timeline["subtitles"][0]["end"]), (0.0, 3.0))
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline["subtitles"][0]["text"], "Sottotitolo aggiornato")

    def test_undo_redo_video_duration_and_source_in_properties(self):
        _image, video, _audio = _make_assets(self.root / "media")
        self.editor.add_media_paths([video])
        original = copy.deepcopy(self.editor.timeline["scenes"][0])
        self.editor.select_item("scene", 0)
        self.editor._property_widgets["duration"].setValue(0.4)
        self.editor._property_widgets["source_in"].setValue(0.2)
        self.editor.apply_properties()
        self.assertEqual(self.editor.timeline["scenes"][0]["duration"], 0.4)
        self.assertEqual(self.editor.timeline["scenes"][0]["source_in"], 0.2)
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline["scenes"][0], original)
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline["scenes"][0]["duration"], 0.4)
        self.assertEqual(self.editor.timeline["scenes"][0]["source_in"], 0.2)

    def test_save_sets_undo_clean_point_and_opening_another_project_clears_history(self):
        image = _make_solid_image(self.root / "media" / "salvata.png", "0x3c78dc")
        self.editor.add_media_paths([image])
        self.editor.select_item("scene", 0)
        self.editor._property_widgets["duration"].setValue(2.0)
        self.editor.apply_properties()
        self.assertFalse(self.editor.undo_stack.isClean())
        self.assertTrue(self.editor.save_current_project())
        self.assertTrue(self.editor.undo_stack.isClean())

        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline["scenes"][0]["duration"], 3.0)
        self.assertFalse(self.editor.undo_stack.isClean())
        self.assertTrue(self.editor.save_current_project())
        reopened = open_project(self.project.path)
        self.assertEqual(reopened.timeline["scenes"][0]["duration"], 3.0)
        reopened_editor = TimelineEditorPage()
        self.addCleanup(reopened_editor.close)
        reopened_editor.set_project(reopened)
        self.assertEqual(reopened_editor.timeline["scenes"][0]["duration"], 3.0)
        self.assertEqual(reopened_editor.undo_stack.count(), 0)

        other = create_project("Altro progetto", self.root / "other")
        self.editor.set_project(other)
        self.assertEqual(self.editor.undo_stack.count(), 0)
        self.assertFalse(self.editor.undo_button.isEnabled())
        self.assertFalse(self.editor.redo_button.isEnabled())
        self.assertTrue(self.editor.undo_stack.isClean())

    def test_import_tracks_edit_properties_save_and_reopen(self):
        image, video, audio = _make_assets(self.root / "media")
        self.editor.add_media_paths([image, video])
        self.editor.add_audio_path(audio)
        self.editor.add_audio_path(audio, background=True)
        self.editor.select_item("scene", 0)
        self.editor._property_widgets["duration"].setValue(1.2)
        self.editor.apply_properties()
        self.editor.select_item("background_audio", 0)
        self.editor._property_widgets["volume"].setValue(0.35)
        self.editor._property_widgets["fade_in"].setValue(0.2)
        self.editor.apply_properties()
        self.editor.add_overlay("text")
        self.editor._property_widgets["text"].setPlainText("Caffè, déjà vu!")
        self.editor._property_widgets["position"].setText("top_center")
        self.editor._property_widgets["outline_width"].setValue(3)
        self.editor._property_widgets["background_color"].setText("black@0.4")
        self.editor.apply_properties()
        self.editor.add_overlay("subtitle")
        self.editor._property_widgets["text"].setPlainText("Audio sincronizzato")
        self.editor._property_widgets["start"].setValue(0.2)
        self.editor._property_widgets["end"].setValue(1.2)
        self.editor.apply_properties()

        self.assertEqual([scene["type"] for scene in self.editor.timeline["scenes"]], ["image", "video"])
        self.assertEqual(self.editor.timeline["audio"], str(audio.resolve()))
        self.assertEqual(self.editor.timeline["background_audio"]["asset"], str(audio.resolve()))
        self.assertAlmostEqual(self.editor.timeline["background_audio"]["volume"], 0.35)
        self.assertAlmostEqual(self.editor.timeline["background_audio"]["fade_in"], 0.2)
        self.assertEqual(len(self.editor.timeline["texts"]), 1)
        self.assertEqual(self.editor.timeline["texts"][0]["position"], "top_center")
        self.assertEqual(len(self.editor.timeline["subtitles"]), 1)
        self.assertAlmostEqual(self.editor.timeline["scenes"][0]["duration"], 1.2)
        self.editor.seek_to(1.2)
        self.assertEqual(self.editor.video_player.source(), QUrl.fromLocalFile(str(video.resolve())))
        self.editor.seek_to(0.5)
        self.assertGreater(self.editor.overlay_grid.count(), 0)

        # The graphics timeline contains independent selectable clip items for each track.
        from reel_engine.gui.editor import _ClipItem
        kinds = {item.kind for item in self.editor.timeline_widget.scene().items() if isinstance(item, _ClipItem)}
        self.assertTrue({"scene", "audio", "background_audio", "text", "subtitle"}.issubset(kinds))

        self.assertTrue(self.editor.save_current_project())
        timeline_path = self.root / "timeline.json"
        timeline_path.write_text(json.dumps(self.editor.timeline, ensure_ascii=False), encoding="utf-8")
        load_timeline(timeline_path)
        reopened = open_project(self.project.path)
        self.assertEqual([entry["id"] for entry in reopened.timeline["scenes"]], ["scene_001", "scene_002"])
        self.assertAlmostEqual(reopened.timeline["scenes"][0]["duration"], 1.2)
        self.assertEqual(reopened.timeline["texts"][0]["text"], "Caffè, déjà vu!")
        self.assertEqual(reopened.timeline["subtitles"][0]["text"], "Audio sincronizzato")
        saved_asset = reopened.timeline["scenes"][0]["asset"]
        self.assertEqual(Path(saved_asset), image.resolve())
        self.assertEqual(open_project(self.project.path).timeline["audio"], str(audio.resolve()))

    def test_images_select_preview_seek_save_reopen_and_advance_during_playback(self):
        image_a, _video, audio = _make_assets(self.root / "media")
        image_b = _make_solid_image(self.root / "media" / "scena_02.png", "0xdc3c3c")
        self.editor.show()
        self.app.processEvents()
        self.editor.add_media_paths([image_a, image_b])
        self.editor.add_audio_path(audio)

        self.assertEqual(len(self.editor.timeline["scenes"]), 2)
        self.assertEqual(self.editor.timeline["scenes"][0]["asset"], str(image_a.resolve()))
        self.assertEqual(self.editor.timeline["scenes"][1]["asset"], str(image_b.resolve()))
        self.assertEqual(self.editor.preview_layout.currentWidget(), self.editor.image_label)
        self.assertFalse(self.editor.image_label.pixmap().isNull())
        # Import selects the last added item; explicitly select the first and
        # verify its preview before testing the second item as well.
        self.assertLess(self.editor.image_label.pixmap().toImage().pixelColor(80, 48).blue(), 100)
        self.editor.select_item("scene", 0)
        self.assertGreater(self.editor.image_label.pixmap().toImage().pixelColor(80, 48).blue(), 200)

        self.editor.select_item("scene", 0)
        self.editor._property_widgets["duration"].setValue(0.25)
        self.editor.apply_properties()
        position = self.editor.timeline_widget.mapFromScene(
            QPointF(_LEFT + 0.25 * _SCALE + 0.2 * _SCALE, _LANE_Y["scene"] + 24)
        )
        QTest.mouseClick(self.editor.timeline_widget.viewport(), Qt.MouseButton.LeftButton, pos=position)
        self.app.processEvents()
        self.assertEqual(self.editor._selected, ("scene", 1))
        self.assertEqual(self.editor.inspector_title.text(), "PROPRIETÀ SCENA")
        clips = [item for item in self.editor.timeline_widget.scene().items() if isinstance(item, _ClipItem)]
        selected = next(item for item in clips if item.kind == "scene" and item.index == 1)
        unselected = next(item for item in clips if item.kind == "scene" and item.index == 0)
        self.assertTrue(selected.isSelected())
        self.assertGreater(selected.pen().widthF(), unselected.pen().widthF())

        self.editor.seek_to(0.1)
        self.assertEqual(self.editor._current_scene, 0)
        self.assertGreater(self.editor.image_label.pixmap().toImage().pixelColor(80, 48).blue(), 200)
        self.editor.seek_to(0.5)
        self.assertEqual(self.editor._current_scene, 1)
        self.assertGreater(self.editor.image_label.pixmap().toImage().pixelColor(80, 48).red(), 200)

        self.editor.seek_to(0)
        self.editor.start_playback()
        QTest.qWait(450)
        self.assertEqual(self.editor._current_scene, 1)
        self.assertEqual(self.editor._selected, ("scene", 1))
        self.assertGreater(self.editor.image_label.pixmap().toImage().pixelColor(80, 48).red(), 200)
        self.editor.stop_playback()

        self.assertTrue(self.editor.save_current_project())
        saved = json.loads(self.project.path.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["timeline"]["scenes"]), 2)
        self.assertEqual(saved["timeline"]["scenes"][0]["asset"], str(image_a.resolve()))
        self.editor.close()
        reopened_editor = TimelineEditorPage()
        self.addCleanup(reopened_editor.close)
        reopened_editor.set_project(open_project(self.project.path))
        reopened_editor.show()
        self.app.processEvents()
        self.assertEqual(len(reopened_editor.timeline["scenes"]), 2)
        self.assertEqual(reopened_editor._current_scene, 0)
        self.assertFalse(reopened_editor.image_label.pixmap().isNull())
        self.assertEqual(Path(reopened_editor.timeline["scenes"][1]["asset"]), image_b.resolve())

    def test_music_continues_across_photos_and_playhead_runs_to_track_end(self):
        image_a, _video, _audio = _make_assets(self.root / "media")
        image_b = _make_solid_image(self.root / "media" / "seconda_foto.png", "0xdc3c3c")
        music = self.root / "media" / "musica_continua.wav"
        subprocess.run([
            shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
            "sine=frequency=660:duration=1.2", "-ac", "1", "-ar", "48000", str(music),
        ], check=True, capture_output=True)
        self.editor.add_media_paths([image_a, image_b])
        for scene in self.editor.timeline["scenes"]:
            scene["duration"] = 0.2
        self.editor._rebuild_timeline()
        self.editor.add_audio_path(music, background=True)
        self.editor._rebuild_timeline()

        self.assertAlmostEqual(timeline_visual_duration(self.editor.timeline), 0.4)
        self.assertIsNone(self.editor.timeline["background_audio"]["duration"])
        self.assertAlmostEqual(timeline_duration(self.editor.timeline), 1.2)

        sync_calls = []
        self.editor._configure_audio_players = lambda seconds, *, play: sync_calls.append((seconds, play))
        self.editor._load_scene_for_playback = lambda: None

        def begin_tail(*, reset_clock=False):
            self.editor.image_timer.stop()
            self.editor._image_clock = None
            self.editor._current_scene = None
            self.editor.image_label.setText("Nessuna immagine in questo intervallo")
            self.editor._tail_start = self.editor._playhead
            self.editor._tail_clock = time.monotonic()

        self.editor._begin_audio_tail = begin_tail
        self.editor._current_scene = 0
        self.editor.start_playback()
        self.assertTrue(self.editor._playing)
        self.assertEqual(sync_calls, [(0.0, True)])

        # Advance the playback state machine through both image boundaries and
        # the empty audio tail without wall-clock sleeps or a live sound device.
        self.editor._image_start_timeline = 0.0
        self.editor._image_clock = time.monotonic() - 0.25
        self.editor._tick_image()
        self.assertAlmostEqual(self.editor._playhead, 0.2)
        self.assertEqual(sync_calls, [(0.0, True)])
        self.editor._current_scene = 1
        self.editor._image_start_timeline = 0.2
        self.editor._image_clock = time.monotonic() - 0.25
        self.editor._tick_image()
        self.assertIsNone(self.editor._current_scene)
        self.assertAlmostEqual(self.editor._playhead, 0.4)
        self.assertEqual(self.editor.image_label.text(), "Nessuna immagine in questo intervallo")
        self.assertEqual(sync_calls, [(0.0, True)], "la musica non va riposizionata al cambio scena")

        self.editor._tail_clock = time.monotonic() - 0.55
        self.editor._tick_image()
        self.assertTrue(self.editor._playing)
        self.assertGreater(self.editor._playhead, 0.9)
        self.assertLess(self.editor._playhead, 1.2)
        self.editor._tail_clock = time.monotonic() - 1.3
        self.editor._tick_image()
        self.assertFalse(self.editor._playing)
        self.assertAlmostEqual(self.editor._playhead, 1.2, delta=0.06)
        self.assertEqual(sync_calls, [(0.0, True)])
        del self.editor._configure_audio_players
        del self.editor._load_scene_for_playback
        del self.editor._begin_audio_tail

    def test_two_imported_photos_are_saved_and_reopened_with_valid_paths(self):
        image_a, _video, _audio = _make_assets(self.root / "media")
        image_b = _make_solid_image(self.root / "media" / "seconda_foto.png", "0xdc3c3c")

        self.editor.add_media_paths([image_a, image_b])
        saved = json.loads(self.project.path.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["timeline"]["scenes"]), 2)
        self.assertEqual(saved["timeline"]["scenes"][0]["asset"], str(image_a.resolve()))
        self.assertEqual(saved["timeline"]["scenes"][1]["asset"], str(image_b.resolve()))
        self.assertTrue(all(Path(scene["asset"]).is_file() for scene in saved["timeline"]["scenes"]))

        reopened = open_project(self.project.path)
        self.assertEqual(len(reopened.timeline["scenes"]), 2)
        self.assertEqual([Path(scene["asset"]) for scene in reopened.timeline["scenes"]],
                         [image_a.resolve(), image_b.resolve()])
        reopened_editor = TimelineEditorPage()
        self.addCleanup(reopened_editor.close)
        reopened_editor.set_project(reopened)
        self.assertFalse(reopened_editor.image_label.pixmap().isNull())
        self.assertEqual(reopened_editor._current_scene, 0)

    def test_import_save_failure_keeps_existing_timeline_unchanged(self):
        from unittest.mock import patch

        image, _video, _audio = _make_assets(self.root / "media")
        with patch("reel_engine.gui.editor.save_project", side_effect=OSError("disco non disponibile")):
            with self.assertRaisesRegex(OSError, "disco non disponibile"):
                self.editor.add_media_paths([image])
        self.assertEqual(self.editor.timeline["scenes"], [])
        self.assertEqual(self.project.timeline["scenes"], [])
        saved = json.loads(self.project.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["timeline"]["scenes"], [])

    def test_empty_preview_uses_message_page_and_image_preview_isolated_from_video_widget(self):
        self.editor.show()
        self.app.processEvents()
        self.assertEqual(self.editor.preview_layout.currentWidget(), self.editor.image_label)
        self.assertIn("Importa", self.editor.image_label.text())
        self.assertFalse(self.editor.video_widget.isVisible())

        image, video, _audio = _make_assets(self.root / "media")
        self.editor.add_media_paths([image, video])
        self.editor.seek_to(0)
        self.assertEqual(self.editor.preview_layout.currentWidget(), self.editor.image_label)
        self.assertFalse(self.editor.image_label.pixmap().isNull())
        self.assertFalse(self.editor.video_widget.isVisible())
        self.editor.seek_to(3.1)
        self.assertEqual(self.editor.preview_layout.currentWidget(), self.editor.video_widget)
        self.assertTrue(self.editor.video_widget.isVisible())
        self.assertTrue(self.editor.overlay_widget.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))

    def test_image_clip_has_cached_thumbnail_and_does_not_decode_again_on_rebuild(self):
        from unittest.mock import patch
        from reel_engine.gui.editor import TimelineWidget

        image_a, _video, _audio = _make_assets(self.root / "media")
        image_b = _make_solid_image(self.root / "media" / "seconda_foto.png", "0xdc3c3c")
        timeline = {"scenes": [
            {"id": "scene_001", "type": "image", "asset": str(image_a), "duration": 3.0},
            {"id": "scene_002", "type": "image", "asset": str(image_b), "duration": 3.0},
        ]}
        widget = TimelineWidget()
        self.addCleanup(widget.close)
        decode = widget._decode_thumbnail
        with patch.object(widget, "_decode_thumbnail", wraps=decode) as decoder:
            widget.set_timeline(timeline)
            self.assertEqual(decoder.call_count, 2)
            widget.set_timeline(timeline)
            self.assertEqual(decoder.call_count, 2)

        clips = [item for item in widget.scene().items() if isinstance(item, _ClipItem) and item.kind == "scene"]
        self.assertEqual(len(clips), 2)
        thumbnails = [child for clip in clips for child in clip.childItems()
                      if child.__class__.__name__ == "QGraphicsPixmapItem"]
        self.assertEqual(len(thumbnails), 2)
        image_pixels = [item.pixmap().toImage().pixelColor(20, 20) for item in thumbnails]
        self.assertNotEqual(image_pixels[0], image_pixels[1])

    def test_image_crossfade_is_composited_in_preview_at_playhead(self):
        image_a = _make_solid_image(self.root / "media" / "blue.png", "0x3c78dc")
        image_b = _make_solid_image(self.root / "media" / "red.png", "0xdc3c3c")
        self.editor.show()
        self.app.processEvents()
        self.editor.add_media_paths([image_a, image_b])
        self.editor.timeline["transitions"] = {
            "between": [{"after": "scene_001", "type": "crossfade", "duration": 0.2}]
        }
        self.editor._rebuild_timeline()
        self.editor.seek_to(2.9)
        self.app.processEvents()

        image = self.editor.image_label.pixmap().toImage()
        color = image.pixelColor(image.width() // 2, image.height() // 2)
        self.assertGreater(color.red(), 100)
        self.assertGreater(color.blue(), 100)
        self.assertEqual(self.editor.preview_layout.currentWidget(), self.editor.image_label)

    def test_crossfade_style_picker_defaults_to_fade_and_persists_outside_timeline(self):
        from reel_engine.rendering import CROSSFADE_STYLES

        self.assertEqual(self.editor.crossfade_style_combo.currentData(), "fade")
        self.assertEqual(
            {self.editor.crossfade_style_combo.itemData(index)
             for index in range(self.editor.crossfade_style_combo.count())},
            CROSSFADE_STYLES,
        )
        self.editor.crossfade_style_combo.setCurrentIndex(
            self.editor.crossfade_style_combo.findData("wipeleft")
        )
        self.assertIn("*", self.editor.project_name.text())
        self.assertTrue(self.editor.save_current_project())
        document = json.loads(self.project.path.read_text(encoding="utf-8"))
        self.assertEqual(document["settings"]["editor"]["crossfade_style"], "wipeleft")
        self.assertEqual(document["timeline"]["schema_version"], 1)
        self.assertEqual(document["timeline"]["scenes"], [])

        reopened = TimelineEditorPage()
        self.addCleanup(reopened.close)
        reopened.set_project(open_project(self.project.path))
        self.assertEqual(reopened._crossfade_style, "wipeleft")
        self.assertEqual(reopened.crossfade_style_combo.currentData(), "wipeleft")

    def test_selected_wipe_style_changes_preview_pixels(self):
        from reel_engine.gui.editor import _crossfade_frame

        outgoing = QImage(64, 32, QImage.Format.Format_RGB32)
        outgoing.fill(QColor("blue"))
        incoming = QImage(64, 32, QImage.Format.Format_RGB32)
        incoming.fill(QColor("red"))
        frame = _crossfade_frame([outgoing, incoming], QSize(64, 32), 0.5, "wipeleft")
        self.assertEqual(frame.pixelColor(8, 16), QColor("red"))
        self.assertEqual(frame.pixelColor(56, 16), QColor("blue"))

    def test_all_supported_crossfade_styles_compose_preview_frames(self):
        from reel_engine.gui.editor import _crossfade_frame
        from reel_engine.rendering import CROSSFADE_STYLES

        outgoing = QImage(64, 32, QImage.Format.Format_RGB32)
        outgoing.fill(QColor("blue"))
        incoming = QImage(64, 32, QImage.Format.Format_RGB32)
        incoming.fill(QColor("red"))
        for style in CROSSFADE_STYLES:
            with self.subTest(style=style):
                frame = _crossfade_frame([outgoing, incoming], QSize(64, 32), 0.5, style)
                self.assertFalse(frame.isNull())
                self.assertEqual(frame.size(), QSize(64, 32))

    def test_video_crossfade_preview_composites_all_image_video_combinations(self):
        from unittest.mock import patch

        image_a = _make_solid_image(self.root / "media" / "crossfade_blue.png", "0x3c78dc")
        image_b = _make_solid_image(self.root / "media" / "crossfade_red.png", "0xdc3c3c")
        _image, video, _audio = _make_assets(self.root / "video-media")
        source_images = {
            "image": (image_a, image_b),
            "video": (video, video),
        }
        self.editor.show()
        self.app.processEvents()

        for first_type, second_type in (("video", "video"), ("image", "video"), ("video", "image")):
            with self.subTest(first=first_type, second=second_type):
                first_asset = source_images[first_type][0]
                second_asset = source_images[second_type][1]
                self.editor.timeline["scenes"] = [
                    {"id": "fade_a", "type": first_type, "asset": str(first_asset), "duration": 3.0},
                    {"id": "fade_b", "type": second_type, "asset": str(second_asset), "duration": 3.0},
                ]
                self.editor.timeline["transitions"] = {
                    "between": [{"after": "fade_a", "type": "crossfade", "duration": 0.2}]
                }
                self.editor._rebuild_timeline()
                self.editor._playhead = 2.9
                self.editor._crossfade_key = None
                self.editor._crossfade_frames = [QImage(), QImage()]

                video_url = QUrl.fromLocalFile(str(Path(video).resolve()))
                with patch.object(self.editor, "_configure_crossfade_sources"), \
                        patch.object(self.editor._crossfade_players[0], "source", return_value=video_url), \
                        patch.object(self.editor._crossfade_players[1], "source", return_value=video_url):
                    self.assertTrue(self.editor._show_image_crossfade(2.9))
                    for slot, kind in enumerate((first_type, second_type)):
                        if kind == "video":
                            frame = QImage(16, 16, QImage.Format.Format_RGB32)
                            frame.fill(Qt.GlobalColor.blue if slot == 0 else Qt.GlobalColor.red)
                            class FakeVideoFrame:
                                def isValid(self):
                                    return True

                                def toImage(self):
                                    return frame

                            self.editor._crossfade_frame_changed(slot, FakeVideoFrame())
                    self.assertTrue(self.editor._show_image_crossfade(2.9))

                image = self.editor.image_label.pixmap().toImage()
                color = image.pixelColor(image.width() // 2, image.height() // 2)
                self.assertGreater(color.red(), 100)
                self.assertGreater(color.blue(), 100)

    def test_video_crossfade_seeks_both_sources_to_the_timeline_time(self):
        from unittest.mock import patch

        _image, video, _audio = _make_assets(self.root / "sync-media")
        self.editor.timeline["scenes"] = [
            {"id": "out", "type": "video", "asset": str(video), "source_in": 0.5, "duration": 3.0},
            {"id": "in", "type": "video", "asset": str(video), "source_in": 1.0, "duration": 3.0},
        ]
        self.editor.timeline["transitions"] = {
            "between": [{"after": "out", "type": "crossfade", "duration": 0.2}]
        }
        self.editor._rebuild_timeline()
        self.editor._crossfade_last_sync = 0
        players = self.editor._crossfade_players
        with patch.object(players[0], "position", return_value=0), \
                patch.object(players[1], "position", return_value=0), \
                patch.object(players[0], "setPosition") as seek_out, \
                patch.object(players[1], "setPosition") as seek_in:
            self.editor._sync_crossfade_players(0, 2.9)

        seek_out.assert_called_once_with(3400)
        seek_in.assert_called_once_with(1100)

    def test_proxy_toggle_changes_preview_source_but_preserves_original_project_asset(self):
        _image, video, _audio = _make_assets(self.root / "proxy-media")
        self.editor._proxy_cache = ProxyCache(self.project.folder)
        proxy = self.editor._proxy_cache.generate(video)
        self.editor.timeline["scenes"] = [
            {"id": "scene_proxy", "type": "video", "asset": str(video),
             "source_in": 0.0, "duration": 0.5},
        ]
        self.editor._rebuild_timeline()
        self.editor.show()
        self.app.processEvents()

        self.editor._show_scene(0, 0.0)
        self.assertEqual(Path(self.editor.video_player.source().toLocalFile()), proxy)
        self.assertEqual(self.editor.timeline["scenes"][0]["asset"], str(video))

        self.editor.proxy_checkbox.setChecked(False)
        self.assertEqual(Path(self.editor.video_player.source().toLocalFile()), Path(video))
        self.assertTrue(self.editor.save_current_project())
        reopened = open_project(self.project.path)
        self.assertFalse(reopened.settings["editor"]["use_proxies"])
        self.assertEqual(reopened.timeline["scenes"][0]["asset"], str(video))

    def test_video_import_queues_proxy_generation_without_changing_timeline_asset(self):
        _image, video, _audio = _make_assets(self.root / "async-proxy-media")
        self.editor._request_video_proxy = self._request_proxy
        started = time.monotonic()
        self.editor.add_media_paths([video])
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2.0)
        self.assertEqual(self.editor.timeline["scenes"][0]["asset"], str(video))
        self.assertTrue(self.editor._proxy_jobs)

        deadline = time.monotonic() + 8.0
        proxy = None
        while time.monotonic() < deadline:
            self.app.processEvents()
            proxy = self.editor._proxy_cache.proxy_for(video)
            if proxy is not None:
                break
            QTest.qWait(50)
        self.assertIsNotNone(proxy)
        self.app.processEvents()
        self.assertEqual(self.editor.timeline["scenes"][0]["asset"], str(video))

    def test_track_header_click_keeps_selection_and_timeline_clicks_still_work(self):
        image_a, _video, _audio = _make_assets(self.root / "media")
        image_b = _make_solid_image(self.root / "media" / "scena_02.png", "0xdc3c3c")
        self.editor.show()
        self.app.processEvents()
        self.editor.add_media_paths([image_a, image_b])
        self.editor.select_item("scene", 0)
        self.app.processEvents()

        selected = next(
            item for item in self.editor.timeline_widget.scene().items()
            if isinstance(item, _ClipItem) and item.kind == "scene" and item.index == 0
        )
        initial_playhead = self.editor._playhead
        for label in self.editor.track_name_labels:
            QTest.mouseClick(label, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertEqual(self.editor._selected, ("scene", 0))
            self.assertTrue(selected.isSelected())
            self.assertTrue(self.editor.inspector.isVisible())
            self.assertTrue(self.editor.preview_stack.isVisible())
            self.assertEqual(self.editor._playhead, initial_playhead)

        # A click in an empty time cell still scrubs to that timeline position.
        seek_time = 1.25
        position = self.editor.timeline_widget.mapFromScene(
            QPointF(_LEFT + seek_time * _SCALE, _LANE_Y["text"] + 24)
        )
        QTest.mouseClick(
            self.editor.timeline_widget.viewport(), Qt.MouseButton.LeftButton, pos=position
        )
        self.app.processEvents()
        self.assertAlmostEqual(self.editor._playhead, seek_time, places=2)
        self.assertEqual(self.editor._current_scene, 0)
        self.assertGreater(self.editor.image_label.pixmap().toImage().pixelColor(80, 48).blue(), 200)

        # Clicking the next image clip still selects it and refreshes preview.
        position = self.editor.timeline_widget.mapFromScene(
            QPointF(_LEFT + 3.5 * _SCALE, _LANE_Y["scene"] + 24)
        )
        QTest.mouseClick(
            self.editor.timeline_widget.viewport(), Qt.MouseButton.LeftButton, pos=position
        )
        self.app.processEvents()
        self.assertEqual(self.editor._selected, ("scene", 1))
        self.assertEqual(self.editor._current_scene, 1)
        self.assertTrue(self.editor.inspector.isVisible())
        self.assertGreater(self.editor.image_label.pixmap().toImage().pixelColor(80, 48).red(), 200)

    def test_drag_video_clip_reorders_and_preserves_timed_tracks_and_transitions(self):
        image_a = _make_solid_image(self.root / "media" / "uno.png", "0x3c78dc")
        image_b = _make_solid_image(self.root / "media" / "due.png", "0xdc3c3c")
        image_c = _make_solid_image(self.root / "media" / "tre.png", "0x3cdc78")
        audio = self.root / "media" / "voce.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b"\0\0" * 8000)
        self.editor.add_media_paths([image_a, image_b, image_c])
        scenes = self.editor.timeline["scenes"]
        scenes[0].update(duration=2.0, source_in=0.1)
        scenes[1].update(duration=4.0, source_in=0.2)
        scenes[2].update(duration=3.0, source_in=0.3)
        self.editor.timeline["audio"] = str(audio.resolve())
        self.editor.timeline["background_audio"] = {
            "asset": str(audio.resolve()), "source_in": 0.25, "duration": 6.0,
            "volume": 0.35, "fade_in": 0.5, "fade_out": 0.75,
        }
        self.editor.timeline["texts"] = [{"start": 0.4, "end": 1.4, "text": "Titolo"}]
        self.editor.timeline["subtitles"] = [{"start": 2.2, "end": 3.0, "text": "Sottotitolo"}]
        self.editor.timeline["transitions"] = {
            "between": [{"after": "scene_001", "type": "crossfade", "duration": 0.2}]
        }
        self.editor._rebuild_timeline()
        original_moved_scene = copy.deepcopy(scenes[2])
        original_audio = copy.deepcopy(self.editor.timeline["audio"])
        original_music = copy.deepcopy(self.editor.timeline["background_audio"])
        original_texts = copy.deepcopy(self.editor.timeline["texts"])
        original_subtitles = copy.deepcopy(self.editor.timeline["subtitles"])
        original_transitions = copy.deepcopy(self.editor.timeline["transitions"])

        self.assertTrue(self.editor.move_scene(2, 0))
        self.assertEqual([item["id"] for item in self.editor.timeline["scenes"]],
                         ["scene_003", "scene_001", "scene_002"])
        self.assertEqual(self.editor.timeline["scenes"][0], original_moved_scene)
        self.assertEqual(self.editor.timeline["scenes"][2]["duration"], 4.0)
        self.assertEqual(self.editor.timeline["scenes"][2]["source_in"], 0.2)
        self.assertEqual(self.editor.timeline["transitions"], original_transitions)
        self.assertEqual(self.editor.timeline["audio"], original_audio)
        self.assertEqual(self.editor.timeline["background_audio"], original_music)
        self.assertEqual(self.editor.timeline["texts"], original_texts)
        self.assertEqual(self.editor.timeline["subtitles"], original_subtitles)
        self.assertEqual(self.editor._selected, ("scene", 0))
        self.assertEqual(self.editor._current_scene, 0)
        self.assertAlmostEqual(self.editor._scene_starts[1], 3.0)

        self.assertTrue(self.editor.save_current_project())
        reopened = open_project(self.project.path)
        self.assertEqual([item["id"] for item in reopened.timeline["scenes"]],
                         ["scene_003", "scene_001", "scene_002"])
        self.assertEqual(reopened.timeline["scenes"][0], original_moved_scene)
        self.assertEqual(reopened.timeline["transitions"], original_transitions)
        self.assertEqual(reopened.timeline["audio"], original_audio)
        self.assertEqual(reopened.timeline["background_audio"], original_music)
        self.assertEqual(reopened.timeline["texts"], original_texts)
        self.assertEqual(reopened.timeline["subtitles"], original_subtitles)

    def test_reorder_rejects_crossfade_that_would_become_orphaned(self):
        image_a = _make_solid_image(self.root / "media" / "prima.png", "0x3c78dc")
        image_b = _make_solid_image(self.root / "media" / "seconda.png", "0xdc3c3c")
        self.editor.add_media_paths([image_a, image_b])
        self.editor.timeline["transitions"] = {
            "between": [{"after": "scene_001", "type": "crossfade", "duration": 0.2}]
        }
        self.editor._rebuild_timeline()
        original = copy.deepcopy(self.editor.timeline["scenes"])

        self.assertFalse(self.editor.move_scene(1, 0))
        self.assertEqual(self.editor.timeline["scenes"], original)
        self.assertIn("transizione", self.editor.status_label.text())

    def test_split_image_with_s_shortcut_preserves_timeline_tracks_and_reopens(self):
        image_a = _make_solid_image(self.root / "media" / "prima.png", "0x3c78dc")
        image_b = _make_solid_image(self.root / "media" / "seconda.png", "0xdc3c3c")
        audio = self.root / "media" / "musica.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b"\0\0" * 8000)
        self.editor.add_media_paths([image_a, image_b])
        self.editor.timeline["scenes"][0].update(duration=3.0, source_in=0.2)
        self.editor.timeline["scenes"][1]["duration"] = 3.0
        self.editor.timeline["audio"] = str(audio.resolve())
        self.editor.timeline["background_audio"] = {
            "asset": str(audio.resolve()), "source_in": 0.1, "duration": 5.0,
            "volume": 0.4, "fade_in": 0.2, "fade_out": 0.3,
        }
        self.editor.timeline["texts"] = [{"start": 0.5, "end": 1.5, "text": "Titolo"}]
        self.editor.timeline["subtitles"] = [{"start": 2.5, "end": 3.2, "text": "Sottotitolo"}]
        self.editor.timeline["transitions"] = {
            "between": [{"after": "scene_001", "type": "crossfade", "duration": 0.2}]
        }
        self.editor._rebuild_timeline()
        self.editor.select_item("scene", 0)
        self.editor.seek_to(1.2)
        original_audio = copy.deepcopy(self.editor.timeline["audio"])
        original_music = copy.deepcopy(self.editor.timeline["background_audio"])
        original_texts = copy.deepcopy(self.editor.timeline["texts"])
        original_subtitles = copy.deepcopy(self.editor.timeline["subtitles"])
        original_end = timeline_visual_duration(self.editor.timeline)
        self.editor.show()
        self.editor.timeline_widget.setFocus()
        QTest.keyClick(self.editor.timeline_widget, Qt.Key.Key_S)
        self.app.processEvents()

        scenes = self.editor.timeline["scenes"]
        self.assertEqual([item["id"] for item in scenes],
                         ["scene_001", "scene_split_001", "scene_002"])
        self.assertEqual(scenes[0]["asset"], str(image_a.resolve()))
        self.assertEqual(scenes[1]["asset"], str(image_a.resolve()))
        self.assertAlmostEqual(scenes[0]["duration"], 1.2)
        self.assertAlmostEqual(scenes[1]["duration"], 1.8)
        self.assertEqual(scenes[0]["source_in"], 0.2)
        self.assertEqual(scenes[1]["source_in"], 0.2)
        self.assertAlmostEqual(timeline_visual_duration(self.editor.timeline), original_end)
        self.assertEqual(self.editor.timeline["transitions"]["between"][0]["after"], "scene_split_001")
        self.assertEqual(self.editor.timeline["audio"], original_audio)
        self.assertEqual(self.editor.timeline["background_audio"], original_music)
        self.assertEqual(self.editor.timeline["texts"], original_texts)
        self.assertEqual(self.editor.timeline["subtitles"], original_subtitles)
        self.assertEqual(self.editor._selected, ("scene", 1))
        self.assertEqual(self.editor._current_scene, 1)
        self.assertAlmostEqual(self.editor._playhead, 1.2)

        self.assertTrue(self.editor.save_current_project())
        reopened = open_project(self.project.path)
        self.assertEqual([item["id"] for item in reopened.timeline["scenes"]],
                         ["scene_001", "scene_split_001", "scene_002"])
        self.assertEqual(reopened.timeline["transitions"]["between"][0]["after"], "scene_split_001")
        self.assertEqual(reopened.timeline["texts"], original_texts)
        self.assertEqual(reopened.timeline["subtitles"], original_subtitles)

    def test_split_video_advances_source_in_and_rejects_frame_edges(self):
        _image, video, _audio = _make_assets(self.root / "media")
        self.editor.add_media_paths([video])
        scene = self.editor.timeline["scenes"][0]
        scene.update(duration=0.5, source_in=0.1)
        self.editor._rebuild_timeline()
        self.editor.select_item("scene", 0)
        original = copy.deepcopy(scene)
        self.editor.seek_to(0.04)
        self.assertFalse(self.editor.split_selected())
        self.assertEqual(self.editor.timeline["scenes"], [original])
        self.editor.seek_to(0.3)
        self.assertTrue(self.editor.split_selected())

        first, second = self.editor.timeline["scenes"]
        self.assertAlmostEqual(first["duration"], 0.3)
        self.assertAlmostEqual(second["duration"], 0.2)
        self.assertEqual(first["asset"], second["asset"])
        self.assertAlmostEqual(first["source_in"] + first["duration"], second["source_in"])
        self.assertAlmostEqual(second["source_in"], 0.4)
        self.assertAlmostEqual(first["duration"] + second["duration"], original["duration"])
        self.assertEqual(self.editor._selected, ("scene", 1))

        self.editor.seek_to(timeline_visual_duration(self.editor.timeline) - 0.04)
        before = copy.deepcopy(self.editor.timeline["scenes"])
        self.assertFalse(self.editor.split_selected())
        self.assertEqual(self.editor.timeline["scenes"], before)

    def test_split_video_preview_and_playback_resume_at_source_cut_after_reopen(self):
        video = _make_segmented_video(self.root / "media" / "blu_poi_rosso.mp4")
        self.editor.add_media_paths([video])
        QTest.qWait(250)
        self.app.processEvents()
        self.editor.timeline["scenes"][0]["duration"] = 3.0
        self.editor.timeline["scenes"][0]["source_in"] = 0.0
        self.editor._rebuild_timeline()
        self.editor.select_item("scene", 0)
        self.editor.seek_to(1.5)
        QTest.qWait(100)
        self.app.processEvents()
        self.assertGreaterEqual(self.editor.video_player.position(), 1450)
        self.assertLess(self.editor.video_player.position(), 1600)

        self.assertTrue(self.editor.split_selected())
        self.assertEqual(self.editor.timeline["scenes"][0]["source_in"], 0.0)
        self.assertEqual(self.editor.timeline["scenes"][0]["duration"], 1.5)
        self.assertEqual(self.editor.timeline["scenes"][1]["source_in"], 1.5)
        self.assertEqual(self.editor.timeline["scenes"][1]["duration"], 1.5)
        self.assertTrue(self.editor.save_current_project())

        reopened_editor = TimelineEditorPage()
        self.addCleanup(reopened_editor.close)
        reopened_editor.set_project(open_project(self.project.path))
        QTest.qWait(300)
        reopened_editor.select_item("scene", 1)
        QTest.qWait(120)
        self.app.processEvents()
        self.assertGreaterEqual(reopened_editor.video_player.position(), 1450)
        self.assertLess(reopened_editor.video_player.position(), 1650)

        # Start just before the cut and verify playback advances into the
        # second segment instead of resetting to the start of the source.
        reopened_editor.seek_to(1.4)
        reopened_editor.start_playback()
        QTest.qWait(800)
        position = reopened_editor.video_player.position()
        self.assertGreaterEqual(position, 1500)
        self.assertLess(position, 2600)
        reopened_editor.stop_playback()

    def test_split_rejects_when_existing_crossfade_would_not_fit(self):
        image_a = _make_solid_image(self.root / "media" / "prima.png", "0x3c78dc")
        image_b = _make_solid_image(self.root / "media" / "seconda.png", "0xdc3c3c")
        self.editor.add_media_paths([image_a, image_b])
        self.editor.timeline["scenes"][0]["duration"] = 1.0
        self.editor.timeline["scenes"][1]["duration"] = 2.0
        self.editor.timeline["transitions"] = {
            "between": [{"after": "scene_001", "type": "crossfade", "duration": 0.4}]
        }
        self.editor._rebuild_timeline()
        original_scenes = copy.deepcopy(self.editor.timeline["scenes"])
        original_transitions = copy.deepcopy(self.editor.timeline["transitions"])
        self.editor.select_item("scene", 0)
        self.editor.seek_to(0.8)

        self.assertFalse(self.editor.split_selected())
        self.assertEqual(self.editor.timeline["scenes"], original_scenes)
        self.assertEqual(self.editor.timeline["transitions"], original_transitions)
        self.assertIn("dissolvenze", self.editor.status_label.text())

    def test_trim_right_is_frame_aligned_ripples_and_preserves_nonvideo_tracks(self):
        image_a = _make_solid_image(self.root / "media" / "trim_a.png", "0x3c78dc")
        image_b = _make_solid_image(self.root / "media" / "trim_b.png", "0xdc3c3c")
        _image, _video, audio = _make_assets(self.root / "audio_media")
        self.editor.add_media_paths([image_a, image_b])
        self.editor.add_audio_path(audio)
        self.editor.add_audio_path(audio, background=True)
        self.editor.add_overlay("text")
        self.editor.add_overlay("subtitle")
        self.editor.timeline["transitions"] = {
            "between": [{"after": "scene_001", "type": "crossfade", "duration": 0.5}]
        }
        self.editor._rebuild_timeline()
        self.editor.project.timeline = self.editor.timeline
        before_aux = {key: copy.deepcopy(self.editor.timeline[key]) for key in
                      ("audio", "background_audio", "texts", "subtitles", "transitions")}

        self.assertTrue(self.editor.trim_scene(0, "right", 7))
        scenes = self.editor.timeline["scenes"]
        self.assertEqual(scenes[0]["duration"], 3.7)
        self.assertEqual(scenes[0]["source_in"], 0.0)
        self.assertAlmostEqual(self.editor._scene_starts[1], 3.2)
        self.assertEqual({key: self.editor.timeline[key] for key in before_aux}, before_aux)
        self.assertEqual(self.editor._selected, ("scene", 0))

        self.assertTrue(self.editor.save_current_project())
        reopened = open_project(self.project.path)
        self.assertEqual(reopened.timeline["scenes"], scenes)
        self.assertEqual({key: reopened.timeline[key] for key in before_aux}, before_aux)

    @unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg/FFprobe non disponibili")
    def test_trim_left_video_rolls_cut_and_preserves_source_out(self):
        image = _make_solid_image(self.root / "media" / "precedente.png", "0x3c78dc")
        video = _make_segmented_video(self.root / "media" / "trim_source.mp4")
        self.editor.add_media_paths([image, video])
        previous, scene = self.editor.timeline["scenes"]
        scene["source_in"] = 0.4
        scene["duration"] = 2.6
        self.editor._rebuild_timeline()
        self.editor.project.timeline = self.editor.timeline

        self.assertTrue(self.editor.trim_scene(1, "left", 2))
        previous, result = self.editor.timeline["scenes"]
        self.assertEqual(previous["duration"], 3.2)
        self.assertEqual(result["source_in"], 0.6)
        self.assertEqual(result["duration"], 2.4)
        self.assertEqual(result["source_in"] + result["duration"], 3.0)
        self.assertEqual(self.editor._scene_starts[1], 3.2)
        self.assertAlmostEqual(sum(item["duration"] for item in self.editor.timeline["scenes"]), 5.6)
        self.assertTrue(self.editor.save_current_project())
        reopened = open_project(self.project.path)
        self.assertEqual(reopened.timeline["scenes"][0]["duration"], 3.2)
        self.assertEqual(reopened.timeline["scenes"][1]["source_in"], 0.6)
        self.assertEqual(reopened.timeline["scenes"][1]["duration"], 2.4)

    def test_trim_rejects_less_than_one_frame_and_transition_conflicts(self):
        image_a = _make_solid_image(self.root / "media" / "min_a.png", "0x3c78dc")
        image_b = _make_solid_image(self.root / "media" / "min_b.png", "0xdc3c3c")
        self.editor.add_media_paths([image_a, image_b])
        self.editor.timeline["scenes"][0]["duration"] = 1.0
        self.editor._rebuild_timeline()
        self.assertFalse(self.editor.trim_scene(0, "right", -10))
        self.assertEqual(self.editor.timeline["scenes"][0]["duration"], 1.0)
        self.assertFalse(self.editor.trim_scene(0, "left", 3))
        self.assertEqual(self.editor.timeline["scenes"][0]["duration"], 1.0)
        self.assertTrue(self.editor.trim_scene(1, "left", 3))
        self.assertEqual(self.editor.timeline["scenes"][0]["duration"], 1.3)
        self.assertEqual(self.editor.timeline["scenes"][1]["duration"], 2.7)
        self.assertEqual(self.editor.timeline["scenes"][1]["source_in"], 0.0)
        self.editor.timeline["scenes"][0]["duration"] = 1.0
        self.editor.timeline["scenes"][1]["duration"] = 3.0

        self.editor.timeline["transitions"] = {
            "between": [{"after": "scene_001", "type": "crossfade", "duration": 0.8}]
        }
        self.editor.timeline["scenes"][0]["duration"] = 1.0
        self.editor._rebuild_timeline()
        self.assertFalse(self.editor.trim_scene(0, "right", -3))
        self.assertEqual(self.editor.timeline["scenes"][0]["duration"], 1.0)

    def test_snap_playhead_ruler_toggle_and_pixel_threshold_across_zoom(self):
        image_a = _make_solid_image(self.root / "media" / "snap_a.png", "0x3c78dc")
        image_b = _make_solid_image(self.root / "media" / "snap_b.png", "0xdc3c3c")
        self.editor.show()
        self.app.processEvents()
        self.editor.add_media_paths([image_a, image_b])
        view, viewport = self.editor.timeline_widget, self.editor.timeline_widget.viewport()
        self.assertIsNotNone(view._playhead_handle_item)
        view._timeline["transitions"] = {
            "start": {"type": "fade_from_black", "duration": 0.4},
            "between": [{"after": "scene_001", "type": "crossfade", "duration": 0.5}],
            "end": {"type": "fade_to_black", "duration": 0.3},
        }
        points = view._snap_points(include_playhead=False)
        self.assertIn(0.4, points)
        self.assertIn(2.5, points)
        self.assertIn(3.0, points)
        self.assertIn(5.2, points)
        view._timeline["transitions"] = {}

        # The threshold is measured after the view transform, so it remains 10
        # viewport pixels at both normal and magnified timeline scales.
        for zoom in (1.0, 2.0):
            view.resetTransform()
            view.scale(zoom, zoom)
            candidate = 3.0 + 8.0 / (zoom * _SCALE)
            snapped, did_snap = view._snap_seconds(candidate, include_playhead=False)
            self.assertTrue(did_snap, zoom)
            self.assertEqual(snapped, 3.0)
            candidate = 3.0 + 12.0 / (zoom * _SCALE)
            _, did_snap = view._snap_seconds(candidate, include_playhead=False)
            self.assertFalse(did_snap, zoom)
        view.resetTransform()

        self.editor.seek_to(0.0)
        start = view.mapFromScene(QPointF(_LEFT, 10))
        end = view.mapFromScene(QPointF(_LEFT + 3.08 * _SCALE, 10))
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        move = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), QPointF(viewport.mapToGlobal(end)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, move)
        self.assertEqual(view._playhead_seconds, 3.0)
        self.assertIsNotNone(view._snap_guide)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(self.editor._playhead, 3.0)

        QTest.keyClick(view, Qt.Key.Key_N)
        self.assertFalse(view.snap_enabled)
        self.assertEqual(self.editor.snap_button.text(), "Snap: Disattivo")
        self.editor.seek_to(3.0)
        start = view.mapFromScene(QPointF(_LEFT + 3.0 * _SCALE, 10))
        end = view.mapFromScene(QPointF(_LEFT + 2.72 * _SCALE, 10))
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        move = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), QPointF(viewport.mapToGlobal(end)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, move)
        self.assertEqual(view._playhead_seconds, 2.7)
        self.assertIsNone(view._snap_guide)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
        self.editor.snap_button.click()
        self.assertTrue(view.snap_enabled)

    @unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg/FFprobe non disponibili")
    def test_snap_reorders_to_clip_edge_with_one_undo_and_export_roundtrip(self):
        image_a = _make_solid_image(self.root / "media" / "riordino_a.png", "0x3c78dc")
        image_b = _make_solid_image(self.root / "media" / "riordino_b.png", "0xdc3c3c")
        self.editor.show()
        self.app.processEvents()
        self.editor.add_media_paths([image_a, image_b])
        view, viewport = self.editor.timeline_widget, self.editor.timeline_widget.viewport()
        first = view._scene_clip_items[0].sceneBoundingRect()
        start = view.mapFromScene(first.center())
        end = view.mapFromScene(QPointF(_LEFT + 6.0 * _SCALE + 7, _LANE_Y["scene"] + 24))
        history_before = self.editor.undo_stack.count()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        move = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), QPointF(viewport.mapToGlobal(end)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, move)
        self.assertIsNotNone(view._snap_guide)
        guide_x = view._snap_guide.line().x1()
        self.assertAlmostEqual(guide_x, _LEFT + 6.0 * _SCALE, delta=1.0)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()
        self.assertEqual([s["id"] for s in self.editor.timeline["scenes"]], ["scene_002", "scene_001"])
        self.assertEqual(self.editor.undo_stack.count(), history_before + 1)
        self.assertTrue(self.editor.undo())
        self.assertEqual([s["id"] for s in self.editor.timeline["scenes"]], ["scene_001", "scene_002"])
        self.assertTrue(self.editor.redo())
        self.assertEqual([s["id"] for s in self.editor.timeline["scenes"]], ["scene_002", "scene_001"])
        self.assertTrue(self.editor.save_current_project())
        reopened = open_project(self.project.path)
        self.assertEqual([s["id"] for s in reopened.timeline["scenes"]], ["scene_002", "scene_001"])

        self.editor._job_manager = JobManager(work_root=self.root / "jobs")
        self.addCleanup(lambda: self.editor._job_manager.shutdown(wait=True))
        output = self.root / "snap_reorder.mp4"
        job = self.editor.start_render(output)
        self.assertTrue(self.editor._job_manager.wait(job.id, timeout=90))
        self.assertEqual(job.status, JobStatus.COMPLETED, job.error)
        probe = subprocess.run([
            shutil.which("ffprobe"), "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(output),
        ], capture_output=True, text=True, check=True, timeout=30)
        self.assertAlmostEqual(float(probe.stdout.strip()), 6.0, delta=0.25)

    def test_snap_assists_right_trim_and_roll_trim_at_playhead(self):
        photo = _make_solid_image(self.root / "media" / "roll_precedente.png", "0x3c78dc")
        video = _make_segmented_video(self.root / "media" / "roll_video.mp4")
        self.editor.show()
        self.app.processEvents()
        self.editor.add_media_paths([photo])
        view, viewport = self.editor.timeline_widget, self.editor.timeline_widget.viewport()
        self.editor.seek_to(2.8)
        clip = view._scene_clip_items[0]
        bounds = clip.sceneBoundingRect()
        start = view.mapFromScene(QPointF(bounds.right() - 8, bounds.center().y()))
        end = view.mapFromScene(QPointF(bounds.right() - 22, bounds.center().y()))
        history = self.editor.undo_stack.count()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        move = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), QPointF(viewport.mapToGlobal(end)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, move)
        self.assertIsNotNone(view._snap_guide)
        self.assertAlmostEqual(view._snap_guide.line().x1(), _LEFT + 2.8 * _SCALE, delta=1.0)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
        self.assertAlmostEqual(self.editor.timeline["scenes"][0]["duration"], 2.8)
        self.assertEqual(self.editor.undo_stack.count(), history + 1)
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline["scenes"][0]["duration"], 3.0)

        self.editor.add_media_paths([video])
        self.editor.timeline["scenes"][1]["source_in"] = 0.5
        self.editor.timeline["scenes"][1]["duration"] = 2.5
        self.editor._rebuild_timeline()
        self.editor.project.timeline = self.editor.timeline
        self.editor.seek_to(2.8)
        clip = view._scene_clip_items[1]
        bounds = clip.sceneBoundingRect()
        start = view.mapFromScene(QPointF(bounds.left() + 8, bounds.center().y()))
        end = view.mapFromScene(QPointF(bounds.left() - 6, bounds.center().y()))
        history = self.editor.undo_stack.count()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        move = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), QPointF(viewport.mapToGlobal(end)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, move)
        self.assertIsNotNone(view._snap_guide)
        self.assertAlmostEqual(view._snap_guide.line().x1(), _LEFT + 2.8 * _SCALE, delta=1.0)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
        scenes = self.editor.timeline["scenes"]
        self.assertAlmostEqual(scenes[0]["duration"], 2.8)
        self.assertAlmostEqual(scenes[1]["source_in"], 0.3)
        self.assertAlmostEqual(scenes[1]["duration"], 2.7)
        self.assertEqual(self.editor.undo_stack.count(), history + 1)
        self.assertTrue(self.editor.undo())
        self.assertAlmostEqual(self.editor.timeline["scenes"][0]["duration"], 3.0)
        self.assertTrue(self.editor.redo())
        self.assertAlmostEqual(self.editor.timeline["scenes"][1]["source_in"], 0.3)

    def test_timeline_edge_drag_trims_instead_of_reordering(self):
        image_a = _make_solid_image(self.root / "media" / "gesture_a.png", "0x3c78dc")
        image_b = _make_solid_image(self.root / "media" / "gesture_b.png", "0xdc3c3c")
        self.editor.show()
        self.app.processEvents()
        self.editor.add_media_paths([image_a, image_b])
        view, viewport = self.editor.timeline_widget, self.editor.timeline_widget.viewport()
        start = view.mapFromScene(QPointF(_LEFT + 3 * _SCALE - 8, _LANE_Y["scene"] + 24))
        end = view.mapFromScene(QPointF(_LEFT + 3 * _SCALE + 14, _LANE_Y["scene"] + 24))
        trim_events = []
        view.trim_requested.connect(lambda *args: trim_events.append(args))
        history_before = self.editor.undo_stack.count()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        self.assertEqual((view._pressed_scene_index, view._trim_side), (0, "right"), (start, view.mapToScene(start), view.itemAt(start).parentItem().sceneBoundingRect()))
        drag_event = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), QPointF(viewport.mapToGlobal(end)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        width_before = view._scene_clip_items[0].rect().width()
        timeline_before = copy.deepcopy(self.editor.timeline)
        QApplication.sendEvent(viewport, drag_event)
        QTest.qWait(90)
        self.app.processEvents()
        self.assertAlmostEqual(view._scene_clip_items[0].rect().width(), width_before + 21.6, delta=1.0)
        self.assertEqual(self.editor.timeline, timeline_before, "il modello cambia soltanto al rilascio")
        self.assertEqual(self.editor.undo_stack.count(), history_before)
        self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.SizeHorCursor)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()

        self.assertEqual([scene["id"] for scene in self.editor.timeline["scenes"]],
                         ["scene_001", "scene_002"])
        self.assertEqual(trim_events, [(0, "right", 3)])
        self.assertEqual(self.editor.undo_stack.count(), history_before + 1)
        self.assertAlmostEqual(self.editor.timeline["scenes"][0]["duration"], 3.3)
        self.assertAlmostEqual(self.editor._scene_starts[1], 3.3)
        self.assertEqual(self.editor._selected, ("scene", 0))

    @unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg/FFprobe non disponibili")
    def test_live_trim_photo_video_undo_reopen_and_export(self):
        photo_a = _make_solid_image(self.root / "media" / "foto_live.png", "0x3c78dc")
        video = _make_segmented_video(self.root / "media" / "video_live.mp4")
        photo_b = _make_solid_image(self.root / "media" / "foto_finale.png", "0xdc3c3c")
        self.editor.show()
        self.app.processEvents()
        self.editor.add_media_paths([photo_a, video, photo_b])
        view = self.editor.timeline_widget
        viewport = view.viewport()

        def drag_edge(index, side, frame_delta, expected_width_delta, expected_x_delta=0.0):
            clip = view._scene_clip_items[index]
            before_width = clip.rect().width()
            before_x = clip.pos().x()
            before_timeline = copy.deepcopy(self.editor.timeline)
            before_history = self.editor.undo_stack.count()
            bounds = clip.sceneBoundingRect()
            y = bounds.center().y()
            x = bounds.left() + 8 if side == "left" else bounds.right() - 8
            start = view.mapFromScene(QPointF(x, y))
            move_scene_x = x + frame_delta * _SCALE / 10
            end = view.mapFromScene(QPointF(move_scene_x, y))
            QTest.mouseMove(viewport, start)
            QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
            drag_event = QMouseEvent(
                QEvent.Type.MouseMove, QPointF(end), QPointF(viewport.mapToGlobal(end)),
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(viewport, drag_event)
            QTest.qWait(90)
            self.app.processEvents()
            self.assertAlmostEqual(clip.rect().width(), before_width + expected_width_delta, delta=1.2)
            self.assertAlmostEqual(clip.pos().x(), before_x + expected_x_delta, delta=1.2)
            self.assertEqual(self.editor.timeline, before_timeline)
            self.assertEqual(self.editor.undo_stack.count(), before_history)
            QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
            self.app.processEvents()
            self.assertEqual(self.editor.undo_stack.count(), before_history + 1)

        # Right edge of an image grows two frames; the following video moves live.
        drag_edge(0, "right", 2, 14.4)
        self.assertEqual(self.editor.timeline["scenes"][0]["duration"], 3.2)
        self.assertAlmostEqual(self.editor._scene_starts[1], 3.2)

        # Left edge of the video rolls the shared cut: the previous photo lengthens
        # and the selected video advances its source_in while its right edge stays.
        drag_edge(1, "left", 2, -14.4, 14.4)
        self.assertEqual(self.editor.timeline["scenes"][0]["duration"], 3.4)
        self.assertEqual(self.editor.timeline["scenes"][1]["source_in"], 0.2)
        self.assertEqual(self.editor.timeline["scenes"][1]["duration"], 2.8)
        self.assertAlmostEqual(self.editor._scene_starts[1], 3.4)

        # Right edge of a video shortens by two frames; one undo command reverses it.
        drag_edge(1, "right", -2, -14.4)
        self.assertEqual(self.editor.timeline["scenes"][1]["duration"], 2.6)
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.timeline["scenes"][1]["duration"], 2.8)
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.timeline["scenes"][1]["duration"], 2.6)

        self.assertTrue(self.editor.save_current_project())
        reopened = open_project(self.project.path)
        reopened_editor = TimelineEditorPage()
        self.addCleanup(reopened_editor.close)
        reopened_editor.set_project(reopened)
        self.assertEqual(reopened_editor.timeline["scenes"][0]["duration"], 3.4)
        self.assertEqual(reopened_editor.timeline["scenes"][1]["source_in"], 0.2)
        self.assertEqual(reopened_editor.timeline["scenes"][1]["duration"], 2.6)

        reopened_editor._job_manager = JobManager(work_root=self.root / "jobs")
        self.addCleanup(lambda: reopened_editor._job_manager.shutdown(wait=True))
        output = self.root / "trim_live_export.mp4"
        job = reopened_editor.start_render(output)
        self.assertTrue(reopened_editor._job_manager.wait(job.id, timeout=90),
                        "il render FFmpeg non è terminato")
        self.assertEqual(job.status, JobStatus.COMPLETED, job.error)
        self.assertTrue(output.is_file())
        probe = subprocess.run([
            shutil.which("ffprobe"), "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(output),
        ], capture_output=True, text=True, check=True, timeout=30)
        self.assertAlmostEqual(float(probe.stdout.strip()), 9.0, delta=0.25)

    def test_live_trim_escape_and_invalid_first_scene_restore_original(self):
        image = _make_solid_image(self.root / "media" / "annulla_trim.png", "0x3c78dc")
        self.editor.show()
        self.app.processEvents()
        self.editor.add_media_paths([image])
        view, viewport = self.editor.timeline_widget, self.editor.timeline_widget.viewport()
        clip = view._scene_clip_items[0]
        bounds = clip.sceneBoundingRect()
        start = view.mapFromScene(QPointF(bounds.right() - 8, bounds.center().y()))
        end = view.mapFromScene(QPointF(bounds.right() + 14, bounds.center().y()))
        history_before = self.editor.undo_stack.count()
        original = copy.deepcopy(self.editor.timeline)
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        drag_event = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), QPointF(viewport.mapToGlobal(end)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, drag_event)
        self.assertGreater(clip.rect().width(), 3 * _SCALE)
        QTest.keyClick(view, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertEqual(self.editor.timeline, original)
        self.assertAlmostEqual(view._scene_clip_items[0].rect().width(), 3 * _SCALE)
        self.assertEqual(self.editor.undo_stack.count(), history_before)

        # The first scene has no previous edit point in sequential JSON v1.
        bounds = view._scene_clip_items[0].sceneBoundingRect()
        start = view.mapFromScene(QPointF(bounds.left() + 8, bounds.center().y()))
        end = view.mapFromScene(QPointF(bounds.left() + 22, bounds.center().y()))
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        drag_event = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), QPointF(viewport.mapToGlobal(end)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, drag_event)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(self.editor.timeline, original)
        self.assertEqual(self.editor.undo_stack.count(), history_before)
        self.assertIn("prima scena", self.editor.status_label.text())

    def test_video_drag_requires_pointer_motion_and_drop_reorders(self):
        image_a = _make_solid_image(self.root / "media" / "prima.png", "0x3c78dc")
        image_b = _make_solid_image(self.root / "media" / "seconda.png", "0xdc3c3c")
        self.editor.show()
        self.app.processEvents()
        self.editor.add_media_paths([image_a, image_b])
        view = self.editor.timeline_widget
        viewport = view.viewport()
        first = view.mapFromScene(QPointF(_LEFT + 0.6 * _SCALE, _LANE_Y["scene"] + 24))
        second_right_half = view.mapFromScene(QPointF(_LEFT + 4.5 * _SCALE, _LANE_Y["scene"] + 24))

        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=first)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=first)
        self.app.processEvents()
        self.assertEqual([item["id"] for item in self.editor.timeline["scenes"]],
                         ["scene_001", "scene_002"])
        self.assertEqual(self.editor._selected, ("scene", 0))

        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=first)
        drag_event = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(second_right_half),
            QPointF(viewport.mapToGlobal(second_right_half)), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, drag_event)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=second_right_half)
        self.app.processEvents()
        self.assertEqual([item["id"] for item in self.editor.timeline["scenes"]],
                         ["scene_002", "scene_001"])
        self.assertEqual(self.editor._selected, ("scene", 1))
        self.assertEqual(self.editor._current_scene, 1)

    def test_unreadable_image_shows_asset_path_and_decoder_error(self):
        broken = self.root / "media" / "broken.png"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_bytes(b"not a PNG image")
        self.editor.add_media_paths([broken])
        self.assertEqual(self.editor.preview_layout.currentWidget(), self.editor.image_label)
        self.assertIn("Immagine non leggibile", self.editor.image_label.text())
        self.assertIn(str(broken), self.editor.image_label.text())
        self.assertGreaterEqual(len(self.editor.image_label.text().splitlines()), 2)
        self.assertIn("Immagine non leggibile", self.editor.status_label.text())

    def test_editor_rejects_unsupported_media_and_invalid_overlay_intervals(self):
        unsupported = self.root / "notes.txt"
        unsupported.write_text("not media", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "non supportato"):
            self.editor.add_media_paths([unsupported])
        self.editor.add_overlay("text")
        self.editor._property_widgets["start"].setValue(1.0)
        self.editor._property_widgets["end"].setValue(1.0)
        self.editor._message = lambda *_args: None
        self.editor.apply_properties()
        self.assertGreater(self.editor.timeline["texts"][0]["end"], self.editor.timeline["texts"][0]["start"])

    @unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg/FFprobe non disponibili")
    def test_editor_submits_real_image_video_audio_project_to_job_manager(self):
        image, video, audio = _make_assets(self.root / "render_media")
        self.editor.add_media_paths([image, video])
        self.editor.add_audio_path(audio)
        self.editor.add_overlay("text")
        self.editor.save_current_project()
        self.editor._job_manager = JobManager(work_root=self.root / "jobs")
        self.addCleanup(lambda: self.editor._job_manager.shutdown(wait=True) if self.editor._job_manager else None)
        output = self.root / "rendered.mp4"
        job = self.editor.start_render(output)
        self.assertTrue(self.editor._job_manager.wait(job.id, timeout=60), "il rendering FFmpeg non è terminato")
        self.assertEqual(job.status, JobStatus.COMPLETED, job.error)
        self.assertTrue(job.published)
        self.assertTrue(output.is_file())
        self.assertGreater(output.stat().st_size, 1000)

    def test_track_lock_toggle_buttons_update_state_labels_and_project_dirty(self):
        expected_keys = {"scene", "audio", "background_audio", "text", "subtitle"}
        self.assertEqual(set(self.editor.track_lock_buttons.keys()), expected_keys)
        self.assertEqual(self.editor._track_locks, {k: False for k in expected_keys})

        self.assertNotIn("*", self.editor.project_name.text())

        # Toggle VIDEO track lock
        self.editor.track_lock_buttons["scene"].click()
        self.assertTrue(self.editor._track_locks["scene"])
        self.assertEqual(self.editor.track_lock_buttons["scene"].text(), "VIDEO: BLOCCATA")
        self.assertIn("*", self.editor.project_name.text())
        self.assertIn("Traccia VIDEO bloccata", self.editor.status_label.text())

        # Toggle all other tracks
        for key, label in (("audio", "AUDIO"), ("background_audio", "MUSICA"),
                           ("text", "TESTI"), ("subtitle", "SOTTOTITOLI")):
            self.editor.track_lock_buttons[key].click()
            self.assertTrue(self.editor._track_locks[key])
            self.assertEqual(self.editor.track_lock_buttons[key].text(), f"{label}: BLOCCATA")

        # Save project persists the locks and cleans dirty state
        self.editor.save_current_project()
        self.assertNotIn("*", self.editor.project_name.text())

        # Reopen project and verify persistence
        reopened = open_project(self.editor.project.path)
        self.assertEqual(
            reopened.settings.get("editor", {}).get("track_locks"),
            {k: True for k in expected_keys},
        )
        self.editor.set_project(reopened)
        self.assertEqual(self.editor._track_locks, {k: True for k in expected_keys})
        for key, label in (("scene", "VIDEO"), ("audio", "AUDIO"), ("background_audio", "MUSICA"),
                           ("text", "TESTI"), ("subtitle", "SOTTOTITOLI")):
            self.assertTrue(self.editor.track_lock_buttons[key].isChecked())
            self.assertEqual(self.editor.track_lock_buttons[key].text(), f"{label}: BLOCCATA")

        # Untoggle VIDEO
        self.editor.track_lock_buttons["scene"].click()
        self.assertFalse(self.editor._track_locks["scene"])
        self.assertEqual(self.editor.track_lock_buttons["scene"].text(), "VIDEO: LIBERA")

    def test_track_lock_scene_blocks_mutations_and_inspector(self):
        image_a = _make_solid_image(self.root / "lock_media" / "a.png", "red")
        image_b = _make_solid_image(self.root / "lock_media" / "b.png", "blue")
        self.editor.add_media_paths([image_a])
        self.assertEqual(len(self.editor.timeline["scenes"]), 1)

        # Lock VIDEO
        self.editor._set_track_lock("scene", True)
        self.assertTrue(self.editor._track_is_locked("scene"))
        self.assertTrue(self.editor.add_media_button.isEnabled(), "la libreria non modifica la traccia bloccata")
        self.assertFalse(self.editor.insert_selected_button.isEnabled())

        # 1. add_media_paths raises PermissionError and does not modify
        with self.assertRaises(PermissionError):
            self.editor.add_media_paths([image_b])
        self.assertEqual(len(self.editor.timeline["scenes"]), 1)

        # 2. Importing to the media library remains independent from the lock.
        with patch("reel_engine.gui.editor.QFileDialog.getOpenFileNames", return_value=([str(image_b)], "")):
            self.editor.import_video_or_images()
        self.assertEqual(len(self.editor.timeline["scenes"]), 1)
        self.assertIn(str(image_b.resolve()), self.editor._media_library_paths)

        # 3. Select scene and check Inspector
        self.editor.select_item("scene", 0)
        self.assertIn("[BLOCCATA]", self.editor.inspector_title.text())
        self.assertFalse(self.editor.apply_button.isEnabled())
        self.assertFalse(self.editor.split_button.isEnabled())
        self.assertFalse(self.editor.delete_button.isEnabled())
        self.assertFalse(self.editor.ripple_delete_button.isEnabled())
        for widget in self.editor._property_widgets.values():
            self.assertFalse(widget.isEnabled())

        # 4. apply_properties fails
        self.assertFalse(self.editor.apply_properties())

        # 5. delete_selected fails
        self.assertFalse(self.editor.delete_selected())
        self.assertEqual(len(self.editor.timeline["scenes"]), 1)

        # 6. ripple_delete_selected fails
        self.assertFalse(self.editor.ripple_delete_selected())

        # 7. split_selected fails
        self.assertFalse(self.editor.split_selected())

        # 8. duplicate_selected_scene fails
        self.assertFalse(self.editor.duplicate_selected_scene())

        # 9. copy works, but paste fails
        self.assertTrue(self.editor.copy_selected_scene())
        self.assertFalse(self.editor.paste_scene())

        # 10. trim_scene fails
        self.assertFalse(self.editor.trim_scene(0, "right", 10))

        # 11. move_scene fails
        self.assertFalse(self.editor.move_scene(0, 0))

        # Unlock scene -> modifications work again
        self.editor._set_track_lock("scene", False)
        self.assertTrue(self.editor.add_media_button.isEnabled())
        self.editor.select_item("scene", 0)
        self.assertTrue(self.editor.apply_button.isEnabled())
        self.assertTrue(self.editor.split_button.isEnabled())
        self.assertTrue(self.editor.delete_button.isEnabled())
        self.assertTrue(self.editor.duplicate_selected_scene())
        self.assertEqual(len(self.editor.timeline["scenes"]), 2)

    def test_track_lock_audio_and_music_blocks_and_preserves_other_tracks(self):
        _image, _video, audio = _make_assets(self.root / "audio_lock_media")
        image_clip = _make_solid_image(self.root / "audio_lock_media" / "pic.png", "green")
        self.editor.add_media_paths([image_clip])

        # Lock AUDIO
        self.editor._set_track_lock("audio", True)
        self.assertFalse(self.editor.add_audio_button.isEnabled())
        with self.assertRaises(PermissionError):
            self.editor.add_audio_path(audio, background=False)
        self.assertIsNone(self.editor.timeline.get("audio"))

        # Other tracks can still be edited! (e.g. text overlay)
        self.assertTrue(self.editor.add_overlay("text"))
        self.assertEqual(len(self.editor.timeline["texts"]), 1)

        # Unlock audio and add it
        self.editor._set_track_lock("audio", False)
        self.assertTrue(self.editor.add_audio_button.isEnabled())
        self.editor.add_audio_path(audio, background=False)
        self.assertIsNotNone(self.editor.timeline.get("audio"))

        # Lock audio again and try to delete it
        self.editor._set_track_lock("audio", True)
        self.editor.select_item("audio", 0)
        self.assertFalse(self.editor.delete_button.isEnabled())
        self.assertFalse(self.editor.delete_selected())
        self.assertIsNotNone(self.editor.timeline.get("audio"))

        # Now test background_audio (MUSICA)
        self.editor._set_track_lock("background_audio", True)
        self.assertFalse(self.editor.add_music_button.isEnabled())
        with self.assertRaises(PermissionError):
            self.editor.add_audio_path(audio, background=True)
        self.assertIsNone(self.editor.timeline.get("background_audio"))

        # Unlock music and add it
        self.editor._set_track_lock("background_audio", False)
        self.editor.add_audio_path(audio, background=True)
        self.assertIsNotNone(self.editor.timeline.get("background_audio"))

        # Lock music and verify apply/delete blocked
        self.editor._set_track_lock("background_audio", True)
        self.editor.select_item("background_audio", 0)
        self.assertFalse(self.editor.apply_button.isEnabled())
        self.assertFalse(self.editor.delete_button.isEnabled())
        self.assertFalse(self.editor.apply_properties())
        self.assertFalse(self.editor.delete_selected())
        self.assertIsNotNone(self.editor.timeline.get("background_audio"))

        # Unlock music and delete
        self.editor._set_track_lock("background_audio", False)
        self.editor.select_item("background_audio", 0)
        self.assertTrue(self.editor.delete_selected())
        self.assertIsNone(self.editor.timeline.get("background_audio"))

    def test_track_lock_text_and_subtitle_blocks_editing_and_ripple_delete_shift(self):
        image_a = _make_solid_image(self.root / "txt_media" / "a.png", "red")
        image_b = _make_solid_image(self.root / "txt_media" / "b.png", "yellow")
        self.editor.add_media_paths([image_a, image_b])

        # Lock TEXT
        self.editor._set_track_lock("text", True)
        self.assertFalse(self.editor.add_text_button.isEnabled())
        self.assertFalse(self.editor.add_overlay("text"))
        self.assertEqual(len(self.editor.timeline.get("texts", [])), 0)

        # Subtitle is free and can be added
        self.assertTrue(self.editor.add_overlay("subtitle"))
        self.assertEqual(len(self.editor.timeline.get("subtitles", [])), 1)

        # Unlock text, add text overlay at 4.0s (on second scene)
        self.editor._set_track_lock("text", False)
        self.editor._playhead = 4.0
        self.assertTrue(self.editor.add_overlay("text"))
        self.assertEqual(len(self.editor.timeline["texts"]), 1)

        # Lock text again
        self.editor._set_track_lock("text", True)
        self.editor.select_item("text", 0)
        self.assertIn("[BLOCCATA]", self.editor.inspector_title.text())
        self.assertFalse(self.editor.apply_button.isEnabled())
        self.assertFalse(self.editor.delete_button.isEnabled())
        self.assertFalse(self.editor.apply_properties())
        self.assertFalse(self.editor.delete_selected())
        self.assertEqual(len(self.editor.timeline["texts"]), 1)

        # Now select first scene and try Ripple Delete:
        # Since text starts at 4.0s (after first scene), ripple deleting scene 0
        # would shift the locked text! Ripple delete must be blocked!
        self.editor.select_item("scene", 0)
        self.assertFalse(self.editor.ripple_delete_selected())
        self.assertIn("TESTI", self.editor.status_label.text())
        self.assertEqual(len(self.editor.timeline["scenes"]), 2)

        # Unlock text: ripple delete now succeeds and shifts text
        self.editor._set_track_lock("text", False)
        self.editor.select_item("scene", 0)
        self.assertTrue(self.editor.ripple_delete_selected())
        self.assertEqual(len(self.editor.timeline["scenes"]), 1)

    def test_track_lock_undo_redo_protection_and_restoration(self):
        image = _make_solid_image(self.root / "undo_lock" / "f.png", "magenta")
        self.editor.add_media_paths([image])

        # Add text overlay while track is free
        self.editor.add_overlay("text")
        self.assertEqual(len(self.editor.timeline["texts"]), 1)
        self.assertTrue(self.editor.undo_stack.canUndo())

        # Now lock text track
        self.editor._set_track_lock("text", True)

        # Undo must be blocked because it would alter the locked text track!
        self.assertFalse(self.editor.undo())
        self.assertIn("Annullamento bloccato", self.editor.status_label.text())
        self.assertEqual(len(self.editor.timeline["texts"]), 1)

        # Unlock text track
        self.editor._set_track_lock("text", False)

        # Undo now succeeds
        self.assertTrue(self.editor.undo())
        self.assertEqual(len(self.editor.timeline.get("texts", [])), 0)

        # Now lock text track again before redo
        self.editor._set_track_lock("text", True)

        # Redo must be blocked
        self.assertFalse(self.editor.redo())
        self.assertIn("Ripristino bloccato", self.editor.status_label.text())
        self.assertEqual(len(self.editor.timeline.get("texts", [])), 0)

        # Unlock and redo
        self.editor._set_track_lock("text", False)
        self.assertTrue(self.editor.redo())
        self.assertEqual(len(self.editor.timeline["texts"]), 1)

    @unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg/FFprobe non disponibili")
    def test_track_lock_all_tracks_reopen_and_export_via_job_manager(self):
        image, video, audio = _make_assets(self.root / "lock_export_media")
        self.editor.add_media_paths([image, video])
        self.editor.add_audio_path(audio)
        self.editor.add_overlay("text")
        self.editor.add_overlay("subtitle")

        # Lock ALL 5 tracks
        for key in ("scene", "audio", "background_audio", "text", "subtitle"):
            self.editor._set_track_lock(key, True)

        self.editor.save_current_project()
        self.assertNotIn("*", self.editor.project_name.text())

        # Reopen and check all 5 are locked
        reopened = open_project(self.editor.project.path)
        self.editor.set_project(reopened)
        for key in ("scene", "audio", "background_audio", "text", "subtitle"):
            self.assertTrue(self.editor._track_is_locked(key))

        # Start render via Job Manager: must complete successfully
        self.editor._job_manager = JobManager(work_root=self.root / "lock_jobs")
        self.addCleanup(lambda: self.editor._job_manager.shutdown(wait=True) if self.editor._job_manager else None)
        output = self.root / "lock_rendered.mp4"
        job = self.editor.start_render(output)
        self.assertTrue(self.editor._job_manager.wait(job.id, timeout=60), "rendering FFmpeg non terminato")
        self.assertEqual(job.status, JobStatus.COMPLETED, job.error)
        self.assertTrue(job.published)
        self.assertTrue(output.is_file())
        self.assertGreater(output.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()






