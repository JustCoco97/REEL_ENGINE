"""Initial REEL ENGINE desktop application."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..project import create_project, diagnose_project, open_project
from .editor import TimelineEditorPage
from .director import AIVideoDirectorDialog
from .services import diagnose_dependencies


APP_NAME = "REEL ENGINE"
_RECENT_KEY = "recentProjects"
_MAX_RECENTS = 12


def default_projects_folder():
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(documents or Path.home()) / APP_NAME / "Projects"


class HomePage(QWidget):
    new_project_requested = Signal()
    director_requested = Signal()
    open_project_requested = Signal()
    diagnostics_requested = Signal()
    projects_folder_requested = Signal()
    recent_project_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(64, 52, 64, 48)
        layout.setSpacing(18)

        brand = QLabel("REEL ENGINE")
        brand.setObjectName("brand")
        brand.setFont(QFont("Segoe UI", 28, QFont.Weight.Bold))
        layout.addWidget(brand)
        tagline = QLabel("Dai tuoi media a un montaggio, con il controllo nelle tue mani.")
        tagline.setObjectName("tagline")
        layout.addWidget(tagline)

        card = QFrame()
        card.setObjectName("homeCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(12)
        heading = QLabel("Inizia un progetto")
        heading.setObjectName("sectionHeading")
        card_layout.addWidget(heading)
        self.new_button = QPushButton("Nuovo progetto")
        self.new_button.setObjectName("primaryButton")
        self.director_button = QPushButton("AI Video Director · bozza guidata")
        self.director_button.setToolTip("Crea un progetto modificabile partendo da un brief e dai tuoi media; provider AI non configurato")
        self.open_button = QPushButton("Apri progetto esistente…")
        self.recent_button = QPushButton("Apri cartella dei progetti recenti")
        self.diagnostics_button = QPushButton("Diagnostica dipendenze")
        for button in (self.new_button, self.director_button, self.open_button, self.recent_button, self.diagnostics_button):
            button.setMinimumHeight(42)
            card_layout.addWidget(button)
        layout.addWidget(card)

        recent_heading = QLabel("Progetti recenti")
        recent_heading.setObjectName("sectionHeading")
        layout.addWidget(recent_heading)
        self.recent_list = QListWidget()
        self.recent_list.setObjectName("recentProjects")
        self.recent_list.setMinimumHeight(120)
        layout.addWidget(self.recent_list, 1)

        self.new_button.clicked.connect(self.new_project_requested)
        self.director_button.clicked.connect(self.director_requested)
        self.open_button.clicked.connect(self.open_project_requested)
        self.recent_button.clicked.connect(self.projects_folder_requested)
        self.diagnostics_button.clicked.connect(self.diagnostics_requested)
        self.recent_list.itemDoubleClicked.connect(self._activate_recent)

    def _activate_recent(self, item):
        project_path = item.data(Qt.ItemDataRole.UserRole)
        if project_path:
            self.recent_project_requested.emit(project_path)

    def set_recent_projects(self, project_paths):
        self.recent_list.clear()
        for path in project_paths:
            item = QListWidgetItem(f"{Path(path).parent.name}  ·  {path}")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.recent_list.addItem(item)


class DiagnosticsPage(QWidget):
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 40, 48, 40)
        header = QHBoxLayout()
        self.back_button = QPushButton("← Home")
        self.title = QLabel("Diagnostica dipendenze")
        self.title.setObjectName("sectionHeading")
        header.addWidget(self.back_button)
        header.addWidget(self.title, 1)
        layout.addLayout(header)
        self.results = QVBoxLayout()
        layout.addLayout(self.results)
        self.refresh_button = QPushButton("Aggiorna controllo")
        layout.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        self.back_button.clicked.connect(self.back_requested)
        self.refresh_button.clicked.connect(self.refresh)

    def refresh(self):
        while self.results.count():
            item = self.results.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for dependency in diagnose_dependencies():
            icon = "✓" if dependency.available else "!"
            row = QLabel(f"{icon}  {dependency.name}    {dependency.detail}")
            row.setObjectName("dependencyOk" if dependency.available else "dependencyMissing")
            row.setMinimumHeight(42)
            self.results.addWidget(row)


class ProjectPage(QWidget):
    home_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 40, 48, 40)
        header = QHBoxLayout()
        self.back_button = QPushButton("← Home")
        self.name_label = QLabel("Progetto")
        self.name_label.setObjectName("sectionHeading")
        header.addWidget(self.back_button)
        header.addWidget(self.name_label, 1)
        layout.addLayout(header)
        self.path_label = QLabel()
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)
        self.status_label = QLabel("Progetto aperto. L’editor della timeline sarà disponibile in un incremento successivo.")
        self.status_label.setObjectName("tagline")
        layout.addWidget(self.status_label)
        missing_title = QLabel("Risorse da recuperare")
        missing_title.setObjectName("sectionHeading")
        layout.addWidget(missing_title)
        self.missing_list = QListWidget()
        layout.addWidget(self.missing_list, 1)
        self.back_button.clicked.connect(self.home_requested)

    def show_project(self, project, missing_resources):
        self.name_label.setText(project.name)
        self.path_label.setText(str(project.path))
        self.missing_list.clear()
        if missing_resources:
            for resource in missing_resources:
                self.missing_list.addItem(resource.message)
        else:
            self.missing_list.addItem("Nessuna risorsa mancante rilevata.")


class NewProjectDialog(QDialog):
    def __init__(self, parent=None, initial_folder=None):
        super().__init__(parent)
        self.setWindowTitle("Nuovo progetto — REEL ENGINE")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Es. Viaggio in Sicilia")
        self.folder_edit = QLineEdit(str(initial_folder or default_projects_folder()))
        browse_button = QPushButton("Scegli…")
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse_button)
        folder_wrap = QWidget()
        folder_wrap.setLayout(folder_row)
        form.addRow("Nome progetto", self.name_edit)
        form.addRow("Cartella progetto", folder_wrap)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Crea progetto")
        layout.addWidget(self.buttons)
        browse_button.clicked.connect(self._browse)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Scegli la cartella del progetto", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)


class MainWindow(QMainWindow):
    def __init__(self, *, settings=None):
        super().__init__()
        self.settings = settings or QSettings("REEL_ENGINE", "REEL_ENGINE")
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1100, 760)
        self.home_page = HomePage()
        self.diagnostics_page = DiagnosticsPage()
        self.editor_page = TimelineEditorPage()
        # Keep the first increment's attribute available to callers while projects
        # now open directly in the visual editor.
        self.project_page = self.editor_page
        self.pages = QStackedWidget()
        for page in (self.home_page, self.diagnostics_page, self.editor_page):
            self.pages.addWidget(page)
        self.setCentralWidget(self.pages)
        self.current_project = None
        self.home_page.new_project_requested.connect(self.new_project)
        self.home_page.director_requested.connect(self.start_ai_video_director)
        self.home_page.open_project_requested.connect(self.open_project_dialog)
        self.home_page.diagnostics_requested.connect(self.show_diagnostics)
        self.home_page.projects_folder_requested.connect(self.open_projects_folder)
        self.home_page.recent_project_requested.connect(self.open_project_path)
        self.diagnostics_page.back_requested.connect(self.show_home)
        self.editor_page.home_requested.connect(self.request_home)
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.editor_page.save_current_project)
        self._apply_style()
        self.refresh_recent_projects()
        self.pages.setCurrentWidget(self.home_page)

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #101820; color: #edf4f7; font-family: 'Segoe UI'; font-size: 14px; }
            QLabel#brand { color: #40d6c2; letter-spacing: 3px; }
            QLabel#tagline { color: #a8bbc5; font-size: 15px; }
            QLabel#sectionHeading { color: #f4f8fa; font-size: 20px; font-weight: 600; }
            QFrame#homeCard { background: #192731; border: 1px solid #29414c; border-radius: 12px; }
            QPushButton { background: #233640; border: 1px solid #3a5661; border-radius: 7px; padding: 8px 14px; text-align: left; }
            QPushButton:hover { background: #2d4650; }
            QPushButton#primaryButton { background: #087e78; border-color: #16b3a5; font-weight: 600; }
            QPushButton#primaryButton:hover { background: #09968e; }
            QListWidget, QLineEdit { background: #17252e; border: 1px solid #344d58; border-radius: 6px; padding: 6px; }
            QLabel#dependencyOk { color: #6ee7bd; }
            QLabel#dependencyMissing { color: #ffc477; }
        """)

    def refresh_recent_projects(self):
        projects = self.settings.value(_RECENT_KEY, [], type=list)
        valid_values = [str(Path(path)) for path in projects if isinstance(path, str)]
        self.home_page.set_recent_projects(valid_values[:_MAX_RECENTS])

    def _remember_project(self, path):
        path = str(Path(path).resolve(strict=False))
        previous = self.settings.value(_RECENT_KEY, [], type=list)
        values = [path] + [str(item) for item in previous if str(item) != path]
        self.settings.setValue(_RECENT_KEY, values[:_MAX_RECENTS])
        self.refresh_recent_projects()

    def new_project(self):
        dialog = NewProjectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.name_edit.text().strip()
        folder = Path(dialog.folder_edit.text()).expanduser()
        if not name:
            self._show_error("Inserisci un nome per il progetto.")
            return
        if name in {".", ".."} or "/" in name or "\\" in name:
            self._show_error("Il nome del progetto non può contenere separatori di cartella.")
            return
        project_folder = folder / name
        try:
            project = create_project(name, project_folder)
            self._show_project(project)
        except Exception as exc:
            self._show_error(f"Non è stato possibile creare il progetto.\n\n{exc}")

    def start_ai_video_director(self):
        dialog = AIVideoDirectorDialog(self, projects_folder=default_projects_folder())
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.project is not None:
            self._show_project(dialog.project)
            return dialog.project
        return None

    def create_project_at(self, name, folder):
        """Create and open a project; exposed as a small testable UI action."""
        project = create_project(name, folder)
        self._show_project(project)
        return project

    def open_project_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Apri progetto REEL ENGINE", str(default_projects_folder()),
            "Progetti REEL ENGINE (project.json);;File JSON (*.json)",
        )
        if path:
            self.open_project_path(path)

    def open_project_path(self, path):
        try:
            project = open_project(path)
            if not self._show_project(project):
                return None
            return project
        except Exception as exc:
            self._show_error(f"Non è stato possibile aprire il progetto.\n\n{exc}")
            return None

    def _show_project(self, project):
        if self.current_project is not None and self._editor_has_unsaved_changes():
            if not self._resolve_unsaved_changes():
                return False
        missing = diagnose_project(project)
        self.current_project = project
        self._remember_project(project.path)
        self.editor_page.set_project(project)
        self.pages.setCurrentWidget(self.editor_page)
        if missing:
            QMessageBox.warning(
                self, "Risorse mancanti",
                f"Il progetto è stato aperto, ma mancano {len(missing)} risorse. "
                "I percorsi da recuperare sono indicati nell'editor.",
            )
        return True

    def show_diagnostics(self):
        self.diagnostics_page.refresh()
        self.pages.setCurrentWidget(self.diagnostics_page)

    def show_home(self):
        if not self._resolve_unsaved_changes():
            return False
        self.pages.setCurrentWidget(self.home_page)
        return True

    def _editor_has_unsaved_changes(self):
        return bool(
            self.editor_page.project is not None
            and (not self.editor_page.undo_stack.isClean()
                 or self.editor_page._track_locks != self.editor_page._saved_track_locks
                 or self.editor_page._use_proxies != self.editor_page._saved_use_proxies
                 or self.editor_page._crossfade_style != self.editor_page._saved_crossfade_style
                 or self.editor_page._media_library_paths != self.editor_page._saved_media_library_paths)
        )

    def _choose_unsaved_action(self):
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Modifiche non salvate")
        dialog.setText("Il progetto contiene modifiche non salvate.")
        save_button = dialog.addButton("SALVA", QMessageBox.ButtonRole.AcceptRole)
        discard_button = dialog.addButton("SCARTA", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = dialog.addButton("ANNULLA", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(save_button)
        dialog.setEscapeButton(cancel_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is save_button:
            return "save"
        if clicked is discard_button:
            return "discard"
        return "cancel"

    def _resolve_unsaved_changes(self):
        if not self._editor_has_unsaved_changes():
            return True
        action = self._choose_unsaved_action()
        if action == "discard":
            try:
                project = open_project(self.editor_page.project.path)
                self.current_project = project
                self.editor_page.set_project(project)
                return True
            except Exception as exc:
                self._show_error(f"Non è stato possibile scartare le modifiche e ricaricare il progetto.\n\n{exc}")
                return False
        if action == "save":
            return bool(self.editor_page.save_current_project())
        return False

    def request_home(self):
        return self.show_home()

    def closeEvent(self, event):
        if not self._resolve_unsaved_changes():
            event.ignore()
            return
        self.editor_page.shutdown_for_teardown()
        event.accept()
        super().closeEvent(event)

    def open_projects_folder(self):
        folder = default_projects_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
                raise OSError("Windows non ha aperto la cartella.")
        except OSError as exc:
            self._show_error(f"Non è stato possibile aprire la cartella dei progetti.\n\n{exc}")

    def _show_error(self, message):
        QMessageBox.critical(self, "REEL ENGINE", message)


def main(argv=None, *, smoke_timeout_ms=None):
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("REEL_ENGINE")
    window = MainWindow()
    window.show()
    if smoke_timeout_ms is not None:
        QTimer.singleShot(smoke_timeout_ms, window.close)
    return app.exec()


__all__ = ["APP_NAME", "MainWindow", "NewProjectDialog", "default_projects_folder", "main"]
