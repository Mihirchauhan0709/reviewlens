"""Apify-based Amazon review scraper.

Calls web_wanderer/amazon-reviews-extractor once per SKU, normalizes the output
to our review schema, and inserts into SQLite. Each SKU is independent so a
single failure doesn't kill the whole run.

Usage:
    export APIFY_TOKEN=apify_api_...
    python -m src.scrape

Cost expectation: ~$0.001 per review + ~$0.00002 per Actor start.
At 500 reviews/SKU * 10 SKUs = 5,000 reviews max => ~$5.
At 150 reviews/SKU average = 1,500 reviews => ~$1.50.
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

# Target ~150 reviews per SKU, balanced across star ratings.
#
# Why per-star stratification matters: Amazon ranks "helpful" or "recent" reviews
# globally, and on a highly-rated SKU that means 95% of returned reviews are 5-star.
# That under-samples exactly the reviews the defect pipeline needs.
#
# By requesting each star bucket separately and capping pages per bucket, we get
# guaranteed coverage of 1- and 2-star reviews even on a 4.8-star product. Yield
# is approximate: SKUs with low complaint volume will return less than the ceiling
# for low-star buckets, which is fine and expected.
#
# Per-bucket ceiling = limit * ~10 reviews/page. limit=3 => ~30/bucket => ~150/SKU.
DEFAULT_SCRAPE_CONFIG: dict[str, Any] = {
    "stars": ["five_star", "four_star", "three_star", "two_star", "one_star"],
    "limit": 3,                  # ~30 reviews per star bucket, ~150 per SKU
    "sort": "recent",            # surface trends, not historical noise
    "avp_reviews": True,         # verified purchase only
    "include_variants": True,    # roll in variant reviews so the SKU view is complete
    "personal_data": False,      # never scrape personal data
    "region": "amazon.com",
    "language": "en",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("reviewlens.scrape")


def normalize_review(raw: dict[str, Any], asin: str) -> dict[str, Any]:
    """Map an Apify output row into our reviews-table column schema.

    The Apify output is generous (aspects, images, profile data, etc.). We keep
    only the fields we'll actually use, and we normalize types: booleans become
    0/1 for SQLite, missing fields become None.
    """
    return {
        "review_id": raw.get("reviewId"),
        # productAsin from the scraper is the canonical ASIN; fall back to the
        # SKU's catalog ASIN if missing.
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


def scrape_sku(client: ApifyClient, asin: str, display_name: str) -> list[dict[str, Any]]:
    """Run one Actor call for a single SKU. Returns normalized review rows."""
    log.info("Scraping %s (%s)...", display_name, asin)

    run_input = {**DEFAULT_SCRAPE_CONFIG, "products": [asin]}
    run = client.actor(ACTOR_ID).call(run_input=run_input)

    if run is None or run.get("status") != "SUCCEEDED":
        log.error("Run for %s did not succeed: %s", asin, run)
        return []

    dataset_id = run["defaultDatasetId"]
    items = list(client.dataset(dataset_id).iterate_items())
    log.info("  -> %d raw items returned", len(items))

    # Filter out items missing the bare minimum we need.
    rows = [normalize_review(item, asin) for item in items if item.get("reviewId")]
    log.info("  -> %d valid rows after normalization", len(rows))
    return rows


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
            rows = scrape_sku(client, sku["asin"], sku["display_name"])
            inserted = insert_reviews(rows)
            log.info("  -> inserted %d new reviews", inserted)
            total_inserted += inserted
        except Exception as exc:
            log.exception("Failed on %s: %s", sku["display_name"], exc)
            failures.append(sku["display_name"])
        # Tiny pause between runs; the Actor handles its own rate limiting but
        # this keeps the Apify dashboard readable if a human is watching.
        time.sleep(1)

    log.info("=" * 60)
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
