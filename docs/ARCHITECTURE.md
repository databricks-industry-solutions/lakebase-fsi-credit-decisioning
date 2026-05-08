# Pitch Guide — Architecture Walkthrough
# Recording: Google Meet, screen share on architecture/reference.png
# Duration: ~4 minutes

## Setup
- Abra `architecture/reference.png` em fullscreen (Preview → View → Best Fit)
- Ou abra `architecture/reference.drawio` no draw.io para versão interativa

---

## Roteiro

### [0:00 – 0:40] The Problem
*Abra a imagem da arquitetura. Aponte para o lado ESQUERDO (legacy stack)*

> "Credit decisioning today requires three separate systems.
> A training environment, a feature store — Redis, Cassandra, Feast —
> and a model serving layer.
> Three teams. Three on-call rotations.
> Three gaps in the lineage trail when the auditor asks
> why a customer was declined."

---

### [0:40 – 1:30] The Databricks Stack
*Aponte para o fluxo central da arquitetura*

> "What we built replaces all of that.
> Raw data comes in, lands in Delta — that's the training layer.
> The same data, after transformation, goes into Lakebase.
> Lakebase is a PostgreSQL endpoint co-located with Databricks.
> The model is trained in a Databricks notebook, registered in MLflow
> under Unity Catalog. And then served from the same platform."

---

### [1:30 – 2:30] The Real-time Path
*Aponte para o caminho de serving: Lakebase → Model → Decision Log*

> "When a credit decision request comes in:
> Step one — feature lookup from Lakebase. Under 15 milliseconds.
> Step two — two external enrichment calls: bureau score and cash flow.
> Step three — KYC check, rules-based, instant.
> Step four — LightGBM inference, under 1 millisecond.
> Step five — the decision is written back to Lakebase
> and logged to Delta with full lineage in Unity Catalog."

---

### [2:30 – 3:20] The Audit Trail
*Aponte para o decision log e UC lineage*

> "Every decision is a row in the audit table.
> The feature vector at decision time. The model version.
> The reason codes. The latency. Everything.
> Unity Catalog shows the lineage:
> raw data → features → model → decision.
> One URL for the auditor. One catalog. One platform."

---

### [3:20 – 4:00] Why This Matters
> "The architecture decision gate: p99 under 22 milliseconds
> at 100 concurrent requests.
> SLA was 50 milliseconds. We cleared it with room to spare.
> No Redis. No Cassandra. No separate feature store.
> Train, serve, audit — one platform."
