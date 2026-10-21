#!/usr/bin/env python3

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a Remotion manifest from scene alignment JSON and MP4 files."
    )
    parser.add_argument(
        "--alignment",
        default="public/scene_alignment.json",
        help="Path to the existing scene alignment JSON.",
    )
    parser.add_argument(
        "--video-dir",
        default="../flow-automation/videos",
        help="Directory containing Shot_001.mp4, Shot_002.mp4, etc.",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_DIR / "src" / "remotion_manifest.json"),
        help="Output manifest path.",
    )
    parser.add_argument(
        "--video-pattern",
        default="Shot_{scene_id:03d}.mp4",
        help="Input video filename pattern.",
    )
    parser.add_argument(
        "--no-copy-videos",
        action="store_true",
        help="Do not stage clips as public/Scene01.mp4, Scene02.mp4, etc. "
             "(Remotion needs them there, so only use this if already staged.)",
    )
    return parser.parse_args()


def get_video_duration(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise ValueError(f"Could not read media duration: {path}") from exc

    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Invalid media duration for {path}: {duration}")

    return duration


def main():
    args = parse_args()

    alignment_path = Path(args.alignment).expanduser().resolve()
    video_dir = Path(args.video_dir).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    public_dir = PROJECT_DIR / "public"

    if not alignment_path.is_file():
        raise FileNotFoundError(f"Alignment JSON not found: {alignment_path}")

    if not video_dir.is_dir():
        raise NotADirectoryError(f"Video directory not found: {video_dir}")

    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe is not installed or is not on PATH.")

    with alignment_path.open("r", encoding="utf-8") as file:
        alignment = json.load(file)

    scenes = alignment.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("Alignment JSON must contain a non-empty 'scenes' list.")

    expected_count = alignment.get("total_scenes")
    if expected_count is not None and expected_count != len(scenes):
        raise ValueError(
            f"total_scenes is {expected_count}, but found {len(scenes)} scenes."
        )

    manifest = []
    source_videos = []
    seen_ids = set()

    print(f"Alignment: {alignment_path}")
    print(f"Video directory: {video_dir}")
    print(f"Scenes to process: {len(scenes)}")
    print()

    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            raise ValueError(f"Scene {index} is not a JSON object.")

        scene_id = scene.get("scene_id")
        if isinstance(scene_id, bool) or not isinstance(scene_id, int):
            raise ValueError(
                f"Scene {index} must have an integer scene_id; got {scene_id!r}."
            )

        if scene_id != index:
            raise ValueError(
                f"Expected scene_id {index}, but found {scene_id}. "
                "Scenes must be numbered consecutively from 1."
            )

        if scene_id in seen_ids:
            raise ValueError(f"Duplicate scene_id: {scene_id}")
        seen_ids.add(scene_id)

        start = scene.get("startSec")
        end = scene.get("endSec")
        text = scene.get("text")

        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or isinstance(end, bool)
            or not isinstance(end, (int, float))
            or not math.isfinite(start)
            or not math.isfinite(end)
            or end <= start
        ):
            raise ValueError(
                f"Scene {scene_id} has invalid startSec/endSec: {start}, {end}"
            )

        if not isinstance(text, str):
            raise ValueError(f"Scene {scene_id} is missing its text string.")

        # Same rounding as the original script (2 decimals) so output matches.
        duration = round(end - start, 2)

        video_path = (
            video_dir / args.video_pattern.format(scene_id=scene_id)
        ).resolve()

        if not video_path.is_file():
            raise FileNotFoundError(
                f"Missing video for scene {scene_id}: {video_path}"
            )

        video_duration = get_video_duration(video_path)

        # Emit exactly the same keys as the original manifest.
        manifest.append(
            {
                "scene_id": scene_id,
                "text": text,
                "startSec": start,
                "endSec": end,
                "durationSec": duration,
                "videoDurationSec": video_duration,
            }
        )
        source_videos.append((scene_id, video_path))

        print(
            f"Scene {scene_id:02d}/{len(scenes):02d}: "
            f"timeline={duration:.2f}s, video={video_duration:.3f}s"
        )

    # Only touch public/ after all inputs have been validated.
    public_dir.mkdir(parents=True, exist_ok=True)

    if not args.no_copy_videos:
        for scene_id, source in source_videos:
            destination = public_dir / f"Scene{scene_id:02d}.mp4"
            if source.resolve() == destination.resolve():
                continue
            shutil.copy2(source, destination)
        print(f"Copied {len(source_videos)} clips -> public/Scene01.mp4 ... "
              f"Scene{len(source_videos):02d}.mp4")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write atomically so an interrupted write doesn't leave a partial manifest.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            json.dump(manifest, file, indent=4, ensure_ascii=False)
            file.write("\n")

        temporary_path.chmod(0o644)
        temporary_path.replace(output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    print()
    print("SUCCESS")
    print(f"Scenes in manifest: {len(manifest)}")
    print(f"Manifest written to: {output_path}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError,
            json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
