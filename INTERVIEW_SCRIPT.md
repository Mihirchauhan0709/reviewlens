# ReviewLens — Interview Script & Talking Points

This is your playbook for using ReviewLens in the SharkNinja interview. Read it twice before the call. Don't memorize it word-for-word — internalize the structure.

---

## The 60-Second Pitch (use this when they ask "tell me about a project")

> "Once I learned I'd be interviewing for this role, I built a working prototype of what an Applied AI co-op project at SharkNinja might actually look like. It's called ReviewLens. I scraped about 1,500 Amazon reviews across 10 Shark and Ninja SKUs, built a pipeline that uses OpenAI's structured outputs to extract quality signals — defect category, component, severity, time-to-failure — and surfaced SKUs where multiple signals converge on a likely quality issue. There's a dashboard that produces what I'd send to a product team Monday morning. I built it in four days, evaluated the extraction against a 50-review hand-labeled set, and the most interesting thing it found was [YOUR REAL FINDING]. Happy to share my screen if you want to see it."

**Why this opener works:**
- Signals you don't wait for permission to build
- Names the company and the role — shows you've thought about *them*
- Has a real number and a real finding (not "I learned a lot")
- Ends with an active offer — they'll usually say yes, and now you're driving the interview

---

## The 3-Minute Demo (if they say "show me")

**Beat 1 — Open the dashboard. Start on the Executive Summary view.**

> "This is what I'd send to a product team Monday morning. Top 5 SKUs ranked by a composite risk score — not raw complaint volume, because bestsellers will always have more complaints. The score weights complaint rate, severity, recent trend, and time-to-failure together. The reason for that is single signals are noise; convergence is signal."

**Beat 2 — Click into the highest-risk SKU.**

> "Here's the deep dive. You can see complaint rate over time — there's a clear inflection around [DATE]. Top defect categories show this is concentrated in [CATEGORY], and the component breakdown points specifically to [COMPONENT]. These review summaries on the right are LLM-generated one-sentence summaries — they're how a non-technical stakeholder skims the evidence without reading 200 reviews."

**Beat 3 — Jump to the Methodology page.**

> "This is the part I think matters most. Before I scaled the extractor, I hand-labeled 50 reviews and built an eval set. Then I iterated the prompt against measured accuracy, not against my impression of whether it 'felt right.' Defect category exact match landed at [X]%, severity within-one at [Y]%. The failure cases I documented mostly came from ambiguous reviews where the customer mixed shipping complaints with product complaints — I tightened the prompt to handle that explicitly."

**Beat 4 — Close.**

> "If I were doing this with internal data — warranty claims, manufacturing batch IDs, support tickets — the same architecture works and the convergence score gets sharper. Public reviews are the constrained version of the problem."

---

## Anticipated Follow-Up Questions (have these answers loaded)

### "How did you choose the SKUs?"

> "Stratified — five Shark, five Ninja, mix of categories, mix of star ratings. I deliberately included some highly-rated SKUs so I wasn't selecting on the dependent variable. If I only pulled 1-star products, every model looks great because everything is a complaint."

*This answer shows statistical thinking and intellectual honesty about your own setup. Recruiters love it.*

### "Why structured outputs instead of just parsing JSON from a regular completion?"

> "Reliability. With unstructured JSON, you spend time defending against malformed outputs, missing fields, and hallucinated enum values. Structured outputs enforce the schema at the API level. For a pipeline that runs at scale, eliminating that whole class of failure is worth it. I learned this the hard way at Kobeyo, where we were extracting skill data from job postings and had to build defensive parsers around an earlier model."

### "How would you scale this?"

> "Three layers. One — extraction is embarrassingly parallel, so async batch the API calls; OpenAI's batch API drops the cost by half for non-realtime workloads. Two — the eval set has to grow with the data; I'd add a sampling step where every Nth extraction gets routed to a human reviewer to keep the ground truth current. Three — the convergence score is currently static weights. With more data you can learn those weights against an actual quality outcome — warranty claim rate, return rate, whatever the business cares about — and the score gets better over time without me tuning it."

### "What's the weakest part of this?"

> "The eval set is 50 reviews. That's defensible for a four-day prototype but not for a production system — I'd want at least 300, and I'd want inter-annotator agreement on a sample to confirm my own labels aren't biased. The other weakness is that I'm relying on public Amazon reviews, which over-index on people having a strong reaction either way. Internal CSAT survey data would balance that out."

*This is the most important question to nail. Showing you can critique your own work is the single strongest signal of seniority.*

### "What would you do differently?"

> "Build the eval set on day zero, not day two. I did it on day two and it still gave me the right iteration loop, but I had to redo some extraction runs. If I'd started with the eval set, I would have caught the shipping-versus-product-complaint ambiguity earlier."

### "Why did you build this?"

> "Honestly, because the JD said 'real AI deployments, not sandbox exercises' and 'identify data gaps and propose solutions, not just flag them.' That's the cultural signal I responded to. I figured the most credible way to interview for a build-things role is to have already built something."

---

## Connecting It Back to Their Values (use these in behavioral answers)

| Value | Hook line |
|---|---|
| **Rarely Satisfied** | "I shipped the first version on day three. The version on day four is meaningfully better because I wasn't satisfied with the day-three findings." |
| **Progress over Perfection** | "I made the call to use Apify's scraper instead of rolling my own Playwright pipeline. That's a $5 decision that bought me back an entire day." |
| **Details Make the Difference** | "The whole project hinges on the eval set. Without it, the prompt iteration is vibes-based. With it, every change is measurable." |
| **Winning is a Team Sport** | (use the Yoga pose project here — leading a team of six, shipping the platform AND the paper) |
| **Communicate for Impact** | "The dashboard's three-sentence brief is the only thing on the executive summary page. Everything else lives one click deeper. A VP doesn't need the methodology — they need to know what to do Monday." |

---

## Questions to Ask Them (lead with these — they signal you understand the work)

1. *"When a co-op gets handed a Jailbreak brief, what's the typical scoping process — does the Technical Team Lead define the problem, or do you scope it together?"*

2. *"I built ReviewLens around the idea that the eval set matters more than the prompt. How does the team currently think about evaluating AI workflows that get shipped — is there a shared evaluation methodology, or does each project define its own?"*

3. *"What's the boundary between an Applied AI project that gets operationalized vs. one that gets handed off to engineering? Is there a productionization step a co-op would own?"*

4. *"You mention $1M in Jailbreak awards — has a co-op or intern ever won one of those?"* (this one is fun, optional, and signals you've read the press)

---

## Things NOT to Say

- ❌ "I used ChatGPT to help me build it" — even if true, this is a tell. Talk about *what you decided*, not what wrote the code.
- ❌ "It's just a prototype" — never apologize for scope. You built it in four days. Own it.
- ❌ "I don't have access to real SharkNinja data" — instead say "this is the public-data version of the problem; the internal-data version is sharper."
- ❌ "I hope this is what you're looking for" — confident framing only.

---

## Day-Of Logistics

- Have the Streamlit dashboard open in a tab BEFORE the call starts. Pre-loaded.
- Have the GitHub repo open in a second tab.
- Have your README open in a third tab — if they ask for a written summary, you can drop the link in chat instantly.
- The Loom video is your backup if screen-sharing has technical issues.

Good luck, Mihir. You've already done the hard part — you decided to build the thing.
