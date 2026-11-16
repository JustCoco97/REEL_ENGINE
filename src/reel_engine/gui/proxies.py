"""Project-local, regenerable video proxy cache for the desktop preview."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path


class ProxyCancelled(Exception):
    """Proxy generation was cancelled before publishing its output."""


class ProxyCache:
    """Resolve and generate lightweight proxies without changing project media refs."""

    def __init__(self, project_folder):
        self.root = Path(project_folder).expanduser().resolve(strict=False) / ".reel_engine" / "proxies"

    @staticmethod
    def fingerprint(source):
        path = Path(source).expanduser().resolve(strict=True)
        stat = path.stat()
        return (str(path).casefold(), stat.st_size, stat.st_mtime_ns,
                getattr(stat, "st_ctime_ns", int(stat.st_ctime * 1_000_000_000)),
                getattr(stat, "st_ino", 0))

    def proxy_path(self, source):
        fingerprint = self.fingerprint(source)
        key = hashlib.sha256(repr(fingerprint).encode("utf-8")).hexdigest()
        return self.root / f"{key}.mp4"

    def proxy_for(self, source):
        """Return a complete proxy for the current source revision, if present."""
        try:
            target = self.proxy_path(source)
        except (OSError, ValueError):
            return None
        return target if target.is_file() and target.stat().st_size > 0 else None

    def generate(self, source, *, cancel_event=None, ffmpeg=None):
        """Generate atomically; return an existing valid cache entry when possible."""
        source = Path(source).expanduser().resolve(strict=True)
        before = self.fingerprint(source)
        target = self.proxy_path(source)
        if target.is_file() and target.stat().st_size > 0:
            return target
        executable = ffmpeg or shutil.which("ffmpeg")
        if not executable:
            raise FileNotFoundError("FFmpeg non trovato: impossibile generare il proxy.")
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f".{target.stem}.{uuid.uuid4().hex}.tmp.mp4"
        command = [
            executable, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", "scale=w='min(960,iw)':h='min(960,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
            "-c:v", "libx264", "-threads", "2", "-preset", "ultrafast", "-crf", "30",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary),
        ]
        try:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            while process.poll() is None:
                if cancel_event is not None and cancel_event.wait(0.1):
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    process.communicate()
                    raise ProxyCancelled("Generazione proxy annullata.")
            _stdout, stderr = process.communicate()
            if process.returncode != 0:
                details = stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(details or f"FFmpeg è terminato con codice {process.returncode}.")
            if cancel_event is not None and cancel_event.is_set():
                raise ProxyCancelled("Generazione proxy annullata.")
            if self.fingerprint(source) != before:
                raise RuntimeError("Il file originale è cambiato durante la generazione; proxy scartato.")
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError("FFmpeg non ha prodotto un proxy valido.")
            os.replace(temporary, target)
            return target
        finally:
            temporary.unlink(missing_ok=True)
