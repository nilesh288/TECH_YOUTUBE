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

    print("Current URL:", page.url)
    print("\n--- Visible buttons ---")

    buttons = page.locator("button")

    for i in range(buttons.count()):
        button = buttons.nth(i)

        if not button.is_visible():
            continue

        text = (button.inner_text() or "").strip().replace("\n", " ")
        aria = button.get_attribute("aria-label")
        title = button.get_attribute("title")
        disabled = button.is_disabled()

        if text or aria or title:
            print(
                f"{i}: text={text!r}, "
                f"aria-label={aria!r}, "
                f"title={title!r}, "
                f"disabled={disabled}"
            )

    print("\nInspection complete. No buttons were clicked.")
