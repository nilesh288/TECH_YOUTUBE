
from playwright.sync_api import sync_playwright

IMAGE_NAME = "Shot_001.jpg"

TEST_PROMPT = (
    "Create a cinematic 4-second video from the attached reference image. "
    "Preserve the bridge design, camera framing, lighting, and realistic "
    "materials. Add subtle natural camera movement and keep the scene stable."
)

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
    search.fill(IMAGE_NAME)
    page.wait_for_timeout(1000)

    result = panel.get_by_role("option", name=f"{IMAGE_NAME} Image")
    print("Matching image results:", result.count())

    if result.count() != 1:
        raise RuntimeError("Expected exactly one matching image; stopping.")

    result.click()
    page.wait_for_timeout(1000)

    prompt = page.locator(
        'div.ProseMirror[contenteditable="true"]'
    ).first

    prompt.click()
    prompt.fill(TEST_PROMPT)
    page.wait_for_timeout(500)

    print("Prompt entered:", repr(prompt.inner_text()))
    print("Prompt HTML:", prompt.inner_html()[:1000])
    print("Image selection and prompt entry test complete.")
    print("Generate was NOT clicked.")
