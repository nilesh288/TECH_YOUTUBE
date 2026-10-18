#!/usr/bin/env python3
"""Safely test Flow duration selection for 4/6/8/10 seconds; never generates."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from production import connect_flow, check_video_settings, set_video_duration  # noqa: E402


def main():
    pw, page = connect_flow()
    settings = page.locator('button[aria-label="Settings trigger"]')
    if settings.count() != 1:
        pw.stop()
        raise RuntimeError("Could not find Flow settings button.")

    check_video_settings(page)
    before = (settings.inner_text() or "").replace("\n", " ")
    found = re.search(r"(?<!\d)(4|6|8|10)s(?!\d)", before)
    original = int(found.group(1)) if found else None
    print("Current settings:", before)
    print("This POC only changes settings; it will NOT click Generate or spend credits.")

    results = {}
    try:
        for seconds in (4, 6, 8, 10):
            try:
                set_video_duration(page, seconds)
                results[seconds] = "PASS"
            except Exception as exc:
                results[seconds] = f"FAIL: {type(exc).__name__}: {exc}"
                print(f"{seconds}s test failed: {exc}")
            print(f"Test {seconds}s: {results[seconds]}")
    finally:
        if original in (4, 6, 8, 10):
            try:
                set_video_duration(page, original)
                print(f"Restored original duration: {original}s")
            except Exception as exc:
                print(f"WARNING: Could not restore original duration: {exc}")
        pw.stop()

    print("\nDURATION POC SUMMARY")
    for seconds, result in results.items():
        print(f"  {seconds}s: {result}")
    print("No video generation was submitted.")


if __name__ == "__main__":
    main()
