# Demo Guide — Architecture Walkthrough

This guide gives you a ~5-minute talk track to walk a customer through the app and architecture diagram.

## Pre-demo checklist
- Open the app and run customer **62850** first — this caches the model
- Confirm both status pills (Model ✓, Lakebase ✓) show green in the sidebar
- Have the architecture image ready: `architecture/reference-v2.drawio.png`

---

## Demo flow

### [0:00 – 0:40] The Problem
*Start at the landing page — point to the two persona cards*

> "Credit decisioning today means three separate systems — a training environment, a feature store, and a model serving layer. That's three teams, three on-call rotations, and three gaps in the lineage when the auditor asks why a customer was declined."

---

### [0:40 – 1:20] Technical Analyst — Customer 62850 (Approve)
*Click Technical Analyst. Run customer 62850.*

> "Feature lookup from Lakebase in about 4ms. Bureau and cash flow via external APIs. KYC check rules-based, LightGBM inference under 1ms. Decision written back to Lakebase with full UC lineage."

**What to point out:**

- **Tier / KYC chips** — labeled inline: "Tier: PREMIUM, KYC: PASS". Model risk teams need to know what each chip represents at a glance, not just a color.

- **Score line** — `credit_scoring_v1 · v4 champion · score 0.849 vs cutoff 0.500`. Not a percentage confidence. "100% confidence" is a red flag for model risk teams — raw score against a defined cutoff is the correct read.

- **Feature vector** — grouped by the 5 Cs of Credit: Character (payment history, derogatory marks), Capacity (income, employment, DTI), Capital (debt, accounts), Conditions (credit score, usage rate), Profile (age). This is the framing a credit underwriter already uses.

- **Model Governance** — model name, version, alias, MRM status visible on the decision screen without requesting a separate report.

---

### [1:20 – 2:00] Customer 941 — Compliance Story
*Run customer 941 (KYC Fail).*

> "Score 668 clears the threshold, but three derogatory marks trip the KYC check. Declined regardless of the model score. The audit trail shows both outcomes side by side — model said approve, rules said decline."

**What to point out:**
- KYC chip reads "KYC: FAIL" with the label — visible without reading reason codes
- Score is still shown against the cutoff even on a KYC decline; the model didn't decline this customer, the rules did

---

### [2:00 – 3:00] Risk Officer View
*Click ← Home, then Risk Officer.*

> "Same data, different lens. Reads live from the same Lakebase tables — every decision you just ran shows up here in real time."

**What to point out:**

- **Score Distribution** — x-axis in estimated FICO bands (300–850), not raw model scores. Cutoff sits at ~575. Risk officers read FICO, not logit outputs.

- **Approval Rate trend** — automatically flags when the rate drops more than 5 percentage points over the last 10 decisions. No manual monitoring needed.

- **Decision Mix donut** — click APPROVE or DECLINE to drill into the top reason codes for that segment. The CRO can see what's driving declines without scrolling the live feed.

---

### [3:00 – 4:00] Architecture (for technical audience)
*Open `architecture/reference-v2.drawio.png` fullscreen.*

> "One PostgreSQL endpoint, three Lakebase tables: credit_features for real-time lookup, credit_limits for the limit check, and decision_log written at decision time to both Delta and Lakebase. Unity Catalog lineage covers raw data through features, model, and decision — one URL for the regulator."

**Benchmark:** p99 under 22ms at 100 concurrent requests. SLA was 50ms.

---

## Scenario quick reference

| Customer | Outcome | Why |
|---|---|---|
| 62850 | APPROVE | Score 849, KYC PASS, Tier PREMIUM |
| 13298 | DECLINE | Score 513, RC01 + RC03 + RC05 |
| 941 | DECLINE (KYC) | Score 668 clears model threshold — 3 derogatory marks block KYC |
