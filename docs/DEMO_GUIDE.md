# Demo Guide — FSI Decisioning with Lakebase

Talk tracks and flow for both segments. Full explanations of every metric and score are in `docs/GLOSSARY.md`. Segment-specific storytelling is in `docs/SEGMENT_GUIDE.md`.

## Pre-demo checklist

- Open the app and run customer **62850** first — this warms the model cache
- Confirm both status pills (Model, Lakebase) show green in the sidebar
- Decide which segment to lead with: **Banking** or **Insurance**
- Have `architecture/reference-v2.drawio.png` ready for the architecture walk

---

## Quick flow — Banking segment (~5 min)

### [0:00 – 0:30] Setup
Select **Banking** on the landing page. Two personas appear: Credit Analyst and Risk Officer.

> "Three systems today — training, feature store, model serving. Three teams, three on-call rotations, three separate logs when the regulator asks why a customer was declined. This shows all three collapsed into one."

---

### [0:30 – 1:30] Customer 62850 — APPROVE

Click **Technical Analyst**. Enter `62850`.

> "Feature lookup from Lakebase in about 4ms. Bureau and cash flow via external APIs at 80-300ms. KYC gate, PEP screening, Open Banking income verification — all before the model runs."

**What to point out:**

- **Tier / KYC chips** — labeled inline: "Tier: PREMIUM, KYC: PASS, PEP: CLEAR, Fraud Velocity: LOW". Model risk teams need labels, not just colors.
- **Score line** — `credit_scoring_v1 · v4 champion · score 0.849 vs cutoff 0.500`. Raw score against a defined cutoff, not a percentage confidence.
- **Open Banking income** — confidence 95%+. Thin-file use case: customer with limited bureau tradelines resolved via transaction data.
- **5 Cs of Credit** — Character (payment history, derogatory marks), Capacity (income, employment, DTI), Capital (debt, accounts), Conditions (credit score, utilization), Profile (age). The framing a credit underwriter already uses.
- **Model Governance** — model name, version, alias, MRM status visible on decision screen. No separate report needed.
- **Cross-sell panel** — Credit Protection, Auto Insurance, Life Insurance propensity scores appear on approval. "Same feature vector, no batch run — the features are already in Lakebase."

---

### [1:30 – 2:00] Model selector — Champion vs Challenger

Switch model to **XGBoost** and re-run.

> "Same customer, different model. The model comparison bar shows scores from all three models side by side — LightGBM, XGBoost, calibrated Logistic Regression. The active model is highlighted. This is SR 26-2 champion/challenger infrastructure. Not a slide — a working demo."

Switch to **Logistic Reg (calibrated)**.

> "The logistic regression uses isotonic calibration. When the risk officer asks whether a predicted 5% PD means 5% actual default rate — isotonic calibration is the answer. IFRS 9 provisioning requires it."

---

### [2:00 – 2:30] Customer 941 — KYC flag

Run `941`.

> "Score 668 clears the model threshold. Three derogatory marks trip the KYC check. Declined regardless of model score. The audit trail shows both outcomes side by side."

**What to point out:**
- KYC chip reads "KYC: FAIL" with the label, not just a red color
- Score still shown against cutoff — the model approved this customer, the rules declined it
- Every reason code traces back to the Lakebase feature vector

---

### [2:30 – 3:30] Risk Officer view

Click **← Home** → **Risk Officer**.

> "Same Lakebase tables. Every decision you just ran shows up here in real time."

**What to point out:**

- **Score Distribution** — x-axis in FICO bands (300–850), not raw model scores. Cutoff at ~575. Risk officers read FICO, not logit outputs.
- **Approval Rate trend** — automatic flag when rate drops more than 5pp over last 10 decisions.
- **Decision Mix donut** — click APPROVE or DECLINE to drill into top reason codes. CRO sees what's driving declines without scrolling the live feed.

---

### [3:30 – 4:30] Architecture

Open `architecture/reference-v2.drawio.png` fullscreen.

> "One PostgreSQL endpoint, three Lakebase tables: credit_features for real-time lookup, credit_limits for the limit check, decision_log written at decision time to both Delta and Lakebase. Unity Catalog lineage covers raw data through features, model, and decision — one URL for the regulator."

**Benchmark:** p99 under 22ms at 100 concurrent requests vs. 50ms SLA target.

---

## Quick flow — Insurance segment (~5 min)

### [0:00 – 0:30] Setup
Select **Insurance** on the landing page. Personas update to Underwriting Analyst and Insurance Risk Officer.

> "Underwriting today is batch — overnight decisions. The adjuster sees the application the next morning. We show real-time actuarial decisioning with Open Insurance cross-insurer data, all on the same platform."

---

### [0:30 – 1:30] Customer 62850 — Underwriting decision

Click **Underwriting Analyst**. Enter `62850`.

> "Lakebase feature lookup in 4ms. Actuarial score computed from age, derogatory marks, payment reliability. Open Insurance pull: cross-insurer claims in the last 3 years, policy count, loss ratio."

**What to point out:**

- **Actuarial Score** — 0-100 scale, higher = higher risk. Bands: 0-30 Preferred, 31-59 Standard, 60-79 Elevated, 80-100 decline territory. A score of 62 = Elevated → rate up +20%.
- **Risk Class chip** — Preferred / Standard / Substandard mapped directly from actuarial score.
- **Open Insurance data** — claims count, policy count, loss ratio from other insurers. "This is SUSEP OPIN Circular 635 in Brazil, equivalent to CLUE in the US. Cross-insurer history in the decision loop."
- **Rate-up indicator** — amber "Rated Up (+20%)" banner on approval. Policy is issued at the Substandard premium tier, not declined.
- **Underwriting Risk Profile** — feature vector in actuarial groups: Risk Profile, Financial Health, Policyholder. Not the 5 Cs — the framing the actuary uses.

---

### [1:30 – 2:00] Model comparison — calibration matters

Switch model to **Logistic Reg (calibrated)**.

> "For insurance pricing, calibration is not optional. A model that ranks risks correctly but predicts 5% loss ratio when actual is 12% will mis-price the book. CNSP 321 and Solvency II actuarial technical note requirements explicitly address this. Isotonic calibration aligns predicted probabilities to observed rates."

---

### [2:00 – 3:00] Risk Officer — insurance view

> "Portfolio approval rate by risk class. Loss ratio trend. Decline drivers: actuarial threshold vs. model. Same Lakebase audit table — every underwriting decision with the full feature snapshot."

---

## Bancassurance cross-sell

Available when segment is **Banking** and decision is **APPROVE**.

> "When this customer gets approved, we immediately score them for three protection products using the same feature vector already in Lakebase. No separate batch run. This is how bancassurance digital leaders run next-best-offer: credit approval triggers real-time product propensity."

Three propensity scores: Credit Protection, Auto Insurance, Life Insurance (0–1 scale).
- Above 0.70: high intent — include in immediate offer
- 0.40–0.70: moderate — include in digital journey
- Below 0.40: low — omit or test

Life event triggers to mention: new credit approval → credit protection; mortgage → home + life; travel MCCs → travel insurance.

---

## Scenario quick reference

| Customer | Segment | Outcome | Why |
|---|---|---|---|
| 62850 | Banking | APPROVE — PREMIUM | Score 0.849, KYC PASS, Tier PREMIUM |
| 13298 | Banking | DECLINE | Score 0.487, RC01 + RC03 + RC05 |
| 941 | Banking | DECLINE (KYC) | Score 0.668 clears model — derogatory marks trip KYC |
| 62850 | Insurance | APPROVE (Rated Up) | Actuarial score ~62 → Substandard → +20% rate |

---

## Metric reference

Full definitions and value interpretations are in `docs/GLOSSARY.md`.

Quick reads:
- **FICO 700+** — Prime or better, standard automated approval
- **Cash Flow 69** — Solid payroll, near-zero balance, occasional overdraft. Standard approval; thin-file use case.
- **Brier Score** — calibration quality. Lower = better. Below 0.10 = excellent.
- **KS Statistic** — separation between approvals and declines. Above 0.40 = good.
- **Actuarial score 62** — Elevated risk class, rate-up tier.
- **Loss Ratio 0.82** — 82 cents of claims per premium dollar. Marginal underwriting result.
