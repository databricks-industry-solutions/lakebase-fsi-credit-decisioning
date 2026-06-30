# Segment Guide — Banking & Insurance
**FSI Credit Decisioning on Lakebase — SA/SE Field Reference**

This guide covers the two demo segments (Banking and Insurance), with persona-specific talk tracks, regulatory hooks, and customer evidence. Use alongside `DEMO_GUIDE.md` for the full architecture walkthrough.

---

## Banking Segment

### Audience

| Role | What they care about |
|---|---|
| Chief Credit Officer / CRO | Capital efficiency, NPL ratios, regulatory standing |
| Head of Data Science | Model governance, champion/challenger infrastructure, MRM sign-off |
| Credit Risk Director | Feature consistency between training and serving, audit readiness |

---

### Pain Today

- **Fragmented stack**: model trained on Delta, features served from Redis or Cassandra, audit logs in a third system. Three on-call rotations, broken lineage, no single source of truth when the regulator asks.
- **Audit scramble**: BACEN BCB 303 or SR 26-2 asks "why was customer X declined?" — the team correlates logs from three systems and reassembles a timeline under deadline pressure.
- **PD calibration fight**: IFRS 9 / CECL provisioning needs calibrated probabilities, not raw model scores. Capital team and model risk team disagree on whether the model output is reliable enough to use directly in provisioning.

---

### Demo Steps

**1. Landing page**

Select Banking. Show the two persona cards (Technical Analyst, Risk Officer).

> "Two roles, two views — same Lakebase tables underneath. We'll start with the analyst."

**2. Technical Analyst — Customer 62850 (APPROVE, PREMIUM)**

Run customer 62850. Call out each panel in sequence:

- **Lakebase feature lookup (~4ms)**
  > "Feature lookup from Lakebase in about 4ms. Compare that to 80–300ms on an external bureau or Redis-backed feature store. Same latency profile in the notebook where the model was trained — that's why predictions are consistent."

- **5 Cs of Credit grouping**
  > "Character, Capacity, Capital, Conditions, Profile. This is the framing a credit underwriter already uses. We didn't invent new vocabulary — we mapped the feature vector onto it."

- **Model Governance panel**
  > "Model name, version, alias, MRM status — visible on the decision screen without opening a separate report. This is what SR 26-2 asks for: model inventory with materiality tracking."

- **PEP: CLEAR / Fraud Velocity: LOW / Open Banking income verified**
  > "PEP is not an auto-decline — FATF Recommendation 12 requires Enhanced Due Diligence, not rejection. The demo handles this correctly. Open Banking cash flow is a live signal: 62M consents in Brazil, 2.3B API calls per week."

**3. Customer 941 (KYC FAIL)**

Run customer 941.

> "Score 668 clears the model threshold, but the PEP EDD flag triggers a compliance escalation — not an auto-decline. The audit trail shows both outcomes side by side: model recommended approve, compliance rule escalated. Both captured, both traceable."

Point to the dual audit entry. This is the answer to "why was this customer's decision different from what the model predicted?" — the question regulators always ask.

**4. Model selector — switch to XGBoost**

> "Same customer, same feature vector, different model. Score drops from 0.849 to 0.712 on XGBoost. Both scores are visible simultaneously in the comparison panel. This is champion/challenger infrastructure, not a PowerPoint. SR 26-2 and CMN 4.557 require exactly this — you're looking at a working implementation."

**5. Risk Officer view**

Navigate to Risk Officer.

> "Same Lakebase tables. Every decision you just ran shows up here in real time. Approval rate trend, score distribution in FICO bands, decision mix. The analyst and the risk officer are looking at the same data with no ETL between them."

Walk through: approval rate trend → score distribution → decision mix drill-down.

---

### Key Talk Track Lines

> "Three systems means three on-call rotations and three audit logs to correlate when BACEN calls."

> "Feature lookup in 4ms from Lakebase vs. 80–300ms from an external feature store. That gap is why your training-serving skew exists."

> "Model name, version, alias, MRM status — on the decision screen. Not in a separate report the model risk team maintains offline."

> "Score 668 clears the model, compliance escalates. Both outcomes captured. The auditor gets a complete picture, not a partial one."

> "Champion/challenger isn't a slide — it's the model selector you just used. CMN 4.557 and SR 26-2 require this infrastructure to exist. It does."

> "IFRS 9 calibration: the comparison panel shows both models' predicted probabilities. Capital can run their own validation without exporting data to R."

---

### Regulatory Hooks

| Regulation | What it requires | Where it shows in demo |
|---|---|---|
| SR 26-2 (April 2026, replaces SR 11-7) | Model inventory with materiality tiers, champion/challenger tracking | Model governance panel + model selector |
| CMN 4.966/2021 (IFRS 9 BR, effective Jan 2025) | Point-in-Time PD calibration, auditability | Score vs. cutoff display + model comparison |
| LGPD Art. 20 / FCRA / GDPR Art. 22 | Right to explanation — reason codes per decision, legally required | Feature vector + 5 Cs grouping per decision |
| Open Finance Brazil (BCB) | Real-time cash flow data from consents (62M consents, 2.3B calls/week) | Open Banking income signal on customer 62850 |
| FATF Recommendation 12 | PEP = Enhanced Due Diligence, not auto-decline | PEP CLEAR on 62850, EDD escalation on 941 |
| BACEN BCB 303 | Full explainability for credit decisions, audit trail retention | Dual audit log (model + compliance outcomes) |

---

### Customer Evidence

| Company | What they did | Why it's relevant |
|---|---|---|
| Bradesco | Real-time credit decisioning on Databricks, Open Finance integration — largest bancassurance in Brazil | Direct BR market reference, Open Finance + credit |
| Nubank | NuScore 0–1000, nuFormer transformer, 70% risk reduction, NuSeguros bancassurance cross-sell | Modern credit stack + bancassurance model |
| Rabobank | IRB + IFRS 9 on Unity Catalog | Best public regulatory reference for IFRS 9 / capital models |
| DBS Bank | S$1B AI value, 95% of NPLs detected 3+ months early, MAS compliance framework | Best public reference for model governance + regulatory compliance |

---

## Insurance Segment

### Audience

| Role | What they care about |
|---|---|
| Chief Underwriting Officer | Combined ratio, underwriting accuracy, real-time decisioning |
| Head of Actuarial | Model documentation for SUSEP/CNSP filing, calibrated probabilities, validation evidence |
| Chief Risk Officer / CDO | Solvency II capital adequacy, ORSA, data lineage |

---

### Pain Today

- **Batch underwriting**: decisions are overnight runs. The adjuster gives a quote 24 hours after application — competitors with real-time scoring are closing before the callback.
- **Broken lineage**: actuarial model in SAS/R, feature engineering in a separate system, policy admin in a third. No end-to-end lineage, hard to validate for SUSEP technical note filing.
- **Open Insurance not integrated**: OPIN data (cross-insurer claims history) has been available since 2024. Most carriers have not ingested it into their underwriting flow.
- **Bancassurance opportunity untapped**: propensity models for cross-sell exist but run in batch overnight, not connected to the decisioning flow at the moment of credit approval.

---

### Demo Steps

**1. Landing page**

Select Insurance. Show the two persona cards.

> "Same architecture as banking — Lakebase features, MLflow-governed models — but the domain framing shifts to actuarial underwriting."

**2. Underwriting Analyst — Customer 62850**

Run customer 62850. Call out each panel:

- **Actuarial Score and Risk Class**
  > "Actuarial score 62 — Elevated risk class. That triggers an automatic rate-up of +20%. Decision: APPROVE (Rated Up). We don't decline elevated risk customers; we price them correctly."

- **Open Insurance data panel**
  > "Three years of claims history, policy count, realized loss ratio — from OPIN, the cross-insurer data sharing program live since 2024 under SUSEP Circular 662. This customer's prior carrier history is in the feature vector."

- **Underwriting Risk Profile**
  > "Actuarial groups, not the 5 Cs. Loss history, policy behavior, vehicle/property exposure. Same idea as banking — map the feature vector onto the vocabulary the underwriter already uses."

**3. Substandard customer — Rated Up flow**

Run a customer with substandard risk class.

> "Amber indicator: Rated Up. We still approve — at the appropriate premium tier. Every feature that drove the rate class is in the audit log. CNSP 321 requires this actuarial documentation. It's generated automatically."

**4. Model comparison — isotonic regression vs. XGBoost**

> "For insurance pricing, you need calibrated probabilities — a predicted 5% loss ratio must mean 5% realized losses, not just a ranking score. Isotonic regression post-calibration is the actuarial standard. This is what CNSP 321 and Solvency II's ORSA actuarial technical note require: documented calibration methodology, not just model accuracy."

Show both model scores side by side.

> "The actuarial team can validate the calibration curve directly from the model comparison panel. No export to R, no separate environment."

**5. Risk Officer view (insurance framing)**

Navigate to Risk Officer.

> "Portfolio approval rate by risk class, loss ratio trend. The risk officer sees the same real-time picture the underwriter just generated — no overnight batch, no stale numbers."

---

### Key Talk Track Lines

> "Batch overnight underwriting means your competitor with real-time scoring closes the deal before your adjuster calls back."

> "Actuarial score 62 — Elevated. Rate-up +20%. APPROVE. We price elevated risk correctly; we don't decline it and lose the premium."

> "Open Insurance: three years of cross-insurer claims history in the feature vector. SUSEP Circular 662 made this mandatory in 2024. Most carriers haven't ingested it yet."

> "Calibrated probabilities, not ranking scores. A predicted 5% loss ratio must mean 5% realized — that's CNSP 321 and Solvency II ORSA. The isotonic calibration panel shows the curve."

> "CNSP 321 requires an actuarial technical note documenting the model, its validation, and its calibration. This audit trail generates that documentation automatically on every decision."

> "EU AI Act, August 2026: insurance underwriting is Annex III high-risk AI. Conformity assessment required. This governance panel is the evidence base."

---

### Regulatory Hooks

| Regulation | What it requires | Where it shows in demo |
|---|---|---|
| CNSP 321/2015 (Brazil) | Actuarial technical note — model documented, validated, filed with SUSEP | Model governance panel + calibration comparison |
| SUSEP OPIN Circular 635 + 662 | Open Insurance data sharing — cross-insurer claims history | Open Insurance claims panel on customer view |
| Solvency II / EIOPA ORSA | Internal models, risk calibration, capital adequacy documentation | Calibrated probability display + model comparison |
| EU AI Act — Annex III (effective Aug 2026) | Insurance underwriting = high-risk AI, conformity assessment required | Full audit trail per decision + model lineage |
| MAS Notice 126 (Singapore) | Fair dealing in insurance — no discriminatory underwriting without documented basis | Feature vector + reason codes per decision |

---

### Customer Evidence

| Company | What they did | Why it's relevant |
|---|---|---|
| Allianz UK Personal Lines | Migrated from SAS/Oracle to Databricks — pricing and risk teams, £80k/month savings | Best public insurer reference; directly addresses SAS migration objection |
| BNP Paribas Cardif | 90% claims automation, 80M customers | Global bancassurance scale reference |
| BCG market study | 2–4x conversion uplift with AI personalization in bancassurance | Market evidence for cross-sell value proposition |

---

## Cross-sell / Bancassurance (Banking + Insurance)

When the Banking segment shows an APPROVE decision, point to the cross-sell propensity panel.

### What to show

Three propensity scores generated on the same decision call:
- Credit Protection (prestamista)
- Auto Insurance
- Life Insurance

### Talk track

> "When this customer gets approved, Databricks immediately scores them for three bancassurance products — same feature vector, no separate batch run. The features are already in Lakebase. This is exactly how Nubank runs NuSeguros: credit approval triggers an immediate product recommendation. No overnight job, no separate pipeline."

### Life events that trigger cross-sell

Mention these as natural trigger points in the conversation:

| Event | Product |
|---|---|
| New credit approval | Credit protection / prestamista |
| Mortgage origination | Home insurance + life insurance |
| Travel MCC detected on statement | Travel insurance |

> "Every one of these is detectable from the transaction feature vector already in Lakebase. No new data pipeline — just a new model on existing features."

---

*Last updated: 2026-06-30*
