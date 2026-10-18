
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    context = browser.contexts[0]

    page = next(
        (pg for pg in context.pages if "flow.google.com" in pg.url),
        context.pages[0]
    )

    print("\n--- TEXT INPUTS ---")
    for i, el in enumerate(page.locator("textarea, input, [contenteditable='true']").all()):
        try:
            if el.is_visible():
                print(
                    i,
                    el.evaluate("""e => ({
                        tag: e.tagName,
                        type: e.getAttribute('type'),
                        placeholder: e.getAttribute('placeholder'),
                        aria: e.getAttribute('aria-label'),
                        role: e.getAttribute('role'),
                        text: (e.innerText || '').slice(0, 80)
                    })""")
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

    print("\n--- INSPECTION COMPLETE ---")
    browser.close()
