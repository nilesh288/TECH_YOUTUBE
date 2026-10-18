from pathlib import Path
from playwright.sync_api import sync_playwright

output = Path.home() / "flow-automation" / "Shot_001.mp4"

if output.exists():
    raise FileExistsError(
        f"{output} already exists. Rename it before running this test."
    )

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(
        "http://127.0.0.1:9222"
    )

    context = browser.contexts[0]
    page = next(
        (pg for pg in context.pages if "flow.google.com" in pg.url),
        context.pages[0]
    )

    # Reuse the existing generated video.
    video = page.get_by_label("Generated video").first
    video.hover()
    page.wait_for_timeout(500)

    # Open the generated video's menu if the resolution menu
    # is not already visible.
    resolution = (
        page.get_by_role("menuitem")
        .filter(has_text="720p")
        .filter(has_text="Original size")
    )

    if not resolution.count() or not resolution.first.is_visible():
       
        menus = page.get_by_role(
            "button", name="More options", exact=True
        )

        if menus.count() != 2:
            raise RuntimeError(
                f"Expected 2 More options buttons, found {menus.count()}."
            )

        menu = menus.nth(1)

        if not menu.is_visible():
            raise RuntimeError(
                "The video toolbar More options button is not visible."
            )

        menu.click()

        page.wait_for_timeout(300)

        download_item = page.get_by_role(
            "menuitem", name="Download", exact=False
        )

        if download_item.count() != 1:
            raise RuntimeError("Could not uniquely identify Download.")

        download_item.click()
        page.wait_for_timeout(500)

        resolution = (
            page.get_by_role("menuitem")
            .filter(has_text="720p")
            .filter(has_text="Original size")
        )

    if resolution.count() != 1 or not resolution.first.is_visible():
        raise RuntimeError("Could not uniquely identify 720p Original size.")

    print("Selecting 720p Original size...")

    with page.expect_download(timeout=60000) as download_event:
        resolution.first.click()

    download = download_event.value
    download.save_as(str(output))

    print("Download completed.")
    print("Original filename:", download.suggested_filename)
    print("Saved to:", output)
    print("File size:", output.stat().st_size, "bytes")
