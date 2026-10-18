
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    context = browser.contexts[0]

    page = next(
        (pg for pg in context.pages if "flow.google.com" in pg.url),
        context.pages[0]
    )

    print("\n--- VISIBLE EDITABLE ELEMENTS ---")
    elements = page.locator(
        "textarea, input, [contenteditable='true'], [role='textbox']"
    )

    for i, el in enumerate(elements.all()):
        try:
            if el.is_visible():
                print(f"\nElement {i}:")
                print(el.evaluate("""e => ({
                    tag: e.tagName,
                    aria: e.getAttribute('aria-label'),
                    placeholder: e.getAttribute('placeholder'),
                    role: e.getAttribute('role'),
                    contenteditable: e.getAttribute('contenteditable'),
                    outerHTML: e.outerHTML.slice(0, 500)
                })"""))
        except Exception:
            pass

    input("\nInspection finished. Press Enter to exit...")
    browser.close()
