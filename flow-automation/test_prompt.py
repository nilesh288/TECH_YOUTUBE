
from playwright.sync_api import sync_playwright

test_prompt = (
    "TEST PROMPT: Wide angle shot of a massive modern "
    "suspension bridge over a calm ocean, photorealistic "
    "3D technical render."
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

    editor = page.locator(
        'div.ProseMirror[contenteditable="true"]'
    ).first

    editor.wait_for(state="visible", timeout=10000)
    editor.click()
    editor.fill(test_prompt)

    page.wait_for_timeout(1000)

    actual_text = editor.inner_text()
    print("Expected:", test_prompt)
    print("Actual:  ", actual_text)
    print("Match:   ", actual_text == test_prompt)

    input("Check the Flow prompt box. Press Enter to exit...")
    browser.close()
