
from pathlib import Path
import re

PROMPTS_FILE = Path.home() / "flow-automation" / "prompts.txt"

def read_prompts():
    text = PROMPTS_FILE.read_text(encoding="utf-8")

    pattern = re.compile(r"(?im)^\s*Shot\s+(\d+)\s*:\s*")
    matches = list(pattern.finditer(text))

    if not matches:
        raise ValueError("No 'Shot N:' headings found.")

    prompts = []
    seen = set()

    for i, match in enumerate(matches):
        number = int(match.group(1))

        if number in seen:
            raise ValueError(f"Duplicate shot number: {number}")

        seen.add(number)

        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        prompt = text[start:end].strip()

        if not prompt:
            raise ValueError(f"Shot {number} has an empty prompt.")

        prompts.append({
            "id": f"{number:03d}",
            "prompt": prompt
        })

    return prompts


if __name__ == "__main__":
    prompts = read_prompts()

    print(f"Total prompts found: {len(prompts)}")
    print()

    for item in prompts:
        print(f"Shot {item['id']}: {item['prompt']}")

    numbers = [int(item["id"]) for item in prompts]
    expected = list(range(1, len(numbers) + 1))

    if numbers == expected:
        print("\nPASS: Shot numbering is sequential.")
    else:
        print("\nWARNING: Shot numbers are not sequential.")
        print("Found:", numbers)
