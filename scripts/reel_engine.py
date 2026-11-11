import argparse
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ENGINE = Path(__file__).resolve().parent.parent
TEMP = ENGINE / "temp" / "renders"
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
class Timeline:
    scenes: tuple[Scene, ...]
    audio_asset: Path
    video: VideoSettings = VideoSettings()


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
    allowed = {"schema_version", "canvas", "audio", "scenes"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"Campi timeline non riconosciuti: {', '.join(sorted(unknown))}")
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ValueError(f"schema_version non supportata: {version!r} (richiesta 1).")

    base_dir = timeline_path.parent
    audio_asset = _resolve_file(raw.get("audio"), base_dir, "audio")
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
        scenes.append(Scene(scene_id, asset, media_type, duration, source_in))

    return Timeline(tuple(scenes), audio_asset, video)


def run(command, cwd=None):
    print("\n>", " ".join(map(str, command)))
    subprocess.run(command, check=True, cwd=cwd)


def _audio_duration(audio):
    duration_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio),
    ]
    return float(subprocess.check_output(duration_cmd, text=True).strip())


def legacy_timeline(images_dir, audio):
    images_dir = Path(images_dir).resolve()
    audio_asset = Path(audio).resolve()
    if not images_dir.is_dir():
        raise ValueError("ERRORE: cartella immagini non trovata.")
    if not audio_asset.is_file():
        raise ValueError("ERRORE: file audio non trovato.")

    images = sorted(
        (p.resolve() for p in images_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES),
        key=lambda p: p.name.lower(),
    )
    if not images:
        raise ValueError("ERRORE: nessuna immagine trovata.")

    scene_duration = _audio_duration(audio_asset) / len(images)
    scenes = tuple(
        Scene(f"scene_{index:03d}", image, "image", scene_duration)
        for index, image in enumerate(images, start=1)
    )
    return Timeline(scenes, audio_asset)


def render_timeline(timeline, output):
    unsupported = [scene.id for scene in timeline.scenes if scene.media_type != "image"]
    if unsupported:
        raise ValueError(
            "Il caricamento e la validazione delle scene video sono supportati, "
            "ma il rendering video sarà aggiunto in una fase successiva: "
            + ", ".join(unsupported)
        )

    output = Path(output).resolve()
    TEMP.mkdir(parents=True, exist_ok=True)
    segments = []
    width, height, fps = timeline.video.width, timeline.video.height, timeline.video.fps

    print("\nREEL ENGINE")
    print(f"Scene: {len(timeline.scenes)}")
    print(f"Formato: verticale {width}x{height} a {fps:g} fps")

    for index, scene in enumerate(timeline.scenes, start=1):
        segment = TEMP / f"scene_{index:03d}.mp4"
        segments.append(segment)
        print(f"\nSCENA {index}/{len(timeline.scenes)}: {scene.asset.name} ({scene.duration:.2f}s)")

        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"zoompan=z='min(zoom+0.001,1.12)':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d=1:s={width}x{height}:fps={fps:g},"
            "setsar=1,format=yuv420p"
        )
        run([
            "ffmpeg", "-y",
            "-loop", "1", "-framerate", str(fps),
            "-i", str(scene.asset),
            "-t", str(scene.duration),
            "-vf", video_filter,
            "-an",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-r", str(fps),
            str(segment),
        ])

    concat_file = TEMP / "concat.txt"
    concat_file.write_text(
        "".join(f"file '{p.name}'\n" for p in segments),
        encoding="utf-8",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    print("\nASSEMBLAGGIO FINALE...")
    run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-i", str(timeline.audio_asset),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(output),
    ], cwd=TEMP)
    print(f"\nVIDEO COMPLETATO: {output}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="REEL ENGINE - Video automatici")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--timeline", help="File JSON della timeline (schema_version 1)")
    mode.add_argument("--images", help="Cartella delle immagini (modalità legacy)")
    parser.add_argument("--audio", help="File audio della narrazione (modalità legacy)")
    parser.add_argument("--output", default="reel_finale.mp4")
    args = parser.parse_args(argv)

    if args.timeline and args.audio:
        parser.error("--audio si usa con --images; la modalità --timeline dichiara l'audio nel JSON.")
    if args.images and not args.audio:
        parser.error("--audio è obbligatorio quando si usa --images.")

    try:
        timeline = load_timeline(args.timeline) if args.timeline else legacy_timeline(args.images, args.audio)
    except ValueError as exc:
        parser.error(str(exc))
    render_timeline(timeline, args.output)


if __name__ == "__main__":
    main()
