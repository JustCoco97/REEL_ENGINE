"""Visual timeline editor backed by the versioned project and render APIs."""

from __future__ import annotations

import html
import copy
import math
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import (QFileSystemWatcher, QLineF, QRectF, QSignalBlocker, QPointF, QSize, QTimer, QUrl, Qt,
                             QObject, QRunnable, QThreadPool, Signal)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QIcon, QImage, QImageReader, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QShortcut, QUndoCommand, QUndoStack,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGraphicsItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsPixmapItem, QGraphicsTextItem, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSplitter, QStackedWidget, QStyle, QTabWidget,
    QVBoxLayout, QWidget,
)

from ..jobs import JobManager, JobStatus
from ..model import IMAGE_SUFFIXES, VIDEO_SUFFIXES
from ..project import Project, collect_project_media, diagnose_project, save_project
from ..rendering import CROSSFADE_STYLES
from ..timing import frames_to_seconds, seconds_to_frames
from ..waveform import WaveformData, WaveformError, default_waveform_cache
from .proxies import ProxyCache, ProxyCancelled


_SCALE = 72.0
_LEFT = 0.0
_LANE_Y = {"scene": 44.0, "audio": 106.0, "text": 168.0, "subtitle": 230.0}
_LANE_LABEL = {"scene": "VIDEO", "audio": "AUDIO", "text": "TESTI", "subtitle": "SOTTOTITOLI"}
_LANE_COLOR = {"scene": "#187d83", "audio": "#725cad", "background_audio": "#9252aa",
               "text": "#b27632", "subtitle": "#3977a6"}
_THUMBNAIL_CACHE_LIMIT = 96
_CROSSFADE_STYLE_LABELS = {
    "fade": "Fade",
    "wipeleft": "Wipe left", "wiperight": "Wipe right", "wipeup": "Wipe up", "wipedown": "Wipe down",
    "slideleft": "Slide left", "slideright": "Slide right", "slideup": "Slide up", "slidedown": "Slide down",
    "circleopen": "Circle open", "circleclose": "Circle close", "dissolve": "Dissolve",
    "smoothleft": "Smooth left", "smoothright": "Smooth right", "fadeblack": "Fade black",
    "fadewhite": "Fade white", "radial": "Radial", "vertopen": "Vertical open",
    "vertclose": "Vertical close", "horzopen": "Horizontal open", "horzclose": "Horizontal close",
    "zoomin": "Zoom in",
}
_UNSET = object()
_TRACK_LOCK_LABELS = {
    "scene": "VIDEO", "audio": "AUDIO", "background_audio": "MUSICA",
    "text": "TESTI", "subtitle": "SOTTOTITOLI",
}
_TRACK_LOCK_DEFAULTS = {key: False for key in _TRACK_LOCK_LABELS}
_TRACK_FIELDS = {
    "scene": ("scenes", "transitions"), "audio": ("audio",),
    "background_audio": ("background_audio",), "text": ("texts",),
    "subtitle": ("subtitles",),
}


def _video_duration(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("FFprobe non è disponibile nel PATH; non posso rilevare la durata del video.")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    duration = float(result.stdout.strip())
    if duration <= 0:
        raise ValueError("FFprobe ha restituito una durata video non valida.")
    return duration


def timeline_scene_starts(timeline):
    """Return scene start times, honoring existing crossfade overlap durations."""
    scenes = timeline.get("scenes", [])
    starts = []
    current = 0.0
    transitions = timeline.get("transitions", {})
    by_after = {item.get("after"): item for item in transitions.get("between", [])
                if isinstance(item, dict)} if isinstance(transitions, dict) else {}
    for index, scene in enumerate(scenes):
        starts.append(current)
        duration = float(scene.get("duration", 0))
        transition = by_after.get(scene.get("id"), {}) if index < len(scenes) - 1 else {}
        overlap = float(transition.get("duration", 0)) if transition.get("type") == "crossfade" else 0.0
        current += duration - overlap
    return starts


def timeline_visual_duration(timeline):
    scenes = timeline.get("scenes", [])
    starts = timeline_scene_starts(timeline)
    return (starts[-1] + float(scenes[-1].get("duration", 0))) if scenes else 0.0


@lru_cache(maxsize=128)
def _probe_audio_duration(path, modified_ns, file_size):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True, timeout=30,
        )
        return max(0.0, float(result.stdout.strip()))
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def _background_audio_timeline_end(timeline):
    track = timeline.get("background_audio")
    if not isinstance(track, dict) or not track.get("asset"):
        return 0.0
    try:
        requested = track.get("duration")
        if requested is not None:
            duration = max(0.0, float(requested))
        else:
            path = Path(str(track["asset"])).expanduser().resolve(strict=True)
            stat = path.stat()
            source_duration = _probe_audio_duration(str(path), stat.st_mtime_ns, stat.st_size)
            duration = max(0.0, source_duration - float(track.get("source_in", 0)))
        fps = float((timeline.get("canvas") or {}).get("fps", 30))
        if fps > 0 and duration > 0:
            duration = math.ceil(duration * fps - 1e-9) / fps
        return duration
    except (OSError, TypeError, ValueError, RuntimeError):
        return 0.0


def timeline_duration(timeline):
    """Return the timeline end, including background music after the last scene."""
    return max(timeline_visual_duration(timeline), _background_audio_timeline_end(timeline))


def _fit_crossfade_frame(image, size):
    layer = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    layer.fill(Qt.GlobalColor.transparent)
    pixmap = QPixmap.fromImage(image).scaled(
        size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(layer)
    painter.drawPixmap((size.width() - pixmap.width()) // 2, (size.height() - pixmap.height()) // 2, pixmap)
    painter.end()
    return layer


def _soft_xfade_layer(image, progress, direction):
    """Return the incoming frame with a feathered xfade wipe alpha mask."""
    width, height = image.width(), image.height()
    edge = width * progress if direction == "left" else width * (1.0 - progress)
    feather = max(2.0, width * 0.035)
    gradient = QLinearGradient(0, 0, width, 0)
    if direction == "left":
        gradient.setColorAt(0.0, QColor(255, 255, 255, 255))
        gradient.setColorAt(max(0.0, min(1.0, (edge - feather) / width)), QColor(255, 255, 255, 255))
        gradient.setColorAt(max(0.0, min(1.0, (edge + feather) / width)), QColor(255, 255, 255, 0))
        gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
    else:
        gradient.setColorAt(0.0, QColor(255, 255, 255, 0))
        gradient.setColorAt(max(0.0, min(1.0, (edge - feather) / width)), QColor(255, 255, 255, 0))
        gradient.setColorAt(max(0.0, min(1.0, (edge + feather) / width)), QColor(255, 255, 255, 255))
        gradient.setColorAt(1.0, QColor(255, 255, 255, 255))
    mask = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    mask.fill(Qt.GlobalColor.transparent)
    mask_painter = QPainter(mask)
    mask_painter.fillRect(0, 0, width, height, gradient)
    mask_painter.end()
    result = image.copy()
    painter = QPainter(result)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, mask)
    painter.end()
    return result


def _crossfade_frame(images, size, progress, style="fade"):
    """Compose a preview frame for the selected FFmpeg xfade style."""
    progress = min(1.0, max(0.0, float(progress)))
    style = style if style in CROSSFADE_STYLES else "fade"
    outgoing, incoming = (_fit_crossfade_frame(image, size) for image in images)
    result = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor("#080d11"))
    painter = QPainter(result)

    if style in {"fadeblack", "fadewhite"}:
        veil = QColor("black" if style == "fadeblack" else "white")
        painter.drawImage(0, 0, outgoing)
        if progress <= 0.5:
            painter.setOpacity(progress * 2.0)
            painter.fillRect(result.rect(), veil)
        else:
            painter.fillRect(result.rect(), veil)
            painter.setOpacity((progress - 0.5) * 2.0)
            painter.drawImage(0, 0, incoming)
    elif style.startswith("slide"):
        width, height = size.width(), size.height()
        dx = width * progress if style == "slideright" else -width * progress
        dy = height * progress if style == "slidedown" else -height * progress
        incoming_x = width - width * progress if style == "slideleft" else -width + width * progress
        incoming_y = height - height * progress if style == "slideup" else -height + height * progress
        if style in {"slideleft", "slideright"}:
            painter.drawImage(int(dx), 0, outgoing)
            painter.drawImage(int(incoming_x), 0, incoming)
        else:
            painter.drawImage(0, int(dy), outgoing)
            painter.drawImage(0, int(incoming_y), incoming)
    elif style == "zoomin":
        width, height = size.width(), size.height()
        painter.setOpacity(1.0 - progress)
        painter.drawImage(0, 0, outgoing)
        scale = 0.82 + 0.18 * progress
        scaled = incoming.scaled(max(1, int(width * scale)), max(1, int(height * scale)),
                                 Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter.setOpacity(progress)
        painter.drawImage((width - scaled.width()) // 2, (height - scaled.height()) // 2, scaled)
    else:
        painter.drawImage(0, 0, outgoing)
        if style in {"fade", "dissolve"}:
            if style == "fade":
                painter.setOpacity(progress)
                painter.drawImage(0, 0, incoming)
            else:
                path = QPainterPath()
                columns, rows = 12, 8
                cell_w, cell_h = size.width() / columns, size.height() / rows
                for row in range(rows):
                    for column in range(columns):
                        threshold = (((column * 37 + row * 19 + column * row * 7) % (columns * rows)) + 1) / (columns * rows)
                        if threshold <= progress:
                            path.addRect(column * cell_w, row * cell_h, cell_w + 1, cell_h + 1)
                painter.save()
                painter.setClipPath(path)
                painter.drawImage(0, 0, incoming)
                painter.restore()
        elif style in {"smoothleft", "smoothright"}:
            painter.drawImage(0, 0, _soft_xfade_layer(incoming, progress, "left" if style == "smoothleft" else "right"))
        else:
            width, height = size.width(), size.height()
            path = QPainterPath()
            full = QPainterPath()
            full.addRect(0, 0, width, height)
            if style.startswith("wipe"):
                if style == "wipeleft":
                    path.addRect(0, 0, width * progress, height)
                elif style == "wiperight":
                    path.addRect(width * (1 - progress), 0, width * progress, height)
                elif style == "wipeup":
                    path.addRect(0, height * (1 - progress), width, height * progress)
                else:
                    path.addRect(0, 0, width, height * progress)
            elif style in {"circleopen", "circleclose"}:
                radius = math.hypot(width, height) * 0.5 * (progress if style == "circleopen" else 1 - progress)
                path.addEllipse(width / 2 - radius, height / 2 - radius, radius * 2, radius * 2)
                if style == "circleclose":
                    full.setFillRule(Qt.FillRule.OddEvenFill)
                    full.addEllipse(width / 2 - radius, height / 2 - radius, radius * 2, radius * 2)
                    path = full
            elif style == "radial":
                path.moveTo(width / 2, height / 2)
                path.arcTo(0, 0, width, height, 90, -360 * progress)
                path.closeSubpath()
            elif style == "vertopen":
                path.addRect(0, height * (1 - progress) / 2, width, height * progress)
            elif style == "vertclose":
                path.addRect(0, 0, width, height * progress / 2)
                path.addRect(0, height * (1 - progress / 2), width, height * progress / 2)
            elif style == "horzopen":
                path.addRect(width * (1 - progress) / 2, 0, width * progress, height)
            elif style == "horzclose":
                path.addRect(0, 0, width * progress / 2, height)
                path.addRect(width * (1 - progress / 2), 0, width * progress / 2, height)
            painter.save()
            painter.setClipPath(path)
            painter.drawImage(0, 0, incoming)
            painter.restore()
    painter.end()
    return result


class _ClipItem(QGraphicsRectItem):
    def __init__(self, rect, kind, index, callback, color, label, thumbnail=None):
        x, y, width, height = rect
        super().__init__(0, 0, width, height)
        self.setPos(x, y)
        self.kind, self.index, self.callback = kind, index, callback
        self.setBrush(QBrush(QColor(color)))
        self.setPen(QPen(QColor("#c2d7de"), 1))
        self._normal_brush = QBrush(self.brush())
        self._normal_pen = QPen(self.pen())
        self._thumbnail_source = thumbnail if isinstance(thumbnail, QPixmap) else None
        self._thumbnail_item = None
        self._title_backing = None
        self._grips = []
        self._label = label
        self._font = QFont("Segoe UI", 8, QFont.Weight.DemiBold)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)

        has_thumbnail = isinstance(thumbnail, QPixmap) and not thumbnail.isNull()
        if has_thumbnail:
            target = QSize(max(1, int(width - 4)), max(1, int(height - 4)))
            scaled = thumbnail.scaled(
                target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            cropped = scaled.copy(
                max(0, (scaled.width() - target.width()) // 2),
                max(0, (scaled.height() - target.height()) // 2),
                target.width(), target.height(),
            )
            image_item = QGraphicsPixmapItem(cropped, self)
            image_item.setPos(2, 2)
            image_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._thumbnail_item = image_item

            self._title_backing = QGraphicsRectItem(1, 1, max(1, width - 2), min(18, height - 2), self)
            self._title_backing.setBrush(QBrush(QColor(0, 0, 0, 155)))
            self._title_backing.setPen(QPen(Qt.PenStyle.NoPen))
            self._title_backing.setZValue(1)

        available_width = max(10, int(width - 8))
        display_label = QFontMetrics(self._font).elidedText(
            label, Qt.TextElideMode.ElideRight, available_width
        )
        text = QGraphicsTextItem(display_label, self)
        self._title_item = text
        text.setDefaultTextColor(QColor("#ffffff"))
        text.setFont(self._font)
        text.setPos(4, 2)
        text.setTextWidth(available_width)
        text.setZValue(2 if has_thumbnail else 1)

        # Subtle edge grips make the trim interaction discoverable without
        # changing the timeline model or intercepting pointer events.
        grip_width = min(4.0, max(2.0, width / 5))
        for grip_x in (0.0, max(0.0, width - grip_width)):
            grip = QGraphicsRectItem(grip_x, 0, grip_width, height, self)
            grip.setBrush(QBrush(QColor(255, 255, 255, 105)))
            grip.setPen(QPen(Qt.PenStyle.NoPen))
            grip.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            grip.setZValue(3)
            self._grips.append(grip)

    def set_visual_width(self, width):
        width = max(9.0, float(width))
        height = self.rect().height()
        self.setRect(0, 0, width, height)
        if self._thumbnail_source is not None and self._thumbnail_item is not None:
            target = QSize(max(1, int(width - 4)), max(1, int(height - 4)))
            scaled = self._thumbnail_source.scaled(
                target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            cropped = scaled.copy(
                max(0, (scaled.width() - target.width()) // 2),
                max(0, (scaled.height() - target.height()) // 2),
                target.width(), target.height(),
            )
            self._thumbnail_item.setPixmap(cropped)
        if self._title_backing is not None:
            self._title_backing.setRect(1, 1, max(1, width - 2), min(18, height - 2))
        available_width = max(10, int(width - 8))
        self._title_item.setPlainText(QFontMetrics(self._font).elidedText(
            self._label, Qt.TextElideMode.ElideRight, available_width
        ))
        self._title_item.setTextWidth(available_width)
        grip_width = min(4.0, max(2.0, width / 5))
        for grip, grip_x in zip(self._grips, (0.0, max(0.0, width - grip_width))):
            grip.setRect(grip_x, 0, grip_width, height)

    def set_highlighted(self, highlighted):
        if highlighted:
            self.setBrush(self._normal_brush.color().lighter(145))
            self.setPen(QPen(QColor("#ffe082"), 3))
        else:
            self.setBrush(self._normal_brush)
            self.setPen(self._normal_pen)

    def mousePressEvent(self, event):
        self.callback(self.kind, self.index)
        super().mousePressEvent(event)


class _WaveformGraphicsItem(QGraphicsItem):
    """Viewport-aware audio peaks; painting work is bounded by visible pixels."""

    def __init__(self, width, height, *, source_in=0.0, volume=1.0, data=None):
        super().__init__()
        self._bounds = QRectF(0, 0, max(1.0, float(width)), max(1.0, float(height)))
        self.source_in = max(0.0, float(source_in))
        self.volume = max(0.0, min(1.0, float(volume)))
        self.data = data
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(0.5)
        self.setToolTip("Waveform audio" if data else "Preparazione waveform audio…")

    def boundingRect(self):
        return self._bounds

    def set_waveform(self, data):
        self.data = data
        self.setToolTip("Waveform audio")
        self.update()

    def set_error(self, message):
        self.setToolTip(f"Waveform non disponibile: {message}")
        self.update()

    def paint(self, painter, option, widget=None):
        if self.data is None:
            return
        exposed = option.exposedRect.intersected(self._bounds)
        if exposed.isEmpty():
            return
        scale = max(0.05, abs(painter.worldTransform().m11()))
        visible_pixels = max(1, int((exposed.width() * scale) + 0.999))
        columns = min(2048, visible_pixels)
        step = exposed.width() / columns
        center = self._bounds.center().y()
        amplitude = max(1.0, self._bounds.height() * 0.43) * self.volume
        rate = self.data.bins_per_second
        count = self.data.bin_count
        peaks = self.data.peaks
        lines = []
        painter.save()
        painter.setClipRect(exposed)
        painter.setPen(QPen(QColor("#c8f4df"), max(1.0, 1.0 / scale)))
        for column in range(columns):
            x0 = exposed.left() + column * step
            x1 = min(exposed.right(), x0 + step)
            time0 = self.source_in + x0 / _SCALE
            time1 = self.source_in + x1 / _SCALE
            first = max(0, int(time0 * rate))
            last = min(count, max(first + 1, int(time1 * rate + 0.999)))
            if first >= count:
                break
            low, high = 32767, -32768
            for index in range(first, last):
                minimum, maximum = struct.unpack_from("<hh", peaks, index * 4)
                low = min(low, minimum)
                high = max(high, maximum)
            x = (x0 + x1) / 2
            y_top = center - max(0, high) / 32768 * amplitude
            y_bottom = center - min(0, low) / 32768 * amplitude
            lines.append(QLineF(x, y_top, x, max(y_top + 1.0 / scale, y_bottom)))
        if lines:
            painter.drawLines(lines)
        painter.restore()


class _WaveformWorkerSignals(QObject):
    ready = Signal(str, object, object)


class _WaveformWorker(QRunnable):
    def __init__(self, cache, audio_path, key):
        super().__init__()
        self.cache, self.audio_path, self.key = cache, audio_path, key
        self.signals = _WaveformWorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            _, data = self.cache.load_or_generate(self.audio_path)
            self.signals.ready.emit(self.key, data, None)
        except Exception as exc:
            self.signals.ready.emit(self.key, None, str(exc))


class _ProxyWorkerSignals(QObject):
    finished = Signal(str, str, str, str)


class _ProxyWorker(QRunnable):
    def __init__(self, cache, source, project_folder, cancel_event):
        super().__init__()
        self.cache = cache
        self.source = str(source)
        self.project_folder = str(project_folder)
        self.cancel_event = cancel_event
        self.signals = _ProxyWorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            proxy = self.cache.generate(self.source, cancel_event=self.cancel_event)
            self.signals.finished.emit(self.source, str(proxy), "", self.project_folder)
        except ProxyCancelled:
            self.signals.finished.emit(self.source, "", "annullato", self.project_folder)
        except Exception as exc:
            self.signals.finished.emit(self.source, "", str(exc), self.project_folder)


class _VideoThumbnailSignals(QObject):
    ready = Signal(str, bytes)


class _VideoThumbnailWorker(QRunnable):
    def __init__(self, source, cancel_event):
        super().__init__()
        self.source = str(source)
        self.cancel_event = cancel_event
        self.signals = _VideoThumbnailSignals()

    def run(self):
        executable = shutil.which("ffmpeg")
        if not executable:
            self.signals.ready.emit(self.source, b"")
            return
        temporary = tempfile.NamedTemporaryFile(prefix="reel-engine-thumb-", suffix=".jpg", delete=False)
        temporary_path = Path(temporary.name)
        temporary.close()
        command = [executable, "-hide_banner", "-loglevel", "error", "-y", "-ss", "0", "-i", self.source,
                   "-frames:v", "1", "-vf", "scale=160:90:force_original_aspect_ratio=decrease",
                   "-threads", "1", "-vcodec", "mjpeg", str(temporary_path)]
        process = None
        try:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            while process.poll() is None:
                if self.cancel_event.wait(0.05):
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    self.signals.ready.emit(self.source, b"")
                    return
            if process.returncode == 0 and temporary_path.is_file() and temporary_path.stat().st_size and not self.cancel_event.is_set():
                self.signals.ready.emit(self.source, temporary_path.read_bytes())
            else:
                self.signals.ready.emit(self.source, b"")
        except (OSError, subprocess.SubprocessError):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            self.signals.ready.emit(self.source, b"")
        finally:
            temporary_path.unlink(missing_ok=True)


class TimelineWidget(QGraphicsView):
    selection_requested = Signal(str, int)
    seek_requested = Signal(float)
    reorder_requested = Signal(int, int)
    trim_requested = Signal(int, str, int)
    trim_preview_requested = Signal(object, float)
    trim_started = Signal()
    trim_cancelled = Signal(float)
    playhead_drag_started = Signal()
    split_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("visualTimeline")
        self.setMinimumHeight(300)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setRenderHints(self.renderHints())
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QBrush(QColor("#101a21")))
        self._scale = _SCALE
        self._timeline = {}
        self._track_locks = dict(_TRACK_LOCK_DEFAULTS)
        self._playhead_item = None
        self._playhead_handle_item = None
        self._thumbnail_cache = OrderedDict()
        self.waveform_cache = default_waveform_cache()
        self._waveform_items_by_key = {}
        self._waveform_jobs = {}
        self._audio_file_watcher = QFileSystemWatcher(self)
        self._audio_file_watcher.fileChanged.connect(self._audio_file_changed)
        self._audio_refresh_path = None
        self._audio_refresh_timer = QTimer(self)
        self._audio_refresh_timer.setSingleShot(True)
        self._audio_refresh_timer.setInterval(100)
        self._audio_refresh_timer.timeout.connect(self._refresh_audio_file)
        self._pressed_scene_index = None
        self._press_position = None
        self._press_scene_x = None
        self._trim_side = None
        self._trim_origin_timeline = None
        self._trim_origin_selected = None
        self._trim_start_playhead = 0.0
        self._trim_preview_playhead = 0.0
        self._trim_last_delta = None
        self._scene_clip_items = {}
        self._playhead_seconds = 0.0
        self._dragging_scene = False
        self._dragging_playhead = False
        self._playhead_drag_origin = 0.0
        self._drop_indicator = None
        self._snap_guide = None
        self.snap_enabled = True
        self.snap_tolerance_px = 10.0
        self.set_timeline({})

    def set_track_locks(self, track_locks, *, rebuild=True):
        self._track_locks = {key: track_locks.get(key) is True for key in _TRACK_LOCK_LABELS}
        if rebuild:
            horizontal = self.horizontalScrollBar().value()
            vertical = self.verticalScrollBar().value()
            self.set_timeline(self._timeline, selected=self._selected, playhead=self._playhead_seconds)
            self.horizontalScrollBar().setValue(horizontal)
            self.verticalScrollBar().setValue(vertical)

    def _track_is_locked(self, kind):
        return bool(self._track_locks.get(kind, False))

    def set_timeline(self, timeline, *, selected=None, playhead=0.0):
        self._timeline = timeline
        self._playhead_seconds = float(playhead)
        self._scene_clip_items = {}
        self._waveform_items_by_key = {}
        self._snap_guide = None
        self._drop_indicator = None
        self._dragging_playhead = False
        scene = QGraphicsScene(self)
        total = max(timeline_duration(timeline), 10.0)
        width = _LEFT + total * self._scale + 40
        scene.setSceneRect(0, 0, width, 298)
        for lane, y in _LANE_Y.items():
            locked = self._track_is_locked(lane)
            if lane == "audio" and self._track_is_locked("background_audio"):
                locked = True
            scene.addRect(0, y, width, 54, QPen(QColor("#233640")), QBrush(QColor("#15232c")))
            if locked:
                scene.addRect(0, y, width, 54, QPen(Qt.PenStyle.NoPen), QBrush(QColor(10, 18, 23, 36)))
        for second in range(0, int(total) + 1, 5):
            x = _LEFT + second * self._scale
            scene.addLine(x, 26, x, 286, QPen(QColor("#2a3f49"), 1, Qt.PenStyle.DotLine))
            label = scene.addText(f"{second // 60:02d}:{second % 60:02d}")
            label.setDefaultTextColor(QColor("#91a7b1"))
            label.setFont(QFont("Segoe UI", 8))
            label.setPos(x + 3, 5)

        starts = timeline_scene_starts(timeline)
        for index, item in enumerate(timeline.get("scenes", [])):
            x = _LEFT + starts[index] * self._scale
            duration = max(0.05, float(item.get("duration", 3)))
            thumbnail = self._thumbnail_for(item.get("asset")) if item.get("type") == "image" else None
            clip = self._add_clip(scene, "scene", index, x, _LANE_Y["scene"], duration,
                                  _LANE_COLOR["scene"], Path(str(item.get("asset", "Scena"))).name,
                                  thumbnail=thumbnail, locked=self._track_is_locked("scene"))
            self._scene_clip_items[index] = clip

        audio = timeline.get("audio")
        if isinstance(audio, str) and audio:
            clip = self._add_clip(scene, "audio", 0, _LEFT, _LANE_Y["audio"], max(total, 1),
                                  _LANE_COLOR["audio"], "Voce · " + Path(audio).name, height=22, locked=self._track_is_locked("audio"))
            self._attach_waveform(clip, audio)
        background = timeline.get("background_audio")
        if isinstance(background, dict) and background.get("asset"):
            clip = self._add_clip(scene, "background_audio", 0, _LEFT, _LANE_Y["audio"] + 27,
                                  float(background.get("duration") or max(total, 1)), _LANE_COLOR["background_audio"],
                                  "Musica · " + Path(background["asset"]).name, height=22,
                                  locked=self._track_is_locked("background_audio"))
            self._attach_waveform(
                clip, background["asset"], source_in=float(background.get("source_in", 0)),
                volume=float(background.get("volume", 1)),
            )
        for kind, color, lane in (("text", _LANE_COLOR["text"], "texts"),
                                  ("subtitle", _LANE_COLOR["subtitle"], "subtitles")):
            for index, item in enumerate(timeline.get(lane, [])):
                start, end = float(item.get("start", 0)), float(item.get("end", 1))
                content = str(item.get("text", "")) or "(vuoto)"
                self._add_clip(scene, kind, index, _LEFT + start * self._scale, _LANE_Y[kind],
                               max(0.05, end - start), color, content.replace("\n", " "),
                                locked=self._track_is_locked(kind))
        self._playhead_item = scene.addLine(_LEFT + playhead * self._scale, 24,
                                            _LEFT + playhead * self._scale, 286,
                                            QPen(QColor("#ff625f"), 2))
        self._playhead_handle_item = scene.addEllipse(
            _LEFT + playhead * self._scale - 5, 15, 10, 9,
            QPen(QColor("#ff625f"), 1), QBrush(QColor("#ff625f")),
        )
        self._playhead_handle_item.setZValue(12)
        self.setScene(scene)
        self._selected = selected
        self._sync_audio_file_watcher(timeline)
        self._update_selection()

    def zoom_by(self, factor):
        """Zoom around the visible timeline center without shifting its time."""
        factor = float(factor)
        if factor <= 0:
            return
        center_seconds = (self.horizontalScrollBar().value() + self.viewport().width() / 2) / self._scale
        self._scale = min(288.0, max(12.0, self._scale * factor))
        self.set_timeline(self._timeline, selected=self._selected, playhead=self._playhead_seconds)
        self.horizontalScrollBar().setValue(max(0, round(center_seconds * self._scale - self.viewport().width() / 2)))

    def zoom_to_fit(self):
        total = max(timeline_visual_duration(self._timeline), 10.0)
        self._scale = min(288.0, max(12.0, (self.viewport().width() - 24) / total))
        self.set_timeline(self._timeline, selected=self._selected, playhead=self._playhead_seconds)
        self.horizontalScrollBar().setValue(0)

    def _sync_audio_file_watcher(self, timeline):
        paths = []
        audio = timeline.get("audio")
        if isinstance(audio, str) and audio:
            paths.append(audio)
        background = timeline.get("background_audio")
        if isinstance(background, dict) and background.get("asset"):
            paths.append(background["asset"])
        desired = set()
        for raw_path in paths:
            try:
                path = Path(str(raw_path)).expanduser().resolve(strict=True)
                if path.is_file():
                    desired.add(str(path))
            except (OSError, RuntimeError):
                continue
        current = set(self._audio_file_watcher.files())
        removed = current - desired
        added = desired - current
        if removed:
            self._audio_file_watcher.removePaths(sorted(removed))
        if added:
            self._audio_file_watcher.addPaths(sorted(added))

    def _audio_file_changed(self, raw_path):
        self._audio_file_watcher.removePath(raw_path)
        self._audio_refresh_path = raw_path
        self._audio_refresh_timer.start()

    def _refresh_audio_file(self):
        raw_path = self._audio_refresh_path
        self._audio_refresh_path = None
        if not raw_path:
            return
        if Path(raw_path).is_file():
            self._audio_file_watcher.addPath(raw_path)
        self.set_timeline(self._timeline, selected=self._selected,
                          playhead=self._playhead_seconds)

    def _attach_waveform(self, clip, audio_path, *, source_in=0.0, volume=1.0):
        key = self.waveform_cache.cache_key(audio_path)
        if not key:
            return
        data = self.waveform_cache.peek(key)
        item = _WaveformGraphicsItem(
            clip.rect().width(), clip.rect().height(), source_in=source_in,
            volume=volume, data=data,
        )
        item.setParentItem(clip)
        self._waveform_items_by_key.setdefault(key, []).append(item)
        if data is None:
            self._request_waveform(audio_path, key)

    def _request_waveform(self, audio_path, key):
        if key in self._waveform_jobs:
            return
        worker = _WaveformWorker(self.waveform_cache, str(audio_path), key)
        worker.signals.ready.connect(self._waveform_ready)
        self._waveform_jobs[key] = worker
        QThreadPool.globalInstance().start(worker)

    def _waveform_ready(self, key, data, error):
        self._waveform_jobs.pop(key, None)
        for item in self._waveform_items_by_key.get(key, []):
            if data is not None:
                item.set_waveform(data)
            else:
                item.set_error(str(error or "errore di decodifica"))

    def _thumbnail_for(self, raw_path):
        if not raw_path:
            return None
        path = Path(str(raw_path)).expanduser()
        try:
            path = path.resolve(strict=True)
            stat = path.stat()
        except (OSError, RuntimeError):
            return None
        key = (str(path).casefold(), stat.st_mtime_ns, stat.st_size)
        cached = self._thumbnail_cache.get(key)
        if cached is not None:
            self._thumbnail_cache.move_to_end(key)
            return cached

        pixmap = self._decode_thumbnail(path)
        if pixmap is None or pixmap.isNull():
            return None
        self._thumbnail_cache[key] = pixmap
        while len(self._thumbnail_cache) > _THUMBNAIL_CACHE_LIMIT:
            self._thumbnail_cache.popitem(last=False)
        return pixmap

    @staticmethod
    def _decode_thumbnail(path):
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        source_size = reader.size()
        if source_size.isValid() and source_size.width() > 0 and source_size.height() > 0:
            scale = min(320 / source_size.width(), 96 / source_size.height(), 1.0)
            if scale < 1.0:
                reader.setScaledSize(QSize(
                    max(1, round(source_size.width() * scale)),
                    max(1, round(source_size.height() * scale)),
                ))
        image = reader.read()
        if image.isNull():
            return None
        pixmap = QPixmap.fromImage(image)
        return pixmap if not pixmap.isNull() else None

    def _add_clip(self, scene, kind, index, x, y, duration, color, label, *, height=44,
                  thumbnail=None, locked=False):
        width = max(9.0, duration * self._scale)
        display_label = ("[LOCKED] " + label) if locked else label
        clip = _ClipItem((x, y + 4, width, height), kind, index,
                         self.selection_requested.emit, color, display_label, thumbnail=thumbnail)
        clip.setToolTip(label + (" [LOCKED]" if locked else ""))
        if locked:
            clip.setOpacity(0.58)
        scene.addItem(clip)
        return clip

    def set_playhead(self, seconds):
        self._playhead_seconds = max(0.0, float(seconds))
        if self._playhead_item is not None:
            x = _LEFT + self._playhead_seconds * self._scale
            self._playhead_item.setLine(x, 24, x, 286)
            if self._playhead_handle_item is not None:
                self._playhead_handle_item.setRect(x - 5, 15, 10, 9)

    def set_snap_enabled(self, enabled):
        self.snap_enabled = bool(enabled)
        if not self.snap_enabled:
            self._hide_snap_guide()

    def _pixel_distance(self, first_seconds, second_seconds):
        first = self.mapFromScene(QPointF(_LEFT + first_seconds * self._scale, 0)).x()
        second = self.mapFromScene(QPointF(_LEFT + second_seconds * self._scale, 0)).x()
        return abs(first - second)

    def _snap_points(self, *, timeline=None, include_playhead=True, playhead_seconds=None):
        timeline = self._timeline if timeline is None else timeline
        scenes = timeline.get("scenes", [])
        starts = timeline_scene_starts(timeline)
        points = []
        transitions = timeline.get("transitions") or {}
        between = transitions.get("between", []) if isinstance(transitions, dict) else []
        by_after = {item.get("after"): item for item in between if isinstance(item, dict)}
        for index, (start, scene) in enumerate(zip(starts, scenes)):
            end = start + float(scene.get("duration", 0))
            points.extend((start, end))
            if index < len(scenes) - 1:
                transition = by_after.get(scene.get("id"), {})
                if transition.get("type") == "crossfade":
                    overlap = float(transition.get("duration", 0))
                    points.extend((end - overlap, starts[index + 1]))
        if isinstance(transitions, dict):
            start_transition = transitions.get("start")
            end_transition = transitions.get("end")
            if isinstance(start_transition, dict):
                points.append(float(start_transition.get("duration", 0)))
            if isinstance(end_transition, dict) and scenes:
                total = timeline_visual_duration(timeline)
                points.append(total - float(end_transition.get("duration", 0)))
        if include_playhead and scenes:
            points.append(self._playhead_seconds if playhead_seconds is None else float(playhead_seconds))
        return sorted({max(0.0, float(point)) for point in points})

    def _snap_seconds(self, seconds, *, timeline=None, include_playhead=True,
                      playhead_seconds=None, exclude_seconds=()):
        timeline = self._timeline if timeline is None else timeline
        fps = float((timeline.get("canvas") or {}).get("fps", 30))
        raw_seconds = max(0.0, float(seconds))
        aligned = frames_to_seconds(seconds_to_frames(raw_seconds, fps), fps)
        if not self.snap_enabled:
            self._hide_snap_guide()
            return aligned, False
        excluded = tuple(float(value) for value in exclude_seconds)
        candidates = [point for point in self._snap_points(
            timeline=timeline, include_playhead=include_playhead,
            playhead_seconds=playhead_seconds
        ) if not any(abs(point - value) < 0.5 / fps for value in excluded)]
        if not candidates:
            self._hide_snap_guide()
            return aligned, False
        nearest = min(candidates, key=lambda point: self._pixel_distance(raw_seconds, point))
        if self._pixel_distance(raw_seconds, nearest) <= self.snap_tolerance_px:
            snapped = frames_to_seconds(seconds_to_frames(nearest, fps), fps)
            self._show_snap_guide(snapped)
            return snapped, True
        self._hide_snap_guide()
        return aligned, False

    def _show_snap_guide(self, seconds):
        if self.scene() is None:
            return
        x = _LEFT + max(0.0, float(seconds)) * self._scale
        if self._snap_guide is None or self._snap_guide.scene() is not self.scene():
            self._snap_guide = self.scene().addLine(
                x, 24, x, 286, QPen(QColor("#ffe082"), 1.5, Qt.PenStyle.DashLine)
            )
            self._snap_guide.setZValue(20)
        else:
            self._snap_guide.setLine(x, 24, x, 286)

    def _hide_snap_guide(self):
        if self._snap_guide is not None and self._snap_guide.scene() is not None:
            self._snap_guide.scene().removeItem(self._snap_guide)
        self._snap_guide = None

    def _trim_delta_at(self, point_x):
        index, side = self._pressed_scene_index, self._trim_side
        timeline = self._trim_origin_timeline
        fps = float((timeline.get("canvas") or {}).get("fps", 30))
        starts = timeline_scene_starts(timeline)
        scene = timeline["scenes"][index]
        edge = starts[index] + (float(scene.get("duration", 0)) if side == "right" else 0.0)
        raw_delta = seconds_to_frames((point_x - self._press_scene_x) / self._scale, fps)
        raw_edge = edge + frames_to_seconds(raw_delta, fps)
        snapped, did_snap = self._snap_seconds(
            raw_edge, timeline=timeline, include_playhead=True,
            playhead_seconds=self._trim_start_playhead, exclude_seconds=(edge,),
        )
        delta = seconds_to_frames(snapped - edge, fps) if did_snap else raw_delta
        return delta, snapped, did_snap

    def set_selected(self, kind, index):
        self._selected = (kind, index)
        self._update_selection()

    def _update_selection(self):
        if self.scene() is None:
            return
        for item in self.scene().items():
            if isinstance(item, _ClipItem):
                selected = (item.kind, item.index) == self._selected
                item.setSelected(selected)
                item.set_highlighted(selected)

    def mousePressEvent(self, event):
        point = self.mapToScene(event.position().toPoint())
        if point.x() < _LEFT and any(
            y <= point.y() < y + 54 for y in _LANE_Y.values()
        ):
            # Track headers are informational. Do not let an empty/background
            # scene click clear the selected clip when the user clicks a label.
            event.accept()
            return

        if (event.button() == Qt.MouseButton.LeftButton and point.y() < 24
                and self._pixel_distance(self._playhead_seconds,
                                         max(0.0, (point.x() - _LEFT) / self._scale))
                    <= self.snap_tolerance_px):
            self._dragging_playhead = True
            self._playhead_drag_origin = self._playhead_seconds
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.playhead_drag_started.emit()
            self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
            event.accept()
            return

        item = self.itemAt(event.position().toPoint())
        while item is not None and not isinstance(item, _ClipItem):
            item = item.parentItem()
        if isinstance(item, _ClipItem):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            playhead_before_selection = self._playhead_seconds
            self.selection_requested.emit(item.kind, item.index)
            if self._track_is_locked(item.kind):
                event.accept()
                return
            if item.kind == "scene" and event.button() == Qt.MouseButton.LeftButton:
                bounds = item.sceneBoundingRect()
                local_x = point.x() - bounds.left()
                edge_width = min(12.0, max(6.0, bounds.width() / 4))
                self._trim_side = (
                    "left" if local_x <= edge_width else
                    "right" if local_x >= bounds.width() - edge_width else None
                )
                self._pressed_scene_index = item.index
                self._press_position = event.position().toPoint()
                self._press_scene_x = point.x()
                self._dragging_scene = False
                if self._trim_side is not None:
                    # Selecting a clip normally seeks to its start; for an edge
                    # gesture preserve the user's existing playhead snap target.
                    self.seek_requested.emit(playhead_before_selection)
                    self._trim_origin_timeline = copy.deepcopy(self._timeline)
                    self._trim_origin_selected = self._selected
                    self._trim_start_playhead = self._playhead_seconds
                    self._trim_preview_playhead = self._playhead_seconds
                    self._trim_last_delta = None
                    self.trim_started.emit()
                    self.viewport().setCursor(
                        Qt.CursorShape.ForbiddenCursor
                        if self._trim_side == "left" and item.index == 0
                        else Qt.CursorShape.SizeHorCursor
                    )
            event.accept()
            return
        if point.x() >= _LEFT:
            self.seek_requested.emit(max(0.0, (point.x() - _LEFT) / self._scale))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        point = self.mapToScene(event.position().toPoint())
        if self._dragging_playhead and event.buttons() & Qt.MouseButton.LeftButton:
            seconds = max(0.0, (point.x() - _LEFT) / self._scale)
            snapped, _ = self._snap_seconds(seconds, include_playhead=False)
            self.set_playhead(snapped)
            self.seek_requested.emit(snapped)
            event.accept()
            return
        if (self._pressed_scene_index is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            distance = (event.position().toPoint() - self._press_position).manhattanLength()
            if self._trim_side is not None:
                self.viewport().setCursor(
                    Qt.CursorShape.ForbiddenCursor
                    if self._trim_side == "left" and self._pressed_scene_index == 0
                    else Qt.CursorShape.SizeHorCursor
                )
                delta_frames, snap_time, did_snap = self._trim_delta_at(point.x())
                fps = float((self._trim_origin_timeline.get("canvas") or {}).get("fps", 30))
                if delta_frames != self._trim_last_delta:
                    candidate = self._trim_preview_candidate(delta_frames)
                    self._trim_last_delta = delta_frames
                    if candidate is not None:
                        if did_snap:
                            self._show_snap_guide(snap_time)
                        self._apply_trim_preview_geometry(candidate)
                        pointer_time = frames_to_seconds(
                            seconds_to_frames(max(0.0, (point.x() - _LEFT) / self._scale), fps), fps
                        )
                        self._trim_preview_playhead = min(
                            pointer_time, timeline_duration(candidate)
                        )
                        self.set_playhead(self._trim_preview_playhead)
                        self.trim_preview_requested.emit(candidate, self._trim_preview_playhead)
                event.accept()
                return
            if self._dragging_scene or distance >= QApplication.startDragDistance():
                self._dragging_scene = True
                self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                seconds = max(0.0, (point.x() - _LEFT) / self._scale)
                snapped, did_snap = self._snap_seconds(seconds, include_playhead=True)
                insertion_x = _LEFT + snapped * self._scale if did_snap else point.x()
                insertion = self._insertion_index_at(insertion_x)
                if did_snap:
                    self._show_snap_guide(snapped)
                self._show_drop_indicator(insertion)
                event.accept()
                return
        else:
            seconds = max(0.0, (point.x() - _LEFT) / self._scale)
            if (point.y() < 24 and self._pixel_distance(self._playhead_seconds, seconds)
                    <= self.snap_tolerance_px):
                self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
                event.accept()
                return
            item = self.itemAt(event.position().toPoint())
            while item is not None and not isinstance(item, _ClipItem):
                item = item.parentItem()
            if isinstance(item, _ClipItem) and item.kind == "scene" and not self._track_is_locked("scene"):
                bounds = item.sceneBoundingRect()
                local_x = point.x() - bounds.left()
                edge_width = min(12.0, max(6.0, bounds.width() / 4))
                if local_x <= edge_width:
                    self.viewport().setCursor(
                        Qt.CursorShape.ForbiddenCursor if item.index == 0
                        else Qt.CursorShape.SizeHorCursor
                    )
                    event.accept()
                    return
                if local_x >= bounds.width() - edge_width:
                    self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
                    event.accept()
                    return
            self.viewport().unsetCursor()
        super().mouseMoveEvent(event)

    def _trim_preview_candidate(self, delta_frames):
        timeline = self._trim_origin_timeline
        scenes = timeline.get("scenes", [])
        index, side = self._pressed_scene_index, self._trim_side
        if index is None or index >= len(scenes) or (side == "left" and index == 0):
            return None
        fps = float((timeline.get("canvas") or {}).get("fps", 30))
        original = scenes[index]
        duration_frames = seconds_to_frames(float(original.get("duration", 0)), fps)
        delta_frames = int(delta_frames)
        candidate = copy.deepcopy(timeline)
        if side == "right":
            candidate["scenes"][index]["duration"] = frames_to_seconds(
                max(1, duration_frames + delta_frames), fps
            )
        else:
            previous = scenes[index - 1]
            previous_frames = seconds_to_frames(float(previous.get("duration", 0)), fps)
            source_frames = seconds_to_frames(float(original.get("source_in", 0)), fps)
            delta_frames = max(delta_frames, 1 - previous_frames)
            delta_frames = min(delta_frames, duration_frames - 1)
            if original.get("type") == "video":
                delta_frames = max(delta_frames, -source_frames)
            candidate["scenes"][index - 1]["duration"] = frames_to_seconds(
                previous_frames + delta_frames, fps
            )
            candidate["scenes"][index]["duration"] = frames_to_seconds(
                duration_frames - delta_frames, fps
            )
            if original.get("type") == "video":
                candidate["scenes"][index]["source_in"] = frames_to_seconds(
                    source_frames + delta_frames, fps
                )
        return candidate

    def _apply_trim_preview_geometry(self, timeline):
        self._timeline = timeline
        starts = timeline_scene_starts(timeline)
        for index, scene in enumerate(timeline.get("scenes", [])):
            clip = self._scene_clip_items.get(index)
            if clip is None:
                continue
            clip.setPos(_LEFT + starts[index] * self._scale, _LANE_Y["scene"] + 4)
            clip.set_visual_width(float(scene.get("duration", 0)) * self._scale)
        total = max(timeline_duration(timeline), 10.0)
        if self.scene() is not None:
            self.scene().setSceneRect(0, 0, _LEFT + total * self._scale + 40, 298)
        self._update_selection()

    def finish_trim_preview(self):
        self._hide_snap_guide()
        self._trim_origin_timeline = None
        self._trim_origin_selected = None
        self._trim_last_delta = None

    def cancel_trim_preview(self):
        if self._trim_origin_timeline is None:
            return
        origin = self._trim_origin_timeline
        selected = self._trim_origin_selected
        playhead = self._trim_start_playhead
        self.set_timeline(origin, selected=selected, playhead=playhead)
        self.trim_cancelled.emit(playhead)
        self.finish_trim_preview()

    def mouseReleaseEvent(self, event):
        if self._dragging_playhead and event.button() == Qt.MouseButton.LeftButton:
            seconds = max(0.0, (self.mapToScene(event.position().toPoint()).x() - _LEFT) / self._scale)
            snapped, _ = self._snap_seconds(seconds, include_playhead=False)
            self.set_playhead(snapped)
            self.seek_requested.emit(snapped)
            self._dragging_playhead = False
            self._hide_snap_guide()
            self.viewport().unsetCursor()
            event.accept()
            return
        if self._pressed_scene_index is not None and event.button() == Qt.MouseButton.LeftButton:
            if self._trim_side is not None:
                point = self.mapToScene(event.position().toPoint())
                delta_frames, _, _ = self._trim_delta_at(point.x())
                if delta_frames:
                    self.trim_requested.emit(self._pressed_scene_index, self._trim_side, delta_frames)
                else:
                    self.cancel_trim_preview()
            elif self._dragging_scene:
                point = self.mapToScene(event.position().toPoint())
                seconds = max(0.0, (point.x() - _LEFT) / self._scale)
                snapped, did_snap = self._snap_seconds(seconds, include_playhead=True)
                insertion_x = _LEFT + snapped * self._scale if did_snap else point.x()
                insertion = self._insertion_index_at(insertion_x)
                source = self._pressed_scene_index
                target = insertion - 1 if source < insertion else insertion
                target = min(max(0, target), max(0, len(self._timeline.get("scenes", [])) - 1))
                if target != source:
                    self.reorder_requested.emit(source, target)
            self._pressed_scene_index = None
            self._press_position = None
            self._press_scene_x = None
            self._trim_side = None
            self._dragging_scene = False
            self._hide_snap_guide()
            self.viewport().unsetCursor()
            self._remove_drop_indicator()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._dragging_playhead:
            self._dragging_playhead = False
            self._hide_snap_guide()
            self.set_playhead(self._playhead_drag_origin)
            self.seek_requested.emit(self._playhead_drag_origin)
            self.viewport().unsetCursor()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self._trim_origin_timeline is not None:
            self.cancel_trim_preview()
            self._pressed_scene_index = None
            self._press_position = None
            self._press_scene_x = None
            self._trim_side = None
            self.viewport().unsetCursor()
            event.accept()
            return
        if (event.key() == Qt.Key.Key_S
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.split_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _insertion_index_at(self, x):
        scenes = self._timeline.get("scenes", [])
        if not scenes or x <= _LEFT:
            return 0
        starts = timeline_scene_starts(self._timeline)
        centers = [start + float(scene.get("duration", 0)) / 2
                   for start, scene in zip(starts, scenes)]
        time_at_x = (x - _LEFT) / self._scale
        nearest = min(range(len(centers)), key=lambda index: abs(time_at_x - centers[index]))
        return nearest if time_at_x < centers[nearest] else nearest + 1

    def _show_drop_indicator(self, insertion):
        self._remove_drop_indicator()
        scenes = self._timeline.get("scenes", [])
        starts = timeline_scene_starts(self._timeline)
        if insertion <= 0:
            x = _LEFT
        elif insertion >= len(scenes):
            x = _LEFT + timeline_visual_duration(self._timeline) * self._scale
        else:
            x = _LEFT + starts[insertion] * self._scale
        self._drop_indicator = self.scene().addLine(
            x, _LANE_Y["scene"], x, _LANE_Y["scene"] + 54,
            QPen(QColor("#ffe082"), 3),
        )
        self._drop_indicator.setZValue(10)

    def _remove_drop_indicator(self):
        if self._drop_indicator is not None and self._drop_indicator.scene() is not None:
            self.scene().removeItem(self._drop_indicator)
        self._drop_indicator = None


class _TimelineSnapshotCommand(QUndoCommand):
    """One editor action represented as before/after timeline snapshots."""

    def __init__(self, editor, label, before, after, before_selection, after_selection,
                 before_playhead, after_playhead):
        super().__init__(label)
        self.editor = editor
        self.before = copy.deepcopy(before)
        self.after = copy.deepcopy(after)
        self.before_selection = before_selection
        self.after_selection = after_selection
        self.before_playhead = float(before_playhead)
        self.after_playhead = float(after_playhead)

    def undo(self):
        self.editor._restore_timeline_snapshot(
            self.before, self.before_selection, self.before_playhead
        )

    def redo(self):
        self.editor._restore_timeline_snapshot(
            self.after, self.after_selection, self.after_playhead
        )


class TimelineEditorPage(QWidget):
    """Project editor: media references, timeline tracks, preview, save and render."""

    home_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._shutting_down = False
        self.project: Project | None = None
        self.timeline: dict = {}
        self._track_locks = dict(_TRACK_LOCK_DEFAULTS)
        self._saved_track_locks = dict(_TRACK_LOCK_DEFAULTS)
        self._use_proxies = True
        self._saved_use_proxies = True
        self._crossfade_style = "fade"
        self._saved_crossfade_style = "fade"
        self._proxy_cache = None
        self._proxy_jobs = {}
        self._proxy_cancel_events = []
        self._proxy_pool = QThreadPool(self)
        self._proxy_pool.setMaxThreadCount(1)
        self._media_thumbnail_pool = QThreadPool(self)
        self._media_thumbnail_pool.setMaxThreadCount(1)
        self._media_thumbnail_cancel_events = {}
        self._media_thumbnail_jobs = set()
        self._media_video_thumbnails = {}
        self.track_lock_buttons = {}
        self._selected = None
        self._scene_starts = []
        self._current_scene = None
        self._image_crossfade_active = False
        self._crossfade_key = None
        self._crossfade_frames = [QImage(), QImage()]
        self._crossfade_stills = OrderedDict()
        self._crossfade_pending = [None, None]
        self._crossfade_last_sync = 0.0
        self._pending_video_seek = None
        self._trim_preview_timer = QTimer(self)
        self._trim_preview_timer.setSingleShot(True)
        self._trim_preview_timer.setInterval(60)
        self._trim_preview_timer.timeout.connect(self._render_trim_preview)
        self._trim_preview_pending = None
        self._playhead = 0.0
        self._playing = False
        self._image_clock = None
        self._tail_clock = None
        self._tail_start = 0.0
        self._job_manager = None
        self._job = None
        self._job_history_snapshot = None
        self._selected_job_log_id = None
        self._scene_clipboard = None
        self._media_library_paths = []
        self._saved_media_library_paths = []
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(100)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        toolbar = QHBoxLayout()
        self.back_button = QPushButton("← Home")
        self.project_name = QLabel("Nessun progetto")
        self.project_name.setObjectName("sectionHeading")
        self.save_button = QPushButton("Salva")
        self.undo_button = QPushButton("↶ Annulla")
        self.redo_button = QPushButton("↷ Ripristina")
        self.add_media_button = QPushButton("Importa media")
        self.collect_media_button = QPushButton("Raccogli media")
        self.add_audio_button = QPushButton("+ Audio")
        self.add_music_button = QPushButton("+ Musica")
        self.add_text_button = QPushButton("+ Testo")
        self.add_subtitle_button = QPushButton("+ Sottotitolo")
        self.render_button = QPushButton("Esporta video")
        self.render_button.setObjectName("primaryButton")
        for widget in (self.back_button, self.project_name, self.undo_button, self.redo_button,
                       self.save_button, self.add_media_button, self.collect_media_button, self.add_audio_button,
                       self.add_music_button, self.add_text_button, self.add_subtitle_button,
                       self.render_button):
            toolbar.addWidget(widget, 1 if widget is self.project_name else 0)
        root.addLayout(toolbar)

        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.setObjectName("editorWorkspace")
        self.media_panel = QWidget()
        self.media_panel.setMinimumWidth(180)
        media_layout = QVBoxLayout(self.media_panel)
        media_layout.setContentsMargins(4, 8, 8, 8)
        media_heading = QLabel("LIBRERIA MEDIA")
        media_heading.setObjectName("sectionHeading")
        media_layout.addWidget(media_heading)
        self.media_search = QLineEdit()
        self.media_search.setPlaceholderText("Cerca per nome…")
        self.media_filter = QComboBox()
        self.media_filter.addItem("Tutti i media", "all")
        self.media_filter.addItem("Foto", "image")
        self.media_filter.addItem("Video", "video")
        media_layout.addWidget(self.media_search)
        media_layout.addWidget(self.media_filter)
        self.media_list = QListWidget()
        self.media_list.setObjectName("mediaLibrary")
        self.media_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.media_list.setIconSize(QSize(64, 48))
        self.media_list.setSpacing(4)
        media_layout.addWidget(self.media_list, 1)
        self.insert_selected_button = QPushButton("Aggiungi selezionati alla timeline")
        self.insert_selected_button.setObjectName("primaryButton")
        self.insert_selected_button.setEnabled(False)
        media_layout.addWidget(self.insert_selected_button)

        preview_panel = QWidget()
        left_layout = QVBoxLayout(preview_panel)
        left_layout.setContentsMargins(4, 8, 8, 8)
        preview_heading = QLabel("ANTEPRIMA")
        preview_heading.setObjectName("sectionHeading")
        left_layout.addWidget(preview_heading)
        self.preview_stack = QWidget()
        preview_grid = QGridLayout(self.preview_stack)
        preview_grid.setContentsMargins(0, 0, 0, 0)
        preview_grid.setSpacing(0)
        # Stack only the media widgets. A separate sibling overlay avoids
        # QVideoWidget's native surface covering the empty/image preview on Windows.
        self.preview_layout = QStackedWidget()
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(280, 200)
        self.image_label = QLabel("Importa un'immagine o un video per iniziare")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(280, 200)
        self.image_label.setStyleSheet("background:#080d11;color:#9aadb6")
        self.preview_layout.addWidget(self.video_widget)
        self.preview_layout.addWidget(self.image_label)
        preview_grid.addWidget(self.preview_layout, 0, 0)
        self.overlay_widget = QWidget(self.preview_stack)
        self.overlay_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.overlay_widget.setStyleSheet("background:transparent")
        self.overlay_grid = QGridLayout(self.overlay_widget)
        self.overlay_grid.setContentsMargins(20, 20, 20, 20)
        for row in range(3):
            self.overlay_grid.setRowStretch(row, 1)
        for column in range(3):
            self.overlay_grid.setColumnStretch(column, 1)
        preview_grid.addWidget(self.overlay_widget, 0, 0)
        self.overlay_widget.raise_()
        self.preview_stack.setStyleSheet("background:#080d11")
        left_layout.addWidget(self.preview_stack, 1)
        controls = QHBoxLayout()
        self.play_button = QPushButton("▶ Riproduci")
        self.time_label = QLabel("00:00 / 00:00")
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        controls.addWidget(self.play_button)
        controls.addWidget(self.position_slider, 1)
        controls.addWidget(self.time_label)
        left_layout.addLayout(controls)

        self.inspector_tabs = QTabWidget()
        self.inspector_tabs.setMinimumWidth(240)
        self.inspector_tabs.setMaximumWidth(380)
        self.inspector = QWidget()
        self.inspector_layout = QVBoxLayout(self.inspector)
        self.inspector_title = QLabel("PROPRIETÀ")
        self.inspector_title.setObjectName("sectionHeading")
        self.inspector_layout.addWidget(self.inspector_title)
        self.inspector_form = QFormLayout()
        self.inspector_layout.addLayout(self.inspector_form)
        self.apply_button = QPushButton("Applica proprietà")
        self.split_button = QPushButton("Dividi scena · S")
        self.delete_button = QPushButton("Elimina selezione")
        self.ripple_delete_button = QPushButton("Ripple Delete · Shift+Canc")
        self.ripple_delete_button.setToolTip(
            "Rimuove la scena selezionata e sposta indietro testi/sottotitoli successivi."
        )
        self.inspector_layout.addWidget(self.apply_button)
        self.inspector_layout.addWidget(self.split_button)
        self.inspector_layout.addWidget(self.delete_button)
        self.inspector_layout.addWidget(self.ripple_delete_button)
        self.inspector_layout.addStretch(1)
        self.inspector_tabs.addTab(self.inspector, "Inspector")

        self.export_panel = QWidget()
        export_layout = QVBoxLayout(self.export_panel)
        self.job_status_label = QLabel("Nessuna esportazione recente.")
        self.job_phase_label = QLabel("In attesa")
        self.job_progress = QProgressBar()
        self.job_progress.setRange(0, 100)
        self.job_progress.setValue(0)
        self.cancel_job_button = QPushButton("Annulla esportazione")
        self.cancel_job_button.setEnabled(False)
        self.job_history = QListWidget()
        self.job_history.setObjectName("exportHistory")
        self.open_job_log_button = QPushButton("Mostra log selezionato")
        self.open_job_log_button.setEnabled(False)
        self.job_log_view = QPlainTextEdit()
        self.job_log_view.setReadOnly(True)
        self.job_log_view.setMaximumHeight(130)
        export_layout.addWidget(self.job_status_label)
        export_layout.addWidget(self.job_phase_label)
        export_layout.addWidget(self.job_progress)
        export_layout.addWidget(self.cancel_job_button)
        export_layout.addWidget(QLabel("STORICO ESPORTAZIONI"))
        export_layout.addWidget(self.job_history, 1)
        export_layout.addWidget(self.open_job_log_button)
        export_layout.addWidget(self.job_log_view)
        self.inspector_tabs.addTab(self.export_panel, "Esportazioni")

        workspace.addWidget(self.media_panel)
        workspace.addWidget(preview_panel)
        workspace.addWidget(self.inspector_tabs)
        workspace.setStretchFactor(0, 0)
        workspace.setStretchFactor(1, 1)
        workspace.setStretchFactor(2, 0)
        workspace.setSizes([230, 620, 280])

        self.editor_splitter = QSplitter(Qt.Orientation.Vertical)
        self.editor_splitter.addWidget(workspace)
        timeline_panel = QWidget()
        timeline_layout = QVBoxLayout(timeline_panel)
        timeline_layout.setContentsMargins(0, 4, 0, 0)

        timeline_header = QHBoxLayout()
        timeline_heading = QLabel("TIMELINE  ·  scala in secondi")
        timeline_heading.setObjectName("sectionHeading")
        timeline_header.addWidget(timeline_heading)
        for key, label in _TRACK_LOCK_LABELS.items():
            button = QPushButton(f"{label}: LIBERA")
            button.setCheckable(True)
            button.setToolTip(f"Blocca o sblocca la traccia {label}")
            button.setObjectName("trackLockButton")
            button.toggled.connect(lambda locked, track=key: self._set_track_lock(track, locked))
            self.track_lock_buttons[key] = button
            timeline_header.addWidget(button)
        timeline_header.addStretch(1)
        self.snap_button = QPushButton("Snap: Attivo")
        self.snap_button.setCheckable(True)
        self.snap_button.setChecked(True)
        self.snap_button.setToolTip("Aggancia i movimenti ai punti di taglio (N)")
        timeline_header.addWidget(self.snap_button)
        self.proxy_checkbox = QCheckBox("Usa proxy")
        self.proxy_checkbox.setChecked(True)
        self.proxy_checkbox.setToolTip("Usa copie leggere dei video solo per l'anteprima; l'export usa sempre gli originali.")
        self.proxy_checkbox.setEnabled(False)
        self.proxy_checkbox.toggled.connect(self._set_proxy_enabled)
        timeline_header.addWidget(self.proxy_checkbox)
        timeline_header.addWidget(QLabel("Transizione"))
        self.crossfade_style_combo = QComboBox()
        self.crossfade_style_combo.setObjectName("crossfadeStyle")
        self.crossfade_style_combo.setToolTip("Stile usato dal preview e dall'esportazione per i crossfade")
        for style in sorted(CROSSFADE_STYLES):
            self.crossfade_style_combo.addItem(_CROSSFADE_STYLE_LABELS[style], style)
        self.crossfade_style_combo.setEnabled(False)
        self.crossfade_style_combo.currentIndexChanged.connect(self._set_crossfade_style)
        timeline_header.addWidget(self.crossfade_style_combo)
        self.zoom_out_button = QPushButton("Zoom −")
        self.zoom_out_button.setObjectName("timelineZoomOut")
        self.zoom_in_button = QPushButton("Zoom +")
        self.zoom_in_button.setObjectName("timelineZoomIn")
        self.zoom_fit_button = QPushButton("Fit")
        self.zoom_fit_button.setObjectName("timelineZoomFit")
        timeline_header.addWidget(self.zoom_out_button)
        timeline_header.addWidget(self.zoom_in_button)
        timeline_header.addWidget(self.zoom_fit_button)
        timeline_layout.addLayout(timeline_header)
        self.timeline_widget = TimelineWidget()
        self.timeline_widget.setMinimumHeight(220)
        self.track_name_column = QWidget()
        self.track_name_column.setObjectName("trackNameColumn")
        self.track_name_column.setFixedWidth(126)
        self.track_name_column.setFixedHeight(298)
        track_names_layout = QVBoxLayout(self.track_name_column)
        track_names_layout.setContentsMargins(0, 0, 4, 0)
        track_names_layout.setSpacing(0)
        track_names_layout.addSpacing(26)
        self.track_name_labels = []
        for name, height, color in (
            ("VIDEO", 54, _LANE_COLOR["scene"]),
            ("VOCE / AUDIO", 27, _LANE_COLOR["audio"]),
            ("MUSICA", 27, _LANE_COLOR["background_audio"]),
            ("TESTI", 54, _LANE_COLOR["text"]),
            ("SOTTOTITOLI", 54, _LANE_COLOR["subtitle"]),
        ):
            label = QLabel(name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedHeight(height)
            label.setStyleSheet(f"background:{color}; color:#f4f8fa; font-weight:600; border:1px solid #101820")
            track_names_layout.addWidget(label)
            self.track_name_labels.append(label)
        timeline_row = QHBoxLayout()
        timeline_row.setContentsMargins(0, 0, 0, 0)
        timeline_row.setSpacing(0)
        timeline_row.addWidget(self.track_name_column)
        timeline_row.addWidget(self.timeline_widget, 1)
        timeline_layout.addLayout(timeline_row, 1)
        self.editor_splitter.addWidget(timeline_panel)
        self.editor_splitter.setStretchFactor(0, 1)
        self.editor_splitter.setStretchFactor(1, 0)
        self.editor_splitter.setSizes([430, 260])
        root.addWidget(self.editor_splitter, 1)
        self.status_label = QLabel("Apri o crea un progetto dalla Home.")
        self.status_label.setObjectName("tagline")
        root.addWidget(self.status_label)

        self.video_audio_output = QAudioOutput(self)
        self.video_audio_output.setVolume(0.0)  # Scene audio is not part of the render mix.
        self.video_player = QMediaPlayer(self)
        self.video_player.setAudioOutput(self.video_audio_output)
        self.video_player.setVideoOutput(self.video_widget)
        self._crossfade_players = [QMediaPlayer(self), QMediaPlayer(self)]
        self._crossfade_sinks = [QVideoSink(self), QVideoSink(self)]
        self._crossfade_status_slots = (self._crossfade_status_0, self._crossfade_status_1)
        self._crossfade_frame_slots = (self._crossfade_frame_0, self._crossfade_frame_1)
        for slot, (player, sink) in enumerate(zip(self._crossfade_players, self._crossfade_sinks)):
            player.setVideoSink(sink)
            player.mediaStatusChanged.connect(self._crossfade_status_slots[slot])
            sink.videoFrameChanged.connect(self._crossfade_frame_slots[slot])
        self.voice_output = QAudioOutput(self)
        self.voice_player = QMediaPlayer(self)
        self.voice_player.setAudioOutput(self.voice_output)
        self.music_output = QAudioOutput(self)
        self.music_player = QMediaPlayer(self)
        self.music_player.setAudioOutput(self.music_output)
        self.image_timer = QTimer(self)
        self.image_timer.setInterval(50)
        self.job_timer = QTimer(self)
        self.job_timer.setInterval(250)

        self.back_button.clicked.connect(self.home_requested)
        self.save_button.clicked.connect(self.save_current_project)
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.undo_stack.canUndoChanged.connect(self._history_state_changed)
        self.undo_stack.canRedoChanged.connect(self._history_state_changed)
        self.undo_stack.cleanChanged.connect(self._update_project_dirty_indicator)
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.undo_shortcut.activated.connect(self.undo)
        self.redo_shortcut = QShortcut(QKeySequence("Ctrl+Y"), self)
        self.redo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.redo_shortcut.activated.connect(self.redo)
        self.copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.timeline_widget)
        self.copy_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.copy_shortcut.activated.connect(self.copy_selected_scene)
        self.paste_shortcut = QShortcut(QKeySequence.StandardKey.Paste, self.timeline_widget)
        self.paste_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.paste_shortcut.activated.connect(self.paste_scene)
        self.duplicate_shortcut = QShortcut(QKeySequence("Ctrl+D"), self.timeline_widget)
        self.duplicate_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.duplicate_shortcut.activated.connect(self.duplicate_selected_scene)
        self.ripple_delete_shortcut = QShortcut(QKeySequence("Shift+Delete"), self.timeline_widget)
        self.ripple_delete_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.ripple_delete_shortcut.activated.connect(self.ripple_delete_selected)
        self.delete_shortcut = QShortcut(QKeySequence.StandardKey.Delete, self.timeline_widget)
        self.delete_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.delete_shortcut.activated.connect(self.delete_selected)
        self.snap_button.toggled.connect(self._set_snap_enabled)
        self.snap_shortcut = QShortcut(QKeySequence("N"), self.timeline_widget)
        self.snap_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.snap_shortcut.activated.connect(self._toggle_snap)
        self.add_media_button.clicked.connect(self.import_video_or_images)
        self.insert_selected_button.clicked.connect(self.add_selected_media_to_timeline)
        self.media_search.textChanged.connect(self._refresh_media_library)
        self.media_filter.currentIndexChanged.connect(self._refresh_media_library)
        self.media_list.itemSelectionChanged.connect(self._media_selection_changed)
        self.media_list.itemDoubleClicked.connect(lambda _item: self.add_selected_media_to_timeline())
        self.zoom_out_button.clicked.connect(lambda: self.timeline_widget.zoom_by(1 / 1.25))
        self.zoom_in_button.clicked.connect(lambda: self.timeline_widget.zoom_by(1.25))
        self.zoom_fit_button.clicked.connect(self.timeline_widget.zoom_to_fit)
        self.cancel_job_button.clicked.connect(self.cancel_render_job)
        self.job_history.currentItemChanged.connect(self._job_history_selection_changed)
        self.open_job_log_button.clicked.connect(self._show_selected_job_log)
        self.collect_media_button.clicked.connect(self.collect_media_into_project)
        self.add_audio_button.clicked.connect(lambda: self.import_audio(background=False))
        self.add_music_button.clicked.connect(lambda: self.import_audio(background=True))
        self.add_text_button.clicked.connect(lambda: self.add_overlay("text"))
        self.add_subtitle_button.clicked.connect(lambda: self.add_overlay("subtitle"))
        self.render_button.clicked.connect(self.render_project)
        self.play_button.clicked.connect(self.toggle_playback)
        self.position_slider.sliderMoved.connect(self._slider_seek)
        self.timeline_widget.selection_requested.connect(self.select_item)
        self.timeline_widget.seek_requested.connect(self.seek_to)
        self.timeline_widget.reorder_requested.connect(self.move_scene)
        self.timeline_widget.trim_requested.connect(self._apply_trim_gesture)
        self.timeline_widget.trim_preview_requested.connect(self._queue_trim_preview)
        self.timeline_widget.trim_started.connect(self.stop_playback)
        self.timeline_widget.trim_cancelled.connect(self._cancel_trim_preview)
        self.timeline_widget.playhead_drag_started.connect(self.stop_playback)
        self.apply_button.clicked.connect(self.apply_properties)
        self.split_button.clicked.connect(self.split_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.ripple_delete_button.clicked.connect(self.ripple_delete_selected)
        self.timeline_widget.split_requested.connect(self.split_selected)
        self.image_timer.timeout.connect(self._tick_image)
        self.video_player.positionChanged.connect(self._video_position_changed)
        self.video_player.mediaStatusChanged.connect(self._video_status_changed)
        self.job_timer.timeout.connect(self._refresh_job)
        self._render_controls_enabled(False)
        self.undo_button.setEnabled(False)
        self.redo_button.setEnabled(False)

    def _set_snap_enabled(self, enabled):
        self.timeline_widget.set_snap_enabled(enabled)
        self.snap_button.setText("Snap: Attivo" if enabled else "Snap: Disattivo")

    def _toggle_snap(self):
        self.snap_button.setChecked(not self.snap_button.isChecked())

    def set_project(self, project: Project):
        self.stop_playback()
        self.undo_stack.clear()
        self.undo_stack.setClean()
        self.project = project
        self._scene_clipboard = None
        self._track_locks = self._read_project_track_locks(project)
        self._saved_track_locks = copy.deepcopy(self._track_locks)
        self._proxy_cache = ProxyCache(project.folder)
        self._use_proxies = self._read_project_proxy_setting(project)
        self._saved_use_proxies = self._use_proxies
        self._crossfade_style = self._read_project_crossfade_style(project)
        self._saved_crossfade_style = self._crossfade_style
        editor_settings = project.settings.get("editor", {})
        stored_media = editor_settings.get("media_library", []) if isinstance(editor_settings, dict) else []
        paths = [str(path) for path in stored_media if isinstance(path, str)] if isinstance(stored_media, list) else []
        for scene in project.timeline.get("scenes", []):
            asset = scene.get("asset")
            if isinstance(asset, str) and asset and asset not in paths:
                paths.append(asset)
        self._media_library_paths = paths
        self._saved_media_library_paths = copy.deepcopy(paths)
        self._refresh_media_library()
        blocker = QSignalBlocker(self.proxy_checkbox)
        self.proxy_checkbox.setChecked(self._use_proxies)
        del blocker
        blocker = QSignalBlocker(self.crossfade_style_combo)
        self.crossfade_style_combo.setCurrentIndex(max(0, self.crossfade_style_combo.findData(self._crossfade_style)))
        self.crossfade_style_combo.setEnabled(True)
        del blocker
        self._sync_track_lock_buttons()
        self.timeline = copy.deepcopy(project.timeline)
        self.timeline_widget.set_track_locks(self._track_locks, rebuild=False)
        self.project.timeline = self.timeline
        self.project_name.setText(project.name)
        self._update_project_dirty_indicator(True)
        self._selected = ("scene", 0) if self.timeline.get("scenes") else None
        self._rebuild_timeline()
        if self._selected:
            self.select_item(*self._selected)
        else:
            self._show_empty_preview()
        missing = diagnose_project(project)
        self.status_label.setText(
            f"{project.path}  ·  {len(missing)} risorse mancanti" if missing else str(project.path)
        )
        self._render_controls_enabled(True)
        self._refresh_history_controls()
        self._update_project_dirty_indicator(self.undo_stack.isClean())
        if self._use_proxies:
            self._ensure_project_proxies()

    @staticmethod
    def _read_project_track_locks(project):
        editor_settings = project.settings.get("editor", {})
        track_locks = editor_settings.get("track_locks", {}) if isinstance(editor_settings, dict) else {}
        if not isinstance(track_locks, dict):
            track_locks = {}
        return {key: track_locks.get(key) is True for key in _TRACK_LOCK_LABELS}

    @staticmethod
    def _read_project_proxy_setting(project):
        editor_settings = project.settings.get("editor", {})
        if not isinstance(editor_settings, dict):
            return True
        return editor_settings.get("use_proxies", True) is True

    @staticmethod
    def _read_project_crossfade_style(project):
        editor_settings = project.settings.get("editor", {})
        style = editor_settings.get("crossfade_style", "fade") if isinstance(editor_settings, dict) else "fade"
        return style if isinstance(style, str) and style in CROSSFADE_STYLES else "fade"

    def _write_project_track_locks(self):
        editor_settings = copy.deepcopy(self.project.settings.get("editor", {}))
        if not isinstance(editor_settings, dict):
            editor_settings = {}
        editor_settings["track_locks"] = copy.deepcopy(self._track_locks)
        editor_settings["use_proxies"] = bool(self._use_proxies)
        editor_settings["crossfade_style"] = self._crossfade_style
        editor_settings["media_library"] = list(self._media_library_paths)
        self.project.settings["editor"] = editor_settings

    def _sync_track_lock_buttons(self):
        has_project = self.project is not None
        self.proxy_checkbox.setEnabled(has_project)
        for key, button in self.track_lock_buttons.items():
            locked = self._track_locks.get(key, False)
            blocker = QSignalBlocker(button)
            button.setChecked(locked)
            button.setText(f"{_TRACK_LOCK_LABELS[key]}: {'BLOCCATA' if locked else 'LIBERA'}")
            button.setEnabled(has_project)
            del blocker
        self.add_media_button.setEnabled(has_project)
        self.insert_selected_button.setEnabled(
            has_project and bool(self.media_list.selectedItems()) and not self._track_is_locked("scene")
        )
        for label, (text, locked) in zip(self.track_name_labels, (
            ("VIDEO", self._track_is_locked("scene")),
            ("VOCE / AUDIO", self._track_is_locked("audio")),
            ("MUSICA", self._track_is_locked("background_audio")),
            ("TESTI", self._track_is_locked("text")),
            ("SOTTOTITOLI", self._track_is_locked("subtitle")),
        )):
            label.setText(text + (" · BLOCCATA" if locked else ""))
        self._media_selection_changed()
        self.add_audio_button.setEnabled(has_project and not self._track_is_locked("audio"))
        self.add_music_button.setEnabled(has_project and not self._track_is_locked("background_audio"))
        self.add_text_button.setEnabled(has_project and not self._track_is_locked("text"))
        self.add_subtitle_button.setEnabled(has_project and not self._track_is_locked("subtitle"))

    def _set_track_lock(self, kind, locked):
        if self.project is None or kind not in _TRACK_LOCK_LABELS:
            return False
        locked = bool(locked)
        if self._track_locks.get(kind, False) == locked:
            return False
        self._track_locks[kind] = locked
        self._write_project_track_locks()
        self._sync_track_lock_buttons()
        self.timeline_widget.set_track_locks(self._track_locks)
        self._show_properties()
        self._refresh_history_controls()
        self._update_project_dirty_indicator(self.undo_stack.isClean())
        label = _TRACK_LOCK_LABELS[kind]
        self.status_label.setText(f"Traccia {label} {'bloccata' if locked else 'sbloccata'} · salva il progetto per conservare lo stato.")
        return True

    def _set_proxy_enabled(self, enabled):
        if self.project is None:
            return
        enabled = bool(enabled)
        if enabled == self._use_proxies:
            return
        self._use_proxies = enabled
        editor_settings = copy.deepcopy(self.project.settings.get("editor", {}))
        if not isinstance(editor_settings, dict):
            editor_settings = {}
        editor_settings["use_proxies"] = enabled
        self.project.settings["editor"] = editor_settings
        self._update_project_dirty_indicator(self.undo_stack.isClean())
        if enabled:
            self._ensure_project_proxies()
        if self._scene_index_at(self._playhead) is not None:
            self.seek_to(self._playhead, preserve_selection=True)
        self.status_label.setText(
            f"Proxy {'attivati' if enabled else 'disattivati'} per l'anteprima · salva per conservare la preferenza."
        )

    def _set_crossfade_style(self, _index):
        if self.project is None:
            return
        style = self.crossfade_style_combo.currentData()
        if style not in CROSSFADE_STYLES or style == self._crossfade_style:
            return
        self._crossfade_style = style
        editor_settings = copy.deepcopy(self.project.settings.get("editor", {}))
        if not isinstance(editor_settings, dict):
            editor_settings = {}
        editor_settings["crossfade_style"] = style
        self.project.settings["editor"] = editor_settings
        self._update_project_dirty_indicator(self.undo_stack.isClean())
        self._show_image_crossfade(self._playhead)
        self.status_label.setText(f"Stile crossfade: {_CROSSFADE_STYLE_LABELS[style]} · salva per conservare la preferenza.")

    def _preview_source_path(self, original):
        original = Path(original)
        if self._use_proxies and self._proxy_cache is not None:
            proxy = self._proxy_cache.proxy_for(original)
            if proxy is not None:
                return proxy
            self._request_video_proxy(original)
        return original

    def _ensure_project_proxies(self):
        if self.project is None or self._proxy_cache is None:
            return
        for scene in self.timeline.get("scenes", []):
            if scene.get("type") == "video":
                self._request_video_proxy(scene.get("asset", ""))

    def _request_video_proxy(self, source):
        if self._shutting_down or self._proxy_cache is None:
            return
        try:
            source_path = Path(source).expanduser().resolve(strict=True)
            existing = self._proxy_cache.proxy_for(source_path)
        except (OSError, ValueError):
            return
        if existing is not None:
            return
        job_key = (str(Path(self.project.folder).resolve(strict=False)).casefold(),
                   str(source_path).casefold())
        if job_key in self._proxy_jobs:
            return
        cancel_event = threading.Event()
        self._proxy_jobs[job_key] = cancel_event
        self._proxy_cancel_events.append(cancel_event)
        worker = _ProxyWorker(self._proxy_cache, source_path, self.project.folder, cancel_event)
        worker.signals.finished.connect(self._proxy_generation_finished)
        self._proxy_pool.start(worker)
        self.status_label.setText(f"Genero proxy in background: {source_path.name}")

    def _proxy_generation_finished(self, source, proxy, error, project_folder):
        job_key = (str(Path(project_folder).resolve(strict=False)).casefold(),
                   str(Path(source).resolve(strict=False)).casefold())
        cancel_event = self._proxy_jobs.pop(job_key, None)
        if cancel_event in self._proxy_cancel_events:
            self._proxy_cancel_events.remove(cancel_event)
        if self._shutting_down:
            return
        if not encoded:
            return
        if error and error != "annullato":
            if self.project is not None and str(self.project.folder) == project_folder:
                self.status_label.setText(
                    f"Proxy non disponibile per {Path(source).name}: {error} · uso l'originale nell'anteprima."
                )
            return
        if not proxy or self.project is None or str(self.project.folder) != project_folder:
            return
        matches = any(
            scene.get("type") == "video"
            and str(Path(scene.get("asset", "")).resolve(strict=False)).casefold()
            == str(Path(source).resolve(strict=False)).casefold()
            for scene in self.timeline.get("scenes", [])
        )
        if matches and self._use_proxies:
            self.seek_to(self._playhead, preserve_selection=True)
            self.status_label.setText(f"Proxy pronto: {Path(source).name} · l'export userà l'originale.")

    def closeEvent(self, event):
        self.shutdown_for_teardown()
        super().closeEvent(event)

    def close(self):
        # QWidget.close() does not deliver closeEvent for a never-shown widget.
        # Run the same idempotent cleanup explicitly for embedded/test instances.
        self.shutdown_for_teardown()
        return super().close()

    def shutdown_for_teardown(self):
        """Stop asynchronous/media activity and detach callbacks before Qt teardown."""
        if self._shutting_down:
            return
        self._shutting_down = True
        for timer_name in ("_trim_preview_timer", "image_timer", "job_timer"):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()
        for cancel_event in self._proxy_cancel_events:
            cancel_event.set()
        self._proxy_pool.clear()
        for cancel_event in self._media_thumbnail_cancel_events.values():
            cancel_event.set()
        self._media_thumbnail_pool.clear()
        for player in (self.video_player, *self._crossfade_players,
                       self.voice_player, self.music_player):
            player.stop()
            player.setSource(QUrl())
        self.video_player.setVideoOutput(None)
        for player in self._crossfade_players:
            player.setVideoSink(None)
        QApplication.processEvents()
        for signal, slot in (
            (self.video_player.positionChanged, self._video_position_changed),
            (self.video_player.mediaStatusChanged, self._video_status_changed),
            (self.job_timer.timeout, self._refresh_job),
            (self.image_timer.timeout, self._tick_image),
            (self.timeline_widget._audio_file_watcher.fileChanged,
             self.timeline_widget._audio_file_changed),
            (self.timeline_widget._audio_refresh_timer.timeout,
             self.timeline_widget._refresh_audio_file),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self.timeline_widget._audio_refresh_timer.stop()
        for signal, slot in (
            (self.undo_stack.canUndoChanged, self._history_state_changed),
            (self.undo_stack.canRedoChanged, self._history_state_changed),
            (self.undo_stack.cleanChanged, self._update_project_dirty_indicator),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        for player, slot in zip(self._crossfade_players, self._crossfade_status_slots):
            try:
                player.mediaStatusChanged.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        for sink, slot in zip(self._crossfade_sinks, self._crossfade_frame_slots):
            try:
                sink.videoFrameChanged.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        # ProxyCache terminates cancelled FFmpeg work within 3 s. Drain the
        # pool before project/temp folders can be removed by the caller.
        self._proxy_pool.waitForDone(-1)
        self._media_thumbnail_pool.waitForDone(-1)

    def _history_state_changed(self, _enabled):
        if not self._shutting_down:
            self._refresh_history_controls()

    def _track_is_locked(self, kind):
        return bool(self._track_locks.get(kind, False))

    def _tracks_changed_by_timeline(self, before, after):
        return {
            track for track, fields in _TRACK_FIELDS.items()
            if any(before.get(field) != after.get(field) for field in fields)
        }

    def _locked_tracks_changed(self, before, after):
        return sorted(track for track in self._tracks_changed_by_timeline(before, after)
                      if self._track_is_locked(track))

    def _describe_locked_tracks(self, tracks):
        return ", ".join(_TRACK_LOCK_LABELS[track] for track in tracks)

    def _refresh_history_controls(self):
        if self._shutting_down:
            return
        can_undo = self.undo_stack.canUndo()
        can_redo = self.undo_stack.canRedo()
        if can_undo:
            command = self.undo_stack.command(self.undo_stack.index() - 1)
            can_undo = not self._locked_tracks_changed(command.after, command.before)
        if can_redo:
            command = self.undo_stack.command(self.undo_stack.index())
            can_redo = not self._locked_tracks_changed(command.before, command.after)
        self.undo_button.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)

    def _render_controls_enabled(self, enabled):
        if not enabled:
            for widget in (self.save_button, self.add_media_button, self.add_audio_button, self.add_music_button,
                           self.add_text_button, self.add_subtitle_button, self.render_button,
                           self.proxy_checkbox):
                widget.setEnabled(False)
            for button in self.track_lock_buttons.values():
                button.setEnabled(False)
        else:
            self.save_button.setEnabled(True)
            self.render_button.setEnabled(True)
            self.proxy_checkbox.setEnabled(True)
            self._sync_track_lock_buttons()

    def _show_empty_preview(self):
        self.video_player.stop()
        self._current_scene = None
        self._image_crossfade_active = False
        self._crossfade_key = None
        self._crossfade_frames = [QImage(), QImage()]
        for player in self._crossfade_players:
            player.stop()
        self._pending_video_seek = None
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText("Importa un'immagine o un video per iniziare")
        self.preview_layout.setCurrentWidget(self.image_label)
        self.timeline_widget.set_playhead(0)
        self.position_slider.setRange(0, 0)
        self.time_label.setText("00:00 / 00:00")
        self._show_properties()

    def _rebuild_timeline(self):
        self._scene_starts = timeline_scene_starts(self.timeline)
        self.timeline_widget.set_timeline(self.timeline, selected=self._selected, playhead=self._playhead)

    def move_scene(self, source_index, target_index):
        """Move one scene as a single undoable timeline action."""
        if self._track_is_locked("scene"):
            self.status_label.setText("Modifica non consentita: la traccia VIDEO e bloccata.")
            return False
        scenes = list(self.timeline.get("scenes", []))
        if (source_index < 0 or source_index >= len(scenes)
                or target_index < 0 or target_index >= len(scenes)
                or source_index == target_index):
            return False
        scene = scenes.pop(source_index)
        scenes.insert(target_index, scene)
        candidate = copy.deepcopy(self.timeline)
        candidate["scenes"] = scenes
        issue = self._scene_order_issue(candidate)
        if issue:
            self.status_label.setText("Riordino non applicato · " + issue)
            return False

        starts = timeline_scene_starts(candidate)
        changed = self._push_timeline_change(
            "Sposta clip", candidate, selection=("scene", target_index),
            playhead=starts[target_index],
        )
        if changed:
            self.status_label.setText("Clip riordinata · salva per conservare l’ordine nel progetto.")
        return changed

    def copy_selected_scene(self):
        """Copy the selected image/video scene into the editor-local clipboard."""
        if self._selected is None or self._selected[0] != "scene":
            self.status_label.setText("Seleziona una clip immagine o video da copiare.")
            return False
        index = self._selected[1]
        scenes = self.timeline.get("scenes", [])
        if index < 0 or index >= len(scenes):
            self.status_label.setText("La clip selezionata non è più disponibile.")
            return False
        self._scene_clipboard = copy.deepcopy(scenes[index])
        self.status_label.setText("Clip copiata · usa Ctrl+V per inserirla al playhead.")
        return True

    def _paste_scene_index(self):
        """Map the playhead to the next legal sequential-timeline cut."""
        scenes = self.timeline.get("scenes", [])
        starts = timeline_scene_starts(self.timeline)
        for index, start in enumerate(starts):
            if start >= self._playhead - 1e-7:
                return index
        return len(scenes)

    @staticmethod
    def _scene_copy_with_new_id(scene, scenes):
        clone = copy.deepcopy(scene)
        existing_ids = {item.get("id") for item in scenes}
        suffix = 1
        while f"scene_copy_{suffix:03d}" in existing_ids:
            suffix += 1
        clone["id"] = f"scene_copy_{suffix:03d}"
        return clone

    def _insert_scene_copy(self, source, index, label, status):
        if self._track_is_locked("scene"):
            self.status_label.setText("Modifica non consentita: la traccia VIDEO e bloccata.")
            return False
        scenes = list(self.timeline.get("scenes", []))
        if index < 0 or index > len(scenes):
            return False
        clone = self._scene_copy_with_new_id(source, scenes)
        candidate = copy.deepcopy(self.timeline)
        candidate_scenes = list(candidate.get("scenes", []))
        candidate_scenes.insert(index, clone)
        candidate["scenes"] = candidate_scenes
        issue = self._scene_order_issue(candidate)
        if issue:
            self.status_label.setText("Inserimento non applicato · " + issue)
            return False
        starts = timeline_scene_starts(candidate)
        changed = self._push_timeline_change(
            label, candidate, selection=("scene", index), playhead=starts[index],
        )
        if changed:
            self.status_label.setText(status)
        return changed

    def paste_scene(self):
        """Insert a copied scene at the playhead's next legal cut."""
        if self._scene_clipboard is None:
            self.status_label.setText("Nessuna clip copiata · seleziona una clip e premi Ctrl+C.")
            return False
        index = self._paste_scene_index()
        scenes = self.timeline.get("scenes", [])
        starts = timeline_scene_starts(self.timeline)
        at_playhead = index < len(scenes) and abs(starts[index] - self._playhead) <= 1e-7
        if at_playhead:
            status = "Clip incollata al playhead · salva per conservare la modifica."
        elif index >= len(scenes):
            status = "Clip incollata in fondo alla sequenza · salva per conservare la modifica."
        else:
            status = "Clip incollata al taglio successivo · salva per conservare la modifica."
        return self._insert_scene_copy(
            self._scene_clipboard, index, "Incolla clip", status,
        )

    def duplicate_selected_scene(self):
        """Duplicate the selected scene immediately after it as one undo action."""
        if self._selected is None or self._selected[0] != "scene":
            self.status_label.setText("Seleziona una clip immagine o video da duplicare.")
            return False
        index = self._selected[1]
        scenes = self.timeline.get("scenes", [])
        if index < 0 or index >= len(scenes):
            self.status_label.setText("La clip selezionata non è più disponibile.")
            return False
        return self._insert_scene_copy(
            scenes[index], index + 1, "Duplica clip",
            "Clip duplicata subito dopo l'originale · salva per conservare la modifica.",
        )

    def trim_scene(self, index, side, delta_frames, *, playhead_after=None):
        """Trim on the frame grid; a left trim rolls the cut with the previous scene."""
        if self._track_is_locked("scene"):
            self.status_label.setText("Trim non consentito: la traccia VIDEO e bloccata.")
            return False
        scenes = self.timeline.get("scenes", [])
        if (side not in {"left", "right"} or index < 0 or index >= len(scenes)
                or isinstance(delta_frames, bool) or int(delta_frames) != delta_frames):
            return False
        delta_frames = int(delta_frames)
        if not delta_frames:
            return False
        fps = float((self.timeline.get("canvas") or {}).get("fps", 30))
        original = scenes[index]
        duration_frames = seconds_to_frames(float(original.get("duration", 0)), fps)
        source_frames = seconds_to_frames(float(original.get("source_in", 0)), fps)
        previous_frames = None
        if side == "left":
            if index == 0:
                self.status_label.setText(
                    "Trim sinistro non disponibile sulla prima scena: la timeline v1 non ammette spazio iniziale."
                )
                return False
            previous = scenes[index - 1]
            previous_frames = seconds_to_frames(float(previous.get("duration", 0)), fps)
            new_duration_frames = duration_frames - delta_frames
            new_source_frames = source_frames + delta_frames if original.get("type") == "video" else source_frames
            new_previous_frames = previous_frames + delta_frames
            if new_previous_frames < 1:
                self.status_label.setText("Trim non applicato · la scena precedente deve durare almeno un fotogramma.")
                return False
        else:
            new_duration_frames = duration_frames + delta_frames
            new_source_frames = source_frames
            new_previous_frames = None
        if new_duration_frames < 1:
            self.status_label.setText("Trim non applicato · la scena deve durare almeno un fotogramma.")
            return False
        if new_source_frames < 0:
            self.status_label.setText("Trim non applicato · il punto iniziale del video non può precedere la sorgente.")
            return False

        candidate = copy.deepcopy(self.timeline)
        if side == "left":
            candidate["scenes"][index - 1]["duration"] = frames_to_seconds(new_previous_frames, fps)
        updated = candidate["scenes"][index]
        updated["duration"] = frames_to_seconds(new_duration_frames, fps)
        if updated.get("type") == "video":
            updated["source_in"] = frames_to_seconds(new_source_frames, fps)
        for source_index in ({index - 1, index} if side == "left" else {index}):
            source_scene = candidate["scenes"][source_index]
            if source_scene.get("type") != "video":
                continue
            try:
                source_duration = _video_duration(source_scene.get("asset"))
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                self.status_label.setText(f"Trim non applicato · impossibile verificare il video: {exc}")
                return False
            source_total_frames = seconds_to_frames(source_duration, fps)
            used_frames = seconds_to_frames(float(source_scene.get("source_in", 0)), fps) + seconds_to_frames(
                float(source_scene.get("duration", 0)), fps
            )
            if used_frames > source_total_frames:
                self.status_label.setText("Trim non applicato · il taglio supererebbe la fine del video sorgente.")
                return False

        issue = self._scene_order_issue(candidate)
        if issue:
            self.status_label.setText("Trim non applicato · " + issue)
            return False
        candidate_end = timeline_visual_duration(candidate)
        for lane in ("texts", "subtitles"):
            if any(float(item.get("end", 0)) > candidate_end + 1e-9
                   for item in candidate.get(lane, [])):
                self.status_label.setText(
                    "Trim non applicato · un testo o sottotitolo terminerebbe oltre il video."
                )
                return False

        if playhead_after is None:
            playhead_after = self._playhead
            if side == "left":
                playhead_after += frames_to_seconds(delta_frames, fps)
        changed = self._push_timeline_change(
            "Trim clip", candidate, selection=("scene", index),
            playhead=min(max(0.0, playhead_after), timeline_duration(candidate)),
        )
        if changed:
            self.status_label.setText(
                f"Trim {('iniziale' if side == 'left' else 'finale')} applicato al fotogramma · "
                "salva per conservare la modifica."
            )
        return changed

    def _apply_trim_gesture(self, index, side, delta_frames):
        widget = self.timeline_widget
        if self._track_is_locked("scene"):
            widget.cancel_trim_preview()
            self.status_label.setText("Trim non consentito: la traccia VIDEO e bloccata.")
            return False
        self._trim_preview_timer.stop()
        self._trim_preview_pending = None
        widget.set_playhead(widget._trim_start_playhead)
        self._playhead = widget._trim_start_playhead
        success = self.trim_scene(
            index, side, delta_frames, playhead_after=widget._trim_preview_playhead
        )
        if not success:
            self._rebuild_timeline()
            self.seek_to(widget._trim_start_playhead, preserve_selection=True)
        widget.finish_trim_preview()

    def _queue_trim_preview(self, candidate, seconds):
        self._trim_preview_pending = (candidate, seconds)
        if not self._trim_preview_timer.isActive():
            self._trim_preview_timer.start()

    def _render_trim_preview(self):
        if self._trim_preview_pending is None:
            return
        candidate, seconds = self._trim_preview_pending
        self._trim_preview_pending = None
        original_timeline = self.timeline
        original_starts = self._scene_starts
        original_selection = self._selected
        self.timeline = candidate
        self._scene_starts = timeline_scene_starts(candidate)
        try:
            self.seek_to(seconds, preserve_selection=True)
        finally:
            self.timeline = original_timeline
            self._scene_starts = original_starts
            self._selected = original_selection
            self._playhead = seconds

    def _cancel_trim_preview(self, seconds):
        self._trim_preview_timer.stop()
        self._trim_preview_pending = None
        self.seek_to(seconds, preserve_selection=True)

    @staticmethod
    def _scene_order_issue(timeline):
        scenes = timeline.get("scenes", [])
        transitions = timeline.get("transitions") or {}
        if not scenes:
            return None
        fps = float((timeline.get("canvas") or {}).get("fps", 30))
        positions = {scene.get("id"): index for index, scene in enumerate(scenes)}
        overlap_frames = [0] * len(scenes)
        between = transitions.get("between", []) if isinstance(transitions, dict) else []
        for transition in between:
            after = transition.get("after")
            index = positions.get(after)
            if index is None or index == len(scenes) - 1:
                return "una transizione tra scene resterebbe senza clip successiva."
            if transition.get("type") == "crossfade":
                frames = seconds_to_frames(float(transition.get("duration", 0)), fps)
                overlap_frames[index] += frames
                overlap_frames[index + 1] += frames
        scene_frames = [seconds_to_frames(float(scene.get("duration", 0)), fps) for scene in scenes]
        if any(overlap > available for overlap, available in zip(overlap_frames, scene_frames)):
            return "le dissolvenze incrociate supererebbero la durata di una scena."
        for key in ("start", "end"):
            transition = transitions.get(key) if isinstance(transitions, dict) else None
            if transition:
                scene = scenes[0] if key == "start" else scenes[-1]
                if seconds_to_frames(float(transition.get("duration", 0)), fps) > seconds_to_frames(
                    float(scene.get("duration", 0)), fps
                ):
                    return f"la dissolvenza {key} supererebbe la durata della nuova scena {key}."
        return None

    def import_video_or_images(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Importa immagini e video", str(self.project.folder if self.project else Path.home()),
            "Media supportati (*.png *.jpg *.jpeg *.webp *.mp4 *.mov *.mkv *.avi *.webm *.m4v);;Tutti i file (*)",
        )
        if paths:
            try:
                self.add_media_library_paths(paths)
            except Exception as exc:
                self._message("Importazione non riuscita", str(exc))

    def add_media_library_paths(self, paths):
        """Add photo/video references to the project bin without changing its timeline."""
        if self.project is None:
            raise RuntimeError("Apri prima un progetto.")
        existing = {str(Path(path).resolve(strict=False)).casefold() for path in self._media_library_paths}
        additions = []
        for raw_path in paths:
            path = Path(raw_path).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"File non trovato: {path}")
            path = path.resolve(strict=False)
            if path.suffix.lower() not in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
                raise ValueError(f"Formato immagine/video non supportato: {path.name}")
            key = str(path).casefold()
            if key not in existing:
                additions.append(str(path))
                existing.add(key)
        if not additions:
            return []
        self._media_library_paths.extend(additions)
        self._write_project_media_library()
        self._refresh_media_library()
        self._update_project_dirty_indicator(self.undo_stack.isClean())
        self.status_label.setText(f"{len(additions)} media aggiunti alla libreria · seleziona gli elementi da montare.")
        return additions

    def _write_project_media_library(self):
        if self.project is None:
            return
        settings = copy.deepcopy(self.project.settings.get("editor", {}))
        if not isinstance(settings, dict):
            settings = {}
        settings["media_library"] = list(self._media_library_paths)
        self.project.settings["editor"] = settings

    def _refresh_media_library(self, *_args):
        if not hasattr(self, "media_list"):
            return
        query = self.media_search.text().strip().casefold()
        filter_kind = self.media_filter.currentData()
        selected = {item.data(Qt.ItemDataRole.UserRole) for item in self.media_list.selectedItems()}
        self.media_list.clear()
        for raw_path in self._media_library_paths:
            path = Path(raw_path)
            kind = "image" if path.suffix.lower() in IMAGE_SUFFIXES else "video"
            if filter_kind != "all" and kind != filter_kind:
                continue
            if query and query not in path.name.casefold():
                continue
            thumbnail = self.timeline_widget._thumbnail_for(raw_path)
            if kind == "video":
                thumbnail = self._media_video_thumbnails.get(str(path).casefold(), thumbnail)
                if thumbnail is None:
                    self._request_video_thumbnail(raw_path)
            if thumbnail is not None:
                icon = QIcon(thumbnail.scaled(64, 48, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
            else:
                icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
            type_label = "FOTO" if kind == "image" else "VIDEO"
            item = QListWidgetItem(icon, f"{type_label}  {path.name}")
            item.setData(Qt.ItemDataRole.UserRole, raw_path)
            item.setToolTip(raw_path)
            self.media_list.addItem(item)
            if raw_path in selected:
                item.setSelected(True)
        self._media_selection_changed()

    def _request_video_thumbnail(self, raw_path):
        key = str(Path(raw_path).resolve(strict=False)).casefold()
        if key in self._media_thumbnail_jobs or self._shutting_down:
            return
        cancel_event = threading.Event()
        self._media_thumbnail_jobs.add(key)
        self._media_thumbnail_cancel_events[key] = cancel_event
        worker = _VideoThumbnailWorker(raw_path, cancel_event)
        worker.signals.ready.connect(self._video_thumbnail_ready)
        self._media_thumbnail_pool.start(worker)

    def _video_thumbnail_ready(self, raw_path, encoded):
        key = str(Path(raw_path).resolve(strict=False)).casefold()
        self._media_thumbnail_jobs.discard(key)
        self._media_thumbnail_cancel_events.pop(key, None)
        if self._shutting_down:
            return
        image = QImage()
        if image.loadFromData(encoded) and not image.isNull():
            self._media_video_thumbnails[key] = QPixmap.fromImage(image)
            self._refresh_media_library()

    def _media_selection_changed(self):
        has_selection = bool(self.media_list.selectedItems())
        self.insert_selected_button.setEnabled(
            self.project is not None and has_selection and not self._track_is_locked("scene")
        )

    def add_selected_media_to_timeline(self):
        paths = [item.data(Qt.ItemDataRole.UserRole) for item in self.media_list.selectedItems()]
        if not paths:
            self.status_label.setText("Seleziona prima uno o più elementi nella libreria media.")
            return []
        return self.add_media_paths(paths)

    def add_media_paths(self, paths):
        if self.project is None:
            raise RuntimeError("Apri prima un progetto.")
        if self._track_is_locked("scene"):
            raise PermissionError("Importazione non consentita: la traccia VIDEO e bloccata.")
        existing_scenes = list(self.timeline.get("scenes", []))
        scenes = list(existing_scenes)
        existing_ids = {item.get("id") for item in scenes}
        next_number = 1
        while f"scene_{next_number:03d}" in existing_ids:
            next_number += 1
        added = []
        for raw_path in paths:
            path = Path(raw_path).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"File non trovato: {path}")
            path = path.resolve(strict=False)
            suffix = path.suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                media_type, duration = "image", 3.0
            elif suffix in VIDEO_SUFFIXES:
                media_type, duration = "video", _video_duration(path)
            else:
                raise ValueError(f"Formato immagine/video non supportato: {path.name}")
            while f"scene_{next_number:03d}" in existing_ids:
                next_number += 1
            scene = {"id": f"scene_{next_number:03d}", "type": media_type,
                     "asset": str(path), "duration": duration, "source_in": 0.0}
            scenes.append(scene)
            added.append(scene)
            existing_ids.add(scene["id"])
            next_number += 1

        if not added:
            return
        # Import remains immediately persisted as before; a failed write does
        # not add an undo entry or alter the open timeline.
        candidate = copy.deepcopy(self.timeline)
        candidate["scenes"] = scenes
        previous_project_timeline = self.project.timeline
        self.project.timeline = candidate
        try:
            self._write_project_track_locks()
            save_project(self.project)
            self._saved_track_locks = copy.deepcopy(self._track_locks)
            self._saved_use_proxies = self._use_proxies
            self._saved_media_library_paths = copy.deepcopy(self._media_library_paths)
        except Exception:
            self.project.timeline = previous_project_timeline
            raise
        candidate_starts = timeline_scene_starts(candidate)
        self._push_timeline_change(
            "Importa media", candidate, selection=("scene", len(scenes) - 1),
            playhead=candidate_starts[-1],
        )
        self.undo_stack.setClean()
        for scene in added:
            if scene.get("type") == "video":
                self._request_video_proxy(scene["asset"])
        preview_message = self.image_label.text()
        if preview_message.startswith(("Immagine non leggibile:", "File scena non trovato:",
                                       "Qt non ha potuto creare l'anteprima:")):
            self.status_label.setText(preview_message)
        else:
            self.status_label.setText(f"{len(added)} scene importate e salvate nel progetto.")

    def import_audio(self, *, background=False):
        track = "background_audio" if background else "audio"
        if self._track_is_locked(track):
            self.status_label.setText(f"Importazione non consentita: la traccia {_TRACK_LOCK_LABELS[track]} e bloccata.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Scegli la traccia audio", str(self.project.folder if self.project else Path.home()),
            "Audio (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wma *.aiff *.aif);;Tutti i file (*)",
        )
        if path:
            try:
                self.add_audio_path(path, background=background)
            except Exception as exc:
                self._message("Importazione audio non riuscita", str(exc))

    def add_audio_path(self, path, *, background=False):
        if self.project is None:
            raise RuntimeError("Apri prima un progetto.")
        track = "background_audio" if background else "audio"
        if self._track_is_locked(track):
            raise PermissionError(f"Modifica non consentita: la traccia {_TRACK_LOCK_LABELS[track]} e bloccata.")
        asset = Path(path).expanduser()
        if not asset.is_file():
            raise FileNotFoundError(f"File non trovato: {asset}")
        asset = asset.resolve(strict=False)
        if asset.suffix.lower() not in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".aif"}:
            raise ValueError(f"Formato audio non supportato: {asset.suffix}")
        candidate = copy.deepcopy(self.timeline)
        if background:
            current = candidate.get("background_audio") or {}
            candidate["background_audio"] = {
                "asset": str(asset), "source_in": float(current.get("source_in", 0)),
                "duration": current.get("duration"), "volume": float(current.get("volume", 0.2)),
                "fade_in": float(current.get("fade_in", 0)), "fade_out": float(current.get("fade_out", 0)),
            }
            selection = ("background_audio", 0)
        else:
            candidate["audio"] = str(asset)
            selection = ("audio", 0)
        self._push_timeline_change(
            "Aggiungi musica" if background else "Aggiungi audio",
            candidate, selection=selection,
        )
        self.status_label.setText("Traccia audio aggiunta · salva per aggiornare il progetto.")

    def add_overlay(self, kind):
        if self.project is None:
            return False
        track = "text" if kind == "text" else "subtitle"
        if self._track_is_locked(track):
            self.status_label.setText(f"Modifica non consentita: traccia {_TRACK_LOCK_LABELS[track]} bloccata.")
            return False
        lane = "texts" if kind == "text" else "subtitles"
        visual_end = timeline_visual_duration(self.timeline)
        start = min(max(0.0, self._playhead), max(0.0, visual_end - 1.0)) if visual_end else 0.0
        end = min(visual_end, start + 3.0) if visual_end else start + 1.0
        if end <= start:
            fps = float(self.timeline.get("canvas", {}).get("fps", 30))
            end = start + 1.0 / max(1.0, fps)
        item = {"start": start, "end": end, "text": "Nuovo titolo" if kind == "text" else "Nuovo sottotitolo",
                "font": "Arial", "size": 48 if kind == "text" else 36,
                "color": "#FFFFFF", "position": "center" if kind == "text" else "bottom_center",
                "outline_color": "black", "outline_width": 2}
        candidate = copy.deepcopy(self.timeline)
        candidate.setdefault(lane, []).append(item)
        return self._push_timeline_change(
            "Aggiungi testo" if kind == "text" else "Aggiungi sottotitolo",
            candidate, selection=(kind, len(candidate[lane]) - 1),
        )

    def select_item(self, kind, index):
        self._selected = (kind, index)
        self.timeline_widget.set_selected(kind, index)
        self._show_properties()
        if kind == "scene":
            starts = self._scene_starts
            if 0 <= index < len(starts):
                self.seek_to(starts[index], preserve_selection=True)
        self._update_text_overlay(self._playhead)

    def _clear_form(self):
        while self.inspector_form.rowCount():
            self.inspector_form.removeRow(0)

    def _spin(self, value, minimum=0.0, maximum=99999.0):
        control = QDoubleSpinBox()
        control.setDecimals(3)
        control.setRange(minimum, maximum)
        control.setSingleStep(0.1)
        control.setValue(float(value))
        return control

    def _show_properties(self):
        self._clear_form()
        self._property_widgets = {}
        selection = self._selected
        if selection is None:
            self.inspector_form.addRow(QLabel("Seleziona un clip nella timeline."))
            self.apply_button.setEnabled(False)
            self.split_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            self.ripple_delete_button.setEnabled(False)
            return
        kind, index = selection
        try:
            item = self._get_selected_data(kind, index)
        except (IndexError, KeyError, TypeError):
            self._selected = None
            return self._show_properties()
        track_locked = self._track_is_locked(kind)
        lock_suffix = " [BLOCCATA]" if track_locked else ""
        self.apply_button.setEnabled(not track_locked)
        self.split_button.setEnabled(kind == "scene" and not track_locked)
        self.delete_button.setEnabled(not track_locked)
        self.ripple_delete_button.setEnabled(kind == "scene" and not track_locked)
        if kind == "scene":
            self.inspector_title.setText(f"PROPRIETÀ SCENA{lock_suffix}")
            self._add_readonly("Tipo", item.get("type", ""))
            self._add_readonly("File", item.get("asset", ""))
            self._property_widgets["duration"] = self._spin(item.get("duration", 3), 0.01)
            self.inspector_form.addRow("Durata (s)", self._property_widgets["duration"])
            self._property_widgets["source_in"] = self._spin(item.get("source_in", 0))
            self.inspector_form.addRow("Ingresso sorgente (s)", self._property_widgets["source_in"])
        elif kind in {"audio", "background_audio"}:
            self.inspector_title.setText(f"PROPRIETÀ AUDIO{lock_suffix}")
            path = item if kind == "audio" else item.get("asset", "")
            self._add_readonly("File", path)
            if kind == "audio":
                self.apply_button.setEnabled(False)
            if kind == "background_audio":
                for key, label, minimum, maximum in (("source_in", "Ingresso (s)", 0, 99999),
                                                      ("duration", "Durata (s)", 0, 99999),
                                                      ("volume", "Volume", 0, 1),
                                                      ("fade_in", "Dissolvenza in (s)", 0, 9999),
                                                      ("fade_out", "Dissolvenza out (s)", 0, 9999)):
                    value = item.get(key)
                    widget = self._spin(0 if value is None else value, minimum, maximum)
                    self._property_widgets[key] = widget
                    self.inspector_form.addRow(label, widget)
        else:
            self.inspector_title.setText(f"PROPRIETÀ {'TESTO' if kind == 'text' else 'SOTTOTITOLO'}{lock_suffix}")
            self._property_widgets["text"] = QPlainTextEdit(str(item.get("text", "")))
            self._property_widgets["text"].setMaximumHeight(92)
            self.inspector_form.addRow("Contenuto", self._property_widgets["text"])
            for key, label, minimum in (("start", "Inizio (s)", 0), ("end", "Fine (s)", 0.01)):
                widget = self._spin(item.get(key, 0), minimum)
                self._property_widgets[key] = widget
                self.inspector_form.addRow(label, widget)
            for key, label, default in (("position", "Posizione", "bottom_center" if kind == "subtitle" else "center"),
                                         ("font", "Font", "Arial"), ("color", "Colore", "#FFFFFF"),
                                         ("outline_color", "Contorno", "black"), ("background_color", "Sfondo", "")):
                widget = QLineEdit(str(item.get(key, default) or ""))
                self._property_widgets[key] = widget
                self.inspector_form.addRow(label, widget)
            size = self._spin(item.get("size", 36), 1, 500)
            outline = self._spin(item.get("outline_width", 2), 0, 20)
            self._property_widgets["size"] = size
            self._property_widgets["outline_width"] = outline
            self.inspector_form.addRow("Dimensione", size)
            self.inspector_form.addRow("Spessore contorno", outline)
        for widget in self._property_widgets.values():
            widget.setEnabled(not track_locked)

    def _add_readonly(self, label, value):
        field = QLineEdit(str(value))
        field.setReadOnly(True)
        field.setToolTip(str(value))
        self.inspector_form.addRow(label, field)

    def _get_selected_data(self, kind, index):
        if kind == "scene":
            return self.timeline["scenes"][index]
        if kind == "audio":
            return self.timeline.get("audio")
        if kind == "background_audio":
            return self.timeline["background_audio"]
        return self.timeline["texts" if kind == "text" else "subtitles"][index]

    def apply_properties(self):
        if self._selected is None:
            return False
        kind, index = self._selected
        if self._track_is_locked(kind):
            self.status_label.setText(f"Modifica non consentita: traccia {_TRACK_LOCK_LABELS[kind]} bloccata.")
            return False
        if kind == "audio" and not self._property_widgets:
            return
        updates = {}
        for key, widget in self._property_widgets.items():
            if isinstance(widget, QDoubleSpinBox):
                value = widget.value()
                if kind == "background_audio" and key == "duration" and value == 0:
                    value = None
            elif isinstance(widget, QPlainTextEdit):
                value = widget.toPlainText()
            else:
                value = widget.text().strip()
            if key == "outline_width":
                value = int(value)
            if key == "background_color" and value == "":
                value = None
            updates[key] = value
        if kind in {"text", "subtitle"} and updates["end"] <= updates["start"]:
            self._message("Intervallo non valido", "La fine deve essere successiva all'inizio.")
            return
        candidate = copy.deepcopy(self.timeline)
        if kind == "scene":
            item = candidate["scenes"][index]
        elif kind == "background_audio":
            item = candidate["background_audio"]
        elif kind == "audio":
            item = None
        else:
            lane = "texts" if kind == "text" else "subtitles"
            item = candidate[lane][index]
        if item is not None:
            item.update(updates)
        label = {
            "scene": "Modifica proprietà clip",
            "background_audio": "Modifica proprietà musica",
            "audio": "Modifica audio",
            "text": "Modifica testo",
            "subtitle": "Modifica sottotitolo",
        }[kind]
        self._push_timeline_change(label, candidate, selection=(kind, index))
        self.status_label.setText("Proprietà aggiornate · salva per conservarle nel progetto.")

    def delete_selected(self):
        if self._selected is None:
            return False
        kind, index = self._selected
        if self._track_is_locked(kind):
            self.status_label.setText(f"Eliminazione non consentita: traccia {_TRACK_LOCK_LABELS[kind]} bloccata.")
            return False
        candidate = copy.deepcopy(self.timeline)
        if kind == "scene":
            scene = candidate["scenes"].pop(index)
            transitions = candidate.get("transitions")
            if isinstance(transitions, dict):
                transitions["between"] = [item for item in transitions.get("between", [])
                                          if item.get("after") != scene.get("id")]
        elif kind == "audio":
            candidate["audio"] = None
        elif kind == "background_audio":
            candidate.pop("background_audio", None)
        else:
            lane = "texts" if kind == "text" else "subtitles"
            candidate[lane].pop(index)
        return self._push_timeline_change("Elimina selezione", candidate, selection=None)

    def ripple_delete_selected(self):
        """Remove one scene and collapse its occupied timeline interval atomically."""
        if self._track_is_locked("scene"):
            self.status_label.setText("Ripple Delete non consentito: la traccia VIDEO e bloccata.")
            return False
        if self._selected is None or self._selected[0] != "scene":
            self.status_label.setText("Seleziona una scena VIDEO per Ripple Delete.")
            return False
        index = self._selected[1]
        scenes = list(self.timeline.get("scenes", []))
        if index < 0 or index >= len(scenes):
            self.status_label.setText("La scena selezionata non è più disponibile.")
            return False

        fps = float((self.timeline.get("canvas") or {}).get("fps", 30))
        selected = scenes[index]
        scene_frames = seconds_to_frames(float(selected.get("duration", 0)), fps)
        transitions = self.timeline.get("transitions") or {}
        between = transitions.get("between", []) if isinstance(transitions, dict) else []
        by_after = {item.get("after"): item for item in between if isinstance(item, dict)}
        incoming_frames = 0
        if index > 0:
            incoming = by_after.get(scenes[index - 1].get("id"), {})
            if incoming.get("type") == "crossfade":
                incoming_frames = seconds_to_frames(float(incoming.get("duration", 0)), fps)
        outgoing = by_after.get(selected.get("id"), {})
        outgoing_frames = (seconds_to_frames(float(outgoing.get("duration", 0)), fps)
                           if outgoing.get("type") == "crossfade" else 0)
        removed_frames = max(0, scene_frames - incoming_frames - outgoing_frames)
        starts = timeline_scene_starts(self.timeline)
        # The incoming crossfade remains visible on the previous scene after
        # ripple removal, so the collapsed interval starts after that overlap.
        cut_start = seconds_to_frames(starts[index], fps) + incoming_frames

        candidate = copy.deepcopy(self.timeline)
        candidate_scenes = list(candidate.get("scenes", []))
        removed = candidate_scenes.pop(index)
        candidate["scenes"] = candidate_scenes

        candidate_transitions = candidate.get("transitions")
        if isinstance(candidate_transitions, dict):
            touching_ids = {removed.get("id")}
            if index > 0:
                touching_ids.add(scenes[index - 1].get("id"))
            candidate_transitions["between"] = [
                item for item in candidate_transitions.get("between", [])
                if item.get("after") not in touching_ids
            ]
            if not candidate_scenes:
                candidate_transitions.clear()
            else:
                for edge, scene in (("start", candidate_scenes[0]), ("end", candidate_scenes[-1])):
                    terminal = candidate_transitions.get(edge)
                    if terminal:
                        scene_frames_at_edge = seconds_to_frames(float(scene.get("duration", 0)), fps)
                        fade_frames = seconds_to_frames(float(terminal.get("duration", 0)), fps)
                        if fade_frames > scene_frames_at_edge:
                            terminal["duration"] = frames_to_seconds(scene_frames_at_edge, fps)

        cut_end = cut_start + removed_frames
        if removed_frames:
            for lane, lock_key in (("texts", "text"), ("subtitles", "subtitle")):
                if self._track_is_locked(lock_key):
                    for item in self.timeline.get(lane, []):
                        item_end = seconds_to_frames(float(item.get("end", 0)), fps)
                        if item_end > cut_start:
                            self.status_label.setText(
                                f"Ripple Delete non consentito: la traccia {_TRACK_LOCK_LABELS[lock_key]} e bloccata."
                            )
                            return False

            def collapse_frame(frame):
                if frame < cut_start:
                    return frame
                if frame >= cut_end:
                    return frame - removed_frames
                return cut_start

            for lane in ("texts", "subtitles"):
                remaining = []
                for item in candidate.get(lane, []):
                    start_frame = seconds_to_frames(float(item["start"]), fps)
                    end_frame = seconds_to_frames(float(item["end"]), fps)
                    shifted_start = collapse_frame(start_frame)
                    shifted_end = collapse_frame(end_frame)
                    if shifted_end <= shifted_start:
                        continue
                    updated = copy.deepcopy(item)
                    updated["start"] = frames_to_seconds(shifted_start, fps)
                    updated["end"] = frames_to_seconds(shifted_end, fps)
                    remaining.append(updated)
                candidate[lane] = remaining

        issue = self._scene_order_issue(candidate)
        if issue:
            self.status_label.setText("Ripple Delete non applicato · " + issue)
            return False
        if candidate_scenes:
            next_index = min(index, len(candidate_scenes) - 1)
            candidate_starts = timeline_scene_starts(candidate)
            selection = ("scene", next_index)
            playhead = candidate_starts[next_index]
        else:
            selection = None
            playhead = 0.0
        changed = self._push_timeline_change(
            "Ripple Delete", candidate, selection=selection, playhead=playhead,
        )
        if changed:
            self.status_label.setText(
                "Scena rimossa; testi e sottotitoli successivi riallineati. "
                "Audio e musica globali restano continui · salva per conservare la modifica."
            )
        return changed

    def split_selected(self):
        """Split the selected scene on the nearest output frame at the playhead."""
        if self._track_is_locked("scene"):
            self.status_label.setText("Split non consentito: la traccia VIDEO e bloccata.")
            return False
        if self._selected is None or self._selected[0] != "scene":
            self.status_label.setText("Seleziona una scena VIDEO prima di dividerla.")
            return False
        index = self._selected[1]
        scenes = self.timeline.get("scenes", [])
        if index < 0 or index >= len(scenes):
            self.status_label.setText("La scena selezionata non è più disponibile.")
            return False

        fps = float((self.timeline.get("canvas") or {}).get("fps", 30))
        original = scenes[index]
        duration_frames = seconds_to_frames(float(original.get("duration", 0)), fps)
        local_seconds = self._playhead - self._scene_starts[index]
        split_frames = seconds_to_frames(local_seconds, fps)
        if split_frames < 1 or split_frames >= duration_frames:
            self.status_label.setText("Split non valido: lascia almeno un fotogramma su entrambi i lati.")
            return False

        existing_ids = {scene.get("id") for scene in scenes}
        suffix = 1
        while f"scene_split_{suffix:03d}" in existing_ids:
            suffix += 1
        second_id = f"scene_split_{suffix:03d}"
        first_frames = split_frames
        second_frames = duration_frames - split_frames
        first = copy.deepcopy(original)
        second = copy.deepcopy(original)
        first["duration"] = frames_to_seconds(first_frames, fps)
        second["id"] = second_id
        second["duration"] = frames_to_seconds(second_frames, fps)
        if original.get("type") == "video":
            source_frames = seconds_to_frames(float(original.get("source_in", 0)), fps)
            second["source_in"] = frames_to_seconds(source_frames + first_frames, fps)

        candidate = copy.deepcopy(self.timeline)
        candidate_scenes = list(candidate.get("scenes", []))
        candidate_scenes[index:index + 1] = [first, second]
        candidate["scenes"] = candidate_scenes
        transitions = candidate.get("transitions")
        if isinstance(transitions, dict):
            for transition in transitions.get("between", []):
                if transition.get("after") == original.get("id"):
                    transition["after"] = second_id
        issue = self._scene_order_issue(candidate)
        if issue:
            self.status_label.setText("Split non applicato · " + issue)
            return False

        starts = timeline_scene_starts(candidate)
        self._push_timeline_change(
            "Dividi scena", candidate, selection=("scene", index + 1),
            playhead=starts[index + 1],
        )
        self.status_label.setText("Scena divisa al fotogramma selezionato · salva per conservare la modifica.")
        return True

    def _push_timeline_change(self, label, candidate, *, selection=_UNSET, playhead=None):
        """Record one validated timeline operation; command redo applies it once."""
        blocked = self._locked_tracks_changed(self.timeline, candidate)
        if blocked:
            self.status_label.setText(
                "Modifica non consentita: traccia/e bloccata/e: " + self._describe_locked_tracks(blocked)
            )
            return False
        if candidate == self.timeline:
            return False
        before_selection = self._selected
        before_playhead = self._playhead
        if selection is _UNSET:
            selection = (before_selection if self._selection_exists(before_selection, candidate)
                         else None)
        if playhead is None:
            playhead = before_playhead
        command = _TimelineSnapshotCommand(
            self, label, self.timeline, candidate, before_selection, selection,
            before_playhead, playhead,
        )
        self.undo_stack.push(command)
        return True

    @staticmethod
    def _selection_exists(selection, timeline):
        if selection is None:
            return False
        kind, index = selection
        if kind == "scene":
            return isinstance(index, int) and 0 <= index < len(timeline.get("scenes", []))
        if kind == "audio":
            return bool(timeline.get("audio"))
        if kind == "background_audio":
            return isinstance(timeline.get("background_audio"), dict)
        lane = "texts" if kind == "text" else "subtitles"
        return isinstance(index, int) and 0 <= index < len(timeline.get(lane, []))

    def _restore_timeline_snapshot(self, timeline, selection, playhead):
        """Restore model and editor presentation without writing project files."""
        self.stop_playback()
        self.timeline = copy.deepcopy(timeline)
        if self.project is not None:
            self.project.timeline = self.timeline
        self._selected = selection if self._selection_exists(selection, self.timeline) else None
        self._scene_starts = timeline_scene_starts(self.timeline)
        self._playhead = min(max(0.0, float(playhead)), timeline_duration(self.timeline))
        self._rebuild_timeline()
        self._show_properties()
        if self.timeline.get("scenes"):
            self.seek_to(self._playhead, preserve_selection=True)
        else:
            self._show_empty_preview()
        self._update_text_overlay(self._playhead)

    def undo(self):
        if not self.undo_stack.canUndo():
            return False
        command = self.undo_stack.command(self.undo_stack.index() - 1)
        blocked = self._locked_tracks_changed(command.after, command.before)
        if blocked:
            self.status_label.setText(
                "Annullamento bloccato: sblocca la traccia " + self._describe_locked_tracks(blocked) + "."
            )
            return False
        label = command.text()
        self.undo_stack.undo()
        self.status_label.setText(f"Operazione annullata · {label}. Salva per conservare il ripristino.")
        return True

    def redo(self):
        if not self.undo_stack.canRedo():
            return False
        command = self.undo_stack.command(self.undo_stack.index())
        blocked = self._locked_tracks_changed(command.before, command.after)
        if blocked:
            self.status_label.setText(
                "Ripristino bloccato: sblocca la traccia " + self._describe_locked_tracks(blocked) + "."
            )
            return False
        label = command.text()
        self.undo_stack.redo()
        self.status_label.setText(f"Operazione ripristinata · {label}. Salva per conservare la modifica.")
        return True

    def _update_project_dirty_indicator(self, clean):
        if self.project is None:
            return
        clean = (bool(clean) and self._track_locks == self._saved_track_locks
                 and self._use_proxies == self._saved_use_proxies
                 and self._crossfade_style == self._saved_crossfade_style
                 and self._media_library_paths == self._saved_media_library_paths)
        self.project_name.setText(self.project.name if clean else self.project.name + " *")
        self.project_name.setToolTip(
            "Progetto salvato" if clean else "Modifiche non salvate · premi Ctrl+S o Salva"
        )

    def save_current_project(self):
        if self.project is None:
            return False
        try:
            self.project.timeline = self.timeline
            self._write_project_track_locks()
            save_project(self.project)
            self._saved_track_locks = copy.deepcopy(self._track_locks)
            self._saved_use_proxies = self._use_proxies
            self._saved_crossfade_style = self._crossfade_style
            self._saved_media_library_paths = copy.deepcopy(self._media_library_paths)
            self.undo_stack.setClean()
            self._update_project_dirty_indicator(self.undo_stack.isClean())
            self.status_label.setText(f"Salvato · {self.project.path}")
            return True
        except Exception as exc:
            self._message("Salvataggio non riuscito", str(exc))
            return False

    def collect_media_into_project(self):
        """Make the current project portable by copying referenced external files."""
        if self.project is None:
            return
        if not self.save_current_project():
            return
        self.collect_media_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.project.timeline = self.timeline
            count = collect_project_media(self.project)
            self.timeline = copy.deepcopy(self.project.timeline)
            self._rebuild_timeline()
            self.undo_stack.setClean()
            self._update_project_dirty_indicator(True)
            self.status_label.setText(f"Media raccolti nel progetto · {count} file copiati")
        except Exception as exc:
            self._message("Raccolta media non riuscita", str(exc))
        finally:
            QApplication.restoreOverrideCursor()
            self.collect_media_button.setEnabled(self.project is not None)

    def render_project(self):
        if self.project is None:
            return
        if not self.save_current_project():
            return
        missing = diagnose_project(self.project)
        if missing:
            self._message("Media mancanti", "Recupera queste risorse prima dell'export:\n\n" +
                          "\n".join(item.message for item in missing))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Esporta video", str(self.project.folder / f"{self.project.name}.mp4"),
            "Video MP4 (*.mp4)",
        )
        if not path:
            return
        try:
            self.start_render(path)
        except Exception as exc:
            self._message("Avvio rendering non riuscito", str(exc))

    def start_render(self, output_path):
        """Save the open project and enqueue it through the existing Job Manager."""
        if self.project is None:
            raise RuntimeError("Apri prima un progetto.")
        if not self.save_current_project():
            raise RuntimeError("Il progetto non è stato salvato.")
        missing = diagnose_project(self.project)
        if missing:
            raise ValueError("Risorse mancanti:\n" + "\n".join(item.message for item in missing))
        try:
            if self._job_manager is None:
                self._job_manager = JobManager()
            self._job = self._job_manager.submit(self.project.path, output_path)
            self.job_timer.start()
            self.job_progress.setValue(round(self._job.progress * 100))
            self.job_status_label.setText("Esportazione accodata")
            self.job_phase_label.setText(self._job.phase)
            self.cancel_job_button.setEnabled(True)
            self._refresh_job_history()
            self.render_button.setEnabled(False)
            self.status_label.setText("Rendering accodato · " + self._job.id[:8])
            return self._job
        except Exception:
            self.render_button.setEnabled(True)
            raise

    def _refresh_job(self):
        if self._job_manager is None or self._job is None:
            self.job_timer.stop()
            return
        job = self._job_manager.get_job(self._job.id)
        self.job_progress.setValue(round(job.progress * 100))
        self.job_phase_label.setText(job.phase)
        self.job_status_label.setText(f"{job.status.value.upper()} · {job.progress:.0%}")
        self.cancel_job_button.setEnabled(job.status in {JobStatus.QUEUED, JobStatus.RUNNING})
        self._refresh_job_history()
        self.status_label.setText(f"Render {job.status.value} · {job.progress:.0%} · {job.phase}")
        if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            self.job_timer.stop()
            self.render_button.setEnabled(True)
            if job.status == JobStatus.COMPLETED:
                self.job_status_label.setText(f"Esportazione completata · {job.output_path}")
                self.status_label.setText(f"Video esportato e verificato · {job.output_path}")
            elif job.status == JobStatus.FAILED:
                self.job_status_label.setText(f"Esportazione non riuscita: {job.error or 'errore non specificato'}")
            elif job.status == JobStatus.CANCELLED:
                self.job_status_label.setText("Esportazione annullata")

    def cancel_render_job(self):
        if self._job_manager is None or self._job is None:
            return False
        try:
            cancelled = self._job_manager.cancel(self._job.id)
        except (KeyError, RuntimeError) as exc:
            self.job_status_label.setText(f"Annullamento non riuscito: {exc}")
            return False
        if cancelled:
            self.job_status_label.setText("Annullamento richiesto…")
            self.job_phase_label.setText("Annullamento in corso")
        self._refresh_job()
        return cancelled

    def _refresh_job_history(self):
        if self._job_manager is None:
            return
        selected_id = None
        current = self.job_history.currentItem()
        if current is not None:
            selected_id = current.data(Qt.ItemDataRole.UserRole)
        jobs = sorted(self._job_manager.list_jobs(), key=lambda job: job.created_at, reverse=True)
        snapshot = tuple((job.id, job.status.value, job.phase, round(job.progress, 3),
                         job.output_path.name, str(job.log_path)) for job in jobs)
        if snapshot == self._job_history_snapshot:
            return
        self._job_history_snapshot = snapshot
        self.job_history.blockSignals(True)
        self.job_history.clear()
        selected_row = -1
        for row, job in enumerate(jobs):
            name = f"{job.status.value.upper()} · {job.output_path.name} · {job.progress:.0%}"
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, job.id)
            item.setToolTip(f"{job.phase}\n{job.output_path}\n{job.log_path}")
            self.job_history.addItem(item)
            if job.id == selected_id or (selected_id is None and self._job and job.id == self._job.id):
                selected_row = row
        if selected_row >= 0:
            self.job_history.setCurrentRow(selected_row)
        self.job_history.blockSignals(False)
        self._job_history_selection_changed(self.job_history.currentItem(), None)

    def _job_history_selection_changed(self, current, _previous):
        if current is None or self._job_manager is None:
            self.open_job_log_button.setEnabled(False)
            return
        self.open_job_log_button.setEnabled(True)
        job_id = current.data(Qt.ItemDataRole.UserRole)
        if job_id == self._selected_job_log_id:
            return
        self._selected_job_log_id = job_id
        try:
            job = self._job_manager.get_job(job_id)
            self.job_log_view.setPlainText(self._read_job_log(job))
        except (KeyError, OSError) as exc:
            self.job_log_view.setPlainText(f"Log non disponibile: {exc}")

    @staticmethod
    def _read_job_log(job):
        try:
            with job.log_path.open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - 16000))
                text = stream.read(16000).decode("utf-8", errors="replace")
        except OSError as exc:
            text = f"Log non disponibile: {exc}"
        return text[-16000:]

    def _show_selected_job_log(self):
        item = self.job_history.currentItem()
        if item is None or self._job_manager is None:
            return
        try:
            job = self._job_manager.get_job(item.data(Qt.ItemDataRole.UserRole))
        except KeyError:
            return
        self._selected_job_log_id = job.id
        self.job_log_view.setPlainText(self._read_job_log(job))
        self.inspector_tabs.setCurrentWidget(self.export_panel)

    def toggle_playback(self):
        if self._playing:
            self.stop_playback()
        else:
            self.start_playback()

    def start_playback(self):
        if not self.timeline.get("scenes"):
            return
        if self._playhead >= timeline_duration(self.timeline):
            self.seek_to(0)
        self._playing = True
        self.play_button.setText("Ⅱ Pausa")
        self._configure_audio_players(self._playhead, play=True)
        self._load_scene_for_playback()

    def stop_playback(self):
        self._playing = False
        self.image_timer.stop()
        self._image_clock = None
        self._tail_clock = None
        self.video_player.pause()
        for player in getattr(self, "_crossfade_players", ()):
            player.pause()
        self.voice_player.pause()
        self.music_player.pause()
        self.play_button.setText("▶ Riproduci")

    def _load_scene_for_playback(self):
        index = self._scene_index_at(self._playhead)
        if index is None:
            if self._playhead < timeline_duration(self.timeline):
                self._begin_audio_tail()
            else:
                self.stop_playback()
            return
        scene = self.timeline["scenes"][index]
        local = max(0.0, self._playhead - self._scene_starts[index])
        self._show_scene(index, local, play=self._playing)
        self._show_image_crossfade(self._playhead, sync=True)
        if self._playing and scene.get("type") == "image":
            self._image_clock = time.monotonic()
            self.image_timer.start()

    def _scene_index_at(self, seconds):
        if not self.timeline.get("scenes") or seconds >= timeline_visual_duration(self.timeline):
            return None
        index = 0
        for item_index, start in enumerate(self._scene_starts):
            if seconds >= start:
                index = item_index
            else:
                break
        return index

    def seek_to(self, seconds, preserve_selection=False):
        total = timeline_duration(self.timeline)
        seconds = min(max(0.0, float(seconds)), total)
        self._playhead = seconds
        self.timeline_widget.set_playhead(seconds)
        blocker = QSignalBlocker(self.position_slider)
        self.position_slider.setRange(0, max(0, int(total * 1000)))
        self.position_slider.setValue(int(seconds * 1000))
        del blocker
        self.time_label.setText(f"{self._format_time(seconds)} / {self._format_time(total)}")
        index = self._scene_index_at(seconds)
        if index is None:
            self._begin_audio_tail(reset_clock=self._playing)
            if self._playing:
                self._configure_audio_players(seconds, play=True)
            return
        if not preserve_selection and self._selected != ("scene", index):
            self._selected = ("scene", index)
            self.timeline_widget.set_selected("scene", index)
            self._show_properties()
        local = max(0.0, seconds - self._scene_starts[index])
        self._show_scene(index, local, play=self._playing)
        self._show_image_crossfade(seconds, sync=True)
        if self._playing and self.timeline["scenes"][index].get("type") == "image":
            self._image_start_timeline = seconds
            self._image_clock = time.monotonic()
            self.image_timer.start()
        self._update_text_overlay(seconds)
        if self._playing:
            self._configure_audio_players(seconds, play=True)

    @staticmethod
    def _format_time(seconds):
        seconds = max(0, int(seconds))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _slider_seek(self, milliseconds):
        self.seek_to(milliseconds / 1000)

    def _show_image_crossfade(self, seconds, *, sync=False):
        """Composite crossfades using cached stills and asynchronous Qt video frames."""
        scenes = self.timeline.get("scenes", [])
        transitions = self.timeline.get("transitions") or {}
        between = transitions.get("between", []) if isinstance(transitions, dict) else []
        by_after = {item.get("after"): item for item in between if isinstance(item, dict)}
        for index, scene in enumerate(scenes[:-1]):
            transition = by_after.get(scene.get("id"), {})
            if transition.get("type") != "crossfade":
                continue
            start = self._scene_starts[index + 1]
            duration = float(transition.get("duration", 0))
            if duration <= 0 or not start <= seconds < start + duration:
                continue
            next_scene = scenes[index + 1]
            key = (scene.get("id"), next_scene.get("id"), start, duration)
            if self._crossfade_key != key:
                self._crossfade_key = key
                self._crossfade_frames = [QImage(), QImage()]
                self._configure_crossfade_sources(index, seconds)
            elif sync:
                self._crossfade_frames = [QImage(), QImage()]
                self._configure_crossfade_sources(index, seconds)
            else:
                self._sync_crossfade_players(index, seconds)
            images = []
            for slot, item in enumerate((scene, next_scene)):
                if item.get("type") == "image":
                    images.append(self._load_crossfade_still(item.get("asset", "")))
                else:
                    images.append(self._crossfade_frames[slot])
            if any(image.isNull() for image in images):
                return True
            size = self.image_label.contentsRect().size()
            if size.width() <= 0 or size.height() <= 0:
                return False
            opacity = min(1.0, max(0.0, (seconds - start) / duration))
            canvas = _crossfade_frame(images, size, opacity, self._crossfade_style)
            self.image_label.setText("")
            self.image_label.setPixmap(QPixmap.fromImage(canvas))
            self.preview_layout.setCurrentWidget(self.image_label)
            self._image_crossfade_active = True
            return True
        if self._crossfade_key is not None:
            self._crossfade_key = None
            self._crossfade_frames = [QImage(), QImage()]
            for player in self._crossfade_players:
                player.pause()
        if self._image_crossfade_active:
            index = self._scene_index_at(seconds)
            if index is not None and scenes[index].get("type") == "video":
                self.preview_layout.setCurrentWidget(self.video_widget)
            elif index is not None and scenes[index].get("type") == "image":
                reader = QImageReader(str(scenes[index].get("asset", "")))
                reader.setAutoTransform(True)
                image = reader.read()
                if not image.isNull():
                    size = self.image_label.contentsRect().size()
                    self.image_label.setPixmap(QPixmap.fromImage(image).scaled(
                        size, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ))
            self._image_crossfade_active = False
        return False

    def _load_crossfade_still(self, asset):
        path = Path(str(asset))
        try:
            stat = path.stat()
            key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        except OSError:
            return QImage()
        image = self._crossfade_stills.get(key)
        if image is None:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            image = reader.read()
            if image.isNull():
                return image
            self._crossfade_stills[key] = image
            while len(self._crossfade_stills) > 16:
                self._crossfade_stills.popitem(last=False)
        else:
            self._crossfade_stills.move_to_end(key)
        return image

    def _configure_crossfade_sources(self, transition_index, seconds):
        scenes = self.timeline["scenes"]
        for slot, scene_index in enumerate((transition_index, transition_index + 1)):
            scene = scenes[scene_index]
            if scene.get("type") != "video":
                self._crossfade_pending[slot] = None
                self._crossfade_players[slot].pause()
                continue
            path = Path(str(scene.get("asset", "")))
            if not path.is_file():
                continue
            local = max(0.0, seconds - self._scene_starts[scene_index])
            player = self._crossfade_players[slot]
            preview_path = self._preview_source_path(path)
            url = QUrl.fromLocalFile(str(preview_path.resolve()))
            media_ready = player.mediaStatus() in {
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
            }
            position = int((float(scene.get("source_in", 0)) + local) * 1000)
            if player.source() != url or not media_ready:
                player.stop()
                player.setSource(url)
                self._crossfade_pending[slot] = (url, position, bool(self._playing))
            else:
                self._crossfade_pending[slot] = None
                player.setPosition(position)
                if self._playing:
                    player.play()
                else:
                    player.pause()
        self._crossfade_last_sync = time.monotonic()

    def _sync_crossfade_players(self, transition_index, seconds):
        """Correct decoder drift occasionally, without seeking on every preview tick."""
        now = time.monotonic()
        if now - self._crossfade_last_sync < 0.25:
            return
        for slot, scene_index in enumerate((transition_index, transition_index + 1)):
            scene = self.timeline["scenes"][scene_index]
            if scene.get("type") != "video":
                continue
            player = self._crossfade_players[slot]
            local = max(0.0, seconds - self._scene_starts[scene_index])
            expected = int((float(scene.get("source_in", 0)) + local) * 1000)
            if abs(player.position() - expected) > 100:
                player.setPosition(expected)
        self._crossfade_last_sync = now

    def _crossfade_status_0(self, status):
        self._crossfade_video_status_changed(0, status)

    def _crossfade_status_1(self, status):
        self._crossfade_video_status_changed(1, status)

    def _crossfade_frame_0(self, frame):
        self._crossfade_frame_changed(0, frame)

    def _crossfade_frame_1(self, frame):
        self._crossfade_frame_changed(1, frame)

    def _crossfade_video_status_changed(self, slot, status):
        pending = self._crossfade_pending[slot]
        if pending is None or status not in {
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        }:
            return
        url, position, should_play = pending
        self._crossfade_pending[slot] = None
        if player.source() != url or self._crossfade_key is None:
            return
        player.setPosition(position)
        if should_play and self._playing:
            player.play()
        else:
            player.pause()

    def _crossfade_frame_changed(self, slot, frame):
        if self._crossfade_key is None or not frame.isValid():
            return
        scenes = self.timeline.get("scenes", [])
        scene_ids = [item.get("id") for item in scenes]
        key_ids = self._crossfade_key[:2]
        try:
            scene_index = scene_ids.index(key_ids[slot])
        except (ValueError, IndexError):
            return
        scene = scenes[scene_index]
        if scene.get("type") != "video":
            return
        expected_url = QUrl.fromLocalFile(str(self._preview_source_path(scene.get("asset", "")).resolve()))
        if self._crossfade_players[slot].source() != expected_url:
            return
        image = frame.toImage()
        if image.isNull():
            return
        size = self.image_label.contentsRect().size()
        if size.width() > 0 and size.height() > 0:
            image = image.scaled(
                size, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._crossfade_frames[slot] = image
        self._show_image_crossfade(self._playhead)

    def _show_scene(self, index, local_seconds, *, play=False):
        if index < 0 or index >= len(self.timeline.get("scenes", [])):
            return
        scene = self.timeline["scenes"][index]
        asset = Path(str(scene.get("asset", "")))
        if not asset.is_file():
            self._show_preview_error(f"File scena non trovato: {asset}", index)
            self._current_scene = index
            return
        self._tail_clock = None
        if scene.get("type") == "image":
            self.image_timer.stop()
            self.video_player.pause()
            self._pending_video_seek = None
            reader = QImageReader(str(asset))
            reader.setAutoTransform(True)
            image = reader.read()
            if image.isNull():
                self._show_preview_error(
                    f"Immagine non leggibile: {reader.errorString()}\n{asset}", index
                )
                self._current_scene = index
                return
            pixmap = QPixmap.fromImage(image)
            if pixmap.isNull():
                self._show_preview_error(f"Qt non ha potuto creare l'anteprima:\n{asset}", index)
                self._current_scene = index
                return
            else:
                self.image_label.setText("")
                self.image_label.setToolTip(str(asset))
                self.image_label.setPixmap(pixmap.scaled(self.image_label.contentsRect().size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                        Qt.TransformationMode.SmoothTransformation))
            self.preview_layout.setCurrentWidget(self.image_label)
            if play:
                self._image_start_timeline = self._playhead
                self._image_clock = time.monotonic()
                self.image_timer.start()
        else:
            self.image_timer.stop()
            preview_asset = self._preview_source_path(asset)
            source_url = QUrl.fromLocalFile(str(preview_asset.resolve()))
            target_position = int((float(scene.get("source_in", 0)) + local_seconds) * 1000)
            self._current_scene = index
            self.preview_layout.setCurrentWidget(self.video_widget)
            media_status = self.video_player.mediaStatus()
            media_ready = media_status in {
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
            }
            if self.video_player.source() != source_url or media_status in {
                QMediaPlayer.MediaStatus.NoMedia,
                QMediaPlayer.MediaStatus.InvalidMedia,
            }:
                self.video_player.stop()
                self._pending_video_seek = (source_url, index, target_position, bool(play))
                self.video_player.setSource(source_url)
            elif not media_ready:
                # A seek on the current URL is still deferred while Qt is
                # loading it; otherwise LoadedMedia can reset the position.
                self._pending_video_seek = (source_url, index, target_position, bool(play))
            else:
                # Adjacent timeline segments can use the same source file.
                # Reuse the loaded media and seek directly so Qt does not
                # asynchronously reset the player to the start of that file.
                self._pending_video_seek = None
                self.video_player.setPosition(target_position)
                if play:
                    self.video_player.play()
                else:
                    self.video_player.pause()
        self._current_scene = index
        self.overlay_widget.raise_()

    def _show_preview_error(self, message, scene_index):
        self.stop_playback()
        self._pending_video_seek = None
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText(message)
        self.image_label.setToolTip(message)
        self.preview_layout.setCurrentWidget(self.image_label)
        self._current_scene = scene_index
        self.status_label.setText(message)

    def _configure_audio_players(self, timeline_seconds, *, play):
        for player, output, path, source_in, volume in (
            (self.voice_player, self.voice_output, self.timeline.get("audio"), 0.0, 1.0),
            (self.music_player, self.music_output,
             (self.timeline.get("background_audio") or {}).get("asset"),
             float((self.timeline.get("background_audio") or {}).get("source_in", 0)),
             float((self.timeline.get("background_audio") or {}).get("volume", 0.2))),
        ):
            if not isinstance(path, str) or not Path(path).is_file():
                player.stop()
                continue
            url = QUrl.fromLocalFile(str(Path(path).resolve()))
            if player.source() != url:
                player.setSource(url)
            output.setVolume(max(0.0, min(1.0, volume)))
            player.setPosition(int((source_in + timeline_seconds) * 1000))
            if play:
                player.play()

    def _video_position_changed(self, milliseconds):
        if not self._playing or self._current_scene is None:
            return
        scene = self.timeline["scenes"][self._current_scene]
        if scene.get("type") != "video":
            return
        local = max(0.0, milliseconds / 1000 - float(scene.get("source_in", 0)))
        if local >= float(scene.get("duration", 0)):
            self._advance_scene()
            return
        self._set_playhead_from_playback(self._scene_starts[self._current_scene] + local)

    def _video_status_changed(self, status):
        if (status in {QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia}
                and self._pending_video_seek is not None):
            source_url, scene_index, position, should_play = self._pending_video_seek
            self._pending_video_seek = None
            if self.video_player.source() == source_url and self._current_scene == scene_index:
                self.video_player.setPosition(position)
                if should_play and self._playing:
                    self.video_player.play()
        if (status == QMediaPlayer.MediaStatus.EndOfMedia and self._playing
                and self._current_scene is not None):
            self._advance_scene()

    def _begin_audio_tail(self, *, reset_clock=False):
        self.image_timer.stop()
        self._image_clock = None
        self.video_player.stop()
        self._current_scene = None
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText("Nessuna immagine in questo intervallo")
        self.image_label.setToolTip("")
        self.preview_layout.setCurrentWidget(self.image_label)
        self._update_text_overlay(self._playhead)
        if self._playing:
            if reset_clock or self._tail_clock is None:
                self._tail_start = self._playhead
                self._tail_clock = time.monotonic()
            self.image_timer.start()
        else:
            self._tail_clock = None

    def _tick_image(self):
        if not self._playing:
            return
        if self._current_scene is None:
            if self._tail_clock is None:
                return
            seconds = self._tail_start + time.monotonic() - self._tail_clock
            total = timeline_duration(self.timeline)
            if seconds >= total:
                self._set_playhead_from_playback(total)
                self.stop_playback()
            else:
                self._set_playhead_from_playback(seconds)
            return
        if self._image_clock is None:
            return
        index = self._current_scene
        elapsed = time.monotonic() - self._image_clock
        local = self._image_start_timeline - self._scene_starts[index] + elapsed
        if local >= float(self.timeline["scenes"][index].get("duration", 3)):
            self._advance_scene()
        else:
            self._set_playhead_from_playback(self._scene_starts[index] + local)

    def _advance_scene(self):
        if self._current_scene is None:
            return
        next_index = self._current_scene + 1
        if next_index >= len(self.timeline.get("scenes", [])):
            visual_end = timeline_visual_duration(self.timeline)
            if timeline_duration(self.timeline) > visual_end:
                self._playhead = visual_end
                self._set_playhead_from_playback(visual_end)
                self._begin_audio_tail(reset_clock=True)
            else:
                self._set_playhead_from_playback(visual_end)
                self.stop_playback()
            return
        self._playhead = self._scene_starts[next_index]
        self._selected = ("scene", next_index)
        self.timeline_widget.set_selected("scene", next_index)
        self._show_properties()
        self._load_scene_for_playback()

    def _set_playhead_from_playback(self, seconds):
        total = timeline_duration(self.timeline)
        self._playhead = min(seconds, total)
        blocker = QSignalBlocker(self.position_slider)
        self.position_slider.setRange(0, max(0, int(total * 1000)))
        self.position_slider.setValue(int(self._playhead * 1000))
        del blocker
        self.time_label.setText(f"{self._format_time(self._playhead)} / {self._format_time(total)}")
        self.timeline_widget.set_playhead(self._playhead)
        self._update_text_overlay(self._playhead)
        self._show_image_crossfade(self._playhead)

    def _update_text_overlay(self, seconds):
        while self.overlay_grid.count():
            child = self.overlay_grid.takeAt(0)
            if child.widget() is not None:
                child.widget().deleteLater()
        active_texts = [item for item in self.timeline.get("texts", [])
                        if float(item.get("start", 0)) <= seconds < float(item.get("end", 0))]
        active_subtitles = [item for item in self.timeline.get("subtitles", [])
                            if float(item.get("start", 0)) <= seconds < float(item.get("end", 0))]
        positions = {
            "top_left": (0, 0), "top_center": (0, 1), "top_right": (0, 2),
            "center_left": (1, 0), "center": (1, 1), "center_right": (1, 2),
            "bottom_left": (2, 0), "bottom_center": (2, 1), "bottom_right": (2, 2),
        }
        groups = {}
        for kind, entries in (("text", active_texts), ("subtitle", active_subtitles)):
            for item in entries:
                position = item.get("position", "bottom_center" if kind == "subtitle" else "center")
                row, column = positions.get(position, (2, 1))
                groups.setdefault((row, column), []).append(item)
        for (row, column), entries in groups.items():
            item = entries[0]
            color = html.escape(str(item.get("color", "#FFFFFF")), quote=True)
            font = html.escape(str(item.get("font", "Arial")), quote=True)
            size = max(1, int(float(item.get("size", 36))))
            background = item.get("background_color")
            background_style = (f"background-color:{html.escape(str(background), quote=True)};padding:6px 10px;"
                                if background else "")
            content = "<br>".join(html.escape(str(value.get("text", ""))).replace("\n", "<br>")
                                   for value in entries)
            label = QLabel(f'<div style="color:{color};font-family:{font};font-size:{size}px;'
                           f'{background_style}">{content}</div>')
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setStyleSheet("background:transparent")
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter if column == 1 else
                               Qt.AlignmentFlag.AlignRight if column == 2 else Qt.AlignmentFlag.AlignLeft)
            self.overlay_grid.addWidget(label, row, column,
                                        Qt.AlignmentFlag.AlignVCenter if row == 1 else
                                        Qt.AlignmentFlag.AlignTop if row == 0 else Qt.AlignmentFlag.AlignBottom)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._current_scene is not None:
            scene = self.timeline.get("scenes", [])[self._current_scene]
            if scene.get("type") == "image":
                self._show_scene(self._current_scene, max(0, self._playhead - self._scene_starts[self._current_scene]))

    def closeEvent(self, event):
        self.stop_playback()
        for player in (self.video_player, self.voice_player, self.music_player):
            player.stop()
            player.setSource(QUrl())
        super().closeEvent(event)

    def _message(self, title, body):
        QMessageBox.critical(self, title, body)


__all__ = ["TimelineEditorPage", "TimelineWidget", "timeline_duration", "timeline_scene_starts",
           "timeline_visual_duration"]


