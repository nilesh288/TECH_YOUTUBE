from playwright.sync_api import sync_playwright

IMAGE_NAME = "Shot_001.jpg"

TEST_PROMPT = (
    "Create a cinematic 4-second video of the massive suspension bridge "
    "shown in the reference image. Preserve the bridge's original structure, "
    "proportions, steel cables, lighting, and realistic materials. Add a slow, "
    "smooth forward camera movement toward the bridge, with subtle natural "
    "ocean movement. Keep the bridge structurally stable and the scene "
    "photorealistic. No cuts, no text, no additional objects."
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

    # Attach the reference image.
    picker = page.locator(
        '[aria-label="Add ingredients to the prompt box"]'
    )

    if (
        picker.count()
        and picker.first.get_attribute("aria-expanded") != "true"
    ):
        picker.first.click()
        page.wait_for_timeout(800)

    panel = page.locator("flow-add-menu-popover-content")
    search = panel.get_by_role("textbox", name="Search assets")
    search.fill(IMAGE_NAME)
    page.wait_for_timeout(1000)

    result = panel.get_by_role(
        "option",
        name=f"{IMAGE_NAME} Image"
    )

    print("Matching image results:", result.count())

    if result.count() != 1:
        raise RuntimeError(
            "Expected exactly one matching image. Stopping safely."
        )

    result.click()
    page.wait_for_timeout(1000)

    # Enter the temporary video prompt.
    prompt = page.locator(
        'div.ProseMirror[contenteditable="true"]'
    ).first

    prompt.click()
    prompt.fill(TEST_PROMPT)

    print("Prompt entered:", prompt.inner_text())
    print("Image attached and prompt entered.")
    print("Generate has NOT been clicked by this script.")
