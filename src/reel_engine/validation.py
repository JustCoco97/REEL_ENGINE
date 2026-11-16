import json
import math
import os
import re
import subprocess
from pathlib import Path

from .model import BackgroundAudio, IMAGE_SUFFIXES, VIDEO_SUFFIXES, Scene, TimedText, Timeline, Transition, TransitionSettings, VideoSettings
from .timing import align_seconds, frames_to_seconds, seconds_to_frames


def _finite_number(value, description, *, minimum=None, exclusive_minimum=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{description}: deve essere un numero.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{description}: deve essere finito.")
    if minimum is not None:
        invalid = number <= minimum if exclusive_minimum else number < minimum
        if invalid:
            op = ">" if exclusive_minimum else ">="
            raise ValueError(f"{description}: deve essere {op} {minimum}.")
    return number


def _resolve_file(value, base_dir, description):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{description}: percorso mancante o non valido.")
    path = Path(value.strip())
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"{description}: file non trovato: {path}")
    return path


def _load_video_settings(raw):
    if raw is None:
        return VideoSettings()
    if not isinstance(raw, dict):
        raise ValueError("'canvas' deve essere un oggetto JSON.")
    allowed = {"width", "height", "fps"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"Impostazioni canvas non riconosciute: {', '.join(sorted(unknown))}")

    settings = VideoSettings(
        width=raw.get("width", 1080),
        height=raw.get("height", 1920),
        fps=_finite_number(raw.get("fps", 30), "canvas.fps", minimum=0, exclusive_minimum=True),
    )
    for name, value in (("canvas.width", settings.width), ("canvas.height", settings.height)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 2 or value % 2:
            raise ValueError(f"{name}: deve essere un intero pari maggiore o uguale a 2.")
    return settings


def load_timeline(path):
    """Load and validate a schema v1 timeline JSON into the normalized model."""
    timeline_path = Path(path).resolve()
    try:
        with timeline_path.open("r", encoding="utf-8-sig") as file:
            raw = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON non valido in {timeline_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("La timeline deve essere un oggetto JSON.")
    allowed = {"schema_version", "canvas", "audio", "background_audio", "texts", "subtitles", "transitions", "scenes"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"Campi timeline non riconosciuti: {', '.join(sorted(unknown))}")
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ValueError(f"schema_version non supportata: {version!r} (richiesta 1).")

    base_dir = timeline_path.parent
    audio_value = raw.get("audio")
    audio_asset = None if audio_value is None else _resolve_file(audio_value, base_dir, "audio")
    video = _load_video_settings(raw.get("canvas"))
    raw_scenes = raw.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ValueError("'scenes' deve contenere almeno una scena.")

    scenes = []
    identifiers = set()
    for index, item in enumerate(raw_scenes, start=1):
        label = f"scenes[{index - 1}]"
        if not isinstance(item, dict):
            raise ValueError(f"{label}: deve essere un oggetto JSON.")
        scene_allowed = {"id", "type", "asset", "duration", "source_in"}
        scene_unknown = set(item) - scene_allowed
        if scene_unknown:
            raise ValueError(f"{label}: campi non riconosciuti: {', '.join(sorted(scene_unknown))}")

        scene_id = item.get("id")
        if not isinstance(scene_id, str) or not scene_id.strip():
            raise ValueError(f"{label}.id: identificativo mancante o non valido.")
        scene_id = scene_id.strip()
        if any(not (char.isalnum() or char in "_-") for char in scene_id) or not scene_id[0].isalnum():
            raise ValueError(f"{label}.id: usa solo lettere, numeri, '_' e '-'.")
        if scene_id in identifiers:
            raise ValueError(f"{label}.id: identificativo duplicato '{scene_id}'.")
        identifiers.add(scene_id)

        media_type = item.get("type")
        if not isinstance(media_type, str) or media_type not in {"image", "video"}:
            raise ValueError(f"{label}.type: deve essere 'image' o 'video'.")
        asset = _resolve_file(item.get("asset"), base_dir, f"{label}.asset")
        valid_suffixes = IMAGE_SUFFIXES if media_type == "image" else VIDEO_SUFFIXES
        if asset.suffix.lower() not in valid_suffixes:
            raise ValueError(f"{label}.asset: estensione incompatibile con type='{media_type}': {asset.suffix}")
        duration = _finite_number(item.get("duration"), f"{label}.duration", minimum=0, exclusive_minimum=True)
        source_in = _finite_number(item.get("source_in", 0), f"{label}.source_in", minimum=0)
        duration = align_seconds(duration, video.fps, minimum_frames=1)
        source_in = align_seconds(source_in, video.fps)
        if media_type == "video":
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(asset)],
                    capture_output=True, text=True, check=True,
                )
                metadata = json.loads(probe.stdout)
                video_streams = [stream for stream in metadata.get("streams", [])
                                 if stream.get("codec_type") == "video"]
                source_duration = float(metadata.get("format", {}).get("duration", "nan"))
            except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"{label}.asset: sorgente video non leggibile: {asset}") from exc
            if not video_streams:
                raise ValueError(f"{label}.asset: nessuno stream video trovato: {asset}")
            if not math.isfinite(source_duration):
                raise ValueError(f"{label}.asset: durata sorgente non valida: {asset}")
            source_frames = seconds_to_frames(source_duration, video.fps)
            used_frames = seconds_to_frames(source_in, video.fps) + seconds_to_frames(duration, video.fps)
            if used_frames > source_frames:
                raise ValueError(
                    f"{label}: source_in + duration ({source_in + duration:g}s) supera "
                    f"la durata della sorgente ({source_duration:g}s)."
                )
        scenes.append(Scene(scene_id, asset, media_type, duration, source_in))

    transitions = _load_transitions(raw.get("transitions"), scenes, video.fps)
    video_frames = sum(seconds_to_frames(scene.duration, video.fps) for scene in scenes)
    crossfade_frames = sum(
        seconds_to_frames(item.duration, video.fps)
        for item in transitions.between if item.type == "crossfade"
    )
    video_duration = frames_to_seconds(video_frames - crossfade_frames, video.fps)

    background_audio = _load_background_audio(raw.get("background_audio"), base_dir)
    if background_audio is not None:
        track_duration = min(background_audio.duration or video_duration, video_duration)
        if background_audio.fade_in + background_audio.fade_out > track_duration:
            raise ValueError(
                "background_audio.fade_in + fade_out non possono superare la durata "
                "effettiva della traccia."
            )

    texts = _load_timed_texts(
        raw.get("texts"), "texts", base_dir, video, video_duration,
        default_position="center", default_size=48,
    )
    subtitles = _load_timed_texts(
        raw.get("subtitles"), "subtitles", base_dir, video, video_duration,
        default_position="bottom_center", default_size=36,
    )
    return Timeline(tuple(scenes), audio_asset, video, background_audio, texts, subtitles, transitions)


def _load_transitions(raw, scenes, fps):
    if raw is None:
        return TransitionSettings()
    if not isinstance(raw, dict):
        raise ValueError("'transitions' deve essere un oggetto JSON.")
    unknown = set(raw) - {"start", "between", "end"}
    if unknown:
        raise ValueError(f"transitions: campi non riconosciuti: {', '.join(sorted(unknown))}")

    start = _load_terminal_transition(
        raw.get("start"), "fade_from_black", "transitions.start", scenes[0], fps
    )
    end = _load_terminal_transition(
        raw.get("end"), "fade_to_black", "transitions.end", scenes[-1], fps
    )
    raw_between = raw.get("between", [])
    if not isinstance(raw_between, list):
        raise ValueError("transitions.between deve essere una lista.")

    scene_ids = [scene.id for scene in scenes]
    positions = {scene_id: index for index, scene_id in enumerate(scene_ids)}
    between = []
    used_after = set()
    overlap_frames = [0] * len(scenes)
    for index, item in enumerate(raw_between):
        label = f"transitions.between[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{label}: deve essere un oggetto JSON.")
        unknown = set(item) - {"after", "type", "duration"}
        if unknown:
            raise ValueError(f"{label}: campi non riconosciuti: {', '.join(sorted(unknown))}")
        after = item.get("after")
        if not isinstance(after, str) or after not in positions or positions[after] == len(scenes) - 1:
            raise ValueError(f"{label}.after: deve identificare una scena seguita da un'altra.")
        if after in used_after:
            raise ValueError(f"{label}.after: transizione duplicata dopo '{after}'.")
        used_after.add(after)

        kind = item.get("type")
        if not isinstance(kind, str) or kind not in {"cut", "crossfade"}:
            raise ValueError(f"{label}.type: deve essere 'cut' o 'crossfade'.")
        if kind == "cut":
            duration = _finite_number(item.get("duration", 0), f"{label}.duration", minimum=0)
            if duration != 0:
                raise ValueError(f"{label}.duration: un cut deve avere durata zero.")
        else:
            duration = _finite_number(item.get("duration"), f"{label}.duration", minimum=0, exclusive_minimum=True)
            duration = align_seconds(duration, fps, minimum_frames=1)
            overlap = seconds_to_frames(duration, fps)
            scene_index = positions[after]
            overlap_frames[scene_index] += overlap
            overlap_frames[scene_index + 1] += overlap
        between.append(Transition(kind, duration, after))

    scene_frames = [seconds_to_frames(scene.duration, fps) for scene in scenes]
    if any(overlap > available for overlap, available in zip(overlap_frames, scene_frames)):
        raise ValueError("La somma delle dissolvenze incrociate non può superare la durata di una scena.")
    return TransitionSettings(start, tuple(between), end)


def _load_terminal_transition(raw, expected_type, description, scene, fps):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{description} deve essere un oggetto JSON.")
    unknown = set(raw) - {"type", "duration"}
    if unknown:
        raise ValueError(f"{description}: campi non riconosciuti: {', '.join(sorted(unknown))}")
    if raw.get("type") != expected_type:
        raise ValueError(f"{description}.type deve essere '{expected_type}'.")
    duration = _finite_number(raw.get("duration"), f"{description}.duration", minimum=0, exclusive_minimum=True)
    duration = align_seconds(duration, fps, minimum_frames=1)
    if seconds_to_frames(duration, fps) > seconds_to_frames(scene.duration, fps):
        raise ValueError(f"{description}.duration supera la durata della scena iniziale/finale.")
    return Transition(expected_type, duration)


def _load_timed_texts(raw, field, base_dir, video, video_duration, *, default_position, default_size):
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"'{field}' deve essere una lista.")

    allowed = {
        "start", "end", "text", "font", "size", "color", "position",
        "outline_color", "outline_width", "background_color", "background_padding",
    }
    positions = {
        "top_left", "top_center", "top_right",
        "center_left", "center", "center_right",
        "bottom_left", "bottom_center", "bottom_right",
    }
    result = []
    for index, item in enumerate(raw):
        label = f"{field}[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{label}: deve essere un oggetto JSON.")
        unknown = set(item) - allowed
        if unknown:
            raise ValueError(f"{label}: campi non riconosciuti: {', '.join(sorted(unknown))}")

        text = item.get("text")
        if not isinstance(text, str) or not text.strip() or any(ord(char) == 0 for char in text):
            raise ValueError(f"{label}.text: deve contenere testo non vuoto.")
        start = _finite_number(item.get("start"), f"{label}.start", minimum=0)
        end = _finite_number(item.get("end"), f"{label}.end", minimum=0, exclusive_minimum=True)
        start = align_seconds(start, video.fps)
        end = align_seconds(end, video.fps)
        if end <= start:
            raise ValueError(f"{label}.end: deve essere successivo a start dopo l'allineamento ai frame.")
        if end > video_duration:
            raise ValueError(f"{label}.end: supera la durata della timeline ({video_duration:g}s).")

        font = _resolve_font(item.get("font", "Arial"), base_dir, f"{label}.font")
        size = _finite_number(item.get("size", default_size), f"{label}.size", minimum=0, exclusive_minimum=True)
        if size > 300:
            raise ValueError(f"{label}.size: deve essere al massimo 300.")
        color = _text_color(item.get("color", "white"), f"{label}.color")
        position = item.get("position", default_position)
        if not isinstance(position, str) or position not in positions:
            raise ValueError(f"{label}.position: usa una delle posizioni supportate.")
        outline_color = _text_color(item.get("outline_color", "black"), f"{label}.outline_color")
        outline_width = item.get("outline_width", 2)
        if isinstance(outline_width, bool) or not isinstance(outline_width, int) or not 0 <= outline_width <= 20:
            raise ValueError(f"{label}.outline_width: deve essere un intero tra 0 e 20.")
        background_color = item.get("background_color")
        if background_color is not None:
            background_color = _text_color(background_color, f"{label}.background_color")
        background_padding = item.get("background_padding", 8)
        if isinstance(background_padding, bool) or not isinstance(background_padding, int) or not 0 <= background_padding <= 100:
            raise ValueError(f"{label}.background_padding: deve essere un intero tra 0 e 100.")

        result.append(TimedText(
            start, end, text, font, size, color, position, outline_color,
            outline_width, background_color, background_padding,
        ))
    return tuple(result)


def _text_color(value, description):
    if not isinstance(value, str) or re.fullmatch(
        r"(?:[A-Za-z]+|#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?)(?:@(?:0(?:\.\d+)?|1(?:\.0+)?))?",
        value,
    ) is None:
        raise ValueError(f"{description}: colore non valido.")
    return value


def _resolve_font(value, base_dir, description):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{description}: nome o percorso font mancante.")
    value = value.strip()
    candidate = Path(value)
    supported = {".ttf", ".otf", ".ttc"}
    if candidate.suffix.lower() in supported:
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        candidate = candidate.resolve()
        if not candidate.is_file():
            raise ValueError(f"{description}: file font non trovato: {candidate}")
        return candidate
    if candidate.suffix:
        raise ValueError(f"{description}: usa un font .ttf, .otf o .ttc.")

    family = value.casefold()
    fonts_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    try:
        import winreg
        registry_key = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, registry_key) as key:
                    value_count = winreg.QueryInfoKey(key)[1]
                    for index in range(value_count):
                        name, filename, _ = winreg.EnumValue(key, index)
                        registered_family = re.sub(r"\s*\([^)]*\)$", "", name).casefold()
                        if registered_family == family:
                            font_path = Path(filename)
                            if not font_path.is_absolute():
                                font_path = fonts_dir / font_path
                            if font_path.is_file():
                                return font_path.resolve()
            except OSError:
                continue
    except ImportError:
        pass

    raise ValueError(f"{description}: font Windows non trovato: {value}")


def _load_background_audio(raw, base_dir):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("'background_audio' deve essere un oggetto JSON.")
    allowed = {"asset", "source_in", "duration", "volume", "fade_in", "fade_out"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(
            f"background_audio: campi non riconosciuti: {', '.join(sorted(unknown))}"
        )

    asset = _resolve_file(raw.get("asset"), base_dir, "background_audio.asset")
    source_in = _finite_number(raw.get("source_in", 0), "background_audio.source_in", minimum=0)
    duration = raw.get("duration")
    if duration is not None:
        duration = _finite_number(duration, "background_audio.duration", minimum=0, exclusive_minimum=True)
    volume = _finite_number(raw.get("volume", 0.2), "background_audio.volume", minimum=0)
    if volume > 1:
        raise ValueError("background_audio.volume: deve essere compreso tra 0 e 1.")
    fade_in = _finite_number(raw.get("fade_in", 0), "background_audio.fade_in", minimum=0)
    fade_out = _finite_number(raw.get("fade_out", 0), "background_audio.fade_out", minimum=0)
    if duration is not None and fade_in + fade_out > duration:
        raise ValueError(
            "background_audio.fade_in + fade_out non possono superare la durata della traccia."
        )
    return BackgroundAudio(asset, source_in, duration, volume, fade_in, fade_out)
