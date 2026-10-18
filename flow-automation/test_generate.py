
from pathlib import Path
import re
from playwright.sync_api import sync_playwright

BASE = Path.home() / "flow-automation"
PROMPTS_FILE = BASE / "prompts.txt"

def read_prompts():
    text = PROMPTS_FILE.read_text(encoding="utf-8")
    pattern = re.compile(r"(?im)^\s*Shot\s+(\d+)\s*:\s*")
    matches = list(pattern.finditer(text))

    prompts = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        prompts.append({
            "number": int(match.group(1)),
            "prompt": text[start:end].strip()
        })
    return prompts

prompts = read_prompts()
if not prompts:
    raise SystemExit("No prompts found.")

first = prompts[0]
print(f"Ready to generate Shot {first['number']:03d}:")
print(first["prompt"])

confirm = input("Type GENERATE to submit this ONE image: ")
if confirm != "GENERATE":
    raise SystemExit("Cancelled. Nothing was generated.")

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
    generate = page.get_by_role(
        "button", name="Start generation"
    )

    editor.wait_for(state="visible", timeout=10000)
    generate.wait_for(state="visible", timeout=10000)

    editor.fill(first["prompt"])
    print("Prompt entered. Submitting generation...")
    generate.click()

    print("Generate button clicked once.")
    print("Watch Chromium to confirm the generation starts.")
    input("After checking Flow, press Enter here to finish this test.")

    browser.close()
