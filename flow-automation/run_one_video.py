
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

    generate = page.locator(
        'button[aria-label="Start generation"]'
    )

    if generate.count() != 1:
        raise RuntimeError(
            "Expected exactly one Generate button. Stopping."
        )

    if not generate.is_visible():
        raise RuntimeError("Generate button is not visible.")

    if generate.is_disabled():
        raise RuntimeError(
            "Generate button is disabled. Stopping."
        )

    print("Ready to generate one video.")
    print("Current URL:", page.url)
    print("Starting generation...")

    generate.click()

    print("Generate button clicked.")
    print("Wait for Google Flow to start generating the video.")
    print("Do not click Generate again.")
