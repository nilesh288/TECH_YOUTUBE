#!/usr/bin/env python3
"""Prepare individually retimed, video-only clips for VN Editor.

Reads target durations from src/remotion_manifest.json and source clips named
Shot_001.mp4, Shot_002.mp4, ... . Original source files are never modified.
No narration, background music, or clip concatenation is performed.
"""

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def parse_args():
    parser = argparse.ArgumentParser(
        description="Retiming individual Google Flow clips for VN Editor."
    )
    parser.add_argument(
        "--manifest",
        default=str(PROJECT_DIR / "src" / "remotion_manifest.json"),
        help="Scene manifest containing durationSec (default: src/remotion_manifest.json).",
    )
    parser.add_argument(
        "--video-dir",
        default=str(PROJECT_DIR.parent / "flow-automation" / "videos"),
        help="Folder containing Shot_001.mp4, Shot_002.mp4, etc.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_DIR / "out" / "vn_clips"),
        help="Final output folder on the SD card for VN-ready clips.",
    )
    parser.add_argument(
        "--temp-dir",
        default="/dev/shm/tech_youtube_vn_clips",
        help="Temporary processing folder (default: /dev/shm/tech_youtube_vn_clips). "
             "Encoded clips are validated in RAM, then copied to --output-dir.",
    )
    parser.add_argument("--fps", type=positive_int, default=30)
    parser.add_argument("--width", type=positive_int, default=1920)
    parser.add_argument("--height", type=positive_int, default=1080)
    parser.add_argument("--crf", type=int, default=18)
    parser.add_argument("--preset", default="veryfast")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace output clips that already exist. Source clips are never changed.",
    )
    return parser.parse_args()


def run_probe(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise ValueError(f"Could not read source duration: {path}") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Invalid source duration {duration!r}: {path}")
    return duration


def main():
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    video_dir = Path(args.video_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    temp_dir = Path(args.temp_dir).expanduser().resolve()

    for command in ("ffmpeg", "ffprobe"):
        if shutil.which(command) is None:
            raise RuntimeError(f"Required command '{command}' is not installed or not on PATH.")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    if not video_dir.is_dir():
        raise NotADirectoryError(f"Source video directory not found: {video_dir}")

    with manifest_path.open("r", encoding="utf-8") as handle:
        scenes = json.load(handle)
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("Manifest must be a non-empty JSON list.")

    # Validate every scene and source before starting any encoding.
    jobs = []
    seen_ids = set()
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            raise ValueError(f"Manifest item {index} is not an object.")
        scene_id = scene.get("scene_id")
        target = scene.get("durationSec")
        if isinstance(scene_id, bool) or not isinstance(scene_id, int) or scene_id < 1:
            raise ValueError(f"Invalid scene_id in manifest item {index}: {scene_id!r}")
        if scene_id in seen_ids:
            raise ValueError(f"Duplicate scene_id: {scene_id}")
        seen_ids.add(scene_id)
        if isinstance(target, bool) or not isinstance(target, (int, float)):
            raise ValueError(f"Scene {scene_id} has invalid durationSec: {target!r}")
        if not math.isfinite(target) or target <= 0:
            raise ValueError(f"Scene {scene_id} has invalid durationSec: {target!r}")

        source = video_dir / f"Shot_{scene_id:03d}.mp4"
        destination = output_dir / f"Scene_{scene_id:03d}.mp4"
        if not source.is_file():
            raise FileNotFoundError(f"Missing source clip for scene {scene_id}: {source}")
        jobs.append((scene_id, source, destination, float(target)))

    existing = [str(dst) for _, _, dst, _ in jobs if dst.exists()]
    if existing and not args.overwrite:
        sample = "\\n".join(existing[:5])
        extra = f"\\n... and {len(existing) - 5} more" if len(existing) > 5 else ""
        raise FileExistsError(
            "Output clip(s) already exist; nothing was processed. "
            "Choose a new --output-dir or pass --overwrite to replace them:\\n"
            f"{sample}{extra}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    print(f"Manifest: {manifest_path}")
    print(f"Source clips: {video_dir}")
    print(f"RAM processing folder: {temp_dir}")
    print(f"Final VN-ready clips on SD card: {output_dir}")
    print(f"Scenes: {len(jobs)} | output: {args.width}x{args.height} at {args.fps} fps")
    print("Workflow: encode and validate in RAM, then copy each clip to the SD card.")
    print("Audio handling: disabled (video-only). No concatenation.\n")

    total_frames = 0
    for position, (scene_id, source, destination, target_sec) in enumerate(jobs, start=1):
        source_sec = run_probe(source)
        target_frames = max(1, int(math.floor(target_sec * args.fps + 0.5)))
        exact_target_sec = target_frames / args.fps
        pts_factor = exact_target_sec / source_sec
        # FFmpeg writes to RAM. Only a validated clip is copied to the SD card.
        ram_output = temp_dir / f".Scene_{scene_id:03d}.tmp.mp4"
        sd_temp_output = destination.with_name(f".{destination.stem}.copy.tmp.mp4")

        vf = (
            f"setpts={pts_factor:.12f}*(PTS-STARTPTS),"
            f"fps={args.fps},"
            # Retiming plus frame-rate conversion can leave the final frame
            # just short of the target boundary. Clone the final frame if
            # needed, then enforce the exact frame count and constant PTS.
            "tpad=stop_mode=clone:stop_duration=1,"
            f"trim=end_frame={target_frames},"
            f"setpts=N/({args.fps}*TB),"
            f"scale={args.width}:{args.height}:force_original_aspect_ratio=increase,"
            f"crop={args.width}:{args.height},"
            "setsar=1"
        )
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-map", "0:v:0",
            "-an",
            "-vf", vf,
            "-frames:v", str(target_frames),
            "-c:v", "libx264",
            "-preset", args.preset,
            "-crf", str(args.crf),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(ram_output),
        ]

        try:
            if ram_output.exists():
                ram_output.unlink()
            if sd_temp_output.exists():
                sd_temp_output.unlink()

            subprocess.run(command, check=True)
            # Confirm the RAM-encoded output duration before copying it to SD.
            encoded_sec = run_probe(ram_output)
            tolerance = 1.01 / args.fps
            if abs(encoded_sec - exact_target_sec) > tolerance:
                raise RuntimeError(
                    f"Scene {scene_id} output duration is {encoded_sec:.3f}s; "
                    f"expected about {exact_target_sec:.3f}s."
                )

            # Copy only a successfully validated clip to the SD card.
            shutil.copy2(ram_output, sd_temp_output)
            sd_temp_output.replace(destination)
        except Exception:
            for temporary_path in (ram_output, sd_temp_output):
                if temporary_path.exists():
                    temporary_path.unlink()
            raise
        finally:
            # Process one scene at a time to keep RAM usage bounded.
            if ram_output.exists():
                ram_output.unlink()

        total_frames += target_frames
        speed = source_sec / exact_target_sec
        print(
            f"[{position:02d}/{len(jobs):02d}] Scene {scene_id:03d}: "
            f"{source_sec:.3f}s -> {exact_target_sec:.3f}s "
            f"(speed {speed:.3f}x) -> {destination.name}"
        )

    print("\\nSUCCESS")
    print(f"Prepared {len(jobs)} individual clips on the SD card: {output_dir}")
    print(f"RAM temporary files were removed from: {temp_dir}")
    print(f"Approximate combined timeline: {total_frames / args.fps:.3f}s")
    print("Import the Scene_001.mp4, Scene_002.mp4, ... clips into VN in numerical order.")
    print("Add voice-over/music and make final edits in VN Editor.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError,
            json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
