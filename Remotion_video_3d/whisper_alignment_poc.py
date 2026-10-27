import sys
import re
import glob
import os
import json
import difflib
from faster_whisper import WhisperModel


def clean_word(word):
    return re.sub(r"[^a-z0-9]", "", word.lower())


def parse_scenes_text(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read().lstrip("\ufeff").strip()

    scenes = []

    pattern = re.compile(
        r"^(?:Scene\s*)?(\d+)[\.\:\-]?\s+(.*?)"
        r"(?=^(?:Scene\s*)?\d+[\.\:\-]?\s+|\Z)",
        re.DOTALL | re.IGNORECASE | re.MULTILINE,
    )

    matches = pattern.findall(text)

    if matches:
        for match in matches:
            scenes.append({
                "scene_id": int(match[0]),
                "text": match[1].strip(),
            })
    else:
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
        ]

        for index, line in enumerate(lines, start=1):
            scenes.append({
                "scene_id": index,
                "text": line,
            })

    return scenes


def find_input_files(folder):
    if not os.path.isdir(folder):
        raise FileNotFoundError(
            f"Input folder does not exist: {folder}"
        )

    audio_files = glob.glob(os.path.join(folder, "*.mp3"))
    script_path = os.path.join(folder, "scenes.txt")

    if not audio_files:
        raise FileNotFoundError(
            f"No MP3 file found in: {folder}"
        )

    if len(audio_files) != 1:
        raise ValueError(
            f"Expected exactly one MP3 file, found "
            f"{len(audio_files)}. Keep only the intended MP3 "
            f"in the input folder."
        )

    if not os.path.isfile(script_path):
        raise FileNotFoundError(
            f"scenes.txt not found in: {folder}"
        )

    return audio_files[0], script_path


def align_scenes(audio_path, script_path):
    scenes = parse_scenes_text(script_path)

    if not scenes:
        raise ValueError(
            "Could not parse any scenes from scenes.txt"
        )

    print(f"Audio file: {os.path.basename(audio_path)}")
    print(f"Scene file: {os.path.basename(script_path)}")
    print(f"Scene count: {len(scenes)}")
    print("=" * 42)
    print("Loading Faster-Whisper (base) on CPU...")
    print("=" * 42)

    model = WhisperModel(
        "base",
        device="cpu",
        compute_type="int8",
    )

    print(f"\nTranscribing: {audio_path}")
    print("Requesting word-level timestamps...\n")

    segments, info = model.transcribe(
        audio_path,
        beam_size=5,
        word_timestamps=True,
        language="en",
    )

    whisper_words = []

    for segment in segments:
        for word in segment.words:
            cleaned = clean_word(word.word)

            if cleaned:
                whisper_words.append({
                    "clean": cleaned,
                    "start": word.start,
                    "end": word.end,
                })

    print(f"Detected language: {info.language}")
    print("\nMapping transcript globally to script scenes...")

    # Flatten the script while preserving each word's scene ID.
    script_words = []
    script_word_to_scene = []

    for scene in scenes:
        words = [
            clean_word(word)
            for word in scene["text"].split()
            if clean_word(word)
        ]

        for word in words:
            script_words.append(word)
            script_word_to_scene.append(scene["scene_id"])

    transcript_words = [
        word["clean"]
        for word in whisper_words
    ]

    # Match the transcript against the complete script.
    assignments = [-1] * len(transcript_words)
    matched_script_indices = set()

    matcher = difflib.SequenceMatcher(
        None,
        transcript_words,
        script_words,
    )

    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            transcript_index = block.a + offset
            script_index = block.b + offset

            assignments[transcript_index] = (
                script_word_to_scene[script_index]
            )

            matched_script_indices.add(script_index)

    # Assign unmatched spoken words to the nearest matched scene.
    for i in range(len(assignments)):
        if assignments[i] != -1:
            continue

        left_scene = -1
        right_scene = -1
        left_distance = float("inf")
        right_distance = float("inf")

        for j in range(i - 1, -1, -1):
            if assignments[j] != -1:
                left_scene = assignments[j]
                left_distance = i - j
                break

        for j in range(i + 1, len(assignments)):
            if assignments[j] != -1:
                right_scene = assignments[j]
                right_distance = j - i
                break

        if left_scene != -1 and right_scene != -1:
            assignments[i] = (
                left_scene
                if left_distance <= right_distance
                else right_scene
            )
        elif left_scene != -1:
            assignments[i] = left_scene
        elif right_scene != -1:
            assignments[i] = right_scene
        else:
            assignments[i] = scenes[0]["scene_id"]

    # Calculate scene time ranges from assigned spoken words.
    scene_times = {}

    for index, word in enumerate(whisper_words):
        scene_id = assignments[index]

        if scene_id not in scene_times:
            scene_times[scene_id] = {
                "start": word["start"],
                "end": word["end"],
            }
        else:
            scene_times[scene_id]["end"] = word["end"]

    # Use fallback timing for scenes without matched words.
    for index, scene in enumerate(scenes):
        scene_id = scene["scene_id"]

        if scene_id in scene_times:
            scene["startSec"] = scene_times[scene_id]["start"]
            scene["endSec"] = scene_times[scene_id]["end"]
        else:
            scene["startSec"] = (
                scenes[index - 1]["endSec"]
                if index > 0
                else 0.0
            )

            scene["endSec"] = scene["startSec"] + 2.0

    # Adjust adjacent boundaries to the midpoint between scenes.
    for index in range(len(scenes) - 1):
        midpoint = (
            scenes[index]["endSec"]
            + scenes[index + 1]["startSec"]
        ) / 2.0

        scenes[index]["endSec"] = round(midpoint, 2)
        scenes[index + 1]["startSec"] = round(midpoint, 2)

    total_script_words = len(script_words)
    total_matched = len(matched_script_indices)

    # Build the scene-wise JSON output.
    output_scenes = []

    for scene in scenes:
        scene_id = scene["scene_id"]

        scene_script_indices = [
            i
            for i, sid in enumerate(script_word_to_scene)
            if sid == scene_id
        ]

        matched_count = sum(
            1
            for i in scene_script_indices
            if i in matched_script_indices
        )

        scene_word_count = len(scene_script_indices)

        coverage = (
            matched_count / scene_word_count * 100
            if scene_word_count
            else 0.0
        )

        start_sec = round(float(scene["startSec"]), 2)
        end_sec = round(float(scene["endSec"]), 2)
        duration = round(
            max(0.0, end_sec - start_sec),
            2,
        )

        output_scenes.append({
            "scene_id": scene_id,
            "text": scene["text"],
            "startSec": start_sec,
            "endSec": end_sec,
            "duration_seconds": duration,
            "matched_script_words": matched_count,
            "total_script_words": scene_word_count,
            "match_coverage_percent": round(coverage, 1),
        })

    overall_coverage = (
        total_matched / total_script_words * 100
        if total_script_words
        else 0.0
    )

    # Construct the complete JSON document.
    output_data = {
        "project": os.path.splitext(
            os.path.basename(audio_path)
        )[0],
        "audio_file": os.path.basename(audio_path),
        "source_script": os.path.basename(script_path),
        "detected_language": info.language,
        "total_scenes": len(output_scenes),
        "alignment_summary": {
            "matched_script_words": total_matched,
            "total_script_words": total_script_words,
            "match_coverage_percent": round(
                overall_coverage,
                1,
            ),
        },
        "scenes": output_scenes,
    }

    # Save beside scenes.txt and the MP3.
    output_path = os.path.join(
        os.path.dirname(os.path.abspath(script_path)),
        "scene_alignment.json",
    )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            output_data,
            f,
            indent=2,
            ensure_ascii=False,
        )
        f.write("\n")

    # Print scene timing results.
    print("\n" + "=" * 42)
    print("SCENE TIMESTAMP OUTPUT")
    print("=" * 42)

    for scene in output_scenes:
        print(
            f"\nScene {scene['scene_id']} "
            f"[{scene['startSec']:.2f}s -> "
            f"{scene['endSec']:.2f}s] "
            f"(duration: "
            f"{scene['duration_seconds']:.2f}s)"
        )

        print(scene["text"])

        print(
            f"Matched script words: "
            f"{scene['matched_script_words']}/"
            f"{scene['total_script_words']} "
            f"({scene['match_coverage_percent']:.1f}%)"
        )

    print("\n" + "=" * 42)
    print("ALIGNMENT SUMMARY")
    print("=" * 42)
    print(f"Scenes processed: {len(output_scenes)}")
    print(
        f"Script words matched: "
        f"{total_matched}/{total_script_words} "
        f"({overall_coverage:.1f}%)"
    )
    print(f"JSON saved to: {output_path}")
    print("No MP4 files required.")


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: python3 whisper_alignment_poc.py "
            "<input_folder>"
        )
        sys.exit(1)

    try:
        audio_path, script_path = find_input_files(
            sys.argv[1]
        )

        align_scenes(audio_path, script_path)

    except (FileNotFoundError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
