
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = Path.home() / "flow-automation"
PROFILE = BASE / "profile"
CHROMIUM = "/usr/bin/chromium"
FLOW_URL = "https://labs.google/fx/tools/flow"

BASE.mkdir(parents=True, exist_ok=True)
PROFILE.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE),
        executable_path=CHROMIUM,
        headless=False,
        accept_downloads=True,
        viewport={"width": 1440, "height": 1000},
    )

    page = context.pages[0] if context.pages else context.new_page()
    page.goto(FLOW_URL, wait_until="domcontentloaded")

    input(
        "\nSign in to Google if needed. Open your Flow project "
        "and select Image mode. Then press Enter here..."
    )

    page.screenshot(
        path=str(BASE / "flow-screen.png"),
        full_page=True
    )

    print("\n--- TEXT INPUTS ---")
    for i, el in enumerate(page.locator("textarea, input").all()):
        try:
            print(
                i,
                el.evaluate(
                    """e => ({
                        tag: e.tagName,
                        type: e.type,
                        placeholder: e.getAttribute('placeholder'),
                        aria: e.getAttribute('aria-label')
                    })"""
                )
            )
        except Exception:
            pass

    print("\n--- VISIBLE BUTTONS ---")
    for i, el in enumerate(page.get_by_role("button").all()):
        try:
            if el.is_visible():
                print(
                    i,
                    repr(el.inner_text()[:100]),
                    "aria=", el.get_attribute("aria-label"),
                    "title=", el.get_attribute("title")
                )
        except Exception:
            pass

    print("\nScreenshot saved to:", BASE / "flow-screen.png")
    input("\nKeep the browser open while you inspect it. Press Enter to close...")

    context.close()
