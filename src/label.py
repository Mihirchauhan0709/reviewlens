"""Interactive CLI for hand-labeling the ground-truth eval set.

Stratified sample: pulls N reviews per (SKU bucket, rating bucket) so the eval set
isn't dominated by 5-star reviews or by a single SKU. Default target = 50 labeled
reviews, matching the README claim.

Usage:
    python -m src.label                    # start labeling, append to eval_set.jsonl
    python -m src.label --count            # show how many are already labeled
    python -m src.label --resample         # pull a new sample (skips already-labeled IDs)

The schema mirrors what the extractor produces. That symmetry is the whole point:
labels and predictions are directly comparable.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from textwrap import fill

from src.storage import get_conn

EVAL_PATH = Path(__file__).parent.parent / "data" / "eval_set.jsonl"
TARGET_SIZE = 50
SEED = 42  # deterministic sampling so a re-run gives the same review order

# These mirror the extractor's enums exactly. Single source of truth for both.
DEFECT_CATEGORIES = [
    "mechanical_failure",
    "performance_degradation",
    "design_flaw",
    "durability",
    "missing_parts",
    "safety_concern",
    "none",
]
SEVERITIES = ["critical", "high", "medium", "low"]
SENTIMENTS = ["positive", "neutral", "negative"]


def already_labeled_ids() -> set[str]:
    if not EVAL_PATH.exists():
        return set()
    out = set()
    with EVAL_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                out.add(json.loads(line)["review_id"])
    return out


def stratified_sample(n: int = TARGET_SIZE) -> list[dict]:
    """Pull a stratified sample across SKU and rating bucket.

    Strategy: target ~5 reviews per SKU (since we have 10 SKUs), and within each
    SKU pull a mix across star ratings. Random sampling within each cell.
    """
    skipped = already_labeled_ids()
    rng = random.Random(SEED)

    with get_conn() as conn:
        skus = conn.execute("SELECT asin, display_name FROM skus").fetchall()
        per_sku = max(1, n // len(skus))

        out = []
        for sku in skus:
            # Within each SKU, try to get coverage across the rating spectrum.
            # Pull twice the budget so we have headroom after dedup.
            rows = conn.execute(
                """
                SELECT review_id, asin, rating, review_title, review_text
                FROM reviews
                WHERE asin = ?
                  AND review_text IS NOT NULL AND length(review_text) > 30
                ORDER BY rating, RANDOM()
                """,
                (sku["asin"],),
            ).fetchall()

            rows = [dict(r) for r in rows if r["review_id"] not in skipped]
            if not rows:
                continue

            # Bucket by rating, then round-robin pick to ensure star diversity.
            buckets: dict[int, list[dict]] = {}
            for r in rows:
                buckets.setdefault(r["rating"] or 0, []).append(r)
            for k in buckets:
                rng.shuffle(buckets[k])

            picked = []
            star_order = [1, 2, 3, 4, 5]
            while len(picked) < per_sku and any(buckets.get(s) for s in star_order):
                for s in star_order:
                    if buckets.get(s):
                        picked.append(buckets[s].pop())
                        if len(picked) >= per_sku:
                            break
            out.extend({**p, "display_name": sku["display_name"]} for p in picked)

    rng.shuffle(out)
    return out[:n]


def prompt_choice(label: str, options: list[str], default: str | None = None) -> str:
    """Display numbered options, accept number or empty (for default)."""
    print(f"\n{label}:")
    for i, opt in enumerate(options, 1):
        marker = " *" if opt == default else "  "
        print(f"  {i}{marker} {opt}")
    while True:
        raw = input(f"  > [{default or '?'}]: ").strip()
        if raw == "" and default is not None:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        if raw in options:
            return raw
        print(f"  invalid; enter 1-{len(options)} or the value")


def prompt_optional_int(label: str) -> int | None:
    raw = input(f"\n{label} (number or empty): ").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        print("  invalid, treating as None")
        return None


def prompt_optional_bool(label: str) -> bool | None:
    raw = input(f"\n{label} [y/n/empty]: ").strip().lower()
    if raw == "y":
        return True
    if raw == "n":
        return False
    return None


def prompt_text(label: str, required: bool = False) -> str:
    while True:
        raw = input(f"\n{label}: ").strip()
        if raw or not required:
            return raw
        print("  required.")


def render_review(r: dict) -> None:
    """Print the review nicely for the labeler."""
    print("\n" + "=" * 70)
    print(f"SKU:    {r['display_name']}  ({r['asin']})")
    print(f"Rating: {r['rating']}/5")
    title = r.get("review_title") or "(no title)"
    print(f"Title:  {title}")
    print("-" * 70)
    text = r.get("review_text") or ""
    print(fill(text, width=70))
    print("=" * 70)


def label_one(r: dict) -> dict:
    """Walk a human through labeling a single review. Returns the label dict."""
    render_review(r)

    is_complaint = prompt_choice("has_quality_complaint", ["true", "false"], default="false")
    has_complaint = is_complaint == "true"

    if has_complaint:
        defect = prompt_choice("defect_category", DEFECT_CATEGORIES, default="durability")
        severity = prompt_choice("severity", SEVERITIES, default="medium")
        component = prompt_text("component_mentioned (1-3 words, blank for none)") or None
        ttf = prompt_optional_int("time_to_failure_days")
    else:
        defect = "none"
        severity = "low"
        component = None
        ttf = None

    sentiment_default = "positive" if (r["rating"] or 3) >= 4 else (
        "negative" if (r["rating"] or 3) <= 2 else "neutral"
    )
    sentiment = prompt_choice("sentiment", SENTIMENTS, default=sentiment_default)
    would_rec = prompt_optional_bool("would_recommend")
    summary = prompt_text("summary (one sentence, factual)", required=True)

    return {
        "review_id": r["review_id"],
        "asin": r["asin"],
        "rating": r["rating"],
        "review_text": r["review_text"],
        "label": {
            "has_quality_complaint": has_complaint,
            "defect_category": defect,
            "component_mentioned": component,
            "severity": severity,
            "time_to_failure_days": ttf,
            "sentiment": sentiment,
            "would_recommend": would_rec,
            "summary": summary,
        },
    }


def append_label(label: dict) -> None:
    EVAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EVAL_PATH.open("a") as f:
        f.write(json.dumps(label, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true", help="show how many are labeled")
    parser.add_argument("--n", type=int, default=TARGET_SIZE, help="target sample size")
    args = parser.parse_args()

    if args.count:
        print(f"Labeled so far: {len(already_labeled_ids())} (target: {TARGET_SIZE})")
        return

    sample = stratified_sample(args.n)
    if not sample:
        sys.exit("Nothing to label. Run the scraper first, or you've labeled everything.")

    print(f"\n{len(sample)} reviews queued for labeling.")
    print("Tips: type a number for enums, hit enter to take the [default],")
    print("Ctrl-C to stop at any time (already-saved labels are preserved).\n")

    try:
        for i, r in enumerate(sample, 1):
            print(f"\n\n[{i}/{len(sample)}]")
            label = label_one(r)
            append_label(label)
            print(f"  saved. running total: {len(already_labeled_ids())}")
    except KeyboardInterrupt:
        print(f"\n\nStopped. {len(already_labeled_ids())} labels saved to {EVAL_PATH}")
        return

    print(f"\n\nDone. {len(already_labeled_ids())} labels in {EVAL_PATH}")


if __name__ == "__main__":
    main()
