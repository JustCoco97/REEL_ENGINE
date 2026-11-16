"""AI Video Director brief and review dialog (local template mode)."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QPlainTextEdit, QVBoxLayout,
)

from ..director import DirectorBrief, DirectorError, DirectorOrchestrator, OllamaProvider


class _WorkerSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class _FunctionWorker(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = _WorkerSignals()

    def run(self):
        try:
            self.signals.succeeded.emit(self.function())
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        finally:
            self.signals.finished.emit()


class AIVideoDirectorDialog(QDialog):
    """Collect a brief, show generated stages and create a normal editable project."""

    def __init__(self, parent=None, *, projects_folder=None, orchestrator=None):
        super().__init__(parent)
        self.setWindowTitle("AI Video Director — REEL ENGINE")
        self.setMinimumSize(760, 720)
        self.orchestrator = orchestrator or DirectorOrchestrator()
        self.plan = None
        self.project = None
        self.media_paths: list[Path] = []
        self._workers = []
        self._busy = False
        root = QVBoxLayout(self)
        notice = QLabel(
            "Scegli una bozza locale deterministica oppure Ollama in locale. Nessun dato viene inviato "
            "a servizi cloud e REEL ENGINE non scarica modelli; verifica lo stato di Ollama qui sotto. "
            "In modalità Ollama, la diagnostica locale conserva una risposta grezza limitata e metadati "
            "per aiutare a risolvere errori: la risposta può ripetere parti del brief. Il prompt, i percorsi "
            "dei media e il testo interno di thinking non vengono salvati. "
            "Controlla sempre storyboard e progetto nell’editor prima dell’export."
        )
        notice.setWordWrap(True)
        notice.setObjectName("tagline")
        root.addWidget(notice)
        form = QFormLayout()
        self.topic_edit = QPlainTextEdit()
        self.topic_edit.setPlaceholderText("Es. Un breve video sul restauro di una Vespa d'epoca")
        self.topic_edit.setMaximumHeight(90)
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(1, 600)
        self.duration_spin.setValue(30)
        self.duration_spin.setSuffix(" s")
        self.format_combo = QComboBox()
        for key, label in (("vertical", "Verticale · 9:16"), ("horizontal", "Orizzontale · 16:9"),
                           ("square", "Quadrato · 1:1")):
            self.format_combo.addItem(label, key)
        self.provider_mode_combo = QComboBox()
        self.provider_mode_combo.addItem("Bozza locale deterministica", "template")
        self.provider_mode_combo.addItem("AI locale tramite Ollama", "ollama")
        self.ollama_model_combo = QComboBox()
        self.ollama_model_combo.setEditable(True)
        self.ollama_model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.ollama_model_combo.setPlaceholderText("Seleziona un modello installato")
        self.ollama_timeout_spin = QDoubleSpinBox()
        self.ollama_timeout_spin.setRange(5, 600)
        self.ollama_timeout_spin.setValue(300)
        self.ollama_timeout_spin.setSuffix(" s")
        self.instructions_edit = QPlainTextEdit()
        self.instructions_edit.setPlaceholderText("Tono, pubblico, messaggi o vincoli da rispettare")
        self.instructions_edit.setMaximumHeight(76)
        self.project_name_edit = QLineEdit()
        self.project_name_edit.setPlaceholderText("Nome progetto")
        self.project_folder_edit = QLineEdit(str(projects_folder or Path.home() / "REEL ENGINE" / "Projects"))
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.project_folder_edit, 1)
        self.browse_folder_button = QPushButton("Scegli…")
        folder_row.addWidget(self.browse_folder_button)
        folder_widget = QLabel()
        folder_widget.setLayout(folder_row)
        form.addRow("Argomento / descrizione", self.topic_edit)
        form.addRow("Durata desiderata", self.duration_spin)
        form.addRow("Formato", self.format_combo)
        form.addRow("Modalità", self.provider_mode_combo)
        form.addRow("Modello Ollama", self.ollama_model_combo)
        form.addRow("Timeout richiesta", self.ollama_timeout_spin)
        form.addRow("Istruzioni aggiuntive", self.instructions_edit)
        form.addRow("Nome progetto", self.project_name_edit)
        form.addRow("Cartella progetto", folder_widget)
        root.addLayout(form)
        ollama_row = QHBoxLayout()
        self.check_ollama_button = QPushButton("Verifica Ollama e modelli")
        self.ollama_status_label = QLabel("Non verificato. Ollama richiesto solo per la modalità AI locale.")
        self.ollama_status_label.setWordWrap(True)
        ollama_row.addWidget(self.check_ollama_button)
        ollama_row.addWidget(self.ollama_status_label, 1)
        root.addLayout(ollama_row)
        media_header = QHBoxLayout()
        media_header.addWidget(QLabel("Contenuti disponibili (immagini e video)"), 1)
        self.add_media_button = QPushButton("Aggiungi media…")
        self.remove_media_button = QPushButton("Rimuovi selezionato")
        media_header.addWidget(self.add_media_button)
        media_header.addWidget(self.remove_media_button)
        root.addLayout(media_header)
        self.media_list = QListWidget()
        self.media_list.setMinimumHeight(90)
        root.addWidget(self.media_list)
        self.preview_label = QLabel("Le fasi interpretazione, struttura narrativa, script e storyboard appariranno qui.")
        self.preview_label.setWordWrap(True)
        self.preview_label.setMinimumHeight(135)
        self.preview_label.setObjectName("directorPreview")
        root.addWidget(self.preview_label, 1)
        actions = QHBoxLayout()
        self.generate_button = QPushButton("Genera bozza e storyboard")
        self.create_button = QPushButton("Prepara progetto e apri nell’editor")
        self.create_button.setObjectName("primaryButton")
        self.create_button.setEnabled(False)
        actions.addWidget(self.generate_button)
        actions.addWidget(self.create_button)
        self.cancel_button = QPushButton("Chiudi")
        actions.addWidget(self.cancel_button)
        root.addLayout(actions)
        self.browse_folder_button.clicked.connect(self._browse_folder)
        self.add_media_button.clicked.connect(self._add_media)
        self.remove_media_button.clicked.connect(self._remove_media)
        self.generate_button.clicked.connect(self.start_draft_generation)
        self.check_ollama_button.clicked.connect(self.check_ollama)
        self.provider_mode_combo.currentIndexChanged.connect(self._provider_mode_changed)
        self.create_button.clicked.connect(self.create_project)
        self.cancel_button.clicked.connect(self.reject)
        self._provider_mode_changed()

    def _provider_mode_changed(self, *_args):
        ollama_mode = self.provider_mode_combo.currentData() == "ollama"
        self.ollama_model_combo.setEnabled(ollama_mode and not self._busy)
        self.ollama_timeout_spin.setEnabled(ollama_mode and not self._busy)

    def _set_busy(self, busy):
        self._busy = bool(busy)
        self.generate_button.setEnabled(not self._busy)
        self.check_ollama_button.setEnabled(not self._busy)
        self.provider_mode_combo.setEnabled(not self._busy)
        self.add_media_button.setEnabled(not self._busy)
        self.remove_media_button.setEnabled(not self._busy)
        self._provider_mode_changed()

    def _launch_worker(self, function, on_success, on_failure):
        worker = _FunctionWorker(function)
        self._workers.append(worker)
        worker.signals.succeeded.connect(on_success)
        worker.signals.failed.connect(on_failure)
        worker.signals.finished.connect(lambda task=worker: self._workers.remove(task) if task in self._workers else None)
        QThreadPool.globalInstance().start(worker)

    def _selected_orchestrator(self):
        if self.provider_mode_combo.currentData() == "ollama":
            model = self.ollama_model_combo.currentText().strip()
            return DirectorOrchestrator(OllamaProvider(
                model, timeout=self.ollama_timeout_spin.value(),
            ))
        return DirectorOrchestrator()

    def check_ollama(self):
        if self._busy:
            return
        self._set_busy(True)
        self.ollama_status_label.setText("Verifico la connessione locale e i modelli installati…")
        provider = OllamaProvider("connection-check", timeout=10)
        self._launch_worker(provider.check_connection, self._ollama_check_succeeded,
                            self._ollama_check_failed)

    def _ollama_check_succeeded(self, models):
        current = self.ollama_model_combo.currentText().strip()
        self.ollama_model_combo.clear()
        self.ollama_model_combo.addItems(models)
        if current:
            self.ollama_model_combo.setCurrentText(current)
        elif models:
            self.ollama_model_combo.setCurrentIndex(0)
        self.ollama_status_label.setText(
            f"Ollama attivo in locale · {len(models)} modelli installati. Nessun modello verrà scaricato automaticamente."
            if models else
            "Ollama raggiungibile, ma non risultano modelli installati. Installa un modello con Ollama prima di generare."
        )
        self._set_busy(False)

    def _ollama_check_failed(self, message):
        self.ollama_status_label.setText(
            "Ollama non raggiungibile. Installa/avvia Ollama e carica un modello localmente; "
            "questa app non scarica modelli. Dettaglio: " + message
        )
        self._set_busy(False)

    def start_draft_generation(self):
        try:
            brief, _name = self._brief()
            orchestrator = self._selected_orchestrator()
        except DirectorError as exc:
            QMessageBox.warning(self, "AI Video Director", str(exc))
            return False
        self.plan = None
        self.create_button.setEnabled(False)
        self._set_busy(True)
        self.preview_label.setText("Genero la bozza in background… L’interfaccia resta disponibile.")
        def report_failure(message):
            provider = orchestrator.provider
            path = getattr(provider, "latest_diagnostic_path", None)
            if path and Path(path).is_file() and str(path) not in message:
                message = f"{message}\nDiagnostica locale: {path}"
            self._draft_failed(message)

        self._launch_worker(lambda: orchestrator.plan(brief), self._draft_succeeded, report_failure)
        return True

    def _draft_succeeded(self, plan):
        self.plan = plan
        self._show_plan(plan)
        self.create_button.setEnabled(True)
        self._set_busy(False)

    def _draft_failed(self, message):
        self.plan = None
        self.preview_label.setText("La bozza non è stata generata. Correggi il brief o la connessione e riprova.\n" + message)
        self.create_button.setEnabled(False)
        self._set_busy(False)
        QMessageBox.warning(self, "AI Video Director", "Generazione non riuscita.\n\n" + message)

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Cartella dei progetti", self.project_folder_edit.text())
        if folder:
            self.project_folder_edit.setText(folder)

    def _add_media(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Scegli contenuti per lo storyboard", "",
            "Media supportati (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.mp4 *.mov *.mkv *.avi *.webm *.m4v)",
        )
        for raw in paths:
            path = Path(raw).resolve(strict=False)
            if path not in self.media_paths:
                self.media_paths.append(path)
                self.media_list.addItem(path.name)

    def _remove_media(self):
        index = self.media_list.currentRow()
        if 0 <= index < len(self.media_paths):
            self.media_paths.pop(index)
            self.media_list.takeItem(index)
            self.plan = None
            self.create_button.setEnabled(False)

    def _brief(self):
        topic = self.topic_edit.toPlainText().strip()
        raw_name = self.project_name_edit.text().strip() or topic
        safe_name = re.sub(r'[<>:"/\\|?*]+', " ", raw_name).strip(" .")
        if not safe_name:
            safe_name = "Progetto AI Director"
        self.project_name_edit.setText(safe_name)
        return DirectorBrief(topic, self.duration_spin.value(), self.format_combo.currentData(),
                             self.instructions_edit.toPlainText().strip(), tuple(self.media_paths)), safe_name

    def generate_draft(self):
        try:
            brief, _name = self._brief()
            self.plan = self._selected_orchestrator().plan(brief)
        except (DirectorError, OSError) as exc:
            QMessageBox.warning(self, "AI Video Director", str(exc))
            self.plan = None
            self.create_button.setEnabled(False)
            return False
        self._show_plan(self.plan)
        self.create_button.setEnabled(True)
        return True

    def _show_plan(self, plan):
        interpretation = plan.phases["interpretation"]
        lines = [f"MODALITÀ: {plan.provider_status}",
                 f"INTERPRETAZIONE: {interpretation['summary']}",
                 f"PUBBLICO: {interpretation['audience']} · TONO: {interpretation['tone']}",
                 "STRUTTURA / STORYBOARD:"]
        for beat, story, scene in zip(plan.phases["narrative"], plan.phases["storyboard"], plan.scenes):
            lines.append(f"• {beat['title']} — {beat['purpose']}\n  Inquadratura: {story['title']} — {story['description']}")
            lines.append(f"  Media: {Path(scene['asset']).name} · {scene['duration']:.1f} s · ID {scene['media_id']}")
            lines.append(f"  Script: {scene['script']}")
        lines.append("\nRivedi le scene nell’editor prima di avviare l’export.")
        self.preview_label.setText("\n".join(lines))

    def create_project(self):
        if self.plan is None:
            return self.start_draft_generation()
        try:
            brief, name = self._brief()
            if brief != self.plan.brief:
                return self.start_draft_generation()
            folder = Path(self.project_folder_edit.text()).expanduser() / name
            if (folder / "project.json").exists():
                QMessageBox.warning(self, "AI Video Director", f"Il progetto esiste già: {folder}")
                return False
            self.project = self.orchestrator.create_project(self.plan, name, folder)
        except (DirectorError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "AI Video Director", f"Non è stato possibile preparare il progetto.\n\n{exc}")
            return False
        self.accept()
        return True


__all__ = ["AIVideoDirectorDialog"]
