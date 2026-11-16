"""Persistent project documents for REEL ENGINE."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_FORMAT = "reel-engine-project"
PROJECT_FORMAT_VERSION = 1
TIMELINE_SCHEMA_VERSION = 1
PROJECT_FILENAME = "project.json"
_KNOWN_TOP_LEVEL = {"format", "format_version", "name", "settings", "timeline"}
_MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff",
                   ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wav", ".mp3",
                   ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".aif"}
_FONT_SUFFIXES = {".ttf", ".otf", ".ttc"}


class ProjectError(ValueError):
    """Base error for invalid or unreadable project documents."""


class ProjectFormatError(ProjectError):
    """The project file is malformed or uses an unsupported format version."""


class ProjectSaveError(OSError):
    """The project could not be saved atomically."""


@dataclass
class MissingResource:
    kind: str
    owner: str
    reference: str
    resolved_path: Path

    @property
    def message(self) -> str:
        return f"Risorsa {self.kind} mancante ({self.owner}): {self.resolved_path}"


@dataclass
class Project:
    name: str
    folder: Path
    timeline: dict
    settings: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.folder / PROJECT_FILENAME


def _empty_timeline(canvas=None):
    return {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "canvas": copy.deepcopy(canvas) if canvas is not None else {"width": 1080, "height": 1920, "fps": 30},
        "audio": None,
        "scenes": [],
    }


def _validate_timeline(timeline):
    if not isinstance(timeline, dict):
        raise ProjectFormatError("timeline deve essere un oggetto JSON.")
    version = timeline.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != TIMELINE_SCHEMA_VERSION:
        raise ProjectFormatError("La timeline del progetto deve usare schema_version 1.")
    if not isinstance(timeline.get("scenes"), list):
        raise ProjectFormatError("timeline.scenes deve essere una lista.")
    if "canvas" in timeline and not isinstance(timeline["canvas"], dict):
        raise ProjectFormatError("timeline.canvas deve essere un oggetto.")
    return timeline


def _media_references(timeline):
    """Yield (mapping, key, kind, owner) for media and explicit font file refs."""
    audio = timeline.get("audio")
    if isinstance(audio, str) and audio.strip():
        # The v1 primary audio field is itself a string path.
        yield timeline, "audio", "audio", "audio"
    background = timeline.get("background_audio")
    if isinstance(background, dict) and isinstance(background.get("asset"), str):
        yield background, "asset", "audio", "background_audio"
    for index, scene in enumerate(timeline.get("scenes", [])):
        if isinstance(scene, dict) and isinstance(scene.get("asset"), str):
            kind = scene.get("type") if scene.get("type") in {"image", "video"} else "media"
            yield scene, "asset", kind, f"scenes[{index}]"
    for group in ("texts", "subtitles"):
        for index, item in enumerate(timeline.get(group, [])):
            if not isinstance(item, dict):
                continue
            font = item.get("font")
            if isinstance(font, str) and Path(font).suffix.lower() in _FONT_SUFFIXES:
                yield item, "font", "font", f"{group}[{index}]"


def _resolve_reference(value, folder):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = folder / path
    return path.resolve(strict=False)


def _portable_timeline(timeline, folder):
    result = copy.deepcopy(timeline)
    root = folder.resolve(strict=False)
    for mapping, key, _kind, _owner in _media_references(result):
        raw = mapping[key]
        path = Path(raw).expanduser()
        resolved = path.resolve(strict=False) if path.is_absolute() else (root / path).resolve(strict=False)
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            mapping[key] = str(resolved)
        else:
            mapping[key] = relative.as_posix()
    return result


def _expanded_timeline(timeline, folder):
    result = copy.deepcopy(timeline)
    for mapping, key, _kind, _owner in _media_references(result):
        mapping[key] = str(_resolve_reference(mapping[key], folder))
    return result


def _document(project):
    payload = copy.deepcopy(project.extra)
    payload.update({
        "format": PROJECT_FORMAT,
        "format_version": PROJECT_FORMAT_VERSION,
        "name": project.name,
        "settings": copy.deepcopy(project.settings),
        "timeline": _portable_timeline(_validate_timeline(project.timeline), project.folder),
    })
    return payload


def create_project(name, folder, *, canvas=None):
    """Create and persist a new empty project in *folder*."""
    if not isinstance(name, str) or not name.strip():
        raise ProjectError("Il nome del progetto non può essere vuoto.")
    project_folder = Path(folder).expanduser().resolve(strict=False)
    project_folder.mkdir(parents=True, exist_ok=True)
    project = Project(name.strip(), project_folder, _empty_timeline(canvas))
    if project.path.exists():
        raise FileExistsError(f"Esiste già un progetto: {project.path}")
    save_project(project)
    return project


def save_project(project):
    """Atomically save a project, returning its project.json path."""
    if not isinstance(project, Project):
        raise TypeError("project deve essere un oggetto Project.")
    if not isinstance(project.name, str) or not project.name.strip():
        raise ProjectError("Il nome del progetto non può essere vuoto.")
    if not isinstance(project.settings, dict) or not isinstance(project.extra, dict):
        raise ProjectError("settings ed extra devono essere oggetti.")
    project.folder = Path(project.folder).expanduser().resolve(strict=False)
    project.folder.mkdir(parents=True, exist_ok=True)
    target = project.path
    temp_path = None
    try:
        payload = json.dumps(_document(project), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=project.folder,
            prefix=f".{PROJECT_FILENAME}.", suffix=".tmp", delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
        return target
    except (OSError, TypeError, ValueError) as exc:
        raise ProjectSaveError(f"Salvataggio atomico non riuscito per {target}: {exc}") from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def open_project(path):
    """Load a project and expand relative media references against its folder."""
    project_path = Path(path).expanduser().resolve(strict=False)
    try:
        with project_path.open("r", encoding="utf-8-sig") as stream:
            payload = json.load(stream)
    except json.JSONDecodeError as exc:
        raise ProjectFormatError(f"JSON progetto non valido in {project_path}: {exc}") from exc
    except OSError:
        raise
    if not isinstance(payload, dict):
        raise ProjectFormatError("Il progetto deve essere un oggetto JSON.")
    if payload.get("format") != PROJECT_FORMAT:
        raise ProjectFormatError(f"Formato progetto non riconosciuto: {payload.get('format')!r}.")
    version = payload.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != PROJECT_FORMAT_VERSION:
        raise ProjectFormatError(f"Versione progetto non supportata: {version!r}.")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProjectFormatError("name progetto mancante o non valido.")
    settings = payload.get("settings", {})
    if not isinstance(settings, dict):
        raise ProjectFormatError("settings deve essere un oggetto JSON.")
    timeline = _validate_timeline(payload.get("timeline"))
    folder = project_path.parent
    extra = {key: copy.deepcopy(value) for key, value in payload.items() if key not in _KNOWN_TOP_LEVEL}
    return Project(name, folder, _expanded_timeline(timeline, folder), copy.deepcopy(settings), extra)


def update_project_timeline(project, timeline):
    """Replace the editable v1 timeline while preserving project settings."""
    _validate_timeline(timeline)
    project.timeline = copy.deepcopy(timeline)
    return project


def diagnose_project(project):
    """Return missing referenced media; missing files do not prevent opening."""
    missing = []
    for mapping, key, kind, owner in _media_references(project.timeline):
        reference = mapping[key]
        resolved = _resolve_reference(reference, project.folder)
        if not resolved.is_file():
            missing.append(MissingResource(kind, owner, str(reference), resolved))
    return missing


def collect_project_media(project):
    """Copy external media into the project and atomically persist portable refs.

    Existing files are never changed. Missing references abort before copying;
    on copy/save failure newly published assets are removed and the model is
    restored to its previous references.
    """
    if not isinstance(project, Project):
        raise TypeError("project deve essere un oggetto Project.")
    references = list(_media_references(project.timeline))
    root = project.folder.resolve(strict=False)
    sources = []
    for mapping, key, kind, owner in references:
        source = _resolve_reference(mapping[key], root)
        if not source.is_file():
            raise FileNotFoundError(f"Risorsa {kind} mancante ({owner}): {source}")
        try:
            source.relative_to(root)
        except ValueError:
            sources.append((mapping, key, kind, source))
    staged = []
    published = []
    created_directories = []
    created_count = 0
    original_values = [(mapping, key, mapping[key]) for mapping, key, *_ in references]
    try:
        for mapping, key, kind, source in sources:
            digest = hashlib.sha256()
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            folder_name = "fonts" if kind == "font" else "media"
            target_dir = root / folder_name
            if not target_dir.exists():
                created_directories.append(target_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{digest.hexdigest()[:16]}_{source.name}"
            if target.exists():
                existing_digest = hashlib.sha256()
                with target.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        existing_digest.update(chunk)
                if existing_digest.hexdigest() != digest.hexdigest():
                    raise OSError(f"Collisione nella cache media: {target}")
            else:
                temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
                staged.append((temporary, target))
                shutil.copy2(source, temporary)
            # Keep the live model's established absolute-path convention;
            # _document() writes it relative when serializing the project.
            mapping[key] = str(target.resolve(strict=False))
        for temporary, target in staged:
            os.replace(temporary, target)
            published.append(target)
            created_count += 1
        save_project(project)
        return created_count
    except Exception:
        for mapping, key, value in original_values:
            mapping[key] = value
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)
        for target in published:
            target.unlink(missing_ok=True)
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


__all__ = [
    "PROJECT_FILENAME", "PROJECT_FORMAT", "PROJECT_FORMAT_VERSION", "Project", "ProjectError",
    "ProjectFormatError", "ProjectSaveError", "MissingResource", "create_project", "save_project",
    "open_project", "update_project_timeline", "diagnose_project", "collect_project_media",
]
