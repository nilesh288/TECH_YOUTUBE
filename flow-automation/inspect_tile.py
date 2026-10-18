from pathlib import Path
from playwright.sync_api import sync_playwright
import re

download_dir = Path.home() / "flow-automation" / "downloads"
download_dir.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(
        "http://127.0.0.1:9222"
    )
    context = browser.contexts[0]
    page = next(
        (pg for pg in context.pages if "flow.google.com" in pg.url),
        context.pages[0]
    )

    tile = page.get_by_role(
        "button",
        name=re.compile("Suspension bridge over calm ocean")
    )
    tile.hover()
    page.wait_for_timeout(300)

    image_tile = page.locator("flow-image-tile").filter(has=tile)
    image_tile.get_by_role("button", name="More options").click()

    page.get_by_role("menuitem").filter(
        has_text=re.compile(r"Download")
    ).click()

    original_size = page.get_by_role("menuitem").filter(
        has_text=re.compile(r"1K.*Original size", re.DOTALL)
    )
    print("1K option count:", original_size.count())

    with page.expect_download(timeout=60000) as download_info:
        original_size.click()

    download = download_info.value
    save_path = download_dir / "Shot_001.png"
    download.save_as(str(save_path))

    print("\nDownload completed.")
    print("Suggested filename:", download.suggested_filename)
    print("Saved path:", save_path)
    print("File exists:", save_path.exists())
    print("File size:", save_path.stat().st_size, "bytes")

    input("\nVerify the result, then press Enter to finish...")
