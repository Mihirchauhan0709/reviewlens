"""Evaluation harness for the extractor.

Compares predictions in signals.db against ground-truth labels in eval_set.jsonl.
Reports per-field metrics and dumps failure cases so you can read them.

This is the loop the interview pitch is built around: "iterated the prompt against
measured accuracy, not against my impression of whether it felt right."

Workflow:
    1. python -m src.label                    # hand-label 50 reviews
    2. python -m src.extract --eval-set       # run extractor on those 50
    3. python -m src.eval                     # see scores + failure cases
    4. (read failures, tighten prompt, repeat 2-3)

Metrics:
  - has_quality_complaint:    exact-match accuracy (this is the binary classifier)
  - defect_category:          exact-match accuracy + confusion matrix
  - severity:                 exact-match AND within-one (severity is ordered)
  - sentiment:                exact-match
  - component_mentioned:      semantic fuzzy match (both null = match, both contain
                              an overlapping noun = match)
  - summary:                  not scored numerically — eyeballed in failure dump
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from src.extract import EVAL_PATH, SIGNALS_DB_PATH

FAILURES_PATH = Path(__file__).parent.parent / "data" / "failures.md"

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def load_eval_set() -> list[dict]:
    out = []
    with EVAL_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_predictions(review_ids: list[str]) -> dict[str, dict]:
    """Pull signals for the specified review IDs. Returns {review_id: prediction-dict}."""
    out: dict[str, dict] = {}
    with sqlite3.connect(SIGNALS_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(review_ids))
        rows = conn.execute(
            f"SELECT * FROM signals WHERE review_id IN ({placeholders})",
            review_ids,
        ).fetchall()
    for r in rows:
        d = dict(r)
        d["has_quality_complaint"] = bool(d["has_quality_complaint"])
        if d.get("would_recommend") is not None:
            d["would_recommend"] = bool(d["would_recommend"])
        out[d["review_id"]] = d
    return out


def component_match(gold: str | None, pred: str | None) -> bool:
    """Both null -> match. Otherwise check word overlap (lowercased)."""
    if gold is None and pred is None:
        return True
    if gold is None or pred is None:
        return False
    gold_words = set(gold.lower().split())
    pred_words = set(pred.lower().split())
    return bool(gold_words & pred_words)


def severity_within_one(gold: str, pred: str) -> bool:
    return abs(SEVERITY_RANK[gold] - SEVERITY_RANK[pred]) <= 1


def evaluate(eval_data: list[dict], preds: dict[str, dict]) -> dict:
    """Compute per-field metrics. Returns nested dict of results."""
    results = {
        "n_total": len(eval_data),
        "n_with_prediction": 0,
        "metrics": {},
        "confusion": defaultdict(Counter),
        "failures": [],
    }

    correct = {
        "has_quality_complaint": 0,
        "defect_category": 0,
        "severity_exact": 0,
        "severity_within_one": 0,
        "sentiment": 0,
        "component_mentioned": 0,
    }

    for item in eval_data:
        rid = item["review_id"]
        gold = item["label"]
        pred = preds.get(rid)
        if pred is None:
            continue
        results["n_with_prediction"] += 1

        # Build a failure record we'll append to if anything is wrong
        wrong: list[str] = []

        if gold["has_quality_complaint"] == pred["has_quality_complaint"]:
            correct["has_quality_complaint"] += 1
        else:
            wrong.append(f"has_quality_complaint: gold={gold['has_quality_complaint']} pred={pred['has_quality_complaint']}")

        if gold["defect_category"] == pred["defect_category"]:
            correct["defect_category"] += 1
        else:
            wrong.append(f"defect_category: gold={gold['defect_category']} pred={pred['defect_category']}")
            results["confusion"]["defect_category"][(gold["defect_category"], pred["defect_category"])] += 1

        if gold["severity"] == pred["severity"]:
            correct["severity_exact"] += 1
        if severity_within_one(gold["severity"], pred["severity"]):
            correct["severity_within_one"] += 1
        else:
            wrong.append(f"severity: gold={gold['severity']} pred={pred['severity']}")

        if gold["sentiment"] == pred["sentiment"]:
            correct["sentiment"] += 1
        else:
            wrong.append(f"sentiment: gold={gold['sentiment']} pred={pred['sentiment']}")

        if component_match(gold["component_mentioned"], pred["component_mentioned"]):
            correct["component_mentioned"] += 1
        else:
            wrong.append(f"component: gold={gold['component_mentioned']!r} pred={pred['component_mentioned']!r}")

        if wrong:
            results["failures"].append({
                "review_id": rid,
                "rating": item["rating"],
                "review_text": item["review_text"],
                "gold": gold,
                "pred": pred,
                "errors": wrong,
            })

    n = results["n_with_prediction"]
    if n > 0:
        results["metrics"] = {k: v / n for k, v in correct.items()}

    return results


def print_report(results: dict) -> None:
    print("\n" + "=" * 64)
    print(f"Eval results: {results['n_with_prediction']}/{results['n_total']} reviews scored")
    print("=" * 64)
    metrics = results["metrics"]
    rows = [
        ("has_quality_complaint",   "exact",       metrics["has_quality_complaint"]),
        ("defect_category",         "exact",       metrics["defect_category"]),
        ("severity",                "exact",       metrics["severity_exact"]),
        ("severity",                "within-one",  metrics["severity_within_one"]),
        ("sentiment",               "exact",       metrics["sentiment"]),
        ("component_mentioned",     "fuzzy",       metrics["component_mentioned"]),
    ]
    print(f"\n{'field':<22} {'match':<14} {'accuracy':>10}")
    print("-" * 48)
    for field, kind, value in rows:
        print(f"{field:<22} {kind:<14} {value*100:>8.1f}%")

    # Defect-category confusion matrix
    if results["confusion"]["defect_category"]:
        print("\nDefect category confusion (gold -> pred -> count):")
        for (g, p), c in sorted(results["confusion"]["defect_category"].items(), key=lambda x: -x[1]):
            print(f"  {g:>24}  ->  {p:<24}  ({c})")

    print(f"\nFailures written to: {FAILURES_PATH}")


def write_failures(results: dict) -> None:
    """Dump the failure cases as a markdown file for human inspection."""
    FAILURES_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Extraction failures ({len(results['failures'])} of {results['n_with_prediction']})\n"]
    for f in results["failures"]:
        lines.append(f"## Review {f['review_id']}  ({f['rating']}/5)\n")
        text = (f["review_text"] or "").replace("\n", " ")
        if len(text) > 300:
            text = text[:300] + "..."
        lines.append(f"> {text}\n")
        lines.append("**Errors:**")
        for err in f["errors"]:
            lines.append(f"- {err}")
        lines.append("\n**Full gold:**")
        lines.append(f"```\n{json.dumps(f['gold'], indent=2)}\n```")
        lines.append("\n**Full pred:**")
        pred_clean = {k: f["pred"].get(k) for k in [
            "has_quality_complaint", "defect_category", "component_mentioned",
            "severity", "time_to_failure_days", "sentiment", "would_recommend", "summary"
        ]}
        lines.append(f"```\n{json.dumps(pred_clean, indent=2)}\n```")
        lines.append("\n---\n")
    FAILURES_PATH.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    args = parser.parse_args()

    eval_data = load_eval_set()
    if not eval_data:
        raise SystemExit("Empty eval set. Run `python -m src.label` first.")

    review_ids = [item["review_id"] for item in eval_data]
    preds = load_predictions(review_ids)

    if not preds:
        raise SystemExit(
            f"No predictions found for any eval-set review.\n"
            f"Run: python -m src.extract --eval-set"
        )

    missing = [rid for rid in review_ids if rid not in preds]
    if missing:
        print(f"Warning: {len(missing)} eval reviews have no prediction yet.")

    results = evaluate(eval_data, preds)
    print_report(results)
    write_failures(results)


if __name__ == "__main__":
    main()
