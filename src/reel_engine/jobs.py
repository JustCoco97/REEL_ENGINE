"""Single-worker render queue with isolated workspaces and safe publication."""

from __future__ import annotations

import json
import inspect
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from .project import Project, diagnose_project, open_project
from .rendering import CROSSFADE_STYLES, RenderCancelledError, render_timeline
from .validation import load_timeline


REGISTRY_FILENAME = "job_registry.json"
REGISTRY_FORMAT = "reel-engine-job-registry"
REGISTRY_VERSION = 1


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobError(RuntimeError):
    """A render job failed before its result could be published."""


class JobRegistryError(RuntimeError):
    """The persistent job registry could not be read or written safely."""


@dataclass
class RenderJob:
    id: str
    project_path: Path
    output_path: Path
    workspace: Path
    log_path: Path
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0
    phase: str = "In attesa"
    error: str | None = None
    created_at: str = field(default_factory=lambda: _now())
    started_at: str | None = None
    finished_at: str | None = None
    published: bool = False
    _cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _same_path(left, right):
    return os.path.normcase(str(Path(left).resolve(strict=False))) == os.path.normcase(
        str(Path(right).resolve(strict=False))
    )


def _protected_project_paths(project: Project):
    paths = {project.path.resolve(strict=False)}
    timeline = project.timeline
    references = []
    if isinstance(timeline.get("audio"), str):
        references.append(timeline["audio"])
    background = timeline.get("background_audio")
    if isinstance(background, dict) and isinstance(background.get("asset"), str):
        references.append(background["asset"])
    for scene in timeline.get("scenes", []):
        if isinstance(scene, dict) and isinstance(scene.get("asset"), str):
            references.append(scene["asset"])
    for group in ("texts", "subtitles"):
        for item in timeline.get(group, []):
            if isinstance(item, dict) and isinstance(item.get("font"), str):
                font_path = Path(item["font"])
                if font_path.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                    references.append(item["font"])
    for reference in references:
        path = Path(reference)
        paths.add((path if path.is_absolute() else project.folder / path).resolve(strict=False))
    return paths


def _error_message(error):
    if isinstance(error, subprocess.CalledProcessError):
        lines = [line.strip() for line in str(error.output or "").splitlines() if line.strip()]
        detail = next((line for line in reversed(lines) if "error" in line.lower()), None)
        return detail or f"FFmpeg è terminato con codice {error.returncode}."
    return str(error) or error.__class__.__name__


class JobManager:
    """Queue and run saved-project renders one at a time.

    The default workspace is ``<engine>/temp/jobs``. Completed and failed
    workspaces are retained for their log; renderer intermediates are removed
    by the renderer's private temporary-directory lifecycle.
    """

    def __init__(self, *, work_root=None, renderer: Callable = render_timeline, quality_check=True,
                 on_update: Callable[[RenderJob], None] | None = None):
        engine_root = Path(__file__).resolve().parents[2]
        self.work_root = Path(work_root).expanduser().resolve(strict=False) if work_root else engine_root / "temp" / "jobs"
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.work_root / REGISTRY_FILENAME
        self.renderer = renderer
        self.quality_check = quality_check
        self.on_update = on_update
        self._queue = queue.Queue()
        self._jobs: dict[str, RenderJob] = {}
        self._lock = threading.RLock()
        self._closed = False
        self.persistence_error: str | None = None
        self._load_registry()
        for job in self._jobs.values():
            if job.status == JobStatus.QUEUED:
                self._queue.put(job.id)
        self._worker = threading.Thread(target=self._work, name="reel-render-job-manager", daemon=True)
        self._worker.start()

    def submit(self, project_path, output_path) -> RenderJob:
        """Enqueue a render from a saved project and return its observable job."""
        if self._closed:
            raise RuntimeError("Il Job Manager è chiuso.")
        source = Path(project_path).expanduser().resolve(strict=False)
        output = Path(output_path).expanduser().resolve(strict=False)
        if _same_path(source, output):
            raise ValueError("La destinazione non può sovrascrivere il file progetto.")
        identifier = uuid.uuid4().hex
        workspace = self.work_root / identifier
        workspace.mkdir(parents=True, exist_ok=False)
        job = RenderJob(identifier, source, output, workspace, workspace / "job.log")
        with self._lock:
            self._jobs[identifier] = job
            try:
                self._persist_registry()
            except JobRegistryError:
                self._jobs.pop(identifier, None)
                raise
        self._write_log(job, f"Lavoro {identifier} accodato; progetto: {source}; destinazione: {output}")
        self._notify(job)
        self._queue.put(job.id)
        return job

    def get_job(self, job_id) -> RenderJob:
        with self._lock:
            return self._jobs[job_id]

    def list_jobs(self) -> list[RenderJob]:
        with self._lock:
            return list(self._jobs.values())

    def wait(self, job_id, timeout=None) -> bool:
        """Wait for a terminal state; return False if timeout expires."""
        return self.get_job(job_id)._done.wait(timeout)

    def cancel(self, job_id) -> bool:
        job = self.get_job(job_id)
        with self._lock:
            if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
                return False
            job._cancel_event.set()
            if job.status == JobStatus.QUEUED:
                job.status = JobStatus.CANCELLED
                job.phase = "Annullato prima dell'avvio"
                job.finished_at = _now()
                job._done.set()
            else:
                job.phase = "Annullamento in corso"
            self._write_log(job, "Richiesta di annullamento ricevuta.")
            self._persist_best_effort()
        self._notify(job)
        return True

    def shutdown(self, *, wait=True, cancel_pending=False):
        """Stop accepting submissions and optionally cancel queued work."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if cancel_pending:
                for job in self._jobs.values():
                    if job.status == JobStatus.QUEUED:
                        job._cancel_event.set()
                        job.status = JobStatus.CANCELLED
                        job.phase = "Annullato durante la chiusura"
                        job.finished_at = _now()
                        job._done.set()
                        self._notify(job)
            self._persist_best_effort()
            self._queue.put(None)
        if wait:
            self._worker.join()

    def _notify(self, job):
        if self.on_update is not None:
            try:
                self.on_update(job)
            except Exception:
                # Observers must not be able to fail a render job.
                pass

    def _load_registry(self):
        if not self.registry_path.exists():
            return
        try:
            with self.registry_path.open("r", encoding="utf-8-sig") as stream:
                document = json.load(stream)
            if not isinstance(document, dict) or document.get("format") != REGISTRY_FORMAT:
                raise JobRegistryError("Formato del registro lavori non riconosciuto.")
            version = document.get("version")
            if isinstance(version, bool) or not isinstance(version, int) or version != REGISTRY_VERSION:
                raise JobRegistryError(f"Versione del registro lavori non supportata: {version!r}.")
            records = document.get("jobs")
            if not isinstance(records, list):
                raise JobRegistryError("Il campo jobs del registro deve essere una lista.")
            recovered = False
            for record in records:
                if not isinstance(record, dict):
                    raise JobRegistryError("Record lavoro non valido nel registro.")
                job_id = record.get("id")
                if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
                    raise JobRegistryError("Identificativo lavoro non valido nel registro.")
                status = JobStatus(record["status"])
                workspace = self.work_root / job_id
                job = RenderJob(
                    id=job_id,
                    project_path=Path(record["project_path"]).resolve(strict=False),
                    output_path=Path(record["output_path"]).resolve(strict=False),
                    workspace=workspace,
                    log_path=workspace / "job.log",
                    status=status,
                    progress=max(0.0, min(1.0, float(record.get("progress", 0.0)))),
                    phase=str(record.get("phase", "In attesa")),
                    error=record.get("error"),
                    created_at=str(record.get("created_at", _now())),
                    started_at=record.get("started_at"),
                    finished_at=record.get("finished_at"),
                    published=bool(record.get("published", False)),
                )
                if status == JobStatus.RUNNING:
                    if job.published and job.output_path.is_file() and job.output_path.stat().st_size > 0:
                        job.status = JobStatus.COMPLETED
                        job.progress = 1.0
                        job.phase = "Completato (recuperato dopo riavvio)"
                        job.finished_at = job.finished_at or _now()
                        job.error = None
                    else:
                        job.status = JobStatus.FAILED
                        job.phase = "Interrotto dal riavvio"
                        job.error = "Il rendering era in esecuzione alla chiusura e non è stato ripreso."
                        job.finished_at = _now()
                    recovered = True
                if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
                    job._done.set()
                self._jobs[job.id] = job
            if recovered:
                self._persist_registry()
                for job in self._jobs.values():
                    if job.phase == "Interrotto dal riavvio":
                        self._write_log(job, "Lavoro marcato come interrotto durante il recupero del registro.")
        except JobRegistryError:
            raise
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise JobRegistryError(f"Registro lavori non leggibile ({self.registry_path}): {exc}") from exc

    def _persist_registry(self):
        with self._lock:
            document = {
                "format": REGISTRY_FORMAT,
                "version": REGISTRY_VERSION,
                "jobs": [
                    {
                        "id": job.id,
                        "project_path": str(job.project_path),
                        "output_path": str(job.output_path),
                        "status": job.status.value,
                        "progress": job.progress,
                        "phase": job.phase,
                        "error": job.error,
                        "created_at": job.created_at,
                        "started_at": job.started_at,
                        "finished_at": job.finished_at,
                        "published": job.published,
                    }
                    for job in self._jobs.values()
                ],
            }
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", newline="\n", dir=self.work_root,
                    prefix=f".{REGISTRY_FILENAME}.", suffix=".tmp", delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.registry_path)
            except (OSError, TypeError, ValueError) as exc:
                raise JobRegistryError(f"Impossibile salvare il registro lavori: {exc}") from exc
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass

    def _persist_best_effort(self):
        try:
            self._persist_registry()
            self.persistence_error = None
        except JobRegistryError as exc:
            self.persistence_error = str(exc)

    def _write_log(self, job, message):
        with self._lock:
            try:
                with job.log_path.open("a", encoding="utf-8") as stream:
                    stream.write(f"[{_now()}] {message}\n")
            except OSError:
                # A full or unavailable log volume must not strand the queue worker.
                pass

    def _remove_staged(self, job, staged):
        try:
            staged.unlink(missing_ok=True)
        except OSError as cleanup_error:
            self._write_log(job, f"Avviso: file provvisorio non rimosso: {cleanup_error}")

    def _set_progress(self, job, phase, fraction):
        with self._lock:
            if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
                return
            job.progress = max(job.progress, min(0.99, max(0.0, float(fraction))))
            job.phase = phase
            self._persist_best_effort()
        self._notify(job)

    def _work(self):
        while True:
            job_id = self._queue.get()
            try:
                if job_id is None:
                    return
                job = self.get_job(job_id)
                if job.status == JobStatus.CANCELLED:
                    continue
                self._execute(job)
            finally:
                self._queue.task_done()

    def _execute(self, job):
        with self._lock:
            if job._cancel_event.is_set():
                job.status = JobStatus.CANCELLED
                job.finished_at = _now()
                job._done.set()
                return
            job.status = JobStatus.RUNNING
            job.phase = "Apertura progetto"
            job.started_at = _now()
            self._persist_best_effort()
        self._notify(job)
        self._write_log(job, "Avvio del lavoro.")
        staged = job.workspace / ("render" + (job.output_path.suffix or ".mp4"))
        try:
            project = open_project(job.project_path)
            missing = diagnose_project(project)
            if missing:
                raise JobError("Risorse multimediali mancanti: " + "; ".join(item.message for item in missing))
            for protected in _protected_project_paths(project):
                if _same_path(protected, job.output_path):
                    raise JobError(f"La destinazione coincide con una risorsa originale del progetto: {protected}")
            timeline_file = job.workspace / "timeline.json"
            timeline_file.write_text(json.dumps(project.timeline, ensure_ascii=False, indent=2), encoding="utf-8")
            timeline = load_timeline(timeline_file)
            self._set_progress(job, "Rendering", 0.01)
            self._write_log(job, "Validazione completata; rendering avviato.")

            def report(phase, fraction):
                self._set_progress(job, phase, fraction)

            def log_line(line):
                if line.strip():
                    self._write_log(job, line)

            render_options = {
                "progress_callback": report, "cancel_event": job._cancel_event,
                "work_root": job.workspace / "render_work", "line_callback": log_line,
            }
            editor_settings = project.settings.get("editor", {})
            crossfade_style = editor_settings.get("crossfade_style", "fade") if isinstance(editor_settings, dict) else "fade"
            if not isinstance(crossfade_style, str) or crossfade_style not in CROSSFADE_STYLES:
                crossfade_style = "fade"
            try:
                renderer_parameters = inspect.signature(self.renderer).parameters.values()
                supports_style = any(
                    parameter.name == "crossfade_style" or parameter.kind == inspect.Parameter.VAR_KEYWORD
                    for parameter in renderer_parameters
                )
            except (TypeError, ValueError):
                supports_style = self.renderer is render_timeline
            if supports_style:
                render_options["crossfade_style"] = crossfade_style
            self.renderer(timeline, staged, **render_options)
            if job._cancel_event.is_set():
                raise RenderCancelledError("Rendering annullato prima della pubblicazione.")
            self._set_progress(job, "Controllo del video", 0.985)
            if self.quality_check:
                self._validate_output(
                    staged,
                    require_audio=timeline.audio_asset is not None or timeline.background_audio is not None,
                )
            if job._cancel_event.is_set():
                raise RenderCancelledError("Rendering annullato prima della pubblicazione.")
            job.output_path.parent.mkdir(parents=True, exist_ok=True)
            self._publish(staged, job.output_path, job)
            try:
                staged.unlink(missing_ok=True)
            except OSError as cleanup_error:
                self._write_log(job, f"Avviso: file provvisorio non rimosso: {cleanup_error}")
            with self._lock:
                job.status = JobStatus.COMPLETED
                job.progress = 1.0
                job.phase = "Completato"
                job.finished_at = _now()
                self._persist_best_effort()
            self._write_log(job, f"Video verificato e pubblicato: {job.output_path}")
        except RenderCancelledError as exc:
            self._remove_staged(job, staged)
            with self._lock:
                job.status = JobStatus.CANCELLED
                job.phase = "Annullato"
                job.error = str(exc)
                job.finished_at = _now()
                self._persist_best_effort()
            self._write_log(job, f"Lavoro annullato: {exc}")
        except Exception as exc:
            self._remove_staged(job, staged)
            with self._lock:
                job.status = JobStatus.FAILED
                job.phase = "Errore"
                job.error = _error_message(exc)
                job.finished_at = _now()
                self._persist_best_effort()
            self._write_log(job, f"Lavoro fallito: {job.error}")
        finally:
            job._done.set()
            self._notify(job)

    def _validate_output(self, output, *, require_audio=True):
        if not output.is_file() or output.stat().st_size <= 0:
            raise JobError("FFmpeg non ha prodotto un file video valido.")
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
                 "-of", "json", str(output)], capture_output=True, text=True, check=True,
            )
            metadata = json.loads(probe.stdout)
            stream_types = {item.get("codec_type") for item in metadata.get("streams", [])}
            duration = float(metadata.get("format", {}).get("duration", "nan"))
            required_streams = {"video", "audio"} if require_audio else {"video"}
            if not required_streams.issubset(stream_types) or not math.isfinite(duration) or duration <= 0:
                expected = "video, audio e durata" if require_audio else "video e durata"
                raise JobError(f"Il file esportato non contiene {expected} validi.")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError as exc:
            detail = _error_message(exc)
            raise JobError(f"Controllo FFmpeg/FFprobe non superato: {detail}") from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise JobError(f"Impossibile verificare il video esportato: {exc}") from exc

    def _publish(self, staged, destination, job):
        """Copy verified output beside its destination, then replace atomically."""
        temporary = None
        try:
            with staged.open("rb") as source, tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, prefix=f".{destination.stem}.{job.id}.",
                suffix=".tmp", delete=False,
            ) as target:
                temporary = Path(target.name)
                while True:
                    if job._cancel_event.is_set():
                        raise RenderCancelledError("Rendering annullato durante la pubblicazione.")
                    chunk = source.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            with self._lock:
                if job._cancel_event.is_set():
                    raise RenderCancelledError("Rendering annullato prima della pubblicazione.")
                os.replace(temporary, destination)
                job.published = True
                self._persist_best_effort()
            temporary = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    self._write_log(job, f"Avviso: file di pubblicazione temporaneo non rimosso: {cleanup_error}")


__all__ = ["REGISTRY_FILENAME", "REGISTRY_FORMAT", "REGISTRY_VERSION", "JobStatus", "JobError",
           "JobRegistryError", "RenderJob", "JobManager"]
