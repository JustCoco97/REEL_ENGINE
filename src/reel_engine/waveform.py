"""Small, cached audio peak envelopes generated with the installed FFmpeg."""
from __future__ import annotations
import array
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import threading
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

_MAGIC = b"REWF"
_HEADER = struct.Struct("<4s d I I")
_VERSION = 1

class WaveformError(RuntimeError):
    """Raised when FFmpeg cannot decode an audio asset for its waveform."""

@dataclass(frozen=True)
class WaveformData:
    """Compact signed min/max PCM peaks, one pair per time bucket."""
    duration: float
    bins_per_second: int
    peaks: bytes

    @property
    def bin_count(self) -> int:
        return len(self.peaks) // 4

class WaveformCache:
    """Disk and bounded-memory cache for source audio envelopes.

    Cache identity includes canonical path and file metadata. Track volume and
    source offset are presentation settings and do not invalidate decoded data.
    """
    def __init__(self, cache_dir, *, bins_per_second=100, sample_rate=8000,
                 max_memory_bytes=64 * 1024 * 1024, ffmpeg_path=None):
        self.cache_dir = Path(cache_dir)
        self.bins_per_second = int(bins_per_second)
        self.sample_rate = int(sample_rate)
        self.max_memory_bytes = max(1024, int(max_memory_bytes))
        self.ffmpeg_path = ffmpeg_path
        if self.bins_per_second <= 0 or self.sample_rate <= 0 or self.sample_rate % self.bins_per_second:
            raise ValueError("sample_rate deve essere divisibile per bins_per_second")
        self._memory = OrderedDict()
        self._memory_bytes = 0
        self._lock = threading.RLock()

    @staticmethod
    def default_cache_dir():
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "REEL_ENGINE" / "waveforms"
        return Path.home() / ".cache" / "REEL_ENGINE" / "waveforms"

    def cache_key(self, audio_path):
        try:
            path = Path(audio_path).expanduser().resolve(strict=True)
            stat = path.stat()
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        identity = "|".join((
            os.path.normcase(str(path)).casefold(), str(stat.st_size),
            str(stat.st_mtime_ns), str(stat.st_ctime_ns), str(_VERSION),
            str(self.bins_per_second), str(self.sample_rate),
        ))
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def peek(self, key_or_path):
        key = (key_or_path if isinstance(key_or_path, str) and len(key_or_path) == 64
               else self.cache_key(key_or_path))
        if not key:
            return None
        with self._lock:
            data = self._memory.get(key)
            if data is not None:
                self._memory.move_to_end(key)
            return data

    def load_or_generate(self, audio_path):
        path = Path(audio_path).expanduser().resolve(strict=True)
        key = self.cache_key(path)
        if not key:
            raise WaveformError(f"File audio non disponibile: {path}")
        with self._lock:
            cached = self._memory.get(key)
            if cached is not None:
                self._memory.move_to_end(key)
                return key, cached
            cache_path = self.cache_dir / key[:2] / f"{key}.rwave"
            cached = self._read_disk(cache_path)
            if cached is not None:
                self._remember(key, cached)
                return key, cached
            data = self._decode(path)
            self._write_disk(cache_path, data)
            self._remember(key, data)
            return key, data

    def _remember(self, key, data):
        old = self._memory.pop(key, None)
        if old is not None:
            self._memory_bytes -= len(old.peaks)
        self._memory[key] = data
        self._memory_bytes += len(data.peaks)
        while self._memory and self._memory_bytes > self.max_memory_bytes:
            _, removed = self._memory.popitem(last=False)
            self._memory_bytes -= len(removed.peaks)

    def _read_disk(self, path):
        try:
            with path.open("rb") as stream:
                header = stream.read(_HEADER.size)
                if len(header) != _HEADER.size:
                    return None
                magic, duration, rate, count = _HEADER.unpack(header)
                if magic != _MAGIC or rate != self.bins_per_second:
                    return None
                peaks = zlib.decompress(stream.read())
                if len(peaks) != count * 4:
                    return None
                return WaveformData(duration, rate, peaks)
        except (OSError, ValueError, zlib.error, struct.error):
            return None

    def _write_disk(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(_HEADER.pack(_MAGIC, data.duration, data.bins_per_second, data.bin_count))
                stream.write(zlib.compress(data.peaks, level=3))
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _decode(self, path):
        ffmpeg = self.ffmpeg_path or shutil.which("ffmpeg")
        if not ffmpeg:
            raise WaveformError("FFmpeg non è disponibile nel PATH; impossibile creare la waveform.")
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path),
                   "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(self.sample_rate),
                   "-f", "s16le", "pipe:1"]
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise WaveformError(f"Impossibile avviare FFmpeg: {exc}") from exc
        samples_per_bin = self.sample_rate // self.bins_per_second
        peaks = bytearray()
        sample_tail = array.array("h")
        byte_tail = b""
        assert process.stdout is not None
        try:
            while True:
                block = process.stdout.read(64 * 1024)
                if not block:
                    break
                block = byte_tail + block
                usable = len(block) & ~1
                byte_tail = block[usable:]
                samples = array.array("h")
                samples.frombytes(block[:usable])
                if sys.byteorder == "big":
                    samples.byteswap()
                if sample_tail:
                    samples = sample_tail + samples
                complete = len(samples) - len(samples) % samples_per_bin
                for offset in range(0, complete, samples_per_bin):
                    bucket = samples[offset:offset + samples_per_bin]
                    peaks.extend(struct.pack("<hh", min(bucket), max(bucket)))
                sample_tail = samples[complete:]
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            process.stdout.close()
        return_code = process.wait()
        if return_code:
            raise WaveformError(f"FFmpeg non riesce a decodificare il file audio: {path}")
        if sample_tail:
            peaks.extend(struct.pack("<hh", min(sample_tail), max(sample_tail)))
        count = len(peaks) // 4
        if count == 0:
            raise WaveformError(f"Il file non contiene una traccia audio leggibile: {path}")
        return WaveformData(count / self.bins_per_second, self.bins_per_second, bytes(peaks))

_DEFAULT_CACHE = None
_DEFAULT_CACHE_LOCK = threading.Lock()
def default_waveform_cache():
    global _DEFAULT_CACHE
    with _DEFAULT_CACHE_LOCK:
        if _DEFAULT_CACHE is None:
            _DEFAULT_CACHE = WaveformCache(WaveformCache.default_cache_dir())
        return _DEFAULT_CACHE

__all__ = ["WaveformCache", "WaveformData", "WaveformError", "default_waveform_cache"]