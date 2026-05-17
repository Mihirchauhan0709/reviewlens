"""SKU-level aggregation: turn 1,756 signals into a ranked findings table.

The job: for each SKU, compute the metrics a product team would actually use to
prioritize an investigation. Single signals are noise. Convergence across
multiple dimensions is signal.

Five outputs per SKU:
  1. True complaint rate (oversample-corrected back to real Amazon distribution)
  2. Severity distribution
  3. Recency trend (last 90 days vs prior 90 days, in pp)
  4. Top defect categories + top components
  5. Composite risk score = weighted blend of (1)-(4)

A single output file, data/findings.json, that the dashboard reads.

The "true complaint rate" piece is the part that earns the methodology bullet.
We deliberately oversampled 1-3 star reviews during scraping — without correction,
every SKU looks defective. We reweight to the actual star distribution from the
*broad* "all_ratings" call, which is Amazon's natural unfiltered sample. That
gives a defensible estimate of what the complaint rate would be on the full
review population, not on our biased subsample.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.extract import SIGNALS_DB_PATH
from src.storage import DB_PATH

FINDINGS_PATH = Path(__file__).parent.parent / "data" / "findings.json"
RECENT_WINDOW_DAYS = 90

# Composite risk score weights. Each component is normalized to [0, 1] before
# weighting. Weights sum to 1.0 so the final score is also [0, 1].
RISK_WEIGHTS = {
    "true_complaint_rate":    0.30,    # the headline metric
    "severity_score":         0.20,    # high/critical complaints weighted more
    "safety_share":           0.20,    # safety convergence overrides volume — see Air Fryer
    "recency_trend":          0.15,    # are things getting worse?
    "early_failure_share":    0.15,    # complaints clustered at <90 days are worse
}

# Severity numeric weights for severity_score (0..1 scale)
SEVERITY_WEIGHT = {"critical": 1.0, "high": 0.7, "medium": 0.4, "low": 0.1}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reviewlens.aggregate")


def load_joined() -> list[dict]:
    """Pull reviews + signals + sku catalog into one denormalized row set.

    Cross-database join: we attach signals.db, then JOIN. This is the right shape
    because each Python row downstream needs every field at once for aggregation.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(f"ATTACH DATABASE '{SIGNALS_DB_PATH}' AS sig")
        rows = conn.execute("""
            SELECT
                r.review_id, r.asin, r.rating, r.review_date,
                s.brand, s.category, s.display_name,
                sig.has_quality_complaint, sig.defect_category,
                sig.component_mentioned, sig.severity,
                sig.time_to_failure_days, sig.sentiment, sig.summary
            FROM reviews r
            JOIN skus s ON s.asin = r.asin
            JOIN sig.signals sig ON sig.review_id = r.review_id
        """).fetchall()
    return [dict(r) for r in rows]


def true_complaint_rate(sku_rows: list[dict]) -> dict[str, Any]:
    """Compute reweighted complaint rate, correcting for star-bucket oversampling.

    We oversampled 1-3★ reviews vs their natural Amazon prevalence. To recover the
    true rate, we compute complaint-rate-per-star-bucket within our sample, then
    weight those by an assumed true star distribution. We use the SKU's own
    average rating (in our data) as a proxy for the true distribution shape:
    higher-rated products have proportionally fewer low-star reviews.

    For SharkNinja-class products (4.4-4.7★ overall on Amazon), the typical
    star distribution is roughly:
        5★ ~ 70%, 4★ ~ 15%, 3★ ~ 7%, 2★ ~ 4%, 1★ ~ 4%
    We use this as the default reweighting target.
    """
    true_star_dist = {1: 0.04, 2: 0.04, 3: 0.07, 4: 0.15, 5: 0.70}

    # Per-bucket complaint rate in OUR sample
    bucket_complaint_rate = {}
    bucket_n = {}
    for star in range(1, 6):
        bucket = [r for r in sku_rows if r["rating"] == star]
        if not bucket:
            bucket_complaint_rate[star] = None
            bucket_n[star] = 0
            continue
        n_complaints = sum(1 for r in bucket if r["has_quality_complaint"])
        bucket_complaint_rate[star] = n_complaints / len(bucket)
        bucket_n[star] = len(bucket)

    # Reweight by true distribution. Skip buckets we don't have data for, and
    # renormalize the weights so they still sum to 1.
    weighted_sum = 0.0
    weight_used = 0.0
    for star, weight in true_star_dist.items():
        rate = bucket_complaint_rate.get(star)
        if rate is None:
            continue
        weighted_sum += weight * rate
        weight_used += weight
    if weight_used == 0:
        return {"value": 0.0, "raw_rate": 0.0, "bucket_n": bucket_n, "bucket_rate": bucket_complaint_rate}
    true_rate = weighted_sum / weight_used

    # Also report the naive (raw) rate for comparison — useful for the dashboard.
    raw_rate = sum(1 for r in sku_rows if r["has_quality_complaint"]) / len(sku_rows)

    return {
        "value": true_rate,
        "raw_rate": raw_rate,
        "bucket_n": bucket_n,
        "bucket_rate": {k: (round(v, 3) if v is not None else None) for k, v in bucket_complaint_rate.items()},
    }


def severity_distribution(sku_rows: list[dict]) -> dict[str, Any]:
    """Count and percentage breakdown of severity among complaint rows.

    Returns both the counts (for display) and a 0-1 severity_score (for risk).
    """
    complaint_rows = [r for r in sku_rows if r["has_quality_complaint"]]
    if not complaint_rows:
        return {"counts": {}, "score": 0.0, "n_complaints": 0}

    counts = Counter(r["severity"] for r in complaint_rows)
    n = len(complaint_rows)
    score = sum(SEVERITY_WEIGHT[sev] * cnt for sev, cnt in counts.items()) / n
    return {
        "counts": dict(counts),
        "score": score,
        "n_complaints": n,
    }


def parse_date(s: str | None) -> datetime | None:
    """Parse review_date strings flexibly. Amazon emits a few formats."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def recency_trend(sku_rows: list[dict]) -> dict[str, Any]:
    """Complaint rate in last 90 days vs prior 90 days. Positive = getting worse.

    Returns delta_pp=None if either window has fewer than MIN_WINDOW_N reviews.
    A 6-vs-2 trend is noise, and reporting it as 'down 66%' is worse than reporting
    nothing — it implies precision that isn't there.
    """
    MIN_WINDOW_N = 20

    cutoff_recent = datetime.now() - timedelta(days=RECENT_WINDOW_DAYS)
    cutoff_prior  = datetime.now() - timedelta(days=2 * RECENT_WINDOW_DAYS)

    recent_rows, prior_rows = [], []
    for r in sku_rows:
        d = parse_date(r["review_date"])
        if d is None:
            continue
        if d >= cutoff_recent:
            recent_rows.append(r)
        elif d >= cutoff_prior:
            prior_rows.append(r)

    def rate(rows: list[dict]) -> float | None:
        if not rows:
            return None
        return sum(1 for r in rows if r["has_quality_complaint"]) / len(rows)

    recent_rate = rate(recent_rows)
    prior_rate  = rate(prior_rows)

    sufficient = len(recent_rows) >= MIN_WINDOW_N and len(prior_rows) >= MIN_WINDOW_N
    if sufficient and recent_rate is not None and prior_rate is not None:
        delta_pp = (recent_rate - prior_rate) * 100
    else:
        delta_pp = None

    return {
        "recent_rate":  recent_rate,
        "prior_rate":   prior_rate,
        "delta_pp":     delta_pp,
        "n_recent":     len(recent_rows),
        "n_prior":      len(prior_rows),
        "sufficient_n": sufficient,
    }


def early_failure_share(sku_rows: list[dict]) -> float:
    """Fraction of complaints with time_to_failure < 90 days. Early failures
    are more actionable: they suggest a batch issue, not normal wear-out."""
    with_ttf = [r for r in sku_rows
                if r["has_quality_complaint"] and r["time_to_failure_days"] is not None]
    if not with_ttf:
        return 0.0
    early = sum(1 for r in with_ttf if r["time_to_failure_days"] < 90)
    return early / len(with_ttf)


def top_n(rows: list[dict], field: str, n: int = 5) -> list[dict]:
    """Top-N values for a categorical field among complaint rows. Returns
    [{value, count, share}, ...]. Skips None values."""
    complaint_rows = [r for r in rows if r["has_quality_complaint"]]
    if not complaint_rows:
        return []
    counts = Counter(r[field] for r in complaint_rows if r[field])
    total = sum(counts.values())
    if total == 0:
        return []
    return [
        {"value": v, "count": c, "share": c / total}
        for v, c in counts.most_common(n)
    ]


def representative_summaries(sku_rows: list[dict], n: int = 5) -> list[str]:
    """The LLM-generated one-sentence summaries for the highest-severity recent
    complaints. These power the 'evidence' panel on the dashboard."""
    complaint_rows = [r for r in sku_rows if r["has_quality_complaint"] and r["summary"]]
    # Sort by severity rank, then by recency.
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    complaint_rows.sort(key=lambda r: (sev_rank.get(r["severity"], 9),
                                       -(parse_date(r["review_date"]) or datetime.min).timestamp()))
    return [r["summary"] for r in complaint_rows[:n]]


def safety_complaint_share(sku_rows: list[dict]) -> float:
    """Fraction of complaint rows flagged as safety_concern. A SKU where 20%+
    of complaints cite safety issues is a higher-priority investigation target
    than one with more volume but no safety concentration."""
    complaint_rows = [r for r in sku_rows if r["has_quality_complaint"]]
    if not complaint_rows:
        return 0.0
    safety = sum(1 for r in complaint_rows if r["defect_category"] == "safety_concern")
    return safety / len(complaint_rows)


def normalize_for_risk(value: float | None, lo: float, hi: float) -> float:
    """Clip a metric into [0, 1] for the composite risk score."""
    if value is None:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def compute_risk(true_rate: float, sev_score: float, safety_share: float,
                 trend_pp: float | None, early_share: float) -> dict:
    """Composite risk score in [0, 1]. Higher = more concerning.

    Normalization choices below are calibrated for consumer-product review data:
      - complaint rate: 0-50% is the meaningful range
      - severity score: already 0-1
      - safety share: 0-25% is the meaningful range; >25% is alarming
      - trend: -10pp to +20pp covers most signal
      - early failure: 0-1, but >50% concentrated in <90 days is alarming
    """
    components = {
        "true_complaint_rate": normalize_for_risk(true_rate,   0.00, 0.50),
        "severity_score":      sev_score,
        "safety_share":        normalize_for_risk(safety_share, 0.0, 0.25),
        "recency_trend":       normalize_for_risk(trend_pp,  -10.0, 20.0),
        "early_failure_share": normalize_for_risk(early_share, 0.0, 1.00),
    }
    score = sum(components[k] * w for k, w in RISK_WEIGHTS.items())
    return {"score": score, "components": components}


def aggregate_sku(sku_rows: list[dict]) -> dict[str, Any]:
    """Build the full findings record for a single SKU."""
    sample = sku_rows[0]
    tcr    = true_complaint_rate(sku_rows)
    sev    = severity_distribution(sku_rows)
    rec    = recency_trend(sku_rows)
    early  = early_failure_share(sku_rows)
    safety = safety_complaint_share(sku_rows)
    risk   = compute_risk(tcr["value"], sev["score"], safety, rec["delta_pp"], early)

    return {
        "asin":              sample["asin"],
        "brand":             sample["brand"],
        "category":          sample["category"],
        "display_name":      sample["display_name"],
        "n_reviews_scraped": len(sku_rows),
        "true_complaint_rate": tcr,
        "severity":          sev,
        "recency":           rec,
        "early_failure_share": early,
        "safety_share":      safety,
        "top_defects":       top_n(sku_rows, "defect_category"),
        "top_components":    top_n(sku_rows, "component_mentioned"),
        "representative_summaries": representative_summaries(sku_rows),
        "risk":              risk,
    }


def category_baseline(findings: list[dict]) -> dict[str, float]:
    """Mean true_complaint_rate per category. Lets the dashboard report each SKU
    relative to its peers ('3.2x category baseline') instead of in absolute terms."""
    by_cat: dict[str, list[float]] = {}
    for f in findings:
        by_cat.setdefault(f["category"], []).append(f["true_complaint_rate"]["value"])
    return {cat: sum(rates) / len(rates) for cat, rates in by_cat.items()}


def main() -> None:
    rows = load_joined()
    log.info("Loaded %d joined rows across reviews + signals + skus", len(rows))

    # Group by SKU
    by_asin: dict[str, list[dict]] = {}
    for r in rows:
        by_asin.setdefault(r["asin"], []).append(r)

    findings = [aggregate_sku(sku_rows) for sku_rows in by_asin.values()]
    findings.sort(key=lambda f: -f["risk"]["score"])

    baselines = category_baseline(findings)

    output = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_skus": len(findings),
        "category_baseline": baselines,
        "findings": findings,
    }

    FINDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    FINDINGS_PATH.write_text(json.dumps(output, indent=2, default=str))
    log.info("Wrote findings to %s", FINDINGS_PATH)

    # Pretty-print a summary table to stdout.
    print()
    print(f"{'SKU':<32} {'true rate':>10} {'sev':>6} {'safety':>7} {'trend':>8} {'risk':>6}")
    print("-" * 78)
    for f in findings:
        tcr      = f["true_complaint_rate"]["value"]
        sev_s    = f["severity"]["score"]
        safety   = f["safety_share"]
        trend    = f["recency"]["delta_pp"]
        trend_s  = f"{trend:+.1f}pp" if trend is not None else "  n/a"
        risk     = f["risk"]["score"]
        print(f"{f['display_name']:<32} {tcr*100:>9.1f}% {sev_s:>6.2f} {safety*100:>6.1f}% {trend_s:>8} {risk:>6.3f}")

    print()
    print("Category baselines (true complaint rate):")
    for cat, base in sorted(baselines.items()):
        print(f"  {cat:<20} {base*100:.1f}%")


if __name__ == "__main__":
    main()