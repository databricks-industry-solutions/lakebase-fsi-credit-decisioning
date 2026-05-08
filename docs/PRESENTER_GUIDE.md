# Presenter Guide — Live App Demo

Practical walkthrough for presenting the credit decisioning demo to a technical risk audience.
Estimated time: 12–15 minutes.

---

## 5 Minutes Before

- [ ] Open the app: `https://lakebase-credit-decisioning-7474660953658462.aws.databricksapps.com`
- [ ] Go to **Technical Analyst** view and run customer `62850` once — this caches the ML model for all subsequent users
- [ ] Keep the architecture diagram open in a second tab: `architecture/reference-v2.drawio.png`
- [ ] Have these three customer IDs ready: `62850` · `13298` · `941`

---

## Opening (1 min)

Start on the **landing page** with both persona cards visible.

> "This is a real-time credit decisioning engine built on Databricks. The two views here reflect how different teams interact with the same platform — the Technical Analyst runs decisions and sees the full scoring trace, the Risk Officer sees portfolio-level outcomes. We'll start with the analyst view."

Click **Technical Analyst**.

---

## Scenario 1 — Clean Approval (3 min)

Enter `62850` and run the decision.

**While it loads, narrate the trace as it builds:**

> "The first step is a feature lookup from Lakebase — that's a PostgreSQL endpoint co-located with Databricks. We're seeing sub-10 millisecond response times. Then bureau enrichment, KYC check, and the LightGBM model inference — all under 50 milliseconds end-to-end."

**When the result appears:**

> "Score 849, clean approval. The feature vector on the right is exactly what the model received — not a reconstruction, not a log, the actual values at decision time. Every reason code is traceable back to a specific feature."

**Point to the decision trace:**

> "This 6-step trace goes into the audit table automatically. If a regulator asks why customer 62850 was approved in March, that's one SQL query against the decision_log table. No cross-referencing three separate systems."

---

## Scenario 2 — Model-Based Decline (2 min)

Enter `13298`.

> "This customer scores 513 — below the approval threshold. Three reason codes: RC01 credit score, RC03 utilization, RC05 debt-to-income. Each one maps to a feature in the vector. The explanation isn't generated post-hoc — it comes from the same feature importances the model was trained on."

---

## Scenario 3 — KYC Hard Block (3 min)

Enter `941`.

> "This is the compliance scenario. The model score is 668 — that would have cleared the threshold. But three derogatory marks on this customer trigger the KYC check, and the application is declined regardless of the score. The audit trail records both: what the model said, and what the rule said."

**Pause here for questions. Common ones:**

- *"How do you handle model updates?"* → MLflow champion alias. Run retraining, register the new version, promote to champion. The app loads whichever version has the alias — zero redeployment.
- *"What's the data freshness?"* → Features are synced from Delta Silver to Lakebase via the pipeline. For production, that's configurable — could be near-real-time with streaming or batch on a schedule.
- *"How does the audit trail satisfy Basel IV / BACEN?"* → Every decision row has: customer_id, feature vector, model version, reason codes, timestamp, latency. Unity Catalog adds lineage — the auditor can trace from raw data to model to decision in one catalog query.

---

## Risk Officer View (2 min)

Click **← Home** and go to **Risk Officer**.

> "The same Lakebase tables that the scoring engine writes to are feeding this dashboard in real time. Approval rate, portfolio exposure by tier, decline reasons, SLA compliance. This isn't a separate BI system — it reads directly from the OLTP audit log."

**Point to the manual review queue:**

> "Applications that fall in the borderline range — say score between 40% and 50% — surface here for human review. The AI summary gives the underwriter the key facts without digging through raw data. The approve/decline recommendation stays with the human."

---

## Closing (1 min)

> "The architecture decision we made: Lakebase as the single OLTP layer. Training data in Delta, serving from Lakebase, every decision logged and auditable through Unity Catalog. One platform to operate, one lineage graph to maintain, one team. That's the SAS replacement narrative — not just a faster backend, but a simpler one."

---

## Anticipated Technical Questions

| Question | Answer |
|---|---|
| What PostgreSQL version? | 17 — Databricks manages it, no admin overhead |
| Can we bring our own model? | Yes — any MLflow-compatible model, Python or otherwise |
| How do we connect our existing bureau API? | The mock uses a standard REST pattern — production replaces the mock function with your bureau endpoint |
| What's the Lakebase SLA? | p99 < 22ms at 100 concurrent requests in our benchmark. Databricks serverless scales automatically |
| Can we query Lakebase directly from SQL? | Yes — via Unity Catalog, `SELECT * FROM catalog.schema.feature_store_live` |
| How do you handle token expiry? | The app uses a Service Principal with generate_database_credential — auto-renews, no manual rotation |
| What does the migration path look like from SAS? | Delta replaces the SAS training environment. Lakebase replaces the feature store. MLflow + UC replaces the model registry. Each component is independent — you can migrate incrementally |
