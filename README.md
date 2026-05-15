# ReviewLens

> An AI-powered quality intelligence pipeline that surfaces emerging product defect signals from customer reviews — built as a working prototype for what an Applied AI co-op project at a consumer products company might look like.

**Live dashboard:** [link after Streamlit Cloud deploy]
**Demo video:** [2-min Loom link]

---

## The Problem

Consumer product companies sell across hundreds of SKUs and dozens of categories. Every week, thousands of customer reviews surface real signals about product quality — defective components, design flaws, durability issues, emerging complaint patterns — but most of that signal never reaches the product team in a structured, prioritized form.

A product manager doesn't need another sentiment score. They need a Monday-morning brief that says: *"these three SKUs need investigation this week, here's why, here's the evidence."*

That's what ReviewLens does.

---

## The Finding

[*FILL IN AFTER DAY 4 — this is the headline of the project*]

> Example placeholder: Across 1,500 reviews of 10 Shark and Ninja SKUs, ReviewLens flagged the [SKU NAME] as having a [3x category-baseline] complaint rate concentrated in the motor assembly, with 60% of complaints occurring within the first 90 days of ownership. The signal converges across three dimensions — complaint volume, severity, and time-to-failure — making it a high-priority candidate for product team investigation.

---

## Approach

1. **Ingest** — Pulled 1,500+ reviews across 10 SKUs (5 Shark, 5 Ninja) using Apify's Amazon scraper. Stored in SQLite with brand, category, rating, date, and verification fields.

2. **Extract** — Built an LLM extraction layer using OpenAI structured outputs that converts free-text reviews into a strict JSON schema: defect category, component mentioned, severity, time-to-failure, sentiment, and a one-sentence summary.

3. **Evaluate** — Hand-labeled a 50-review ground truth set before scaling. Iterated the extraction prompt against measured per-field accuracy, not vibes. Final accuracy: [FILL IN].

4. **Aggregate** — Rolled up to SKU level with a multi-signal risk score weighted across complaint rate, severity, recency trend, and time-to-failure. Single signals are noise; convergence is signal.

5. **Surface** — Streamlit dashboard with three views: executive summary, SKU deep dive, and methodology. Auto-generated executive brief synthesizes the top-priority SKUs into a three-sentence summary a non-technical stakeholder can act on.

---

## Evaluation

Built a 50-review hand-labeled eval set before running extraction at scale. Stratified across SKUs and ratings to ensure coverage of both negative and positive reviews.

| Field | Metric | Result |
|---|---|---|
| `has_quality_complaint` | exact match | [FILL IN]% |
| `defect_category` | exact match | [FILL IN]% |
| `severity` | within one | [FILL IN]% |
| `component_mentioned` | semantic match | [FILL IN]% |
| `sentiment` | exact match | [FILL IN]% |

Failure mode analysis is documented in `notebooks/insight_exploration.ipynb`.

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
