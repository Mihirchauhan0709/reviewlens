"""LLM-generated executive brief.

Reads findings.json, takes the top-N highest-risk SKUs, formats them into a
compact payload, calls the OpenAI API with prompts/executive_brief.txt, and
writes the resulting three-sentence brief to data/brief.txt.

The output is the Monday-morning briefing that anchors the dashboard's executive
summary view. The whole point of this layer is to translate the structured
findings table into prose a director or VP can absorb in 90 seconds.

Usage:
    export OPENAI_API_KEY=sk-...
    python -m src.brief                # default: top 3 SKUs
    python -m src.brief --n 5          # top 5
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from openai import OpenAI

from src.aggregate import FINDINGS_PATH

BRIEF_PATH = Path(__file__).parent.parent / "data" / "brief.txt"
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "executive_brief.txt"
MODEL = "gpt-4o-mini-2024-07-18"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reviewlens.brief")


def compact_finding(f: dict, category_baseline: dict[str, float]) -> dict:
    """Strip findings down to just what the brief needs. The prompt is short
    and the model performs better when we give it less to wade through."""
    base_rate = category_baseline.get(f["category"], 0)
    sku_rate  = f["true_complaint_rate"]["value"]
    ratio = (sku_rate / base_rate) if base_rate > 0 else None

    top_defect    = f["top_defects"][0]    if f["top_defects"]    else None
    top_component = f["top_components"][0] if f["top_components"] else None
    trend         = f["recency"]["delta_pp"]

    return {
        "display_name":             f["display_name"],
        "category":                 f["category"],
        "true_complaint_rate_pct":  round(sku_rate * 100, 1),
        "category_baseline_pct":    round(base_rate * 100, 1),
        "vs_baseline_ratio":        round(ratio, 2) if ratio is not None else None,
        "safety_complaint_share_pct": round(f["safety_share"] * 100, 1),
        "severity_score":           round(f["severity"]["score"], 2),
        "n_complaints":             f["severity"]["n_complaints"],
        "top_defect_category":      top_defect["value"] if top_defect else None,
        "top_defect_share_pct":     round(top_defect["share"] * 100, 1) if top_defect else None,
        "top_component":            top_component["value"] if top_component else None,
        "top_component_share_pct":  round(top_component["share"] * 100, 1) if top_component else None,
        "trend_delta_pp":           round(trend, 1) if trend is not None else None,
        "trend_note":               "insufficient sample" if trend is None else None,
        "representative_complaints": f["representative_summaries"][:3],
    }


def load_top_findings(n: int) -> tuple[list[dict], dict]:
    """Load findings.json, return the top-N most risky plus category baselines."""
    if not FINDINGS_PATH.exists():
        sys.exit(f"No findings at {FINDINGS_PATH}. Run `python -m src.aggregate` first.")
    data = json.loads(FINDINGS_PATH.read_text())
    baselines = data["category_baseline"]
    top = data["findings"][:n]
    return [compact_finding(f, baselines) for f in top], baselines


def generate_brief(top_findings: list[dict]) -> str:
    """Call the model with the brief prompt + findings payload."""
    system_prompt = PROMPT_PATH.read_text()
    user_msg = (
        f"Top-N SKU findings:\n\n"
        f"{json.dumps(top_findings, indent=2)}\n\n"
        f"Write the brief."
    )
    client = OpenAI()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_msg},
        ],
        temperature=0.3,   # a hair of variation, but the prompt rules dominate
    )
    return resp.choices[0].message.content.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=3, help="number of top SKUs to brief on")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY in the environment.")

    top, _ = load_top_findings(args.n)
    log.info("Generating brief for top %d SKUs: %s",
             len(top), ", ".join(f["display_name"] for f in top))

    brief = generate_brief(top)

    BRIEF_PATH.parent.mkdir(parents=True, exist_ok=True)
    BRIEF_PATH.write_text(brief + "\n")
    log.info("Wrote brief to %s\n", BRIEF_PATH)
    print("\n" + "=" * 72)
    print(brief)
    print("=" * 72)


if __name__ == "__main__":
    main()
