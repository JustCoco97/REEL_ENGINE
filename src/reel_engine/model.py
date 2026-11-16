from dataclasses import dataclass
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


@dataclass(frozen=True)
class VideoSettings:
    width: int = 1080
    height: int = 1920
    fps: float = 30.0


@dataclass(frozen=True)
class Scene:
    id: str
    asset: Path
    media_type: str
    duration: float
    source_in: float = 0.0


@dataclass(frozen=True)
class BackgroundAudio:
    asset: Path
    source_in: float = 0.0
    duration: float | None = None
    volume: float = 0.2
    fade_in: float = 0.0
    fade_out: float = 0.0


@dataclass(frozen=True)
class TimedText:
    start: float
    end: float
    text: str
    font: Path
    size: float
    color: str
    position: str
    outline_color: str
    outline_width: int
    background_color: str | None
    background_padding: int


@dataclass(frozen=True)
class Transition:
    type: str
    duration: float
    after: str | None = None


@dataclass(frozen=True)
class TransitionSettings:
    start: Transition | None = None
    between: tuple[Transition, ...] = ()
    end: Transition | None = None


@dataclass(frozen=True)
class Timeline:
    scenes: tuple[Scene, ...]
    audio_asset: Path | None
    video: VideoSettings = VideoSettings()
    background_audio: BackgroundAudio | None = None
    texts: tuple[TimedText, ...] = ()
    subtitles: tuple[TimedText, ...] = ()
    transitions: TransitionSettings = TransitionSettings()