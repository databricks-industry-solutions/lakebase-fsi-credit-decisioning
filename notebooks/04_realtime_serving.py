# Databricks notebook source
# MAGIC %md
# MAGIC # Notebook 04 — Real-time Serving Benchmark
# MAGIC
# MAGIC Measures end-to-end credit decisioning latency:
# MAGIC `customer_id → features (Lakebase) → score (MLflow model) → APPROVE/DECLINE`
# MAGIC
# MAGIC **Decision gate:**
# MAGIC - p99 < 50ms at 100 concurrent → Architecture A: Lakebase-only (no Redis)
# MAGIC - p99 ≥ 50ms → Architecture C: Lakebase + Redis hybrid
# MAGIC
# MAGIC Previous spike result: p50=4ms, p95=14ms (feature-only lookup, us-west-2).
# MAGIC This notebook adds the full decision loop: feature + model inference + reason codes.

# COMMAND ----------
# MAGIC %pip install lightgbm

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
dbutils.widgets.text("target_p99_ms",  "50",                             "SLA target p99 (ms)")

LB_HOST      = dbutils.widgets.get("lakebase_host")
LB_PORT      = int(dbutils.widgets.get("lakebase_port"))
LB_DB        = dbutils.widgets.get("lakebase_db")
LB_USER      = dbutils.widgets.get("lakebase_user")
LB_PASS      = dbutils.widgets.get("lakebase_pass")




# COMMAND ----------
# MAGIC %run ./util_lakebase_auth

# COMMAND ----------

# ── Authentication ─────────────────────────────────────────────────────────────
if not LB_PASS:
    LB_PASS = _get_lakebase_credential(LB_HOST, LB_USER)
CATALOG      = dbutils.widgets.get("uc_catalog").strip()
SCHEMA       = dbutils.widgets.get("uc_schema").strip()
MODEL_NAME   = dbutils.widgets.get("model_name").strip()
TARGET_P99   = float(dbutils.widgets.get("target_p99_ms"))

# COMMAND ----------
# MAGIC %md ## 1. Dependencies + model load

# COMMAND ----------

import psycopg2
import psycopg2.pool
import mlflow
import mlflow.lightgbm
import numpy as np
import pandas as pd
import statistics
import random
import time
import concurrent.futures
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
mlflow.set_registry_uri("databricks-uc")

# Load champion model once — reuse across all requests
MODEL_URI = f"models:/{CATALOG}.{SCHEMA}.{MODEL_NAME}@champion"
print(f"Loading model: {MODEL_URI}")
model = mlflow.lightgbm.load_model(MODEL_URI)
print("Model loaded.")

FEATURES = [
    "age", "income_monthly", "credit_score", "open_accounts",
    "total_debt", "payment_history", "utilization", "months_employed",
    "derogatory_marks",
]

CONCURRENCY_LEVELS = [1, 10, 50, 100, 500]
WARMUP_QUERIES     = 100
MEASURE_N          = 500

# COMMAND ----------
# MAGIC %md ## 2. Connection pool

# COMMAND ----------

# Size pool generously: max_conc * 1.5 to avoid pool exhaustion during burst tests
POOL_MAX = max(CONCURRENCY_LEVELS) + 50
pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=5,
    maxconn=POOL_MAX,
    host=LB_HOST, port=LB_PORT, dbname=LB_DB,
    user=LB_USER, password=LB_PASS,
    sslmode="require", connect_timeout=5,
)
print(f"Connection pool: min=5 max={POOL_MAX}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. Full decision loop
# MAGIC
# MAGIC Each request does exactly what production credit decisioning does:
# MAGIC 1. Point lookup in Lakebase by `customer_id` — feature retrieval
# MAGIC 2. Model inference via LightGBM — credit score
# MAGIC 3. Business rules — APPROVE / DECLINE + reason codes
# MAGIC
# MAGIC The timer wraps the full round-trip, not just the DB call.

# COMMAND ----------

REASON_CODES = {
    "credit_score":     (lambda v: v < 600,  "RC01: Credit score below threshold"),
    "payment_history":  (lambda v: v < 0.75, "RC02: Poor payment history"),
    "utilization":      (lambda v: v > 0.75, "RC03: High credit utilization"),
    "derogatory_marks": (lambda v: v >= 2,   "RC04: Multiple derogatory marks"),
    "debt_to_income":   (lambda v: v > 0.50, "RC05: Debt-to-income ratio exceeded"),
}


def decision_request(n_rows: int) -> float:
    """Single credit decisioning request: feature lookup + limit check + model score + decision.
    Returns elapsed milliseconds for the full round-trip.
    """
    customer_id = random.randint(0, n_rows - 1)
    conn = pool.getconn()
    try:
        t_start = time.perf_counter()

        cur = conn.cursor()

        # 1. Feature lookup
        cur.execute(
            "SELECT age, income_monthly, credit_score, open_accounts, total_debt, "
            "payment_history, utilization, months_employed, derogatory_marks, "
            "debt_to_income "
            "FROM credit_features WHERE customer_id = %s",
            (customer_id,),
        )
        row = cur.fetchone()

        if row is None:
            cur.close()
            return (time.perf_counter() - t_start) * 1000

        # 1b. Limit check — second Lakebase table in the same connection
        cur.execute(
            "SELECT available, tier FROM credit_limits WHERE customer_id = %s",
            (customer_id,),
        )
        limit_row = cur.fetchone()
        cur.close()

        # 2. Model inference
        feature_dict = dict(zip(
            FEATURES + ["debt_to_income"],
            [float(v) if v is not None else 0.0 for v in row],
        ))
        X = pd.DataFrame([{f: feature_dict[f] for f in FEATURES}])
        score = float(model.predict(X)[0])

        # 3. Decision: score threshold + limit availability check
        limit_available = float(limit_row[0] if limit_row else 0.0)
        limit_block = limit_available < 100.0
        _decision = "APPROVE" if (score >= 0.5 and not limit_block) else "DECLINE"
        _reasons = [
            msg for field, (check, msg) in REASON_CODES.items()
            if field in feature_dict and check(feature_dict[field])
        ]
        if limit_block:
            _reasons.append("RC06: Insufficient available credit")

        return (time.perf_counter() - t_start) * 1000
    finally:
        pool.putconn(conn)

# Verify single request works before benchmark
n_rows = N_ROWS  # matches the n_rows widget — must equal notebook 01
sample_ms = decision_request(n_rows)
print(f"Single decision request: {sample_ms:.1f}ms  ✓")

# COMMAND ----------
# MAGIC %md ## 4. Benchmark

# COMMAND ----------

results = []
print(f"{'conc':>6}  {'p50':>9}  {'p95':>9}  {'p99':>9}  {'max':>9}  {'tput':>10}")
print("─" * 62)

for conc in CONCURRENCY_LEVELS:
    # Warmup — fill connection pool, warm JIT
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(conc, 50)) as ex:
        list(ex.map(lambda _: decision_request(n_rows), range(WARMUP_QUERIES)))

    # Measure
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as ex:
        latencies = sorted(ex.map(lambda _: decision_request(n_rows), range(MEASURE_N)))
    elapsed = time.perf_counter() - t0

    p50 = round(statistics.median(latencies), 1)
    p95 = round(latencies[int(0.95 * len(latencies))], 1)
    p99 = round(latencies[int(0.99 * len(latencies))], 1)
    mx  = round(max(latencies), 1)
    tput = round(MEASURE_N / elapsed, 0)

    results.append({
        "concurrency": conc, "p50_ms": p50, "p95_ms": p95,
        "p99_ms": p99, "max_ms": mx, "throughput_rps": tput,
    })
    status = "✓" if p99 < TARGET_P99 else "✗"
    print(f"{conc:>6}  {p50:>8.1f}ms  {p95:>8.1f}ms  {p99:>8.1f}ms  {mx:>8.1f}ms  {tput:>9.0f}/s  {status}")

pool.closeall()

# COMMAND ----------
# MAGIC %md ## 5. Results table + decision

# COMMAND ----------

df_results = pd.DataFrame(results)
display(spark.createDataFrame(df_results))

# Architecture decision
worst_p99   = df_results["p99_ms"].max()
at_100_conc = df_results[df_results["concurrency"] == 100]["p99_ms"].values[0]
verdict     = "PASS" if worst_p99 < TARGET_P99 else "FAIL"

print()
print("=" * 60)
print(f"  Full decision loop (feature + model + reason codes)")
print(f"  SLA target:    p99 < {TARGET_P99}ms")
print(f"  Worst p99:     {worst_p99:.1f}ms  (across all concurrency levels)")
print(f"  p99 @ 100req/s: {at_100_conc:.1f}ms")
print(f"  VERDICT:       {verdict}")
print("=" * 60)

if verdict == "PASS":
    print()
    print("  Architecture: A — Lakebase-only feature store")
    print("  Lakebase meets the end-to-end SLA for real-time credit decisioning.")
    print("  No Redis, no Cassandra, no separate serving infrastructure required.")
else:
    print()
    print("  Architecture: C — Lakebase + Redis hybrid serving layer")
    print(f"  p99 {worst_p99:.0f}ms exceeds {TARGET_P99}ms target.")
    print("  Recommendation: add Redis as L1 cache for hot features (top 10% customer_ids).")

# COMMAND ----------
# MAGIC %md ## 6. Latency breakdown (feature vs inference)

# COMMAND ----------

# Separate timing to show where latency comes from
import time as _time

timings = []
_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=5, maxconn=20,
    host=LB_HOST, port=LB_PORT, dbname=LB_DB,
    user=LB_USER, password=LB_PASS, sslmode="require",
)
for _ in range(200):
    cid = random.randint(0, n_rows - 1)
    conn = _pool.getconn()
    try:
        t0 = _time.perf_counter()
        cur = conn.cursor()
        cur.execute(
            "SELECT age, income_monthly, credit_score, open_accounts, total_debt, "
            "payment_history, utilization, months_employed, derogatory_marks "
            "FROM credit_features WHERE customer_id = %s",
            (cid,),
        )
        row = cur.fetchone()
        cur.close()
        t_db = (_time.perf_counter() - t0) * 1000

        t0 = _time.perf_counter()
        X = pd.DataFrame([dict(zip(FEATURES, [float(v) if v is not None else 0.0 for v in row]))])
        _ = model.predict(X)
        t_model = (_time.perf_counter() - t0) * 1000

        timings.append({"db_ms": t_db, "model_ms": t_model, "total_ms": t_db + t_model})
    finally:
        _pool.putconn(conn)
_pool.closeall()

tdf = pd.DataFrame(timings)
print("Latency breakdown (200 sequential requests):")
print(f"  DB (Lakebase):  p50={tdf['db_ms'].quantile(0.5):.1f}ms  p95={tdf['db_ms'].quantile(0.95):.1f}ms  p99={tdf['db_ms'].quantile(0.99):.1f}ms")
print(f"  Model (LightGBM): p50={tdf['model_ms'].quantile(0.5):.1f}ms  p95={tdf['model_ms'].quantile(0.95):.1f}ms")
print(f"  Total:            p50={tdf['total_ms'].quantile(0.5):.1f}ms  p95={tdf['total_ms'].quantile(0.95):.1f}ms  p99={tdf['total_ms'].quantile(0.99):.1f}ms")

# COMMAND ----------
# MAGIC %md
# MAGIC ## ✓ Notebook 04 complete
# MAGIC
# MAGIC | Metric | Value |
# MAGIC |---|---|
# MAGIC | Architecture verdict | PASS/FAIL (see output above) |
# MAGIC | p99 @ 100 concurrent | see results table |
# MAGIC | Throughput | see results table |
# MAGIC
# MAGIC **Next:** Run `05_decision_log.py` — log decisions to Delta and visualize UC lineage.
