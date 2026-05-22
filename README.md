# ReviewLens

> An AI-powered quality intelligence pipeline that surfaces emerging product defect signals from customer reviews — built as a working prototype for what an Applied AI co-op project at a consumer products company might look like.

**Live dashboard:** https://mihir-reviewlens.streamlit.app/

---

## The Problem

Consumer product companies sell across hundreds of SKUs and dozens of categories. Every week, thousands of customer reviews surface real signals about product quality — defective components, design flaws, durability issues, emerging complaint patterns — but most of that signal never reaches the product team in a structured, prioritized form.

A product manager doesn't need another sentiment score. They need a Monday-morning brief that says: *"these three SKUs need investigation this week, here's why, here's the evidence."*

That's what ReviewLens does.

---

## The Finding

Across **1,780 customer reviews of 10 Shark and Ninja SKUs**, ReviewLens flagged the **Ninja Creami** as the highest-risk SKU in the dataset.

**35% of its complaints — 40 reviews — were flagged as safety concerns**, the highest concentration across all SKUs analyzed. Within those, the signal converges sharply on the **blade assembly**: 26% of complaints name the blade specifically, with a consistent thermal failure pattern described across reviews — smoke, melted lids, burnt components — often within the first month of ownership.

Three independent dimensions converge: defect category (safety_concern), component mention (blade/lid), and time-to-failure (early-life). Single signals are noise; convergence is signal. With access to internal data, the first move would be to cross-reference these reviews against manufacturing batch IDs and ship dates to identify whether this is a production-batch issue or a fundamental design problem with the blade-assembly thermal tolerances.

---

## Approach

1. **Ingest** — Pulled 1,780 reviews across 10 SKUs (5 Shark, 5 Ninja) using Apify's Amazon scraper. Star-stratified sampling: separate API calls per rating bucket to force coverage of 1-3★ reviews, which is where defect signal lives. Stored in SQLite with brand, category, rating, date, and verification fields.

2. **Extract** — Built an LLM extraction layer using OpenAI structured outputs (`gpt-4o-mini`) that converts free-text reviews into a strict JSON schema: defect category, component mentioned, severity, time-to-failure, sentiment, and a one-sentence summary. 1,756 reviews extracted with parallel workers + rate-limit-aware retry.

3. **Evaluate** — Hand-labeled a 50-review ground truth set before scaling. Iterated the extraction prompt against measured per-field accuracy, not vibes. Headline result: severity within-one at 98%, has-quality-complaint at 82%, defect-category exact at 54% (concentrated weakness on the inherently-ambiguous `design_flaw` category — a finding in itself).

4. **Aggregate** — Rolled up to SKU level with a multi-signal risk score weighted across complaint rate (30%), severity (20%), safety-complaint share (20%), recency trend (15%), and early-failure clustering (15%). Complaint rates reweighted to reverse the star-stratified oversampling and recover a defensible true-rate estimate. Single signals are noise; convergence is signal.

5. **Surface** — Streamlit dashboard with three views: executive summary, SKU deep dive, and methodology. Auto-generated executive brief synthesizes the top-priority SKUs into a three-sentence summary a non-technical stakeholder can act on.

---

## Evaluation

Built a 50-review hand-labeled eval set before running extraction at scale. Stratified across SKUs and ratings to ensure coverage of both negative and positive reviews.

| Field | Metric | Result |
|---|---|---|
| `has_quality_complaint` | exact match | 82% |
| `defect_category` | exact match | 54% |
| `severity` | exact match | 68% |
| `severity` | within-one | 98% |
| `component_mentioned` | fuzzy match | 74% |
| `sentiment` | exact match | 86% |

**The 98% severity within-one is the headline result** — when the model misjudges severity, it's almost always off by one notch (medium vs. high), never catastrophic. That's the model demonstrating understanding of the ordinal scale.

**The 54% defect_category accuracy is the concentrated weakness.** Confusion is dominated by one category (`design_flaw`), which overlaps with `durability` for time-of-failure cases and with `performance_degradation` for poor-outcome cases. The schema needs to be split into `design_inconvenience` and `structural_design_issue` in v2 to be reliably learnable.

---

## What I'd Build With Internal Data

Working with public review data is the constrained version of this problem. With access to internal data, the pipeline gets meaningfully sharper:

- **Warranty claims** join in directly — converts "complaint rate" into "failure rate per units sold"
- **Manufacturing batch IDs** let you cluster defects by production run, not just SKU
- **Support ticket text** is a much higher-signal corpus than public reviews (customers are more specific when they want help)
- **Return reason codes** add a structured ground-truth signal the extraction layer can be evaluated against

The same pipeline architecture supports all of these — the schema gets richer, the convergence score gets more dimensions, and the eval set grows.

---

## Tech Stack

Python, OpenAI structured outputs, Apify Amazon scraper, SQLite, pandas, Streamlit. Deployed on Streamlit Cloud.

---

## Repo Structure

```
reviewlens/
├── README.md                    # this file
├── requirements.txt
├── data/
│   ├── reviews.db               # scraped reviews
│   ├── signals.db               # extracted signals
│   └── eval_set.jsonl           # hand-labeled ground truth
├── src/
│   ├── scrape.py                # Apify ingestion
│   ├── extract.py               # LLM extraction with structured outputs
│   ├── eval.py                  # accuracy measurement vs. eval_set
│   ├── aggregate.py             # SKU-level rollups + risk score
│   └── brief.py                 # LLM executive summary generation
├── prompts/
│   ├── extraction.txt
│   └── executive_brief.txt
├── app.py                       # Streamlit dashboard
└── notebooks/
    └── insight_exploration.ipynb
```

---

## Build Time

4 days, solo. Built specifically as preparation for a SharkNinja Applied AI & Analytics co-op interview, as a concrete answer to: *"what would a Jailbreak project actually look like?"*
