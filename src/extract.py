"""LLM-based extraction: customer review -> structured quality signal.

Uses OpenAI Structured Outputs (json_schema response format) for strict schema
enforcement. The schema mirrors the eval-set label schema exactly, so predictions
are directly comparable to ground truth without parsing tricks.

Two modes:
  --review-ids r1,r2,r3      extract only specific review IDs (for eval iteration)
  --all                       extract all un-extracted reviews (production run)

Outputs land in data/signals.db. Idempotent: re-running won't redo a review unless
--force is passed.

Usage:
    export OPENAI_API_KEY=sk-...
    python -m src.extract --review-ids R1,R2,R3            # targeted
    python -m src.extract --all                            # full corpus
    python -m src.extract --eval-set                       # only the 50 labeled reviews
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from openai import OpenAI

from src.storage import DB_PATH, get_conn

SIGNALS_DB_PATH = Path(__file__).parent.parent / "data" / "signals.db"
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "extraction.txt"
EVAL_PATH = Path(__file__).parent.parent / "data" / "eval_set.jsonl"

MODEL = "gpt-4o-mini-2024-07-18"   # fast + cheap + structured-outputs native
MAX_WORKERS = 3                     # tuned for Tier 1 200K TPM limit on gpt-4o-mini

# JSON schema for the response_format. Must mirror eval_set labels exactly.
EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "has_quality_complaint": {"type": "boolean"},
        "defect_category": {
            "type": "string",
            "enum": [
                "mechanical_failure",
                "performance_degradation",
                "design_flaw",
                "durability",
                "missing_parts",
                "safety_concern",
                "none",
            ],
        },
        "component_mentioned": {"type": ["string", "null"]},
        "severity": {
            "type": "string",
            "enum": ["critical", "high", "medium", "low"],
        },
        "time_to_failure_days": {"type": ["integer", "null"]},
        "sentiment": {
            "type": "string",
            "enum": ["positive", "neutral", "negative"],
        },
        "would_recommend": {"type": ["boolean", "null"]},
        "summary": {"type": "string"},
    },
    "required": [
        "has_quality_complaint",
        "defect_category",
        "component_mentioned",
        "severity",
        "time_to_failure_days",
        "sentiment",
        "would_recommend",
        "summary",
    ],
}

SIGNALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    review_id              TEXT PRIMARY KEY,
    has_quality_complaint  INTEGER,            -- 0/1
    defect_category        TEXT,
    component_mentioned    TEXT,
    severity               TEXT,
    time_to_failure_days   INTEGER,
    sentiment              TEXT,
    would_recommend        INTEGER,            -- 0/1 or NULL
    summary                TEXT,
    model                  TEXT,               -- which model version produced this
    extracted_at           TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_defect ON signals(defect_category);
CREATE INDEX IF NOT EXISTS idx_signals_severity ON signals(severity);
"""

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reviewlens.extract")


def init_signals_db() -> None:
    SIGNALS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SIGNALS_DB_PATH) as conn:
        conn.executescript(SIGNALS_SCHEMA)


def load_prompt() -> str:
    return PROMPT_PATH.read_text()


def fetch_reviews(review_ids: list[str] | None = None, all_unextracted: bool = False) -> list[dict]:
    """Pull reviews to extract. Joins against signals.db to skip already-done ones."""
    with get_conn() as conn:
        if review_ids:
            placeholders = ",".join("?" * len(review_ids))
            rows = conn.execute(
                f"SELECT review_id, asin, rating, review_text FROM reviews "
                f"WHERE review_id IN ({placeholders}) AND review_text IS NOT NULL",
                review_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT review_id, asin, rating, review_text FROM reviews "
                "WHERE review_text IS NOT NULL AND length(review_text) > 10"
            ).fetchall()
    rows = [dict(r) for r in rows]

    if all_unextracted:
        with sqlite3.connect(SIGNALS_DB_PATH) as conn:
            done = {r[0] for r in conn.execute("SELECT review_id FROM signals")}
        rows = [r for r in rows if r["review_id"] not in done]

    return rows


def load_eval_review_ids() -> list[str]:
    if not EVAL_PATH.exists():
        sys.exit(f"No eval set found at {EVAL_PATH}. Run `python -m src.label` first.")
    ids = []
    with EVAL_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                ids.append(json.loads(line)["review_id"])
    return ids


def extract_one(client: OpenAI, system_prompt: str, review: dict) -> dict | None:
    """Call the model on one review. Returns the parsed signal dict or None on failure.

    Handles 429 rate limits with exponential backoff. The OpenAI SDK has its own
    auto-retry, but for token-per-minute limits the right move is to actually sleep
    long enough for the TPM window to roll over.
    """
    user_msg = (
        f"Review (rating: {review['rating']}/5):\n"
        f"{review['review_text']}\n\n"
        f"Return only the JSON object."
    )
    max_attempts = 5
    backoff = 5  # seconds; doubles each retry

    for attempt in range(1, max_attempts + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "quality_signal",
                        "strict": True,
                        "schema": EXTRACTION_SCHEMA,
                    },
                },
                temperature=0,
            )
            signal = json.loads(resp.choices[0].message.content)
            return signal
        except Exception as exc:
            # 429 / rate-limit errors: sleep and retry. Anything else: give up immediately.
            err_str = str(exc)
            is_rate_limit = "429" in err_str or "rate_limit" in err_str.lower()
            if is_rate_limit and attempt < max_attempts:
                log.warning("    rate limited on %s, sleeping %ds (attempt %d/%d)",
                            review["review_id"], backoff, attempt, max_attempts)
                time.sleep(backoff)
                backoff *= 2
                continue
            log.error("    failed on %s after %d attempts: %s",
                      review["review_id"], attempt, exc)
            return None
    return None


def save_signal(review_id: str, signal: dict) -> None:
    with sqlite3.connect(SIGNALS_DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO signals (
                review_id, has_quality_complaint, defect_category, component_mentioned,
                severity, time_to_failure_days, sentiment, would_recommend, summary,
                model, extracted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                review_id,
                1 if signal["has_quality_complaint"] else 0,
                signal["defect_category"],
                signal["component_mentioned"],
                signal["severity"],
                signal["time_to_failure_days"],
                signal["sentiment"],
                None if signal["would_recommend"] is None else (1 if signal["would_recommend"] else 0),
                signal["summary"],
                MODEL,
            ),
        )


def run(reviews: list[dict]) -> None:
    if not reviews:
        log.info("Nothing to do.")
        return

    client = OpenAI()
    system_prompt = load_prompt()
    log.info("Extracting %d reviews with %s, %d workers", len(reviews), MODEL, MAX_WORKERS)

    done = 0
    failed = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(extract_one, client, system_prompt, r): r for r in reviews}
        for future in as_completed(futures):
            review = futures[future]
            signal = future.result()
            if signal is None:
                failed += 1
                continue
            save_signal(review["review_id"], signal)
            done += 1
            if done % 25 == 0:
                rate = done / (time.time() - t0)
                log.info("  %d/%d  (%.1f/s)", done, len(reviews), rate)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info("Done. extracted=%d  failed=%d  elapsed=%.1fs", done, failed, elapsed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-ids", help="comma-separated review IDs")
    parser.add_argument("--all", action="store_true", help="extract all un-extracted reviews")
    parser.add_argument("--eval-set", action="store_true", help="extract only labeled eval reviews")
    parser.add_argument("--force", action="store_true", help="re-extract even if already done")
    args = parser.parse_args()

    if not any([args.review_ids, args.all, args.eval_set]):
        sys.exit("Pick one of: --review-ids, --all, --eval-set")

    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY in the environment.")

    init_signals_db()

    if args.eval_set:
        ids = load_eval_review_ids()
        log.info("Extracting %d eval-set reviews", len(ids))
        reviews = fetch_reviews(review_ids=ids)
    elif args.review_ids:
        ids = [s.strip() for s in args.review_ids.split(",") if s.strip()]
        reviews = fetch_reviews(review_ids=ids)
    else:
        reviews = fetch_reviews(all_unextracted=not args.force)

    if args.force and not args.all:
        # Targeted re-extract: wipe just those rows so insert-or-replace overwrites cleanly
        with sqlite3.connect(SIGNALS_DB_PATH) as conn:
            placeholders = ",".join("?" * len(reviews))
            conn.execute(
                f"DELETE FROM signals WHERE review_id IN ({placeholders})",
                [r["review_id"] for r in reviews],
            )

    run(reviews)


if __name__ == "__main__":
    main()