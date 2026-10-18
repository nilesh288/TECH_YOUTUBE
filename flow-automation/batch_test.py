
from pathlib import Path
import re
import time

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_DIR = Path.home() / "flow-automation"
PROMPTS_FILE = BASE_DIR / "prompts.txt"
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

TEST_COUNT = 2
GENERATION_TIMEOUT_MS = 180_000
POLL_INTERVAL_SECONDS = 2


def read_prompts():
    text = PROMPTS_FILE.read_text(encoding="utf-8")
    pattern = re.compile(r"(?im)^\s*Shot\s+(\d+)\s*:\s*")
    matches = list(pattern.finditer(text))

    if not matches:
        raise ValueError("No Shot N: headings found in prompts.txt")

    prompts = []
    seen = set()

    for i, match in enumerate(matches):
        number = int(match.group(1))
        if number in seen:
            raise ValueError(f"Duplicate shot number: {number}")
        seen.add(number)

        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        prompt = text[start:end].strip()

        if not prompt:
            raise ValueError(f"Shot {number} has an empty prompt")

        prompts.append({"number": number, "prompt": prompt})

    prompts.sort(key=lambda item: item["number"])

    if [item["number"] for item in prompts] != list(
        range(1, len(prompts) + 1)
    ):
        raise ValueError("Shot numbers must be sequential, starting at 1")

    return prompts


def get_image_tile(page, prompt):
    # Flow commonly displays part of the prompt as the tile's accessible name.
    # Use a distinctive initial phrase to locate the matching result.
    phrase = " ".join(prompt.split()[:6])
    return page.locator("flow-image-tile").filter(
        has_text=re.compile(re.escape(phrase), re.IGNORECASE)
    )


def tile_signature(tile):
    """Read the image URL to distinguish an existing tile from a new result."""
    try:
        return tile.locator("img").first.evaluate(
            "(img) => img.currentSrc || img.src"
        )
    except Exception:
        return None


def wait_for_new_tile(page, previous_signatures, prompt):
    deadline = time.monotonic() + GENERATION_TIMEOUT_MS / 1000

    while time.monotonic() < deadline:
        tiles = page.locator("flow-image-tile")

        for index in range(tiles.count()):
            tile = tiles.nth(index)
            try:
                signature = tile_signature(tile)
                if signature and signature not in previous_signatures:
                    return tile
            except Exception:
                continue

        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        "No new image tile detected before timeout. "
        "Check Flow manually; the script will not download an old image."
    )


def get_all_signatures(page):
    signatures = set()
    tiles = page.locator("flow-image-tile")

    for index in range(tiles.count()):
        signature = tile_signature(tiles.nth(index))
        if signature:
            signatures.add(signature)

    return signatures


def download_tile_1k(page, tile, shot_number):
    tile.hover()
    page.wait_for_timeout(400)

    menu_button = tile.get_by_role("button", name="More options")
    menu_button.click()

    download_item = page.get_by_role("menuitem").filter(
        has_text=re.compile(r"^download\s*Download$", re.IGNORECASE)
    )
    download_item.click()

    original_size = page.get_by_role("menuitem").filter(
        has_text=re.compile(r"1K.*Original size", re.DOTALL)
    )

    output_path = DOWNLOAD_DIR / f"Shot_{shot_number:03d}.jpg"

    with page.expect_download(timeout=60_000) as download_info:
        original_size.click()

    download = download_info.value
    download.save_as(str(output_path))

    # Confirm that the file content is JPEG, not just named .jpg.
    with output_path.open("rb") as file:
        signature = file.read(3)

    if signature != b"\xff\xd8\xff":
        output_path.unlink(missing_ok=True)
        raise ValueError(
            f"Downloaded file is not a JPEG; removed {output_path.name}"
        )

    print(
        f"Saved Shot_{shot_number:03d}.jpg "
        f"({output_path.stat().st_size:,} bytes)"
    )


def main():
    prompts = read_prompts()[:TEST_COUNT]

    if len(prompts) < TEST_COUNT:
        raise ValueError(
            f"Need at least {TEST_COUNT} prompts; found {len(prompts)}"
        )

    print(f"Two-prompt test. Output folder: {DOWNLOAD_DIR}")
    print("Make sure the remote-debugging Chromium window is open in Flow.")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = next(
            (pg for pg in context.pages if "flow.google.com" in pg.url),
            context.pages[0],
        )

        for item in prompts:
            shot_number = item["number"]
            prompt = item["prompt"]
            output_path = DOWNLOAD_DIR / f"Shot_{shot_number:03d}.jpg"

            if output_path.exists():
                print(
                    f"Shot {shot_number:03d} already exists; "
                    "skipping to avoid overwriting."
                )
                continue

            print(f"\n--- Shot {shot_number:03d} ---")
            print("Prompt:", prompt[:140])

            # Record all existing image URLs before submitting the prompt.
            previous_signatures = get_all_signatures(page)

            editor = page.locator(
                'div.ProseMirror[contenteditable="true"]'
            ).first
            editor.fill(prompt)

            generate_button = page.get_by_role(
                "button", name="Start generation"
            )
            generate_button.click()

            print("Generation submitted. Waiting for a new image...")

            tile = wait_for_new_tile(page, previous_signatures, prompt)

            print("New image detected. Downloading at 1K...")
            download_tile_1k(page, tile, shot_number)

        print("\nTwo-prompt test finished.")


if __name__ == "__main__":
    try:
        main()
    except (TimeoutError, PlaywrightTimeoutError, Exception) as exc:
        print(f"\nSTOPPED: {type(exc).__name__}: {exc}")
        print(
            "Check Google Flow manually before retrying. "
            "A generation may have completed even if the download failed."
        )
