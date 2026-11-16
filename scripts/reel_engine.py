import argparse
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
SRC = ENGINE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from reel_engine.model import IMAGE_SUFFIXES, Scene, Timeline, VideoSettings
from reel_engine.rendering import CROSSFADE_STYLES, audio_duration, render_timeline, run
from reel_engine.validation import load_timeline


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

    scene_duration = audio_duration(audio_asset) / len(images)
    scenes = tuple(
        Scene(f"scene_{index:03d}", image, "image", scene_duration)
        for index, image in enumerate(images, start=1)
    )
    return Timeline(scenes, audio_asset)


def main(argv=None):
    parser = argparse.ArgumentParser(description="REEL ENGINE - Video automatici")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--timeline", help="File JSON della timeline (schema_version 1)")
    mode.add_argument("--images", help="Cartella delle immagini (modalità legacy)")
    parser.add_argument("--audio", help="File audio della narrazione (modalità legacy)")
    parser.add_argument("--output", default="reel_finale.mp4")
    parser.add_argument(
        "--crossfade-style", choices=sorted(CROSSFADE_STYLES), default="fade",
        help="Stile FFmpeg per tutti i crossfade della timeline (default: fade).",
    )
    args = parser.parse_args(argv)

    if args.timeline and args.audio:
        parser.error("--audio si usa con --images; la modalità --timeline dichiara l'audio nel JSON.")
    if args.images and not args.audio:
        parser.error("--audio è obbligatorio quando si usa --images.")

    try:
        timeline = load_timeline(args.timeline) if args.timeline else legacy_timeline(args.images, args.audio)
    except ValueError as exc:
        parser.error(str(exc))
    render_timeline(timeline, args.output, crossfade_style=args.crossfade_style)


if __name__ == "__main__":
    main()
