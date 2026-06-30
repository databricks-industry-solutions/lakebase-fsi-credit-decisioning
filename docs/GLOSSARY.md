# Glossary — Credit Decision Engine

This glossary explains every score, metric, indicator, and decision output shown in the
demo application. Written for credit officers, risk managers, and compliance leads who
need to understand what each value represents and how it influences the credit decision.

---

## Quick Reference Table

| Indicator | Range / Values | Direction | Appears In |
|---|---|---|---|
| FICO Score | 300 – 850 | Higher = better | Banking segment — Bureau panel |
| Cash Flow Score | 0 – 100 | Higher = better | Banking segment — Bureau panel |
| KYC / AML Status | PASS / FAIL | PASS = clear | Both segments |
| PEP Status | CLEAR / FLAGGED | CLEAR = no action | Banking segment |
| Fraud Velocity | LOW / MEDIUM / HIGH | LOW = best | Banking segment |
| Open Banking Income Confidence | 0 – 100% | Higher = more reliable | Banking segment |
| Model Score | 0.000 – 1.000 | Higher = more likely to repay | Both segments |
| Decision Cutoff | 0.500 (banking) / 0.450 (insurance) | Score must meet or exceed | Both segments |
| AUC-ROC | 0.5 – 1.0 | Higher = better discrimination | Model Governance panel |
| Brier Score | 0.00 – 1.00 | Lower = better calibration | Model Governance panel |
| KS Statistic | 0.0 – 1.0 | Higher = sharper separation | Model Governance panel |
| Actuarial Score | 0 – 100 | Higher = lower risk | Insurance segment |
| Loss Ratio | 0.00 – 3.50+ | Lower = better underwriting | Insurance segment |
| Cross-sell Propensity | 0.0 – 1.0 | Higher = more likely to accept | Banking — approved only |
| Credit Tier | STANDARD / PREMIUM | Tier determines limit eligibility | Both segments |
| Adverse Reason Codes | RC01 – RC07 | Identifies decline drivers | Both segments |

---

## Credit Bureau Scores

### FICO Score
**Range:** 300 – 850  
**What it measures:** Creditworthiness as assessed by a major credit bureau (Experian,
Equifax, or TransUnion) using tradeline history, payment patterns, utilization, and
length of credit history. The FICO score is the primary external bureau signal in the
banking segment.

| Band | Score | What it means in practice |
|---|---|---|
| Exceptional | 800 – 850 | Qualifies for lowest available rates, private banking products, waived fees |
| Super-Prime | 740 – 799 | Best conventional rates; automated approval in most standard products |
| Prime | 670 – 739 | Standard automated approval; full product eligibility |
| Near-Prime | 580 – 669 | Some lenders require manual review; limited product eligibility |
| Deep Subprime | 300 – 579 | Secured products only; collateral or co-signer typically required |

**Demo note:** The bureau call is simulated (40 – 60 ms) to represent a real external
API call to a bureau like Experian. In production this would be a live credit pull.
The score shown in the External Enrichment panel is the bureau's score for that
customer, which may differ slightly from the internal credit score stored in Lakebase.

---

### Cash Flow Score
**Range:** 0 – 100  
**What it measures:** Transaction-level behavioral signal derived from bank account data
(open banking / cash flow analytics provider such as Prism Data or MX). It captures
income stability, balance cushion, and overdraft frequency — factors not visible in
bureau tradeline data.

| Band | Score | What it means in practice |
|---|---|---|
| Excellent | 90 – 100 | Multiple income streams, 3+ month cash buffer, zero NSF events |
| Good | 70 – 89 | Stable payroll income, predictable recurring expenses, net-positive flow |
| Marginal | 60 – 69 | Income present but balance near zero; approximately one overdraft per month |
| Stressed | 40 – 59 | Chronic shortfalls; 2 – 4 overdraft events per month |
| Distressed | 0 – 39 | Chronic negative cash flow; high risk of missed payments |

**Demo note:** Computed from simulated transaction data derived from the customer's
`income_monthly` feature. In production this score would come from an open banking API
aggregator with access to real account transaction history.

---

## Compliance and Identity Checks

### KYC / AML Status
**Values:** PASS / FAIL  
**What it measures:** Know Your Customer and Anti-Money Laundering gate. A mandatory
compliance check that must pass before any credit is extended, regardless of the model
score.

**PASS:** Identity verified against government-issued documents, watchlist screening
clean, no sanctions hits, PEP status reviewed and escalated if required.

**FAIL types and correct responses:**

| Fail Type | What it means | Correct response |
|---|---|---|
| Identity mismatch | Stated identity does not match document data | Step-up authentication; pause application |
| Sanctions hit | Name appears on OFAC, UN, or EU financial sanctions lists | Hard block; SAR filing required |
| PEP match | Customer identified as Politically Exposed Person | EDD escalation — NOT auto-decline (see PEP below) |
| AML flag | Transaction patterns match money laundering typologies | Manual AML review; pause application |
| Adverse media | Significant negative press linked to financial crime | Enhanced due diligence |

**In the demo:** KYC FAIL is triggered deterministically when `derogatory_marks >= 3`
(RC07) or `payment_history < 0.50` (RC08 — AML flag). A KYC FAIL overrides the model
score and forces a DECLINE regardless of creditworthiness.

**Important:** The KYC gate is a hard block independent of the model score. The demo
shows both outcomes side by side: the model score may have cleared the cutoff, but a
KYC FAIL still produces a DECLINE. This is intentional and correct — it demonstrates
separation of model risk and compliance risk in the audit trail.

---

### PEP Status
**Values:** CLEAR / FLAGGED  
**What it means:** Politically Exposed Person screening. A PEP is an individual who
holds or has held a prominent public position, or is closely associated with one.
Three tiers apply:

1. Head of state, senior government official, military officer, or executive of a
   state-owned enterprise
2. Immediate family members (spouse, children, parents, siblings)
3. Close associates with significant joint financial interests or well-known business
   connections

**FLAGGED does not mean declined.** Under FATF Recommendation 12, PEP status triggers
Enhanced Due Diligence (EDD), not automatic rejection. The appropriate response is:
escalation to a senior compliance officer, enhanced ongoing monitoring, senior
management approval for account opening, and quarterly review of the relationship.
Auto-declining a PEP without EDD review is a compliance gap.

**In the demo:** PEP is checked deterministically per customer ID. A FLAGGED result is
displayed as a labeled chip on the decision screen. The application continues to
process but compliance teams should treat the application as requiring EDD before
finalising.

---

### Fraud Velocity
**Values:** LOW / MEDIUM / HIGH  
**What it measures:** Anomaly signal based on transaction velocity within short time
windows (1-hour, 24-hour, and 7-day lookback periods). HIGH velocity means the number
or value of transactions in a recent window is statistically unusual relative to the
customer's established baseline — a common indicator of account takeover or first-party
fraud rings.

| Level | What it means | Suggested response |
|---|---|---|
| LOW | Transaction volume within normal baseline | No additional friction |
| MEDIUM | Elevated activity; above baseline but not extreme | Note in file; re-check if other flags present |
| HIGH | Unusual spike in transaction volume | Manual fraud review before decision; consider step-up auth |

**In the demo:** Velocity is proxied from the `open_accounts` feature. In production it
would be computed from real-time transaction event data.

---

### Open Banking Income Verification
**Values:** VERIFIED or NOT VERIFIED; confidence score 0 – 100%  
**What it measures:** Income confirmation using bank transaction data from an open
banking provider (Open Finance / Open Banking APIs per local regulatory framework).
The verified income figure is derived from recurring inbound credits (payroll, rental,
business income) and compared against the stated monthly income on the application.

- Verified: verified income is within ±15% of stated income
- Not Verified: variance exceeds 15%, or insufficient transaction history

The confidence score reflects the reliability of the match: how many months of
transaction data are available, how consistent the income cadence is, and whether the
data source is primary banking relationship or secondary account.

**Why this matters:** Open banking income verification is the primary tool for
thin-file resolution — customers with limited credit bureau history but active banking
relationships. The demo shows the income confidence percentage inline with the bureau
and cash flow scores.

---

## Model Scores and Decision Logic

### Model Score vs. Cutoff
**Range:** 0.000 – 1.000  
**What it measures:** Raw probability output from the credit scoring model. The score
represents the estimated probability that the customer will repay the obligation — not
a percentage confidence in the model's prediction.

A score of 0.849 means the model estimates an 84.9% probability of repayment. A score
of 0.200 means 20% probability of repayment.

**Cutoffs by segment:**

| Segment | Cutoff | Decision logic |
|---|---|---|
| Banking | 0.500 | APPROVE if score >= 0.500 AND KYC = PASS AND available credit >= $100 |
| Insurance | 0.450 | APPROVE if score >= 0.450 AND KYC = PASS AND available credit >= $100 |

**Common misreading to avoid:** A score of 0.85 is not "85% model confidence" — it is
the estimated repayment probability. Model risk teams read the raw score against the
cutoff, not as a standalone percentage. Displaying "100% confidence" would be a red
flag indicating model overfit or probability clipping.

**Display in the app:** The decision banner shows the score against the cutoff:
`score 0.849 vs cutoff 0.500`. The gauge chart scales the score as a percentage (0–100)
for visual readability only; the underlying decision uses the raw decimal value.

**Manual review queue:** Decisions where the score falls between 0.400 and 0.500
(borderline declines within 10 percentage points of the cutoff) are surfaced in the
Risk Officer view as candidates for manual underwriting review.

---

### Decision — APPROVE / DECLINE
**Values:** APPROVE / DECLINE  
**How the combined rule works:** A decision is APPROVE only when all three conditions
are satisfied simultaneously:

1. Model score >= segment cutoff (0.500 for banking, 0.450 for insurance)
2. KYC / AML gate = PASS
3. Available credit limit >= $100 (credit headroom check)

Any single condition failing produces a DECLINE. The reason codes explain which
condition failed and why.

**Separation of reasons:** The decision banner shows the model score against the cutoff
even on a KYC decline. This is deliberate — it separates model risk from compliance
risk in the audit trail and allows model validation teams to assess model performance
independently of compliance outcomes.

---

## Model Validation Metrics

These metrics appear in the Model Governance section and describe model quality
properties assessed during training and validation. They are recorded in MLflow and
linked to each model version in Unity Catalog.

### AUC-ROC (Area Under the Receiver Operating Characteristic Curve)
**Range:** 0.5 – 1.0  
**Direction:** Higher is better  
**What it measures:** Discrimination — the model's ability to rank good borrowers above
bad borrowers across all possible score thresholds. An AUC of 0.5 means the model
performs no better than random assignment. An AUC of 1.0 means perfect rank ordering.

| AUC | Interpretation |
|---|---|
| > 0.80 | Good discrimination for consumer credit models |
| 0.70 – 0.80 | Acceptable; meets minimum threshold for most retail credit scoring |
| 0.60 – 0.70 | Weak; additional features or re-training required |
| 0.50 – 0.60 | Poor; near-random |

**What AUC does not measure:** Calibration. A model can have high AUC (good ranking)
but poorly calibrated probabilities (predicted rates do not match observed default
rates). For IFRS 9 and CECL Probability of Default (PD) estimation, calibration is
also required.

---

### Brier Score
**Range:** 0.00 – 1.00  
**Direction:** Lower is better  
**What it measures:** Calibration quality — the mean squared error between the model's
predicted probability and the actual outcome (1 = defaulted, 0 = repaid). It answers
the question: when the model says 30% probability of default, do approximately 30% of
those customers actually default?

| Brier Score | Interpretation |
|---|---|
| < 0.10 | Excellent calibration |
| 0.10 – 0.20 | Acceptable |
| 0.20 – 0.25 | Marginal |
| 0.25 | Reference: a model that predicts the base rate for every customer |
| > 0.25 | Worse than naïve baseline |

**Why this matters for regulatory compliance:** IFRS 9, CECL, and Basel IV IRB all
require PD estimates that are aligned to observed default frequencies. A model with
strong AUC but high Brier Score has good ranking but unreliable probability estimates —
it can discriminate between good and bad customers but cannot produce reliable loss
forecasts. The Logistic Regression challenger in this demo uses isotonic calibration
(Platt scaling) to reduce Brier Score explicitly.

---

### KS Statistic (Kolmogorov-Smirnov)
**Range:** 0.0 – 1.0  
**Direction:** Higher is better  
**What it measures:** Maximum separation between the cumulative score distributions of
good borrowers and bad borrowers. It identifies the single score threshold where the
gap between the two populations is largest.

| KS | Interpretation |
|---|---|
| > 0.40 | Good separation for consumer credit |
| 0.30 – 0.40 | Acceptable |
| 0.20 – 0.30 | Marginal; cutoff selection becomes imprecise |
| < 0.20 | Poor separation |

**Practical use:** The KS statistic is commonly used to set or validate the decision
cutoff. A high KS means there is a clear threshold that cleanly separates approvals
from declines with minimal misclassification. A low KS means the score distributions
of good and bad customers overlap significantly, making any cutoff imprecise.

---

## Model Governance Panel

The Model Governance section on each decision screen surfaces metadata that model risk
management (MRM) teams require for oversight without requesting a separate report.

### Model Name and Version
**Format:** `credit_scoring_v1 · v4 · champion`  
The model name, version number, and serving alias are shown on every decision output.
Version numbers are assigned sequentially by MLflow when the model is registered in
Unity Catalog. This links each decision in the audit log to a specific, reproducible
model artifact.

### Model Alias — Champion vs. Challenger
**Champion:** The model version currently serving production traffic. Only one version
per model name can hold the `champion` alias at a time. All decisions in the audit log
are made by the champion.

**Challenger:** One or more model versions registered in Unity Catalog and loaded in
parallel, used for shadow scoring and comparison. In this demo, XGBoost and Logistic
Regression are registered as challengers. Their scores appear in the Model Comparison
panel but do not drive the production decision unless the operator explicitly selects
them via the model selector. Challenger comparison supports the champion/challenger
promotion workflow required under SR 11-7 and similar MRM frameworks.

### MRM Status
**Display:** `MRM status: Active · production`  
Indicates the model's position in the Model Risk Management lifecycle. Relevant
frameworks include:

- **SR 26-2 (US):** Federal Reserve / OCC supervisory guidance on model risk management
- **CMN 4.557 (Brazil):** Central Bank of Brazil resolution on model risk management
  for financial institutions

The demo shows MRM status as informational metadata. In a production deployment, this
field would be populated from a model risk management system or model registry with
formal validation sign-off tracking.

---

## 5 Cs of Credit Framework

The Feature Vector panel groups the nine model input features into the 5 Cs of Credit —
the standard credit analysis framework used by underwriters. This maps machine learning
features to concepts the credit team already uses.

| Category | Features in this model | What it captures |
|---|---|---|
| Character | Payment history, Derogatory marks | Historical willingness to pay; past delinquencies |
| Capacity | Monthly income, Employment tenure, Debt-to-income | Ability to service new debt from current cash flow |
| Capital | Total debt, Open accounts | Existing financial obligations and credit portfolio |
| Conditions | Credit score, Utilization ratio | Current creditworthiness and leverage level |
| Profile | Age | Life-stage proxy for income stability and future earning capacity |

**Feature values shown in the app:**
- Payment history: 0.0 – 1.0 (proportion of payments made on time; 1.0 = perfect)
- Utilization ratio: 0.0 – 1.0 (credit used / credit available; lower is better)
- Debt-to-income: 0.0 – 1.0 (annual total debt / annual income; lower is better)
- Monthly income, total debt: USD values from the synthetic dataset

---

## Adverse Reason Codes

Reason codes identify which features drove a DECLINE or would be the primary adverse
factors for an approved application. They are required by ECOA (US), CCD (EU), and
equivalent regulations in other jurisdictions when providing adverse action notices.

| Code | Label | Trigger condition | What it means |
|---|---|---|---|
| RC01 | Low credit score | Credit score < 600 | Bureau / internal score below standard approval threshold |
| RC02 | High utilization ratio | Utilization > 75% | Customer is using more than 75% of available revolving credit |
| RC03 | Debt-to-income ratio | DTI > 50% | Total annual debt exceeds 50% of annual income |
| RC04 | Derogatory marks | Derogatory marks >= 2 | Multiple late payments, collections, or charge-offs on record |
| RC05 | High debt relative to income | DTI > 50% | Overlaps with RC03 in the current mapping; may reflect combined debt burden |
| RC06 | Insufficient available credit | Available credit < $100 | Credit limit is effectively exhausted; new exposure cannot be approved |
| RC07 | KYC / AML compliance flag | Derogatory marks >= 3 (demo proxy) | Customer triggered KYC review gate |
| RC08 | AML flag | Payment history < 50% | Transaction pattern consistent with AML typology (demo proxy) |

**Insurance segment reason codes** follow the same RC structure but are relabeled to
reflect insurance underwriting language:
- RC01: Credit worthiness below acceptable threshold (< 550)
- RC02: Premium payment reliability below 70%
- RC03: Financial stress index exceeds limit (utilization > 80%)
- RC04: Multiple adverse financial events (derogatory marks >= 2)
- RC05: Total financial exposure too high (DTI > 55%)

---

## Credit Tier

**Values:** STANDARD / PREMIUM (also BASIC and RESTRICTED in portfolio data)  
**What it means:** The customer's credit tier determines product eligibility and credit
limit level. Tier is assigned based on the combined signal from the credit score and
payment history.

| Tier | Criteria | Typical credit limit | Products |
|---|---|---|---|
| PREMIUM | Credit score >= 700 AND payment history > 80% | Higher limits | Full product range; premium cards, private banking |
| STANDARD | Below PREMIUM threshold but above subprime | Standard limits | Core retail credit products |
| BASIC | Below standard threshold | Lower limits | Basic credit products; enhanced monitoring |
| RESTRICTED | Active compliance or arrears flag | Minimal or zero availability | Existing obligations only |

The credit tier and the available credit limit are both checked as part of the
decision logic. A PREMIUM-tier customer with zero available credit (limit exhausted)
still produces a DECLINE (RC06).

---

## Insurance Segment

### Actuarial Score
**Range:** 0 – 100  
**Direction:** In this demo, higher = lower risk (more desirable policyholder)  
**What it measures:** Composite actuarial risk rating derived from age, derogatory
marks (adverse financial events), and payment history (premium payment reliability).
Used by insurance underwriters to determine risk class and premium adjustment.

| Band | Score | Risk Class | Premium treatment |
|---|---|---|---|
| Preferred | 72 – 100 | Preferred | Best available premium; standard or discounted |
| Standard | 52 – 71 | Standard | Base premium rate |
| Substandard | 10 – 51 | Substandard | Rated up: +20% surcharge applied (shown in demo as "Rated Up (+20%)") |

**Demo note:** Score < 52 produces a "Substandard" risk class and triggers the
"Rated Up (+20%)" label on the decision banner. The policy can still be issued
(if the model score clears the 0.450 cutoff and KYC passes) but at a higher premium.

### Risk Class
**Values:** Preferred / Standard / Substandard  
Direct mapping from the actuarial score (see table above). Substandard does not mean
decline — it means the risk is accepted at a rated-up premium. Underwriters use the
risk class to set the premium adjustment factor before binding the policy.

### Open Insurance / Loss Ratio
**Range:** 0.00 – 3.50+  
**What it measures:** Historical loss ratio from cross-insurer data (sourced from
platforms equivalent to CLUE in the US, ISO, SUSEP in Brazil, or EIOPA in the EU).
Loss ratio = claims paid / premiums collected over the customer's insuring history.

| Loss Ratio | Interpretation |
|---|---|
| < 0.60 | Profitable underwriting history; preferred segment |
| 0.60 – 1.00 | Marginal; claims consume most or all premiums |
| > 1.00 | Historical underwriting loss; insurer paid out more than it collected |

The demo also shows `Claims (3yr)` — the number of claims filed across all insurers in
the past 3 years — and `Policies` — the number of active policies currently in force
across insurers. High claims count combined with a loss ratio > 1.0 is a strong
adverse underwriting signal.

---

## Cross-sell Propensity

**Range:** 0.0 – 1.0  
**Shown when:** Banking segment, decision = APPROVE only  
**What it measures:** Estimated probability that the approved customer will accept an
insurance or ancillary product offer. Computed at decision time from the customer's
credit profile and model score.

| Score | Interpretation | Recommended action |
|---|---|---|
| > 0.70 | High intent | Include in immediate outbound campaign; prioritise in next-best-offer |
| 0.40 – 0.70 | Moderate intent | Include in standard next-best-offer queue |
| < 0.40 | Low intent | Omit from campaign or deprioritise |

Three products are scored:
- **Credit Protection:** Credit life / payment-protection insurance — highest propensity
  for newly approved credit customers; natural bancassurance fit
- **Auto Insurance:** Motor insurance — income and age dependent
- **Life Insurance:** Term or whole life — increases with age and income stability

---

## Latency Measurements

All latency figures shown in the Decision Pipeline chart are measured wall-clock time in
milliseconds from the application's perspective.

| Component | Typical range | What is being measured |
|---|---|---|
| Lakebase lookup | 2 – 15 ms (target: p95 < 15 ms) | PostgreSQL query to fetch feature vector from Lakebase (3 tables) |
| KYC / AML gate | 8 – 15 ms | Rules-based compliance check |
| Bureau API | 40 – 60 ms (demo); 80 – 300 ms (production) | External HTTP call to credit bureau |
| Cash Flow API | 30 – 55 ms (demo) | External HTTP call to cash flow analytics provider |
| Actuarial score | 25 – 45 ms (insurance segment) | External HTTP call to actuarial scoring service |
| Open Insurance | 30 – 55 ms (insurance segment) | External cross-insurer data API |
| LightGBM inference | < 1 ms | In-process model inference; model is loaded in memory |
| Pipeline total | 50 – 120 ms end-to-end (demo) | Full time from customer ID entry to decision written to audit log |

**Benchmark from the landing page:** p50 = 4 ms, p95 = 14 ms, p99 = 22 ms at 100
concurrent requests on Databricks Serverless (us-east-1). This reflects the Lakebase
feature lookup component, which is the platform-specific differentiator. The external
API calls (bureau, cash flow) dominate total end-to-end latency in production and are
outside the platform's control.

**Color coding in the pipeline chart:** Internal components (Lakebase, model inference,
KYC) are shown in green. External API calls are shown in amber, reflecting that their
latency is not controlled by the platform.

---

## Glossary Notes

This application uses synthetic data generated from statistical distributions. All
customer IDs, scores, financial figures, and compliance flags are generated
programmatically and do not represent real individuals or actual credit decisions.

External API calls (bureau, cash flow, KYC, actuarial, open insurance) are simulated
with realistic latency ranges and logic. In a production deployment, these would be
replaced by live integrations with the corresponding third-party data providers.

Model metrics (AUC, Brier Score, KS Statistic) are computed on the synthetic test set
generated in Notebook 02. They are representative of model behavior on the demo
dataset and are logged to MLflow alongside the registered model artifact in Unity
Catalog.
