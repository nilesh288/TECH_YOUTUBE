
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    context = browser.contexts[0]
    page = next(
        (pg for pg in context.pages if "flow.google.com" in pg.url),
        context.pages[0]
    )

    picker = page.locator(
        '[aria-label="Add ingredients to the prompt box"]'
    )

    if picker.count() and picker.first.get_attribute("aria-expanded") != "true":
        picker.first.click()
        page.wait_for_timeout(800)

    panel = page.locator("flow-add-menu-popover-content")
    search = panel.get_by_role("textbox", name="Search assets")
    search.fill("Shot_001.jpg")
    page.wait_for_timeout(1000)

    result = panel.get_by_role("option", name="Shot_001.jpg Image")
    print("Matching image results:", result.count())

    if result.count() == 1:
        result.click()
        page.wait_for_timeout(1200)
        print("Image result clicked.")
    else:
        print("Expected exactly one image; stopping without clicking.")

    print("Picker expanded:", picker.first.get_attribute("aria-expanded"))

    prompt = page.locator('div.ProseMirror[contenteditable="true"]').first
    print("Prompt editor count:", page.locator(
        'div.ProseMirror[contenteditable="true"]'
    ).count())

    if prompt.count():
        print("Prompt text:", repr(prompt.inner_text()))
        print("Prompt HTML:", prompt.inner_html()[:1500])

    print("Generate was not clicked.")
