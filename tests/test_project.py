import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from reel_engine.project import (
    PROJECT_FILENAME,
    PROJECT_FORMAT,
    ProjectFormatError,
    ProjectSaveError,
    create_project,
    diagnose_project,
    open_project,
    save_project,
    update_project_timeline,
    collect_project_media,
)


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reel-project-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "My Project"
        self.folder.mkdir()

    def test_create_persists_empty_versioned_timeline(self):
        project = create_project("Nuovo", self.folder)
        self.assertEqual(project.path, self.folder / PROJECT_FILENAME)
        document = json.loads(project.path.read_text(encoding="utf-8"))
        self.assertEqual(document["format"], PROJECT_FORMAT)
        self.assertEqual(document["format_version"], 1)
        self.assertEqual(document["timeline"]["schema_version"], 1)
        self.assertEqual(document["timeline"]["scenes"], [])
        self.assertIsNone(document["timeline"]["audio"])

    def test_save_reopen_and_update_preserves_settings_and_timeline(self):
        project = create_project("Montaggio", self.folder)
        project.settings = {"export": {"quality": "high"}, "custom": True}
        project.extra["future_field"] = {"kept": 7}
        timeline = {
            "schema_version": 1, "canvas": {"width": 720, "height": 1280, "fps": 25},
            "audio": None, "scenes": [], "background_audio": None,
            "texts": [{"start": 0, "end": 1, "text": "Città"}],
            "subtitles": [], "transitions": {"between": []},
        }
        update_project_timeline(project, timeline)
        save_project(project)
        reopened = open_project(project.path)
        self.assertEqual(reopened.name, "Montaggio")
        self.assertEqual(reopened.settings, project.settings)
        self.assertEqual(reopened.extra, project.extra)
        self.assertEqual(reopened.timeline, timeline)

    def test_in_project_media_is_saved_relative_and_reopened_absolute(self):
        media = self.folder / "media" / "foto speciale.png"
        media.parent.mkdir()
        media.write_bytes(b"image")
        project = create_project("Foto", self.folder)
        project.timeline["scenes"] = [{"id": "s1", "type": "image", "asset": str(media), "duration": 2}]
        save_project(project)
        stored = json.loads(project.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["timeline"]["scenes"][0]["asset"], "media/foto speciale.png")
        reopened = open_project(project.path)
        self.assertEqual(Path(reopened.timeline["scenes"][0]["asset"]), media.resolve())

    def test_external_absolute_media_stays_absolute(self):
        outside = self.root / "external.wav"
        outside.write_bytes(b"audio")
        project = create_project("Audio", self.folder)
        project.timeline["audio"] = str(outside)
        save_project(project)
        stored = json.loads(project.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["timeline"]["audio"], str(outside.resolve()))
        self.assertEqual(open_project(project.path).timeline["audio"], str(outside.resolve()))

    def test_missing_assets_are_diagnosed_without_blocking_open(self):
        project = create_project("Missing", self.folder)
        project.timeline.update({
            "audio": "media/voce.wav",
            "background_audio": {"asset": "media/musica.mp3"},
            "scenes": [{"id": "immagine", "type": "image", "asset": "media/a.png"},
                       {"id": "video", "type": "video", "asset": "media/b.mp4"}],
            "texts": [{"font": "fonts/font personalizzato.ttf"}],
        })
        save_project(project)
        reopened = open_project(project.path)
        missing = diagnose_project(reopened)
        self.assertEqual({item.kind for item in missing}, {"audio", "image", "video", "font"})
        self.assertTrue(all(item.resolved_path.is_absolute() for item in missing))
        self.assertTrue(all(str(item.resolved_path) in item.message for item in missing))

    def test_absolute_reference_outside_project_reports_missing_path(self):
        project = create_project("Lost", self.folder)
        absent = self.root / "not-here.wav"
        project.timeline["audio"] = str(absent)
        save_project(project)
        missing = diagnose_project(open_project(project.path))
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0].resolved_path, absent.resolve())

    def test_collect_external_assets_copies_and_persists_relative_refs(self):
        image = self.root / "foto.png"
        audio = self.root / "voice.wav"
        font = self.root / "titoli.ttf"
        image.write_bytes(b"image-original")
        audio.write_bytes(b"audio-original")
        font.write_bytes(b"font-original")
        project = create_project("Portable", self.folder)
        project.timeline.update({
            "audio": str(audio),
            "scenes": [{"id": "one", "type": "image", "asset": str(image), "duration": 1}],
            "texts": [{"font": str(font)}],
        })
        self.assertEqual(collect_project_media(project), 3)
        self.assertTrue(Path(project.timeline["audio"]).is_absolute())
        self.assertTrue(Path(project.timeline["scenes"][0]["asset"]).is_absolute())
        self.assertTrue(Path(project.timeline["texts"][0]["font"]).is_absolute())
        reopened = open_project(project.path)
        self.assertEqual(len(diagnose_project(reopened)), 0)
        self.assertEqual(Path(reopened.timeline["audio"]).parent, (self.folder / "media").resolve())
        self.assertEqual(Path(reopened.timeline["scenes"][0]["asset"]).parent, (self.folder / "media").resolve())
        self.assertEqual(Path(reopened.timeline["texts"][0]["font"]).parent, (self.folder / "fonts").resolve())
        self.assertEqual(image.read_bytes(), b"image-original")
        self.assertEqual(audio.read_bytes(), b"audio-original")
        self.assertEqual(font.read_bytes(), b"font-original")
        stored = json.loads(project.path.read_text(encoding="utf-8"))
        self.assertTrue(stored["timeline"]["audio"].startswith("media/"))
        self.assertEqual(collect_project_media(project), 0)

    def test_collect_media_missing_reference_aborts_without_partial_copy(self):
        existing = self.root / "existing.mp4"
        existing.write_bytes(b"video")
        project = create_project("Incomplete", self.folder)
        project.timeline["scenes"] = [
            {"id": "one", "type": "video", "asset": str(existing), "duration": 1},
            {"id": "two", "type": "video", "asset": str(self.root / "missing.mp4"), "duration": 1},
        ]
        with self.assertRaises(FileNotFoundError):
            collect_project_media(project)
        self.assertEqual(project.timeline["scenes"][0]["asset"], str(existing))
        self.assertFalse((self.folder / "media").exists())

    def test_collect_media_save_failure_restores_refs_and_removes_new_copies(self):
        external = self.root / "external.wav"
        external.write_bytes(b"audio")
        project = create_project("Rollback", self.folder)
        project.timeline["audio"] = str(external)
        with mock.patch("reel_engine.project.save_project", side_effect=ProjectSaveError("write failed")):
            with self.assertRaises(ProjectSaveError):
                collect_project_media(project)
        self.assertEqual(project.timeline["audio"], str(external))
        self.assertFalse((self.folder / "media").exists())

    def test_collect_media_interrupted_copy_cleans_temporary_file_and_folder(self):
        external = self.root / "external.mp4"
        external.write_bytes(b"video")
        project = create_project("Interrupted", self.folder)
        project.timeline["scenes"] = [{"id": "scene", "type": "video", "asset": str(external), "duration": 1}]

        def partial_copy(_source, target):
            Path(target).write_bytes(b"partial")
            raise OSError("copy interrupted")

        with mock.patch("reel_engine.project.shutil.copy2", side_effect=partial_copy):
            with self.assertRaisesRegex(OSError, "copy interrupted"):
                collect_project_media(project)
        self.assertEqual(project.timeline["scenes"][0]["asset"], str(external))
        self.assertFalse((self.folder / "media").exists())

    def test_invalid_json_and_unsupported_version_raise_clear_errors(self):
        path = self.folder / PROJECT_FILENAME
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(ProjectFormatError, "JSON progetto non valido"):
            open_project(path)
        path.write_text(json.dumps({"format": PROJECT_FORMAT, "format_version": 2}), encoding="utf-8")
        with self.assertRaisesRegex(ProjectFormatError, "Versione progetto non supportata"):
            open_project(path)

    def test_failed_atomic_replace_preserves_existing_file_and_cleans_temp(self):
        project = create_project("Safe", self.folder)
        original = project.path.read_bytes()
        project.settings["edited"] = True
        with mock.patch("reel_engine.project.os.replace", side_effect=OSError("simulated replace failure")):
            with self.assertRaises(ProjectSaveError):
                save_project(project)
        self.assertEqual(project.path.read_bytes(), original)
        self.assertEqual(list(self.folder.glob(f".{PROJECT_FILENAME}.*.tmp")), [])

    def test_create_refuses_to_replace_existing_project(self):
        create_project("One", self.folder)
        with self.assertRaises(FileExistsError):
            create_project("Two", self.folder)


if __name__ == "__main__":
    unittest.main()
