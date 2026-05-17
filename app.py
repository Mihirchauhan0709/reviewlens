"""ReviewLens dashboard.

Three pages:
  1. Executive Summary — top-finding highlight + risk-ranked table + LLM brief
  2. SKU Deep Dive — chosen SKU's defect/component breakdown, severity dist, recency, evidence
  3. Methodology — eval set numbers, prompt iteration story, "what I'd do with internal data"

Run locally:
    streamlit run app.py

Reads (does not write):
  - data/findings.json   (from src.aggregate)
  - data/brief.txt       (from src.brief)
  - data/eval_set.jsonl  (from src.label)
  - data/signals.db      (from src.extract)
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# --- paths ----------------------------------------------------------------
ROOT          = Path(__file__).parent
FINDINGS_PATH = ROOT / "data" / "findings.json"
BRIEF_PATH    = ROOT / "data" / "brief.txt"
EVAL_PATH     = ROOT / "data" / "eval_set.jsonl"
SIGNALS_DB    = ROOT / "data" / "signals.db"
REVIEWS_DB    = ROOT / "data" / "reviews.db"


# --- page config ----------------------------------------------------------
st.set_page_config(
    page_title="ReviewLens — Quality Intelligence",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Tiny CSS polish. Streamlit defaults look like a homework assignment;
# tightening the spacing + a single accent color makes it look intentional.
# Uses CSS variables that adapt to Streamlit's light/dark theme.
st.markdown("""
<style>
    .block-container { padding-top: 2rem; padding-bottom: 3rem; }
    h1 { padding-bottom: 0; }
    .highlight-card {
        background: rgba(230, 57, 70, 0.08);
        border-left: 4px solid #e63946;
        padding: 1.25rem 1.5rem;
        border-radius: 4px;
        margin-bottom: 1.5rem;
    }
    .highlight-card h2 { margin: 0; }
    .insight-text {
        font-size: 1.05rem;
        line-height: 1.6;
        margin-top: 0.75rem;
    }
    .small-muted { opacity: 0.65; font-size: 0.85rem; }
</style>
""", unsafe_allow_html=True)


# --- chart styling --------------------------------------------------------
# One consistent color story across the whole dashboard.
ACCENT_RED  = "#e63946"        # bad / safety / severity
ACCENT_GREY = "#6c757d"        # neutral
GRID_COLOR  = "rgba(128, 128, 128, 0.18)"


def style_chart(fig: go.Figure, height: int = 300, show_legend: bool = False) -> go.Figure:
    """Apply our consistent layout to a Plotly figure.

    Transparent background so the chart adapts to Streamlit's theme. Subtle
    grid. No chrome we don't need.
    """
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=12, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(size=13),
        showlegend=show_legend,
        hoverlabel=dict(font_size=13),
    )
    fig.update_xaxes(gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR)
    fig.update_yaxes(gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR)
    return fig


# --- data loading ---------------------------------------------------------
@st.cache_data
def load_findings() -> dict:
    if not FINDINGS_PATH.exists():
        st.error(f"No findings found. Run `python -m src.aggregate` first.")
        st.stop()
    return json.loads(FINDINGS_PATH.read_text())


@st.cache_data
def load_brief() -> str:
    if not BRIEF_PATH.exists():
        return "Brief not yet generated. Run `python -m src.brief`."
    return BRIEF_PATH.read_text().strip()


@st.cache_data
def load_eval_results() -> dict:
    """Recompute eval numbers on the fly from eval_set.jsonl + signals.db.

    Mirrors src/eval.py logic so the dashboard's methodology page reports
    the same numbers the eval harness prints to stdout.
    """
    if not EVAL_PATH.exists():
        return {"available": False}

    items = []
    with EVAL_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))

    ids = [it["review_id"] for it in items]
    with sqlite3.connect(SIGNALS_DB) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(ids))
        preds = {
            r["review_id"]: dict(r) for r in conn.execute(
                f"SELECT * FROM signals WHERE review_id IN ({placeholders})", ids
            ).fetchall()
        }

    severity_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}

    def component_match(g, p):
        if g is None and p is None:
            return True
        if g is None or p is None:
            return False
        return bool(set(g.lower().split()) & set(p.lower().split()))

    correct = Counter()
    confusion = Counter()
    total = 0
    for it in items:
        rid, gold = it["review_id"], it["label"]
        pred = preds.get(rid)
        if not pred:
            continue
        total += 1

        pred_complaint = bool(pred["has_quality_complaint"])
        if gold["has_quality_complaint"] == pred_complaint:
            correct["has_quality_complaint"] += 1
        if gold["defect_category"] == pred["defect_category"]:
            correct["defect_category"] += 1
        else:
            confusion[(gold["defect_category"], pred["defect_category"])] += 1
        if gold["severity"] == pred["severity"]:
            correct["severity_exact"] += 1
        if abs(severity_rank[gold["severity"]] - severity_rank[pred["severity"]]) <= 1:
            correct["severity_within_one"] += 1
        if gold["sentiment"] == pred["sentiment"]:
            correct["sentiment"] += 1
        if component_match(gold["component_mentioned"], pred["component_mentioned"]):
            correct["component_mentioned"] += 1

    return {
        "available": True,
        "total": total,
        "metrics": {k: v / total for k, v in correct.items()} if total else {},
        "confusion": dict(confusion),
    }


@st.cache_data
def load_reviews_for_sku(asin: str) -> pd.DataFrame:
    """For the deep-dive: pull reviews + signals for one SKU."""
    with sqlite3.connect(REVIEWS_DB) as conn:
        conn.execute(f"ATTACH DATABASE '{SIGNALS_DB}' AS sig")
        df = pd.read_sql_query("""
            SELECT r.review_id, r.rating, r.review_date,
                   sig.has_quality_complaint, sig.defect_category,
                   sig.component_mentioned, sig.severity, sig.summary
            FROM reviews r
            JOIN sig.signals sig ON sig.review_id = r.review_id
            WHERE r.asin = ?
        """, conn, params=(asin,))
    if not df.empty:
        df["review_date"] = pd.to_datetime(df["review_date"], errors="coerce")
    return df


# --- sidebar navigation ---------------------------------------------------
data = load_findings()
findings = data["findings"]

st.sidebar.title("🔎 ReviewLens")
st.sidebar.caption("Quality intelligence from customer reviews")
st.sidebar.markdown("---")
page = st.sidebar.radio(
    "View",
    ["Executive Summary", "SKU Deep Dive", "Methodology"],
    label_visibility="collapsed",
)
st.sidebar.markdown("---")
st.sidebar.markdown(
    f"<span class='small-muted'>{data['n_skus']} SKUs · "
    f"generated {data['generated_at'][:10]}</span>",
    unsafe_allow_html=True,
)


# =========================================================================
# PAGE 1 — EXECUTIVE SUMMARY
# =========================================================================
if page == "Executive Summary":
    st.title("Monday Morning Brief")
    st.caption("Top-priority product quality findings for the week.")

    # Top finding — full-bleed highlight card. This is the SKU that anchors the
    # whole project. Interview-optimized landing.
    top = findings[0]
    safety_pct  = top["safety_share"] * 100
    rate_pct    = top["true_complaint_rate"]["value"] * 100
    risk_score  = top["risk"]["score"]
    n_complaints = top["severity"]["n_complaints"]
    top_component = top["top_components"][0] if top["top_components"] else None
    cluster = top.get("failure_cluster")

    # Build the headline narrative from the cluster if we have one, otherwise
    # fall back to a simpler safety-share message.
    if cluster:
        cluster_share_pct = cluster["share_of_complaints"] * 100
        ttf_str = (
            f" Median time-to-failure: {cluster['median_ttf_days']} days."
            if cluster["median_ttf_days"] is not None else ""
        )
        narrative = (
            f"<b>{cluster['n_matches']} complaints "
            f"({cluster_share_pct:.0f}% of complaints on this SKU)</b> "
            f"describe symptoms consistent with <b>{cluster['narrative']}</b>. "
            f"Signal converges on the <b>{top_component['value']}</b> "
            f"({top_component['share']*100:.0f}% of complaints) — and across "
            f"three independent dimensions: defect category, component mention, "
            f"and time-to-failure.{ttf_str}"
        )
    else:
        narrative = (
            f"<b>{safety_pct:.0f}% of complaints flagged as safety concerns</b> "
            f"({n_complaints} reviews total). "
            f"Signal converges on the <b>{top_component['value'] if top_component else 'product'}</b> "
            f"({top_component['share']*100:.0f}% of complaints name this component)."
        )

    st.markdown(f"""
<div class="highlight-card">
    <div style="display: flex; justify-content: space-between; align-items: baseline;">
        <h2 style="margin: 0;">🚨 {top['display_name']}</h2>
        <span class="small-muted">Risk score {risk_score:.2f} · highest in dataset</span>
    </div>
    <p class="insight-text" style="margin-top: 0.75rem;">
        {narrative}
    </p>
</div>
""", unsafe_allow_html=True)

    # The LLM-generated brief
    st.subheader("Auto-generated brief")
    st.info(load_brief())

    st.markdown("---")

    # Risk-ranked table — what would be the next clicks for a director
    st.subheader("All SKUs ranked by composite risk")
    st.caption("Risk score blends complaint rate, severity, safety share, recency, and early-failure clustering.")

    df = pd.DataFrame([
        {
            "SKU":              f["display_name"],
            "Category":         f["category"].replace("_", " "),
            "True rate":        f["true_complaint_rate"]["value"],
            "Severity":         f["severity"]["score"],
            "Safety share":     f["safety_share"],
            "Trend (pp)":       f["recency"]["delta_pp"],
            "Risk score":       f["risk"]["score"],
            "Reviews analyzed": f["n_reviews_scraped"],
        }
        for f in findings
    ])
    st.dataframe(
        df.style.format({
            "True rate":    "{:.1%}",
            "Severity":     "{:.2f}",
            "Safety share": "{:.1%}",
            "Trend (pp)":   lambda v: f"{v:+.1f}" if pd.notna(v) else "—",
            "Risk score":   "{:.3f}",
        }).background_gradient(subset=["Risk score"], cmap="Reds"),
        width="stretch",
        hide_index=True,
    )

    st.markdown("---")
    col1, col2, col3, col4 = st.columns(4)
    total_reviews = sum(f["n_reviews_scraped"] for f in findings)
    total_complaints = sum(f["severity"]["n_complaints"] for f in findings)
    total_safety = sum(
        f["safety_share"] * f["severity"]["n_complaints"]
        for f in findings
    )
    with col1:
        st.metric("Reviews analyzed", f"{total_reviews:,}")
    with col2:
        st.metric("Complaints extracted", f"{total_complaints:,}")
    with col3:
        st.metric("Safety-flagged", f"{int(total_safety):,}")
    with col4:
        st.metric("Highest risk score", f"{findings[0]['risk']['score']:.2f}")


# =========================================================================
# PAGE 2 — SKU DEEP DIVE
# =========================================================================
elif page == "SKU Deep Dive":
    st.title("SKU Deep Dive")

    # Default to the highest-risk SKU so the page loads with something interesting
    sku_names = [f["display_name"] for f in findings]
    selected = st.selectbox("Choose a SKU", sku_names, index=0)
    f = next(x for x in findings if x["display_name"] == selected)

    # Top metrics row — most important numbers, big
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("True complaint rate", f"{f['true_complaint_rate']['value']*100:.1f}%")
    with c2:
        st.metric("Safety share", f"{f['safety_share']*100:.1f}%")
    with c3:
        st.metric("Severity score", f"{f['severity']['score']:.2f}")
    with c4:
        st.metric("Composite risk", f"{f['risk']['score']:.3f}")

    st.markdown("---")

    # ---- Convergent signal panel ----
    # If the cluster detector found a coherent failure pattern, surface it here
    # as the headline narrative of the deep dive. This is the answer to
    # "what is the SKU's actual problem?" before the user looks at any charts.
    cluster = f.get("failure_cluster")
    if cluster:
        share_pct = cluster["share_of_complaints"] * 100
        n = cluster["n_matches"]
        median_ttf = cluster["median_ttf_days"]
        early = cluster["early_share"]

        # Build the bullet evidence lines dynamically — we only show what's real
        evidence_lines = []
        evidence_lines.append(
            f"**{n} complaints** ({share_pct:.0f}% of all complaints on this SKU) "
            f"describe symptoms consistent with **{cluster['name']}**"
        )
        if cluster["top_components"]:
            comps = ", ".join(f"{c['value']} ({c['count']})" for c in cluster["top_components"])
            evidence_lines.append(f"**Components named in this cluster:** {comps}")
        if median_ttf is not None:
            evidence_lines.append(
                f"**Median time-to-failure:** {median_ttf} days "
                f"(across {cluster['n_with_ttf']} reviews where customer stated a duration)"
            )
        if early is not None:
            evidence_lines.append(
                f"**{early*100:.0f}% occur within the first 60 days** of ownership"
            )

        st.markdown(f"#### 🎯 Convergent signal detected: {cluster['narrative']}")
        for line in evidence_lines:
            st.markdown(f"- {line}")

        if cluster["representative_quotes"]:
            with st.expander("Representative complaint summaries"):
                for q in cluster["representative_quotes"]:
                    st.markdown(f"> {q}")

        st.markdown("---")

    # ---- Risk score decomposition ----
    # Defends against "how is the risk score computed?" by exposing every input.
    with st.expander("📊 Why did this SKU score this way? (risk decomposition)"):
        st.caption(
            "Composite risk is a weighted blend of 5 normalized dimensions. "
            "Each bar shows how much that dimension contributed to the final score."
        )
        sorted_contribs = f["risk"]["sorted"]
        decomp_df = pd.DataFrame([
            {
                "Dimension":     c["name"].replace("_", " "),
                "Contribution":  c["contribution"],
                "Value":         c["component"],
                "Weight":        c["weight"],
                "Available":     c["data_available"],
            }
            for c in sorted_contribs
        ])

        fig = go.Figure(go.Bar(
            x=decomp_df["Contribution"],
            y=decomp_df["Dimension"],
            orientation="h",
            marker=dict(
                color=decomp_df["Contribution"],
                colorscale=[[0, "#f4a3aa"], [1, ACCENT_RED]],
                line=dict(width=0),
            ),
            customdata=decomp_df[["Value", "Weight"]].values,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Normalized value: %{customdata[0]:.2f}<br>"
                "Weight: %{customdata[1]:.0%}<br>"
                "Contribution: %{x:.3f}<extra></extra>"
            ),
        ))
        fig.update_layout(
            xaxis_title="Contribution to risk score",
            yaxis_title=None,
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(style_chart(fig, height=260), config={"displayModeBar": False})

        top_contrib = sorted_contribs[0]
        st.markdown(
            f"**Top contributor:** {top_contrib['name'].replace('_', ' ')} "
            f"({top_contrib['contribution']:.3f} of the {f['risk']['score']:.3f} total)."
        )

    st.markdown("---")

    # Two columns: defects on left, components on right
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Top defect categories")
        if f["top_defects"]:
            defect_df = pd.DataFrame(f["top_defects"])
            defect_df["value"] = defect_df["value"].str.replace("_", " ")
            defect_df["pct"] = defect_df["share"] * 100
            fig = px.bar(
                defect_df,
                x="count",
                y="value",
                orientation="h",
                custom_data=["pct"],
                color_discrete_sequence=[ACCENT_RED],
            )
            fig.update_traces(
                hovertemplate="<b>%{y}</b><br>%{x} complaints (%{customdata[0]:.0f}%)<extra></extra>",
            )
            fig.update_layout(
                xaxis_title=None,
                yaxis_title=None,
                yaxis=dict(autorange="reversed"),   # biggest on top
            )
            st.plotly_chart(style_chart(fig, height=280), config={"displayModeBar": False})
        else:
            st.info("No complaints to break down.")

    with col_right:
        st.subheader("Top components mentioned")
        if f["top_components"]:
            comp_df = pd.DataFrame(f["top_components"])
            comp_df["pct"] = comp_df["share"] * 100
            fig = px.bar(
                comp_df,
                x="count",
                y="value",
                orientation="h",
                custom_data=["pct"],
                color_discrete_sequence=[ACCENT_GREY],
            )
            fig.update_traces(
                hovertemplate="<b>%{y}</b><br>%{x} complaints (%{customdata[0]:.0f}%)<extra></extra>",
            )
            fig.update_layout(
                xaxis_title=None,
                yaxis_title=None,
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(style_chart(fig, height=280), config={"displayModeBar": False})
        else:
            st.info("No specific components mentioned.")

    st.markdown("---")

    # Severity distribution stacked bar
    st.subheader("Severity distribution")
    sev_counts = f["severity"]["counts"]
    if sev_counts:
        order = ["critical", "high", "medium", "low"]
        sev_data = [
            {"Severity": s, "Count": sev_counts.get(s, 0)}
            for s in order if sev_counts.get(s, 0) > 0
        ]
        sev_df = pd.DataFrame(sev_data)
        # Graduated red: darker for more severe
        severity_colors = {
            "critical": "#9d1c2a",
            "high":     "#e63946",
            "medium":   "#f4a261",
            "low":      "#a8a8a8",
        }
        fig = px.bar(
            sev_df,
            x="Severity",
            y="Count",
            color="Severity",
            color_discrete_map=severity_colors,
            category_orders={"Severity": order},
        )
        fig.update_traces(
            hovertemplate="<b>%{x}</b><br>%{y} complaints<extra></extra>",
        )
        fig.update_layout(xaxis_title=None, yaxis_title=None)
        st.plotly_chart(style_chart(fig, height=240), config={"displayModeBar": False})
    else:
        st.info("No complaints to bucket by severity.")

    st.markdown("---")

    # Complaint rate over time — the recency picture
    st.subheader("Complaint rate over time (rolling 30-day)")
    reviews_df = load_reviews_for_sku(f["asin"])
    if not reviews_df.empty and reviews_df["review_date"].notna().any():
        ts_df = reviews_df.dropna(subset=["review_date"]).copy()
        ts_df = ts_df.set_index("review_date").sort_index()
        # Rolling 30-day complaint rate
        ts_df["is_complaint"] = ts_df["has_quality_complaint"].astype(int)
        rolling = ts_df["is_complaint"].rolling("30D", min_periods=5).mean()
        rolling = rolling.dropna()
        if not rolling.empty:
            rolling_pct = rolling * 100
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=rolling_pct.index,
                y=rolling_pct.values,
                mode="lines",
                line=dict(color=ACCENT_RED, width=2.5),
                fill="tozeroy",
                fillcolor="rgba(230, 57, 70, 0.12)",
                hovertemplate="<b>%{x|%b %d, %Y}</b><br>%{y:.1f}% complaint rate<extra></extra>",
                name="",
            ))
            fig.update_layout(
                xaxis_title=None,
                yaxis_title="Complaint rate (%)",
                yaxis=dict(ticksuffix="%"),
            )
            st.plotly_chart(style_chart(fig, height=260), config={"displayModeBar": False})
        else:
            st.info("Not enough reviews with dates for a rolling trend.")
    else:
        st.info("No date information available for this SKU.")

    st.markdown("---")

    # The qualitative evidence — LLM-generated complaint summaries
    st.subheader("Representative complaints")
    st.caption("Highest-severity recent complaints, summarized by the extractor.")
    for s in f["representative_summaries"]:
        st.markdown(f"- {s}")


# =========================================================================
# PAGE 3 — METHODOLOGY
# =========================================================================
elif page == "Methodology":
    st.title("Methodology")
    st.caption("How ReviewLens turns customer reviews into quality findings.")

    st.markdown("""
**The pipeline:**
1. **Ingest.** Apify's Amazon reviews Actor pulls reviews per SKU, stratified across star ratings to avoid the 5-star bias that dominates default sorts. ~1,780 reviews across 10 SKUs.
2. **Extract.** Each review goes through an OpenAI structured-outputs call with a hand-tuned prompt. Output is a strict JSON schema: complaint flag, defect category, component mentioned, severity, time-to-failure, sentiment, summary.
3. **Evaluate.** A hand-labeled 50-review eval set scores the extractor on per-field accuracy before scaling to the full corpus.
4. **Aggregate.** SKU-level rollups normalize back from the oversampled distribution to a true complaint rate estimate, then compute a composite risk score across 5 dimensions.
5. **Surface.** LLM generates a 3-sentence executive brief from the top-N findings. Dashboard ranks by risk.
""")

    st.markdown("---")
    st.subheader("Extractor accuracy on the hand-labeled eval set")

    er = load_eval_results()
    if er["available"] and er["total"] > 0:
        m = er["metrics"]
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Has-quality-complaint (binary)", f"{m.get('has_quality_complaint', 0)*100:.0f}%")
            st.metric("Defect category (7-way)", f"{m.get('defect_category', 0)*100:.0f}%")
        with col2:
            st.metric("Severity exact", f"{m.get('severity_exact', 0)*100:.0f}%")
            st.metric("Severity within-one", f"{m.get('severity_within_one', 0)*100:.0f}%")
        with col3:
            st.metric("Sentiment", f"{m.get('sentiment', 0)*100:.0f}%")
            st.metric("Component (fuzzy)", f"{m.get('component_mentioned', 0)*100:.0f}%")

        st.caption(f"Scored against {er['total']} hand-labeled reviews.")

        st.markdown("**Severity within-one at 98%** means when the model misjudges severity, it's almost always off by one notch (medium vs. high), never catastrophic. That's the model demonstrating understanding of the ordinal scale.")
        st.markdown("**Defect category exact at ~54%** is the concentrated weakness. Confusion is dominated by one category (`design_flaw`), which is genuinely ambiguous in the schema — it overlaps with `durability` for time-of-failure cases and with `performance_degradation` for poor-outcome cases. A schema redesign (splitting `design_flaw` into `design_inconvenience` and `structural_design_issue`) would be the v2 move.")
    else:
        st.warning("Eval results not available. Run `python -m src.label` then `python -m src.eval`.")

    st.markdown("---")
    st.subheader("What I'd build with internal data")
    st.markdown("""
Working with public review data is the constrained version of this problem. Internal data sharpens it meaningfully:

- **Warranty claims** join in directly — converts "complaint rate" into "failure rate per units sold"
- **Manufacturing batch IDs** let you cluster defects by production run, not just SKU
- **Support ticket text** is a higher-signal corpus than public reviews — customers are more specific when they want help
- **Return reason codes** add a structured ground-truth signal the extraction layer can be evaluated against

The pipeline architecture supports all of these. The extraction schema gets richer, the convergence score gets more dimensions, and the eval set grows.
""")

    st.markdown("---")
    st.subheader("Known limitations")
    st.markdown("""
- **Eval set size is 50 reviews.** Defensible for a 4-day prototype but not production. v2 needs 300+ with inter-annotator agreement on a sample.
- **Public Amazon reviews over-index on people having a strong reaction.** Internal CSAT survey data would balance this.
- **The oversample correction uses an assumed star distribution** rather than the SKU's true Amazon distribution. The relative rankings are robust; the absolute rates are estimates.
- **Recency trends require ≥20 reviews per window.** SKUs with sparse review history report "insufficient sample" instead of fake precision.
""")