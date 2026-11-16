import math
import shutil
import subprocess
import threading
import tempfile
import time
from collections import deque
from pathlib import Path

from .timing import frames_to_seconds, seconds_to_frames


class RenderCancelledError(Exception):
    """Raised when a render process is stopped by its owning job."""


# Curated, portable transition names supported by FFmpeg's xfade filter.
# Custom expressions are deliberately excluded from the public CLI.
CROSSFADE_STYLES = frozenset({
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circleopen", "circleclose", "dissolve", "smoothleft", "smoothright",
    "fadeblack", "fadewhite", "radial", "vertopen", "vertclose",
    "horzopen", "horzclose", "zoomin",
})


def run(command, cwd=None, *, progress_callback=None, cancel_event=None, line_callback=None, duration=None):
    print("\n>", " ".join(map(str, command)))
    if progress_callback is None and cancel_event is None and line_callback is None:
        subprocess.run(command, check=True, cwd=cwd)
        return

    command = list(command)
    if progress_callback is not None:
        command[1:1] = ["-progress", "pipe:1", "-nostats"]
    if cancel_event is not None and cancel_event.is_set():
        raise RenderCancelledError("Rendering annullato prima dell'avvio.")
    chunks = deque(maxlen=30)
    progress_values = {}

    def read_output(stream):
        for raw_line in iter(stream.readline, ""):
            line = raw_line.rstrip("\r\n")
            chunks.append(line)
            if line_callback is not None:
                line_callback(line)
            if "=" in line:
                key, value = line.split("=", 1)
                progress_values[key] = value
                if key == "progress" and value == "continue" and progress_callback and duration:
                    stamp = progress_values.get("out_time_us") or progress_values.get("out_time_ms")
                    try:
                        progress_callback(min(1.0, max(0.0, int(stamp) / 1_000_000 / duration)))
                    except (TypeError, ValueError):
                        pass

    process = subprocess.Popen(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    reader = threading.Thread(target=read_output, args=(process.stdout,), daemon=True)
    reader.start()
    while process.poll() is None:
        if cancel_event is not None and cancel_event.wait(0.05):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            reader.join()
            process.stdout.close()
            raise RenderCancelledError("Rendering annullato dall'utente.")
        time.sleep(0.02)
    reader.join()
    if process.returncode:
        message = "\n".join(chunks[-30:])
        process.stdout.close()
        raise subprocess.CalledProcessError(process.returncode, command, output=message)
    process.stdout.close()
    if progress_callback is not None:
        progress_callback(1.0)


def audio_duration(audio):
    duration_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio),
    ]
    return float(subprocess.check_output(duration_cmd, text=True).strip())


def _text_position(position):
    margin = 24
    positions = {
        "top_left": (str(margin), str(margin)),
        "top_center": ("(w-text_w)/2", str(margin)),
        "top_right": (f"w-text_w-{margin}", str(margin)),
        "center_left": (str(margin), "(h-text_h)/2"),
        "center": ("(w-text_w)/2", "(h-text_h)/2"),
        "center_right": (f"w-text_w-{margin}", "(h-text_h)/2"),
        "bottom_left": (str(margin), f"h-text_h-{margin}"),
        "bottom_center": ("(w-text_w)/2", f"h-text_h-{margin}"),
        "bottom_right": (f"w-text_w-{margin}", f"h-text_h-{margin}"),
    }
    return positions[position]


def _drawtext_filter(item, text_file, font_file):
    x, y = _text_position(item.position)
    options = [
        f"fontfile='{font_file}'",
        f"textfile='{text_file}'",
        "expansion=none",
        f"fontsize={item.size:g}",
        f"fontcolor='{item.color}'",
        f"borderw={item.outline_width}",
        f"bordercolor='{item.outline_color}'",
        f"x={x}",
        f"y={y}",
        f"enable='gte(t,{item.start:.9g})*lt(t,{item.end:.9g})'",
        "fix_bounds=1",
    ]
    if item.background_color is not None:
        options.extend([
            "box=1",
            f"boxcolor='{item.background_color}'",
            f"boxborderw={item.background_padding}",
        ])
    return "drawtext=" + ":".join(options)


def _video_duration_frames(timeline):
    scene_frames = sum(seconds_to_frames(scene.duration, timeline.video.fps) for scene in timeline.scenes)
    overlap_frames = sum(
        seconds_to_frames(transition.duration, timeline.video.fps)
        for transition in timeline.transitions.between
        if transition.type == "crossfade"
    )
    return scene_frames - overlap_frames


def _has_video_transitions(transitions):
    return (
        transitions.start is not None
        or transitions.end is not None
        or any(item.type == "crossfade" for item in transitions.between)
    )


def _build_transition_graph(timeline, draw_filters, video_duration, tail_duration=0.0,
                            crossfade_style="fade"):
    fps = timeline.video.fps
    graph = []
    for index in range(len(timeline.scenes)):
        graph.append(
            f"[{index}:v:0]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[scene{index}]"
        )

    current = "scene0"
    elapsed_frames = seconds_to_frames(timeline.scenes[0].duration, fps)
    transitions_by_after = {item.after: item for item in timeline.transitions.between}
    for index, scene in enumerate(timeline.scenes[:-1]):
        next_index = index + 1
        transition = transitions_by_after.get(scene.id)
        label = f"join{next_index}"
        if transition is not None and transition.type == "crossfade":
            overlap_frames = seconds_to_frames(transition.duration, fps)
            offset_frames = elapsed_frames - overlap_frames
            graph.append(
                f"[{current}][scene{next_index}]xfade=transition={crossfade_style}:"
                f"duration={frames_to_seconds(overlap_frames, fps):.9g}:"
                f"offset={frames_to_seconds(offset_frames, fps):.9g}[{label}]"
            )
            elapsed_frames += seconds_to_frames(timeline.scenes[next_index].duration, fps) - overlap_frames
        else:
            graph.append(
                f"[{current}][scene{next_index}]concat=n=2:v=1:a=0,"
                f"settb=AVTB,setpts=PTS-STARTPTS[{label}]"
            )
            elapsed_frames += seconds_to_frames(timeline.scenes[next_index].duration, fps)
        current = label

    for index, draw_filter in enumerate(draw_filters):
        label = f"texted{index}"
        graph.append(f"[{current}]{draw_filter}[{label}]")
        current = label

    if timeline.transitions.start is not None:
        fade = timeline.transitions.start.duration
        label = "faded_in"
        graph.append(f"[{current}]fade=t=in:st=0:d={fade:.9g}[{label}]")
        current = label
    if timeline.transitions.end is not None:
        fade = timeline.transitions.end.duration
        fade_start = max(0, video_duration - fade)
        label = "faded_out"
        graph.append(f"[{current}]fade=t=out:st={fade_start:.9g}:d={fade:.9g}[{label}]")
        current = label
    if tail_duration > 0:
        graph.append(
            f"[{current}]tpad=stop_mode=add:stop_duration={tail_duration:.9g}:color=black,"
            f"format=yuv420p[vout]"
        )
    else:
        graph.append(f"[{current}]format=yuv420p[vout]")
    return graph


def render_timeline(timeline, output, *, progress_callback=None, cancel_event=None, work_root=None,
                    line_callback=None, crossfade_style="fade"):
    if crossfade_style not in CROSSFADE_STYLES:
        choices = ", ".join(sorted(CROSSFADE_STYLES))
        raise ValueError(f"Stile crossfade non supportato: {crossfade_style!r}. Scegli tra: {choices}.")
    output = Path(output).resolve()
    render_root = Path(work_root).resolve() if work_root is not None else Path(__file__).resolve().parents[2] / "temp" / "renders"
    render_root.mkdir(parents=True, exist_ok=True)
    width, height, fps = timeline.video.width, timeline.video.height, timeline.video.fps
    transition_graph = _has_video_transitions(timeline.transitions)
    video_frames = _video_duration_frames(timeline)
    video_duration = frames_to_seconds(video_frames, fps)
    background = timeline.background_audio
    background_duration = 0.0
    if background is not None:
        background_duration = (
            float(background.duration) if background.duration is not None
            else max(0.0, audio_duration(background.asset) - background.source_in)
        )
    output_frames = max(video_frames, math.ceil(background_duration * fps - 1e-9))
    output_duration = frames_to_seconds(output_frames, fps)
    tail_duration = max(0.0, output_duration - video_duration)
    total_scene_frames = sum(seconds_to_frames(scene.duration, fps) for scene in timeline.scenes)
    completed_scene_frames = 0

    def render_progress(phase, fraction):
        if progress_callback is not None:
            progress_callback(phase, min(0.98, max(0.0, fraction)))

    print("\nREEL ENGINE")
    print(f"Scene: {len(timeline.scenes)}")
    print(f"Formato: verticale {width}x{height} a {fps:g} fps")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="render_", dir=render_root) as work_directory:
        work_directory = Path(work_directory)
        segments = []
        for index, scene in enumerate(timeline.scenes, start=1):
            segment = work_directory / f"scene_{index:03d}.mp4"
            segments.append(segment)
            print(f"\nSCENA {index}/{len(timeline.scenes)}: {scene.asset.name} ({scene.duration:.2f}s)")
            frame_count = max(1, seconds_to_frames(scene.duration, fps))
            source_frame = seconds_to_frames(scene.source_in, fps)
            aligned_duration = frames_to_seconds(frame_count, fps)
            aligned_source_in = frames_to_seconds(source_frame, fps)
            video_filter = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                f"zoompan=z='min(zoom+0.001,1.12)':"
                f"x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':"
                f"d=1:s={width}x{height}:fps={fps:g},"
                "setsar=1,format=yuv420p"
            )
            command = ["ffmpeg", "-y"]
            if scene.media_type == "image":
                command.extend(["-loop", "1", "-framerate", str(fps)])
            else:
                command.extend(["-ss", str(aligned_source_in)])
            command.extend(["-i", str(scene.asset), "-vf", video_filter,
                            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-r", str(fps), "-frames:v", str(frame_count), "-t", str(aligned_duration),
                            str(segment)])
            scene_frames = seconds_to_frames(scene.duration, fps)
            scene_progress = None
            if progress_callback is not None:
                scene_progress = lambda fraction, done=completed_scene_frames, length=scene_frames: render_progress(
                    f"Rendering scena {index}/{len(timeline.scenes)}",
                    0.78 * (done + fraction * length) / max(total_scene_frames, 1),
                )
            if scene_progress is None and cancel_event is None and line_callback is None:
                run(command)
            else:
                run(command, progress_callback=scene_progress, cancel_event=cancel_event,
                    line_callback=line_callback, duration=aligned_duration)
            completed_scene_frames += scene_frames

        concat_file = work_directory / "concat.txt"
        concat_file.write_text(
            "".join(f"file '{path.name}'\n" for path in segments),
            encoding="utf-8",
        )
        print("\nASSEMBLAGGIO FINALE...")
        text_items = [(item, "text") for item in timeline.texts]
        text_items.extend((item, "subtitle") for item in timeline.subtitles)
        draw_filters = []
        for index, (item, prefix) in enumerate(text_items):
            text_file = f"{prefix}_{index:03d}.txt"
            font_file = f"font_{index:03d}{item.font.suffix.lower()}"
            (work_directory / text_file).write_text(item.text, encoding="utf-8")
            shutil.copy2(item.font, work_directory / font_file)
            draw_filters.append(_drawtext_filter(item, text_file, font_file))

        command = ["ffmpeg", "-y"]
        filter_graph = []
        if transition_graph:
            for segment in segments:
                command.extend(["-i", str(segment)])
            next_input_index = len(segments)
            filter_graph.extend(_build_transition_graph(
                timeline, draw_filters, video_duration, tail_duration, crossfade_style
            ))
        else:
            command.extend(["-f", "concat", "-safe", "0", "-i", str(concat_file)])
            next_input_index = 1

        voice_index = None
        if timeline.audio_asset is not None:
            voice_index = next_input_index
            command.extend(["-i", str(timeline.audio_asset)])
            next_input_index += 1
        background_index = None
        if background is not None:
            background_index = next_input_index
            command.extend(["-i", str(background.asset)])

        if voice_index is not None and background_index is not None:
            audio_filters = [
                f"[{voice_index}:a:0]atrim=duration={output_duration:.9g},asetpts=PTS-STARTPTS,"
                f"apad,atrim=duration={output_duration:.9g}[voice]"
            ]
            music_filter = (
                f"[{background_index}:a:0]atrim=start={background.source_in:.9g}:"
                f"duration={background_duration:.9g},asetpts=PTS-STARTPTS,"
                f"volume={background.volume:.9g}"
            )
            if background.fade_in > 0:
                music_filter += f",afade=t=in:st=0:d={background.fade_in:.9g}"
            if background.fade_out > 0:
                fade_start = max(0, background_duration - background.fade_out)
                music_filter += f",afade=t=out:st={fade_start:.9g}:d={background.fade_out:.9g}"
            music_filter += f",apad,atrim=duration={output_duration:.9g}[music]"
            audio_filters.append(music_filter)
            audio_filters.append(
                f"[voice][music]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
                f"atrim=duration={output_duration:.9g}[aout]"
            )
            filter_graph.extend(audio_filters)
            command.extend(["-map", "[aout]"])
        elif background_index is not None:
            music_filter = (
                f"[{background_index}:a:0]atrim=start={background.source_in:.9g}:"
                f"duration={background_duration:.9g},asetpts=PTS-STARTPTS,"
                f"volume={background.volume:.9g}"
            )
            if background.fade_in > 0:
                music_filter += f",afade=t=in:st=0:d={background.fade_in:.9g}"
            if background.fade_out > 0:
                fade_start = max(0, background_duration - background.fade_out)
                music_filter += f",afade=t=out:st={fade_start:.9g}:d={background.fade_out:.9g}"
            music_filter += f",apad,atrim=duration={output_duration:.9g}[aout]"
            filter_graph.append(music_filter)
            command.extend(["-map", "[aout]"])
        elif voice_index is not None and transition_graph:
            filter_graph.append(
                f"[{voice_index}:a:0]atrim=duration={output_duration:.9g},asetpts=PTS-STARTPTS,"
                f"apad,atrim=duration={output_duration:.9g}[aout]"
            )
            command.extend(["-map", "[aout]"])
        elif voice_index is not None:
            command.extend([
                "-map", f"{voice_index}:a:0", "-af",
                f"apad,atrim=duration={output_duration:.9g}",
            ])

        if filter_graph:
            command.extend(["-filter_complex", ";".join(filter_graph)])
        if transition_graph:
            command.extend(["-map", "[vout]"])
        else:
            command.extend(["-map", "0:v:0"])
            video_filters = list(draw_filters)
            if tail_duration > 0:
                video_filters.append(
                    f"tpad=stop_mode=add:stop_duration={tail_duration:.9g}:color=black"
                )
            if video_filters:
                command.extend(["-vf", ",".join(video_filters)])

        if voice_index is not None or background_index is not None:
            command.extend(["-c:a", "aac", "-b:a", "192k"])
        command.extend(["-t", f"{output_duration:.9g}"])
        if transition_graph or draw_filters or tail_duration > 0:
            command.extend([
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(fps),
            ])
        else:
            command.extend(["-c:v", "copy"])
        command.extend(["-movflags", "+faststart", str(output)])
        render_progress("Assemblaggio finale", 0.78)
        final_progress = None
        if progress_callback is not None:
            final_progress = lambda fraction: render_progress("Assemblaggio finale", 0.78 + fraction * 0.20)
        if final_progress is None and cancel_event is None and line_callback is None:
            run(command, cwd=work_directory)
        else:
            run(command, cwd=work_directory, progress_callback=final_progress, cancel_event=cancel_event,
                line_callback=line_callback, duration=output_duration)
    print(f"\nVIDEO COMPLETATO: {output}")
