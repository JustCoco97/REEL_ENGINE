import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from reel_engine.gui import services


class DependencyDiagnosticTests(unittest.TestCase):
    def test_dependency_report_includes_runtime_and_ffmpeg_tools(self):
        results = services.diagnose_dependencies()
        self.assertEqual({item.name for item in results}, {"Python", "PySide6", "FFmpeg", "FFprobe"})
        self.assertTrue(next(item for item in results if item.name == "Python").available)

    @mock.patch("reel_engine.gui.services.subprocess.run")
    @mock.patch("reel_engine.gui.services.shutil.which", return_value=None)
    @mock.patch("reel_engine.gui.services.importlib.util.find_spec", return_value=None)
    def test_missing_dependencies_are_reported_without_launching_commands(self, _find_spec, which, run):
        results = services.diagnose_dependencies()
        self.assertFalse(next(item for item in results if item.name == "PySide6").available)
        self.assertEqual(next(item for item in results if item.name == "FFmpeg").detail, "Non trovato nel PATH")
        self.assertEqual(next(item for item in results if item.name == "FFprobe").detail, "Non trovato nel PATH")
        which.assert_any_call("ffmpeg")
        which.assert_any_call("ffprobe")
        run.assert_not_called()


PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None
if PYSIDE_AVAILABLE:
    from PySide6.QtCore import QSettings, QTimer, Qt
    from PySide6.QtGui import QUndoCommand
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialogButtonBox

    from reel_engine.gui.app import MainWindow, NewProjectDialog, main


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 non installato nell'ambiente di test")
class DesktopApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["REEL ENGINE tests"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reel-gui-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / "ui.ini"), QSettings.Format.IniFormat)
        self.window = MainWindow(settings=self.settings)
        self.window.show()
        self.addCleanup(self.window.close)

    def test_create_and_reopen_projects_updates_recent_list(self):
        project_path = self.root / "Created Project" / "project.json"
        project = self.window.create_project_at("Created Project", project_path.parent)
        self.assertTrue(project_path.is_file())
        self.assertEqual(self.window.current_project.name, "Created Project")
        self.assertEqual(self.window.pages.currentWidget(), self.window.project_page)
        reopened = self.window.open_project_path(project_path)
        self.assertIsNotNone(reopened)
        self.assertEqual(reopened.name, "Created Project")
        self.assertEqual(self.window.home_page.recent_list.count(), 1)

    def test_ai_video_director_has_dedicated_home_entry_point(self):
        self.assertTrue(self.window.home_page.director_button.isVisible())
        self.assertIn("provider AI non configurato", self.window.home_page.director_button.toolTip())
        self.assertIsNotNone(self.window.home_page.director_requested)

    def test_new_project_dialog_has_name_and_folder_fields(self):
        dialog = NewProjectDialog(self.window, initial_folder=self.root)
        self.assertEqual(dialog.folder_edit.text(), str(self.root))
        self.assertTrue(dialog.name_edit.isEnabled())
        self.assertEqual(dialog.windowTitle(), "Nuovo progetto — REEL ENGINE")

    def test_home_new_project_action_creates_project_from_dialog(self):
        def complete_dialog():
            dialog = QApplication.activeModalWidget()
            dialog.name_edit.setText("From Home")
            dialog.folder_edit.setText(str(self.root))
            dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).click()

        QTimer.singleShot(0, complete_dialog)
        self.window.new_project()
        self.assertTrue((self.root / "From Home" / "project.json").is_file())
        self.assertEqual(self.window.current_project.name, "From Home")

    def test_invalid_project_name_shows_error_and_does_not_escape_folder(self):
        def complete_dialog():
            dialog = QApplication.activeModalWidget()
            dialog.name_edit.setText("../outside")
            dialog.folder_edit.setText(str(self.root))
            dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).click()

        QTimer.singleShot(0, complete_dialog)
        with patch("reel_engine.gui.app.QMessageBox.critical") as show_error:
            self.window.new_project()
        self.assertIn("non può contenere separatori", show_error.call_args.args[2])
        self.assertFalse((self.root.parent / "outside" / "project.json").exists())

    def test_projects_folder_action_opens_default_location(self):
        folder = self.root / "Projects"
        with patch("reel_engine.gui.app.default_projects_folder", return_value=folder), \
             patch("reel_engine.gui.app.QDesktopServices.openUrl", return_value=True) as open_url:
            self.window.open_projects_folder()
        self.assertTrue(folder.is_dir())
        open_url.assert_called_once()

    def test_home_navigation_to_diagnostics_and_back(self):
        QTest.mouseClick(self.window.home_page.diagnostics_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.pages.currentWidget(), self.window.diagnostics_page)
        names = [self.window.diagnostics_page.results.itemAt(i).widget().text()
                 for i in range(self.window.diagnostics_page.results.count())]
        self.assertTrue(any("FFmpeg" in name for name in names))
        self.assertTrue(any("FFprobe" in name for name in names))
        QTest.mouseClick(self.window.diagnostics_page.back_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.pages.currentWidget(), self.window.home_page)

    def test_open_error_is_shown_as_a_friendly_message(self):
        with patch("reel_engine.gui.app.QMessageBox.critical") as show_error:
            result = self.window.open_project_path(self.root / "missing-project.json")
        self.assertIsNone(result)
        self.assertIn("Non è stato possibile aprire il progetto", show_error.call_args.args[2])

    def _make_editor_dirty(self):
        class DirtyCommand(QUndoCommand):
            def __init__(self):
                super().__init__("modifica test")
                self.before = {"scenes": [], "audio": None}
                self.after = {"scenes": [{"id": "test"}], "audio": None}

            def undo(self):
                pass

            def redo(self):
                pass

        self.window.editor_page.undo_stack.push(DirtyCommand())

    def test_home_guard_supports_save_discard_cancel_and_save_failure(self):
        project = self.window.create_project_at("Guard", self.root / "guard")
        self._make_editor_dirty()
        with patch.object(self.window, "_choose_unsaved_action", return_value="cancel"):
            self.assertFalse(self.window.show_home())
        self.assertEqual(self.window.pages.currentWidget(), self.window.editor_page)
        self.assertFalse(self.window.editor_page.undo_stack.isClean())

        with patch.object(self.window, "_choose_unsaved_action", return_value="save"):
            with patch.object(self.window.editor_page, "save_current_project", return_value=False):
                self.assertFalse(self.window.show_home())
        self.assertEqual(self.window.pages.currentWidget(), self.window.editor_page)

        with patch.object(self.window, "_choose_unsaved_action", return_value="save"):
            self.assertTrue(self.window.show_home())
        self.assertTrue(self.window.editor_page.undo_stack.isClean())
        self.assertEqual(self.window.pages.currentWidget(), self.window.home_page)
        self.assertEqual(self.window.current_project, project)

        self.window._show_project(project)
        self._make_editor_dirty()
        with patch.object(self.window, "_choose_unsaved_action", return_value="discard"):
            self.assertTrue(self.window.show_home())
        self.assertTrue(self.window.editor_page.undo_stack.isClean())

    def test_project_switch_is_blocked_or_resolved_before_replacing_editor_project(self):
        first = self.window.create_project_at("First", self.root / "first")
        second = self.window.create_project_at("Second", self.root / "second")
        self.window._show_project(first)
        self._make_editor_dirty()
        with patch.object(self.window, "_choose_unsaved_action", return_value="cancel"):
            self.assertIsNone(self.window.open_project_path(second.path))
        self.assertEqual(self.window.current_project.path, first.path)
        with patch.object(self.window, "_choose_unsaved_action", return_value="discard"):
            self.assertTrue(self.window._show_project(second))
        self.assertEqual(self.window.current_project.path, second.path)

    def test_close_guard_blocks_cancel_and_failed_save_then_allows_discard(self):
        self.window.create_project_at("Close", self.root / "close")
        self._make_editor_dirty()
        with patch.object(self.window, "_choose_unsaved_action", return_value="cancel"):
            self.assertFalse(self.window.close())
        with patch.object(self.window, "_choose_unsaved_action", return_value="save"):
            with patch.object(self.window.editor_page, "save_current_project", return_value=False):
                self.assertFalse(self.window.close())
        self.assertFalse(self.window.editor_page._shutting_down)
        with patch.object(self.window, "_choose_unsaved_action", return_value="discard"):
            self.assertTrue(self.window.close())
        self.assertTrue(self.window.editor_page._shutting_down)

    def test_close_without_dirty_state_does_not_offer_prompt(self):
        with patch.object(self.window, "_choose_unsaved_action") as choose:
            self.assertTrue(self.window.close())
        choose.assert_not_called()

    def test_application_event_loop_starts_and_closes_cleanly(self):
        self.window.close()
        result = main(argv=["REEL ENGINE smoke"], smoke_timeout_ms=100)
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
