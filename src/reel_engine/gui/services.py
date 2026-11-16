"""GUI-facing helpers kept separate from the core project and render services."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    available: bool
    detail: str


def _command_status(name, executable):
    if not executable:
        return DependencyStatus(name, False, "Non trovato nel PATH")
    try:
        result = subprocess.run(
            [executable, "-version"], capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DependencyStatus(name, False, f"Non avviabile: {exc}")
    output = (result.stdout or result.stderr).splitlines()
    if result.returncode != 0:
        return DependencyStatus(name, False, f"Comando terminato con codice {result.returncode}")
    return DependencyStatus(name, True, output[0].strip() if output else "Versione non rilevata")


def diagnose_dependencies():
    """Inspect the Python/Qt runtime and external executables without changing the system."""
    qt_available = importlib.util.find_spec("PySide6") is not None
    qt_detail = "PySide6 disponibile" if qt_available else "Non installato nell'ambiente Python corrente"
    if qt_available:
        try:
            from PySide6 import __version__ as qt_version
            qt_detail = f"PySide6 {qt_version}"
        except (ImportError, AttributeError):
            pass
    return [
        DependencyStatus("Python", True, sys.version.split()[0]),
        DependencyStatus("PySide6", qt_available, qt_detail),
        _command_status("FFmpeg", shutil.which("ffmpeg")),
        _command_status("FFprobe", shutil.which("ffprobe")),
    ]
