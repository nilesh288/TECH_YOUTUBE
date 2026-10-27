
from pathlib import Path
import json
import random
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
IMAGE_PROMPTS = BASE / "image_prompt.txt"
VIDEO_PROMPTS = BASE / "final_video_prompt.json"
ALIGNMENT_FILE = BASE / "scene_alignment.json"
VIDEO_PROMPTS_SOURCE = BASE / "video_prompts.json"
IMAGE_DIR = BASE / "downloads"
VIDEO_DIR = BASE / (
    "duration_poc_videos" if "--duration-poc" in sys.argv else "videos"
)

IMAGE_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

IMAGE_TIMEOUT = 180
VIDEO_TIMEOUT = 360

PROMPT_FIELDS = (
    ("character_action", "Character Action"),
    ("camera_motion", "Camera Motion"),
    ("environment_dynamics", "Environment Dynamics"),
    ("technical_settings", "Technical Settings"),
)


def read_prompts(path):
    if not path.exists():
        raise FileNotFoundError(f"Missing prompt file: {path}")

    text = path.read_text(encoding="utf-8")
    headings = list(re.finditer(
        r"(?im)^[ \t]*Shot[ \t]+(\d+)[ \t]*:[ \t]*",
        text
    ))

    if not headings:
        raise ValueError(f"No 'Shot N:' headings in {path.name}")

    prompts = []
    seen = set()

    for i, heading in enumerate(headings):
        number = int(heading.group(1))

        if number in seen:
            raise ValueError(f"Duplicate Shot {number} in {path.name}")
        seen.add(number)

        end = (
            headings[i + 1].start()
            if i + 1 < len(headings)
            else len(text)
        )
        prompt = text[heading.end():end].strip()

        if not prompt:
            raise ValueError(f"Shot {number} has an empty prompt")

        prompts.append({"number": number, "prompt": prompt})

    prompts.sort(key=lambda item: item["number"])
    numbers = [p["number"] for p in prompts]

    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(
            f"Shot numbers must be sequential from 1. Found: {numbers}"
        )

    return prompts


def choose_flow_duration(alignment_duration):
    """Map narration duration to a supported, credit-conscious Flow duration."""
    if alignment_duration <= 5:
        return 4
    if alignment_duration <= 7:
        return 6
    if alignment_duration <= 10:
        return 8
    return 10


def load_json_file(path):
    if not path.exists():
        raise FileNotFoundError(f"Missing input file: {path}")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {path.name}: {exc}"
        ) from exc


def get_scene_list(data, label):
    scenes = data.get("scenes") if isinstance(data, dict) else data

    if not isinstance(scenes, list) or not scenes:
        raise ValueError(
            f"{label} must contain a non-empty scenes list."
        )

    return scenes


def get_scene_number(scene, fallback):
    if not isinstance(scene, dict):
        raise ValueError(f"Scene {fallback} must be an object.")

    for key in ("scene_number", "sceneNumber", "number"):
        value = scene.get(key)
        if type(value) is int and value > 0:
            return value

    scene_id = scene.get("scene_id")
    if isinstance(scene_id, str):
        match = re.search(r"(\d+)$", scene_id)
        if match:
            return int(match.group(1))

    return fallback


def get_alignment_duration(scene, number):
    duration = scene.get(
        "duration_seconds",
        scene.get("duration")
    )

    if (
        isinstance(duration, (int, float))
        and not isinstance(duration, bool)
    ):
        duration = float(duration)
    else:
        start = scene.get(
            "startSec",
            scene.get("start_seconds", scene.get("start"))
        )
        end = scene.get(
            "endSec",
            scene.get("end_seconds", scene.get("end"))
        )

        if (
            not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
        ):
            raise ValueError(
                f"Alignment scene {number} needs duration_seconds "
                "or start/end times."
            )

        duration = float(end) - float(start)

    if duration <= 0:
        raise ValueError(
            f"Alignment scene {number} has invalid duration: {duration}"
        )

    return duration


def build_video_prompt_json():
    """Combine Whisper scene timing with generated video prompt fields."""
    alignment_data = load_json_file(ALIGNMENT_FILE)
    prompt_data = load_json_file(VIDEO_PROMPTS_SOURCE)

    alignment_scenes = get_scene_list(
        alignment_data, ALIGNMENT_FILE.name
    )
    source_scenes = get_scene_list(
        prompt_data, VIDEO_PROMPTS_SOURCE.name
    )

    alignment_by_number = {}

    for index, scene in enumerate(alignment_scenes, 1):
        number = get_scene_number(scene, index)

        if number in alignment_by_number:
            raise ValueError(
                f"Duplicate scene {number} in {ALIGNMENT_FILE.name}"
            )

        alignment_by_number[number] = scene

    prompts_by_number = {}

    for index, scene in enumerate(source_scenes, 1):
        number = get_scene_number(scene, index)

        if number in prompts_by_number:
            raise ValueError(
                f"Duplicate scene {number} in {VIDEO_PROMPTS_SOURCE.name}"
            )

        prompts_by_number[number] = scene

    alignment_numbers = set(alignment_by_number)
    prompt_numbers = set(prompts_by_number)

    if alignment_numbers != prompt_numbers:
        missing_prompts = sorted(alignment_numbers - prompt_numbers)
        extra_prompts = sorted(prompt_numbers - alignment_numbers)

        raise ValueError(
            "Scene numbers do not match. "
            f"Missing prompts for scenes: {missing_prompts}; "
            f"prompts without alignment: {extra_prompts}."
        )

    final_scenes = []

    for number in sorted(alignment_numbers):
        alignment = alignment_by_number[number]
        source = prompts_by_number[number]

        whisper_duration = get_alignment_duration(alignment, number)
        flow_duration = choose_flow_duration(whisper_duration)

        nested = source.get("video_prompt")
        fields_source = nested if isinstance(nested, dict) else source

        video_prompt = {}

        for key, _label in PROMPT_FIELDS:
            value = fields_source.get(key)

            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Scene {number}: missing or empty prompt field "
                    f"{key!r} in {VIDEO_PROMPTS_SOURCE.name}."
                )

            video_prompt[key] = value.strip()

        final_scenes.append({
            "scene_number": number,
            "duration_seconds": flow_duration,
            "video_prompt": video_prompt,
        })

    project = (
        alignment_data.get("project", "voice_over")
        if isinstance(alignment_data, dict)
        else "voice_over"
    )

    output = {
        "project": project,
        "total_scenes": len(final_scenes),
        "scenes": final_scenes,
    }

    VIDEO_PROMPTS.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"\nGenerated: {VIDEO_PROMPTS.name}")
    print(f"Project: {output['project']}")
    print(f"Total scenes: {output['total_scenes']}")

    for scene in final_scenes:
        number = scene["scene_number"]
        whisper_duration = get_alignment_duration(
            alignment_by_number[number], number
        )
        flow_duration = scene["duration_seconds"]

        print(
            f"\nScene {number}: Whisper {whisper_duration:.2f}s "
            f"-> Flow {flow_duration}s"
        )

        for key, label in PROMPT_FIELDS:
            print(f"  {label}: {scene['video_prompt'][key]}")

    return output


def read_video_scenes(path):
    data = load_json_file(path)
    scenes = data.get("scenes") if isinstance(data, dict) else None

    if not isinstance(scenes, list) or not scenes:
        raise ValueError("JSON must contain a non-empty scenes list.")

    if data.get("total_scenes") != len(scenes):
        raise ValueError("total_scenes does not match scenes length.")

    prompts = []
    seen = set()

    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            raise ValueError(f"Scene {index} must be an object.")

        number = get_scene_number(scene, index)

        if number in seen:
            raise ValueError(f"Duplicate scene number {number}.")

        seen.add(number)

        duration = scene.get("duration_seconds")

        if type(duration) is not int or duration not in {4, 6, 8, 10}:
            raise ValueError(
                f"Scene {number}: duration must be 4, 6, 8, or 10."
            )

        prompt = scene.get("video_prompt")

        if isinstance(prompt, dict):
            parts = []

            for key, label in PROMPT_FIELDS:
                value = prompt.get(key)

                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"Scene {number}: video_prompt field "
                        f"{key!r} is empty."
                    )

                parts.append(f"{label}: {value.strip()}")

            prompt_text = "\n".join(parts)

        elif isinstance(prompt, str):
            # Backward compatibility with the previous flat-string schema.
            prompt_text = prompt.strip()

        else:
            prompt_text = ""

        if not prompt_text:
            raise ValueError(f"Scene {number}: video_prompt is empty.")

        prompts.append({
            "number": number,
            "prompt": prompt_text,
            "duration_seconds": duration,
        })

    prompts.sort(key=lambda item: item["number"])
    return prompts


def connect_flow():
    pw = sync_playwright().start()

    try:
        browser = pw.chromium.connect_over_cdp(
            "http://127.0.0.1:9222"
        )
        context = browser.contexts[0]

        page = next(
            (p for p in context.pages if "flow.google.com" in p.url),
            None
        )

        if page is None:
            raise RuntimeError(
                "Open Google Flow in your remote-debugging Chromium window."
            )

        return pw, page

    except Exception:
        pw.stop()
        raise


def tile_signature(element):
    # Check for a child before evaluate(), which can wait for a missing element.
    for selector, expression in (
        ("video", "(e) => e.currentSrc || e.src"),
        ("img", "(e) => e.currentSrc || e.src"),
    ):
        try:
            children = element.locator(selector)

            if children.count() == 0:
                continue

            value = children.first.evaluate(expression, timeout=2000)

            if value:
                return value

        except Exception:
            pass

    return None


def signatures(page, selector, image=False):
    found = set()
    elements = page.locator(selector)

    for i in range(elements.count()):
        value = tile_signature(elements.nth(i))

        if value:
            found.add(value)

    return found


def wait_for_new(page, selector, old, timeout, image=False):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        elements = page.locator(selector)

        for i in range(elements.count()):
            element = elements.nth(i)
            value = tile_signature(element)

            if value and value not in old:
                return element

        time.sleep(2)

    matched = page.locator(selector).count()

    raise TimeoutError(
        f"Timed out waiting for a new result tile. "
        f"Selector matches: {matched}. Check Flow before retrying."
    )


def save_image(page, tile, number):
    tile.hover()
    page.wait_for_timeout(400)

    tile.get_by_role("button", name="More options").click()

    item = page.get_by_role("menuitem").filter(
        has_text=re.compile(r"^download\s*Download$", re.I)
    )

    if item.count() != 1:
        raise RuntimeError("Could not identify image Download menu.")

    item.click()

    original = page.get_by_role("menuitem").filter(
        has_text=re.compile(r"1K.*Original size", re.I | re.S)
    )

    if original.count() != 1:
        raise RuntimeError("Could not identify 1K Original size.")

    output = IMAGE_DIR / f"Shot_{number:03d}.jpg"

    with page.expect_download(timeout=60000) as event:
        original.click()

    event.value.save_as(str(output))

    with output.open("rb") as f:
        if f.read(3) != b"\xff\xd8\xff":
            output.unlink(missing_ok=True)
            raise RuntimeError("Download was not a valid JPEG.")

    return output


def attach_image(page, number):
    name = f"Shot_{number:03d}.jpg"

    toggle = page.locator(
        '[aria-label="Add ingredients to the prompt box"]'
    )

    print("ATTACH: locating ingredients button", flush=True)

    if toggle.count() != 1:
        raise RuntimeError("Could not find Add ingredients button.")

    if toggle.get_attribute("aria-expanded") != "true":
        print("ATTACH: opening ingredients panel", flush=True)
        toggle.click(timeout=10000)
        page.wait_for_timeout(400)

    print("ATTACH: finding asset search", flush=True)

    panel = page.locator("flow-add-menu-popover-content")
    search = panel.get_by_role("textbox", name="Search assets")

    print(f"ATTACH: searching for {name}", flush=True)
    search.fill(name, timeout=10000)
    page.wait_for_timeout(500)

    print("ATTACH: checking search result", flush=True)

    asset = panel.get_by_role(
        "option",
        name=re.compile(re.escape(name) + r".*Image", re.I)
    )

    if asset.count() != 1 or not asset.first.is_visible():
        raise RuntimeError(
            f"{name} not found in Flow assets. Upload it to this project."
        )

    print("ATTACH: selecting reference image", flush=True)
    asset.first.click(timeout=10000)
    page.wait_for_timeout(300)

    print("ATTACH: reference image selected", flush=True)

    if toggle.get_attribute("aria-expanded") == "true":
        toggle.click()
        page.wait_for_timeout(200)


def check_video_settings(page, duration=None):
    button = page.locator('button[aria-label="Settings trigger"]')

    if button.count() != 1:
        raise RuntimeError("Could not inspect Flow video settings.")

    current = (button.inner_text() or "").replace("\n", " ")
    expected = ("Video", "720p", "crop_16_9", "x1")

    if any(value not in current for value in expected):
        raise RuntimeError(
            f"Unexpected Flow video settings: {current!r}"
        )

    if duration is not None and f"{duration}s" not in current:
        raise RuntimeError(
            f"Expected {duration}s duration; Flow settings show {current!r}"
        )

    if duration is None and not any(
        f"{seconds}s" in current for seconds in (4, 6, 8, 10)
    ):
        raise RuntimeError(
            f"Could not identify a supported duration in {current!r}"
        )


def configure_video_duration(page, duration):
    if duration not in {4, 6, 8, 10}:
        raise ValueError(f"Unsupported duration: {duration}")

    settings = page.locator('button[aria-label="Settings trigger"]')

    if settings.count() != 1:
        raise RuntimeError("Could not find Flow settings button.")

    option = page.get_by_role(
        "radio", name=f"{duration}s", exact=True
    )

    if not option.is_visible():
        settings.click()
        page.wait_for_timeout(300)

    option = page.get_by_role(
        "radio", name=f"{duration}s", exact=True
    )

    if option.count() != 1:
        raise RuntimeError(
            f"Expected one radio option for {duration}s; "
            f"found {option.count()}."
        )

    option.click()
    page.wait_for_timeout(300)

    current = settings.inner_text()

    if f"{duration}s" not in current:
        raise RuntimeError(
            f"Selected {duration}s but Flow settings show {current!r}."
        )

    check_video_settings(page, duration)


def save_video(page, video, number):
    # The result is a FLOW-VIDEO-TILE, not a video element.
    video.scroll_into_view_if_needed()
    page.mouse.move(0, 0)
    page.wait_for_timeout(300)
    video.hover()

    tile_menu = video.get_by_role(
        "button", name="More options", exact=True
    )
    tile_menu.wait_for(state="visible", timeout=10000)
    tile_menu.click()
    page.wait_for_timeout(300)

    download_item = page.get_by_role(
        "menuitem", name="Download", exact=False
    )
    download_item.wait_for(state="visible", timeout=10000)
    download_item.click()
    page.wait_for_timeout(400)

    original = page.get_by_role("menuitem").filter(
        has_text=re.compile(r"720p.*Original size", re.I | re.S)
    )
    original.first.wait_for(state="visible", timeout=10000)

    output = VIDEO_DIR / f"Shot_{number:03d}.mp4"

    with page.expect_download(timeout=60000) as event:
        original.first.click()

    event.value.save_as(str(output))

    with output.open("rb") as f:
        header = f.read(12)

    if len(header) < 8 or header[4:8] != b"ftyp":
        output.unlink(missing_ok=True)
        raise RuntimeError("Download did not appear to be a valid MP4.")

    return output


def report(label, results, folder):
    print(f"\n{'=' * 48}\n{label} REPORT\n{'=' * 48}")

    print(f"Successful: {len(results['ok'])}")
    for name in results["ok"]:
        print(f"  OK      {name}")

    print(f"Skipped:   {len(results['skip'])}")
    for name in results["skip"]:
        print(f"  SKIPPED {name}")

    print(f"Failed:    {len(results['fail'])}")
    for number, error in results["fail"]:
        print(f"  FAILED  Shot_{number:03d}: {error}")

    print(f"Output folder: {folder}")


def run_batch(prompts, mode):
    is_video = mode == "video"
    output_dir = VIDEO_DIR if is_video else IMAGE_DIR
    results = {"ok": [], "skip": [], "fail": []}

    if is_video:
        missing = [
            f"Shot_{p['number']:03d}.jpg"
            for p in prompts
            if not (
                IMAGE_DIR / f"Shot_{p['number']:03d}.jpg"
            ).exists()
        ]

        if missing:
            raise FileNotFoundError(
                "Missing local reference images in downloads/: "
                + ", ".join(missing)
            )

    pw, page = connect_flow()

    try:
        if is_video:
            check_video_settings(page)

        for index, item in enumerate(prompts):
            number = item["number"]
            prompt = item["prompt"]
            duration = item.get("duration_seconds", 4)
            suffix = "mp4" if is_video else "jpg"
            filename = f"Shot_{number:03d}.{suffix}"
            output = output_dir / filename

            if output.exists():
                print(f"\nSKIP {filename}: already exists.")
                results["skip"].append(filename)
                continue

            print(f"\n--- {mode.upper()} {filename} ---")

            try:
                if is_video:
                    # Reload between shots to reset the previous prompt and
                    # ingredient selection. Generated project assets persist.
                    if index > 0:
                        page.reload(wait_until="domcontentloaded")
                        page.locator(
                            'div.ProseMirror[contenteditable="true"]'
                        ).first.wait_for(timeout=30000)

                    configure_video_duration(page, duration)

                    old = signatures(page, "flow-video-tile")
                    attach_image(page, number)

                else:
                    old = signatures(
                        page, "flow-image-tile", image=True
                    )

                print("PROMPT: locating editor", flush=True)

                editor = page.locator(
                    'div.ProseMirror[contenteditable="true"]'
                ).first

                editor.wait_for(state="visible", timeout=10000)

                print(
                    f"PROMPT: filling editor ({len(prompt)} characters)",
                    flush=True,
                )

                editor.fill(prompt, timeout=15000)
                print("PROMPT: editor filled", flush=True)

                generate = page.get_by_role(
                    "button", name="Start generation"
                )

                if not generate.is_enabled():
                    raise RuntimeError("Generate button is disabled.")

                generate.click()
                print("Generation submitted; waiting for result...")

                if is_video:
                    result = wait_for_new(
                        page, "flow-video-tile", old, VIDEO_TIMEOUT
                    )
                    saved = save_video(page, result, number)

                else:
                    result = wait_for_new(
                        page, "flow-image-tile", old,
                        IMAGE_TIMEOUT, image=True
                    )
                    saved = save_image(page, result, number)

                print(
                    f"SUCCESS: {saved.name} "
                    f"({saved.stat().st_size:,} bytes)"
                )
                results["ok"].append(saved.name)

            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                print(f"FAILED Shot_{number:03d}: {message}")
                results["fail"].append((number, message))

                print(
                    "Check Flow manually before retrying. Generation may "
                    "have completed even if downloading failed."
                )

            if index < len(prompts) - 1:
                remaining = [
                    p for p in prompts[index + 1:]
                    if not (
                        output_dir
                        / f"Shot_{p['number']:03d}.{suffix}"
                    ).exists()
                ]

                if remaining:
                    low, high = (50, 90) if is_video else (20, 30)
                    delay = random.randint(low, high)
                    print(
                        f"Waiting {delay} seconds before next prompt..."
                    )
                    time.sleep(delay)

    finally:
        pw.stop()

    report(mode.upper(), results, output_dir)


def main():
    if "--check" in sys.argv:
        success = True

        for path in (IMAGE_PROMPTS, VIDEO_PROMPTS):
            try:
                prompts = (
                    read_video_scenes(path)
                    if path == VIDEO_PROMPTS
                    else read_prompts(path)
                )

                print(
                    f"OK: {path.name}: "
                    f"{len(prompts)} validated prompts."
                )

            except Exception as exc:
                success = False
                print(f"ERROR: {path.name}: {exc}")

        if not success:
            sys.exit(1)

        print("Both prompt files passed validation.")
        return

    print("\nGOOGLE FLOW PRODUCTION")
    print("1. Image generation")
    print("2. Video generation")

    choice = input("Choose 1 or 2: ").strip()

    if choice == "1":
        mode = "image"
        path = IMAGE_PROMPTS

        print(
            "\nEnsure Google Flow is set to IMAGE generation and 1K output."
        )

    elif choice == "2":
        mode = "video"
        path = VIDEO_PROMPTS

        if path.exists():
            answer = input(
                f"\n{path.name} already exists. Regenerate it from "
                f"{ALIGNMENT_FILE.name} and "
                f"{VIDEO_PROMPTS_SOURCE.name}? [y/N]: "
            ).strip().lower()

            if answer in {"y", "yes"}:
                build_video_prompt_json()
            else:
                print(f"Keeping existing {path.name}.")

        else:
            print(
                f"\n{path.name} does not exist; generating it now."
            )
            build_video_prompt_json()

        print(
            "\nEnsure all reference images are uploaded to this Flow project."
        )

        proceed = input(
            f"\nGenerate Google Flow videos using {path.name}? [y/N]: "
        ).strip().lower()

        if proceed not in {"y", "yes"}:
            print(
                "Cancelled before video generation. "
                "No Google Flow credits used."
            )
            return

    else:
        print("Invalid choice.")
        return

    prompts = (
        read_video_scenes(path)
        if mode == "video"
        else read_prompts(path)
    )

    print(f"\nValidated {len(prompts)} prompts in {path.name}.")

    if mode == "video":
        print("Durations: " + ", ".join(
            f"Shot {p['number']}={p['duration_seconds']}s"
            for p in prompts
        ))

    print(
        f"Output directory: "
        f"{IMAGE_DIR if mode == 'image' else VIDEO_DIR}"
    )

    if mode == "image":
        print("Random delay between prompts: 20–30 seconds.")
    else:
        print("Random delay between prompts: 50–90 seconds.")
        print("Video settings will be checked before generation.")

    confirm = input(
        "\nThis process uses Google Flow credits. "
        "Type YES to continue: "
    )

    if confirm.strip() != "YES":
        print("Cancelled. Nothing generated.")
        return

    run_batch(prompts, mode)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Check Google Flow before retrying.")
        sys.exit(130)
    except Exception as exc:
        print(f"\nSTOPPED: {type(exc).__name__}: {exc}")
        sys.exit(1)
