
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(
        "http://127.0.0.1:9222"
    )
    context = browser.contexts[0]

    page = next(
        (pg for pg in context.pages if "flow.google.com" in pg.url),
        context.pages[0]
    )

    print("\n--- VISIBLE IMAGES ---")
    images = page.locator("img")

    for i, img in enumerate(images.all()):
        try:
            if img.is_visible():
                print(i, img.evaluate("""e => ({
                    alt: e.alt,
                    src: e.currentSrc.slice(0, 180),
                    width: e.naturalWidth,
                    height: e.naturalHeight
                })"""))
        except Exception:
            pass

    print("\n--- VISIBLE BUTTONS ---")
    for i, button in enumerate(page.get_by_role("button").all()):
        try:
            if button.is_visible():
                print(
                    i,
                    repr(button.inner_text()[:80]),
                    "aria=", button.get_attribute("aria-label"),
                    "title=", button.get_attribute("title")
                )
        except Exception:
            pass

    page.screenshot(
        path="flow-after-generation.png",
        full_page=True
    )
    print("\nScreenshot saved as flow-after-generation.png")

    input("Inspect the output above, then press Enter to close...")
    browser.close()
