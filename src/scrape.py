"""Apify-based Amazon review scraper.

Strategy: per SKU, make four Actor calls — one broad call for context, then three
targeted calls for 1★, 2★, and 3★ reviews. The single-rating filter is the only
star-filtering parameter on this Actor that reliably works; the multi-star `stars`
array silently collapses to mostly 5★ output (verified by empirical run).

We deliberately oversample low-star reviews because that's where defect signal
lives. The dashboard later normalizes back to true complaint rate by weighting
each star bucket appropriately.

Cost: ~40 actor calls + ~2000 reviews => ~$2.

Usage:
    export APIFY_TOKEN=apify_api_...
    python -m src.scrape
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any

from apify_client import ApifyClient

from src.skus import get_skus_with_asins
from src.storage import init_db, insert_reviews, review_count_by_sku, upsert_skus

ACTOR_ID = "web_wanderer/amazon-reviews-extractor"

# Base config used for every call. Per-call overrides specify the rating filter.
BASE_CONFIG: dict[str, Any] = {
    "sort": "recent",
    "include_variants": True,
    "personal_data": False,
    "region": "amazon.com",
    "language": "en",
    "limit": 10,                 # max 10 pages, lets Amazon's own cap kick in
    # Notably NOT setting avp_reviews: many real complaints come from unverified
    # purchasers (gifts, returns, third-party sellers). Filtering here loses signal.
}

# Per-rating calls. Oversample 1/2/3 ★ because that's where complaints live.
# "all" gives us a broad sample that's mostly 4-5★, used for true-rate normalization.
RATING_CALLS = [
    ("all", {**BASE_CONFIG, "rating": "all"}),
    ("one_star", {**BASE_CONFIG, "rating": "one_star"}),
    ("two_star", {**BASE_CONFIG, "rating": "two_star"}),
    ("three_star", {**BASE_CONFIG, "rating": "three_star"}),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("reviewlens.scrape")


def normalize_review(raw: dict[str, Any], asin: str) -> dict[str, Any]:
    """Map an Apify output row into our reviews-table column schema."""
    return {
        "review_id": raw.get("reviewId"),
        "asin": raw.get("productAsin") or asin,
        "rating": int(raw["rating"]) if raw.get("rating") is not None else None,
        "review_date": raw.get("reviewDate"),
        "review_title": raw.get("reviewTitle"),
        "review_text": raw.get("reviewText"),
        "verified_purchase": 1 if raw.get("verifiedPurchase") else 0,
        "helpful_vote_count": raw.get("helpfulVoteCount") or 0,
        "variant_asin": raw.get("variantAsin"),
        "country": raw.get("country"),
        "language": raw.get("language"),
        "scraped_at": raw.get("scrapedAt"),
    }


def scrape_one_call(
    client: ApifyClient, asin: str, label: str, config: dict[str, Any]
) -> list[dict]:
    """Run one Actor call for a single SKU + rating filter."""
    run_input = {**config, "products": [asin]}
    run = client.actor(ACTOR_ID).call(run_input=run_input)

    if run is None or run.get("status") != "SUCCEEDED":
        log.error("    [%s] run did not succeed: %s", label, run)
        return []

    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    rows = [normalize_review(i, asin) for i in items if i.get("reviewId")]
    log.info("    [%s] %d raw -> %d valid", label, len(items), len(rows))
    return rows


def scrape_sku(client: ApifyClient, asin: str, display_name: str) -> int:
    """Run all rating calls for one SKU. Returns count of new rows inserted."""
    log.info("Scraping %s (%s)...", display_name, asin)
    all_rows: list[dict] = []
    for label, config in RATING_CALLS:
        try:
            rows = scrape_one_call(client, asin, label, config)
            all_rows.extend(rows)
        except Exception as exc:
            log.exception("    [%s] failed: %s", label, exc)
        time.sleep(0.5)  # tiny gap between calls
    # Insert at the end — insert_reviews uses INSERT OR IGNORE so duplicates
    # (same review appearing in both the "all" call and a star-specific call)
    # are silently deduplicated by review_id.
    return insert_reviews(all_rows)


def main() -> None:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("Set APIFY_TOKEN in the environment before running.")

    init_db()
    skus = get_skus_with_asins()
    upsert_skus(skus)
    log.info("Loaded catalog: %d SKUs", len(skus))

    client = ApifyClient(token)
    total_inserted = 0
    failures: list[str] = []

    for sku in skus:
        try:
            inserted = scrape_sku(client, sku["asin"], sku["display_name"])
            log.info("  => %d new reviews for %s", inserted, sku["display_name"])
            total_inserted += inserted
        except Exception as exc:
            log.exception("Failed on %s: %s", sku["display_name"], exc)
            failures.append(sku["display_name"])

    log.info("=" * 64)
    log.info("Done. Inserted %d new reviews total.", total_inserted)
    if failures:
        log.warning("Failed SKUs (retry individually): %s", failures)

    log.info("Counts per SKU:")
    for row in review_count_by_sku():
        log.info(
            "  %-30s %-18s n=%-5d avg=%.2f",
            row["display_name"],
            row["category"],
            row["n_reviews"],
            row["avg_rating"] or 0,
        )


if __name__ == "__main__":
    main()