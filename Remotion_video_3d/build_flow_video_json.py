
import argparse
import json
import re
import sys
from pathlib import Path


PROMPT_FIELDS = [
    "character_action",
    "camera_motion",
    "environment_dynamics",
    "technical_settings",
]


def load_json(path):
    """Load JSON data from a file."""
    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def get_scene_number(scene, index):
    """Get the scene number from common alignment/prompt formats."""
    number = scene.get("scene_number")

    if number is not None:
        match = re.search(r"\d+", str(number))
        if match:
            return int(match.group())

    scene_id = scene.get("scene_id")

    if scene_id is not None:
        match = re.search(r"\d+", str(scene_id))
        if match:
            return int(match.group())

    return index + 1


def get_alignment_duration(scene):
    """Read the scene duration or calculate it from start/end timestamps."""
    duration = scene.get("duration_seconds")

    if duration is not None:
        duration = float(duration)
    else:
        start = scene.get("startSec", scene.get("start_sec"))
        end = scene.get("endSec", scene.get("end_sec"))

        if start is None or end is None:
            raise ValueError(
                f"Scene {scene.get('scene_id', '?')} has no valid "
                "duration_seconds or start/end timestamps."
            )

        duration = float(end) - float(start)

    if duration <= 0:
        raise ValueError(
            f"Invalid scene duration: {duration}"
        )

    return duration


def choose_flow_duration(alignment_duration):
    """
    Map Whisper narration duration to a Google Flow duration.

    Up to 5 seconds       -> 4 seconds
    Above 5 up to 7 sec  -> 6 seconds
    Above 7 up to 10 sec -> 8 seconds
    Above 10 seconds     -> 10 seconds
    """
    if alignment_duration <= 5:
        return 4
    elif alignment_duration <= 7:
        return 6
    elif alignment_duration <= 10:
        return 8
    else:
        return 10


def build_output(alignment_data, prompts):
    """Combine alignment information with video prompt fields."""

    # Support either a JSON object containing "scenes"
    # or a direct list of alignment scenes.
    if isinstance(alignment_data, list):
        alignment_scenes = alignment_data
        project = "Video_Project"

    elif isinstance(alignment_data, dict):
        alignment_scenes = alignment_data.get("scenes")

        project = alignment_data.get("project")

        if not project:
            audio_file = alignment_data.get(
                "audio_file", "Video_Project"
            )
            project = Path(audio_file).stem

    else:
        raise ValueError(
            "Alignment JSON must be an object or a list of scenes."
        )

    if not isinstance(alignment_scenes, list):
        raise ValueError(
            "Alignment JSON must contain a 'scenes' array."
        )

    if not isinstance(prompts, list):
        raise ValueError(
            "Video prompts JSON must contain a JSON array."
        )

    if len(alignment_scenes) != len(prompts):
        raise ValueError(
            f"Scene count mismatch: alignment has "
            f"{len(alignment_scenes)} scenes, but prompts have "
            f"{len(prompts)} scenes."
        )

    # Index alignment scenes by scene number.
    alignment_by_number = {}

    for index, scene in enumerate(alignment_scenes):
        if not isinstance(scene, dict):
            raise ValueError(
                f"Alignment scene {index + 1} must be a JSON object."
            )

        number = get_scene_number(scene, index)

        if number in alignment_by_number:
            raise ValueError(
                f"Duplicate alignment scene number: {number}"
            )

        alignment_by_number[number] = scene

    output_scenes = []
    seen_numbers = set()

    for index, prompt in enumerate(prompts):
        if not isinstance(prompt, dict):
            raise ValueError(
                f"Video prompt scene {index + 1} must be a JSON object."
            )

        number = get_scene_number(prompt, index)

        if number in seen_numbers:
            raise ValueError(
                f"Duplicate video prompt scene number: {number}"
            )

        seen_numbers.add(number)

        if number not in alignment_by_number:
            raise ValueError(
                f"No alignment data found for prompt scene {number}."
            )

        alignment_scene = alignment_by_number[number]

        original_duration = get_alignment_duration(alignment_scene)
        flow_duration = choose_flow_duration(original_duration)

        # Keep the four AI-generated prompt fields nested.
        video_prompt = {}

        for field in PROMPT_FIELDS:
            value = prompt.get(field)

            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Scene {number}: missing or empty '{field}'."
                )

            video_prompt[field] = value.strip()

        output_scenes.append({
            "scene_number": number,
            "duration_seconds": flow_duration,
            "video_prompt": video_prompt,
        })

        print(
            f"Scene {number}: narration={original_duration:.2f}s "
            f"-> Google Flow={flow_duration}s"
        )

    return {
        "project": project,
        "total_scenes": len(output_scenes),
        "scenes": output_scenes,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Combine Whisper alignment and video prompts into "
            "Google Flow input JSON."
        )
    )

    parser.add_argument(
        "alignment_json",
        help="Path to scene_alignment.json",
    )

    parser.add_argument(
        "video_prompts_json",
        help="Path to the video prompts JSON array",
    )

    parser.add_argument(
        "-o",
        "--output",
        help=(
            "Output JSON path. Defaults to flow_video_input.json "
            "beside the video prompts file."
        ),
    )

    args = parser.parse_args()

    alignment_path = Path(args.alignment_json)
    prompts_path = Path(args.video_prompts_json)

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = (
            prompts_path.resolve().parent / "flow_video_input.json"
        )

    try:
        alignment_data = load_json(alignment_path)
        prompts = load_json(prompts_path)

        result = build_output(alignment_data, prompts)

        # Create the output directory if it does not exist.
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with output_path.open("w", encoding="utf-8") as file:
            json.dump(
                result,
                file,
                indent=2,
                ensure_ascii=False,
            )
            file.write("\n")

        print(
            f"\nSuccessfully generated:\n"
            f"{output_path.resolve()}"
        )

    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ) as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
