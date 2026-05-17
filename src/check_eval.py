"""Find which lines in eval_set.jsonl are not valid JSON.

Usage: python -m src.check_eval
"""

import json
from pathlib import Path

EVAL_PATH = Path(__file__).parent.parent / "data" / "eval_set.jsonl"

if not EVAL_PATH.exists():
    raise SystemExit(f"No file at {EVAL_PATH}")

with EVAL_PATH.open() as f:
    lines = f.readlines()

print(f"File has {len(lines)} lines.\n")

broken = []
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    if not stripped:
        print(f"Line {i}: EMPTY (ok, ignored)")
        continue
    try:
        obj = json.loads(stripped)
        rid = obj.get("review_id", "?")
        print(f"Line {i}: OK  review_id={rid}")
    except json.JSONDecodeError as e:
        print(f"Line {i}: BROKEN  ({e.msg} at char {e.pos})")
        # Print the area around the break for context
        start = max(0, e.pos - 40)
        end = min(len(stripped), e.pos + 40)
        print(f"  context: ...{stripped[start:end]}...")
        broken.append(i)

print()
if broken:
    print(f"BROKEN LINES: {broken}")
else:
    print("All lines valid JSON.")