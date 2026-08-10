# Databricks notebook source
# MAGIC %md
# MAGIC # Notebook 05 — Decision Log + Unity Catalog Lineage
# MAGIC
# MAGIC Completes the unified-platform story:
# MAGIC every credit decision is logged back to Delta, giving you a full audit trail
# MAGIC traceable through Unity Catalog lineage.
# MAGIC
# MAGIC **The regulatory angle** (BACEN / OCC / Basel IV):
# MAGIC regulators require that you can answer "why was this customer declined?"
# MAGIC In a traditional multi-system stack that means reconstructing a decision from a model log,
# MAGIC a feature extract, and a separate data warehouse — three systems, weeks of work.
# MAGIC
# MAGIC Here, the full chain lives in one catalog:
# MAGIC ```
# MAGIC credit_features_silver  ──→  credit_scoring_v1  ──→  decision_log
# MAGIC       (Delta)                  (MLflow UC)             (Delta)
# MAGIC ```
# MAGIC One `DESCRIBE HISTORY` + one lineage graph in the UC Explorer.
# MAGIC
# MAGIC **Runtime:** ~3 min.

# COMMAND ----------
# MAGIC %pip install lightgbm "databricks-sdk>=0.40.0"

# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
# MAGIC %md ## 0. Parameters

# COMMAND ----------

dbutils.widgets.text("lakebase_host",
    "",
    "Lakebase Host")
dbutils.widgets.text("lakebase_port",  "5432",                          "Port")
dbutils.widgets.text("lakebase_db",    "databricks_postgres",            "Database")
dbutils.widgets.text("lakebase_user",  "", "User")
dbutils.widgets.text("lakebase_pass",  "",                               "Password (token)")
dbutils.widgets.text("uc_catalog",     "",                           "UC Catalog")
dbutils.widgets.text("uc_schema",      "lakebase_credit_demo",           "UC Schema")
dbutils.widgets.text("model_name",     "credit_scoring_v1",              "Registered model")
dbutils.widgets.text("n_decisions",    "10000",                          "Decisions to simulate")

LB_HOST     = dbutils.widgets.get("lakebase_host")
LB_PORT     = int(dbutils.widgets.get("lakebase_port"))
LB_DB       = dbutils.widgets.get("lakebase_db")
LB_USER     = dbutils.widgets.get("lakebase_user")
LB_PASS     = dbutils.widgets.get("lakebase_pass")




# COMMAND ----------
# MAGIC %run ./util_lakebase_auth

# COMMAND ----------

# ── Authentication ─────────────────────────────────────────────────────────────
if not LB_PASS:
    LB_PASS = _get_lakebase_credential(LB_HOST, LB_USER)
CATALOG     = dbutils.widgets.get("uc_catalog").strip()
SCHEMA      = dbutils.widgets.get("uc_schema").strip()
MODEL_NAME  = dbutils.widgets.get("model_name").strip()
N_DECISIONS = int(dbutils.widgets.get("n_decisions"))

# COMMAND ----------

import json
import psycopg2
from psycopg2.extras import execute_values
import mlflow
import mlflow.lightgbm
import pandas as pd
import random
import time
from datetime import datetime, timezone
from pyspark.sql import SparkSession
from pyspark.sql.types import (
    StructType, StructField,
    LongType, StringType, FloatType, DoubleType, TimestampType,
)
import pyspark.sql.functions as F

spark = SparkSession.builder.getOrCreate()
mlflow.set_registry_uri("databricks-uc")

def get_conn():
    return psycopg2.connect(
        host=LB_HOST, port=LB_PORT, dbname=LB_DB,
        user=LB_USER, password=LB_PASS,
        sslmode="require", connect_timeout=10,
    )

FEATURES = [
    "age", "income_monthly", "credit_score", "open_accounts",
    "total_debt", "payment_history", "utilization", "months_employed",
    "derogatory_marks",
]

# Load model
MODEL_URI = f"models:/{CATALOG}.{SCHEMA}.{MODEL_NAME}@champion"
model = mlflow.lightgbm.load_model(MODEL_URI)
model_version = mlflow.tracking.MlflowClient().get_model_version_by_alias(
    f"{CATALOG}.{SCHEMA}.{MODEL_NAME}", "champion"
).version
print(f"Model: {MODEL_URI}  version={model_version}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. Simulate N credit decisions
# MAGIC
# MAGIC We run a batch of decisions to populate the log — same code path as notebook 04
# MAGIC but collecting the full feature snapshot per decision for auditability.

# COMMAND ----------

REASON_CODES = {
    "credit_score":     (lambda v: v < 600,  "RC01"),
    "payment_history":  (lambda v: v < 0.75, "RC02"),
    "utilization":      (lambda v: v > 0.75, "RC03"),
    "derogatory_marks": (lambda v: v >= 2,   "RC04"),
    "debt_to_income":   (lambda v: v > 0.50, "RC05"),
}

dbutils.widgets.text("n_rows", "100000", "Dataset size (rows)")
n_rows = int(dbutils.widgets.get("n_rows"))
conn = get_conn()
cur = conn.cursor()

decisions = []
t0 = time.perf_counter()

for i in range(N_DECISIONS):
    cid = random.randint(0, n_rows - 1)
    t_req = time.perf_counter()

    cur.execute(
        "SELECT age, income_monthly, credit_score, open_accounts, total_debt, "
        "payment_history, utilization, months_employed, derogatory_marks, "
        "debt_to_income "
        "FROM credit_features WHERE customer_id = %s",
        (cid,),
    )
    row = cur.fetchone()
    if row is None:
        continue

    feat = dict(zip(FEATURES + ["debt_to_income"], [float(v) if v is not None else 0.0 for v in row]))
    X = pd.DataFrame([{f: feat[f] for f in FEATURES}])
    score = float(model.predict(X)[0])
    decision = "APPROVE" if score >= 0.5 else "DECLINE"
    reasons = ",".join(
        code for field, (check, code) in REASON_CODES.items()
        if field in feat and check(feat[field])
    )
    latency_ms = (time.perf_counter() - t_req) * 1000

    decisions.append((
        cid,
        json.dumps({f: round(feat[f], 4) for f in FEATURES + ["debt_to_income"]}),  # features_snapshot
        model_version,
        round(score, 6),
        decision,
        reasons or None,
        round(latency_ms, 2),
        datetime.now(timezone.utc),
    ))

    if (i + 1) % 2000 == 0:
        print(f"  {i + 1:,}/{N_DECISIONS:,} decisions...")

cur.close()
conn.close()

elapsed = time.perf_counter() - t0
approve_rate = sum(1 for d in decisions if d[4] == "APPROVE") / len(decisions)
avg_ms = sum(d[6] for d in decisions) / len(decisions)

print(f"\nGenerated {len(decisions):,} decisions in {elapsed:.1f}s")
print(f"Approval rate: {approve_rate:.1%}")
print(f"Avg latency:   {avg_ms:.1f}ms")

# COMMAND ----------
# MAGIC %md ## 2. Write decision_log to Delta (Unity Catalog)

# COMMAND ----------

SCHEMA_DL = StructType([
    StructField("customer_id",        LongType(),      False),
    StructField("features_snapshot",  StringType(),    True),
    StructField("model_version",      StringType(),    True),
    StructField("score",              DoubleType(),    True),
    StructField("decision",           StringType(),    True),
    StructField("reason_codes",       StringType(),    True),
    StructField("latency_ms",         FloatType(),     True),
    StructField("decision_ts",        TimestampType(), True),
])

df_decisions = spark.createDataFrame(decisions, schema=SCHEMA_DL)

(
    df_decisions.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .partitionBy("decision")
    .saveAsTable(f"{CATALOG}.{SCHEMA}.decision_log")
)

count = spark.table(f"{CATALOG}.{SCHEMA}.decision_log").count()
print(f"decision_log (Delta): {CATALOG}.{SCHEMA}.decision_log → {count:,} rows")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2b. Write decision_log to Lakebase (OLTP — same connection as features + limits)
# MAGIC
# MAGIC This is the third Lakebase table. Storing decisions here closes the loop:
# MAGIC a single SQL query against Lakebase answers any regulatory audit request without
# MAGIC touching Delta or the training catalog.

# COMMAND ----------

conn = get_conn()
conn.autocommit = True
cur = conn.cursor()
cur.execute("""
    DROP TABLE IF EXISTS decision_log CASCADE; -- ok: demo setup

    CREATE TABLE decision_log (
        customer_id        BIGINT,
        features_snapshot  TEXT,
        model_version      TEXT,
        score              DOUBLE PRECISION,
        decision           TEXT,
        reason_codes       TEXT,
        latency_ms         FLOAT,
        decision_ts        TIMESTAMPTZ,
        PRIMARY KEY (customer_id, decision_ts)
    );

    CREATE INDEX decision_log_customer_idx ON decision_log (customer_id);
    CREATE INDEX decision_log_ts_idx       ON decision_log (decision_ts DESC);

    COMMENT ON TABLE decision_log IS
        'Every credit decision — queryable for real-time audit, no Delta join needed';
""")
print("Lakebase: decision_log table created.")
cur.close()
conn.close()

conn = get_conn()
conn.autocommit = False
cur = conn.cursor()
t_lb = time.perf_counter()
execute_values(
    cur,
    """INSERT INTO decision_log
       (customer_id, features_snapshot, model_version, score,
        decision, reason_codes, latency_ms, decision_ts)
       VALUES %s ON CONFLICT DO NOTHING""",
    decisions,
    page_size=2_000,
)
conn.commit()
elapsed_lb = time.perf_counter() - t_lb
cur.close()
conn.close()

conn = get_conn()
cur = conn.cursor()
cur.execute("SELECT count(*), avg(latency_ms) FROM decision_log")
lb_row = cur.fetchone()
cur.close()
conn.close()
print(f"Lakebase decision_log: {lb_row[0]:,} rows | avg_latency={lb_row[1]:.1f}ms | "
      f"insert_time={elapsed_lb:.1f}s")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. Audit query — reconstruct any decision
# MAGIC
# MAGIC This is the regulatory audit query. Given a `customer_id` and a `decision_ts`,
# MAGIC you can reconstruct exactly what the model saw and why it decided what it did.
# MAGIC Zero joins to external systems — everything is in the same catalog.

# COMMAND ----------

# Sample: pick a DECLINE decision and reconstruct it
sample_decline = (
    spark.table(f"{CATALOG}.{SCHEMA}.decision_log")
    .filter(F.col("decision") == "DECLINE")
    .orderBy(F.rand())
    .limit(1)
    .toPandas()
)

if not sample_decline.empty:
    row = sample_decline.iloc[0]
    print("=" * 60)
    print("  AUDIT RECONSTRUCTION")
    print("=" * 60)
    print(f"  customer_id:    {row['customer_id']}")
    print(f"  decision_ts:    {row['decision_ts']}")
    print(f"  decision:       {row['decision']}")
    print(f"  score:          {row['score']:.4f}")
    print(f"  reason_codes:   {row['reason_codes']}")
    print(f"  model_version:  {row['model_version']}")
    print(f"  latency_ms:     {row['latency_ms']:.1f}ms")
    print(f"  features:       {row['features_snapshot'][:120]}...")
    print("=" * 60)
    print()
    print("  All three components traceable in UC:")
    print(f"  Raw data  → {CATALOG}.{SCHEMA}.credit_features_silver")
    print(f"  Model     → {CATALOG}.{SCHEMA}.{MODEL_NAME} v{model_version}")
    print(f"  Decision  → {CATALOG}.{SCHEMA}.decision_log")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4. Decision analytics

# COMMAND ----------

display(
    spark.table(f"{CATALOG}.{SCHEMA}.decision_log")
    .groupBy("decision", "reason_codes")
    .agg(
        F.count("*").alias("count"),
        F.round(F.avg("score"), 4).alias("avg_score"),
        F.round(F.avg("latency_ms"), 2).alias("avg_latency_ms"),
    )
    .orderBy("decision", F.desc("count"))
)

# COMMAND ----------
# MAGIC %md
# MAGIC ## 5. Unity Catalog lineage
# MAGIC
# MAGIC Navigate to **Catalog Explorer → {catalog}.{schema}.decision_log → Lineage**
# MAGIC to see the full graph:
# MAGIC
# MAGIC ```
# MAGIC credit_features_bronze (Delta)
# MAGIC         │
# MAGIC         ▼  [notebook 03]
# MAGIC credit_features_silver (Delta)
# MAGIC         │
# MAGIC         ├──→ feature_store_live (UC view → Lakebase)
# MAGIC         │
# MAGIC         ▼  [notebook 02 + 04]
# MAGIC credit_scoring_v1 (MLflow model, UC registry)
# MAGIC         │
# MAGIC         ▼  [notebook 05]
# MAGIC decision_log (Delta, partitioned by decision)
# MAGIC ```
# MAGIC
# MAGIC This lineage graph is the audit artifact. A regulator asking
# MAGIC "show me where this decision came from" gets a single URL.

# COMMAND ----------
# MAGIC %md
# MAGIC ## ✓ Notebook 05 complete — Pipeline is done
# MAGIC
# MAGIC | Table | Purpose |
# MAGIC |---|---|
# MAGIC | `credit_features_bronze` | Raw synthetic features (Delta) |
# MAGIC | `credit_features_silver` | Engineered features (Delta) |
# MAGIC | `feature_store_live` | UC view over Lakebase (real-time serving) |
# MAGIC | `credit_scoring_v1` | LightGBM model (MLflow UC registry) |
# MAGIC | `decision_log` | Full audit trail (Delta, partitioned by decision) |
# MAGIC
# MAGIC **The pitch in one sentence:**
# MAGIC Train in Databricks, serve features from Lakebase in <15ms p95,
# MAGIC log decisions to Delta, audit the full pipeline through Unity Catalog lineage —
# MAGIC without a single external system.
# MAGIC
# MAGIC **Next steps:**
# MAGIC - Run `06_credit_app/` for the interactive UI demo (Databricks App)
# MAGIC - Use the App's Technical Analyst and Risk Officer views to walk through the decision flow
