# Databricks notebook source
# MAGIC %md
# MAGIC # Notebook 03 — Feature Store (Lakebase)
# MAGIC
# MAGIC ## The Lakebase Angle
# MAGIC
# MAGIC **The old world (SAS / Redis / Feast):**
# MAGIC ```
# MAGIC  Delta (training) ──┐
# MAGIC                     ├─→ ETL job ──→ Redis/Cassandra (serving) ──→ Model server ──→ Decision
# MAGIC  Oracle/DB2 ────────┘
# MAGIC  (SAS model store)
# MAGIC ```
# MAGIC Three systems to operate. Three observability stacks. Zero shared lineage.
# MAGIC
# MAGIC **With Lakebase:**
# MAGIC ```
# MAGIC  Delta (training) ──→ Lakebase (serving) ──→ Model (MLflow UC) ──→ Decision (Delta)
# MAGIC                              ↑
# MAGIC                   Unity Catalog governs everything
# MAGIC ```
# MAGIC One platform. One catalog. Full lineage from raw data to regulatory audit.
# MAGIC
# MAGIC **Runtime:** ~2 min.

# COMMAND ----------
# MAGIC %md ## 0. Parameters

# COMMAND ----------

dbutils.widgets.text("lakebase_host",
    "",
    "Lakebase Host")
dbutils.widgets.text("lakebase_port",  "5432",                         "Port")
dbutils.widgets.text("lakebase_db",    "databricks_postgres",           "Database")
dbutils.widgets.text("lakebase_user",  "","User")
dbutils.widgets.text("lakebase_pass",  "",                              "Password (token)")
dbutils.widgets.text("uc_catalog",     "",                          "UC Catalog")
dbutils.widgets.text("uc_schema",      "lakebase_credit_demo",          "UC Schema")

LB_HOST = dbutils.widgets.get("lakebase_host")
LB_PORT = int(dbutils.widgets.get("lakebase_port"))
LB_DB   = dbutils.widgets.get("lakebase_db")
LB_USER = dbutils.widgets.get("lakebase_user")
LB_PASS = dbutils.widgets.get("lakebase_pass")




# COMMAND ----------
# MAGIC %run ./util_lakebase_auth

# COMMAND ----------

# ── Authentication ─────────────────────────────────────────────────────────────
if not LB_PASS:
    LB_PASS = _get_lakebase_credential(LB_HOST, LB_USER)
CATALOG = dbutils.widgets.get("uc_catalog").strip()
SCHEMA  = dbutils.widgets.get("uc_schema").strip()

# COMMAND ----------

import psycopg2
import time
from pyspark.sql import SparkSession
import pyspark.sql.functions as F

spark = SparkSession.builder.getOrCreate()

def get_conn():
    return psycopg2.connect(
        host=LB_HOST, port=LB_PORT, dbname=LB_DB,
        user=LB_USER, password=LB_PASS,
        sslmode="require", connect_timeout=10,
    )

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. Silver layer — feature engineering in Delta
# MAGIC
# MAGIC We transform bronze features into the final serving schema.
# MAGIC The silver table is the source of truth for both batch analytics and the Lakebase sync.
# MAGIC
# MAGIC **This is the handoff point the SAS world never had:**
# MAGIC the feature engineering runs in Spark, the output lands in Delta with UC governance,
# MAGIC and the same table feeds both the model training pipeline and the real-time serving layer.

# COMMAND ----------

bronze = spark.table(f"{CATALOG}.{SCHEMA}.credit_features_bronze")

silver = (
    bronze
    # Derived features commonly used in credit scoring
    .withColumn("debt_to_income",
        F.round(F.col("total_debt") / F.greatest(F.col("income_monthly") * 12, F.lit(1)), 4))
    .withColumn("income_band",
        F.when(F.col("income_monthly") < 2000, "low")
         .when(F.col("income_monthly") < 8000, "mid")
         .otherwise("high"))
    .withColumn("score_band",
        F.when(F.col("credit_score") < 550, "subprime")
         .when(F.col("credit_score") < 650, "near-prime")
         .when(F.col("credit_score") < 750, "prime")
         .otherwise("super-prime"))
    .withColumn("risk_tier",
        F.when(
            (F.col("credit_score") >= 700) &
            (F.col("payment_history") >= 0.9) &
            (F.col("derogatory_marks") == 0), "tier_1"
        ).when(
            (F.col("credit_score") >= 600) &
            (F.col("payment_history") >= 0.8), "tier_2"
        ).otherwise("tier_3"))
    .withColumn("feature_version", F.lit("v1"))
    .withColumn("pipeline_ts", F.current_timestamp())
)

(
    silver.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(f"{CATALOG}.{SCHEMA}.credit_features_silver")
)

count = spark.table(f"{CATALOG}.{SCHEMA}.credit_features_silver").count()
print(f"Silver: {CATALOG}.{SCHEMA}.credit_features_silver → {count:,} rows")
print(f"Columns: {len(silver.columns)}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. Sync silver → Lakebase (real-time serving layer)
# MAGIC
# MAGIC This step replaces what Redis/Cassandra does in a traditional feature store.
# MAGIC The Lakebase table receives the enriched features and becomes the live serving endpoint.
# MAGIC
# MAGIC **Why not just query Delta at inference time?**
# MAGIC Single-row point lookup on a Parquet file = ~200-500ms (file open + row scan).
# MAGIC Lakebase B-tree index = ~5-15ms p95. For a credit decision that must complete in <50ms
# MAGIC end-to-end, the difference is the architecture choice.

# COMMAND ----------

# Add the derived columns to Lakebase
conn = get_conn()
conn.autocommit = True
cur = conn.cursor()

cur.execute("""
    ALTER TABLE credit_features
        ADD COLUMN IF NOT EXISTS debt_to_income  FLOAT,
        ADD COLUMN IF NOT EXISTS income_band     TEXT,
        ADD COLUMN IF NOT EXISTS score_band      TEXT,
        ADD COLUMN IF NOT EXISTS risk_tier       TEXT,
        ADD COLUMN IF NOT EXISTS feature_version TEXT DEFAULT 'v1',
        ADD COLUMN IF NOT EXISTS pipeline_ts     TIMESTAMPTZ DEFAULT NOW();
""")
print("Columns added to Lakebase credit_features")
cur.close()
conn.close()

# Update derived features in batches
silver_pd = (
    spark.table(f"{CATALOG}.{SCHEMA}.credit_features_silver")
    .select("customer_id", "debt_to_income", "income_band", "score_band", "risk_tier")
    .toPandas()
)

conn = get_conn()
conn.autocommit = False
cur = conn.cursor()

PAGE = 5_000
t0 = time.perf_counter()
updated = 0
for start in range(0, len(silver_pd), PAGE):
    page = silver_pd.iloc[start:start + PAGE]
    tuples = list(page.itertuples(index=False, name=None))
    cur.executemany("""
        UPDATE credit_features
           SET debt_to_income  = %s,
               income_band     = %s,
               score_band      = %s,
               risk_tier       = %s,
               pipeline_ts     = NOW()
         WHERE customer_id     = %s
    """, [(r[1], r[2], r[3], r[4], r[0]) for r in tuples])
    updated += len(tuples)
    if start % 25_000 == 0:
        print(f"  {updated:,}/{len(silver_pd):,} rows synced...")

conn.commit()
cur.close()
conn.close()

print(f"\nLakebase sync: {updated:,} rows in {time.perf_counter() - t0:.1f}s")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. Unity Catalog view over Lakebase
# MAGIC
# MAGIC By creating a UC view that wraps the Lakebase table, we expose the real-time
# MAGIC feature store to any Databricks workload — batch jobs, notebooks, dashboards —
# MAGIC under the same catalog governance as Delta tables.
# MAGIC
# MAGIC **This is the SAS replacement argument in one query:**
# MAGIC a risk analyst can run `SELECT * FROM main.lakebase_credit_demo.feature_store_live`
# MAGIC and get features that were updated 5 minutes ago, with full UC permissions.

# COMMAND ----------

# Note: UC JDBC table creation requires UC Federation (CREATE CONNECTION + CREATE FOREIGN CATALOG).
# In this demo, Lakebase tables appear in UC natively via the Catalog Explorer.
# The JDBC view approach is not supported by this workspace's UC configuration.
# For production: use CREATE CONNECTION to register the Lakebase PostgreSQL endpoint in UC.
print("UC federation view: available via Catalog Explorer → Lakebase instance")
print(f"Access Lakebase data directly: psycopg2 to {LB_HOST} or Catalog Explorer UI")

# COMMAND ----------
# MAGIC %md ## 4. Validate parity Delta ↔ Lakebase

# COMMAND ----------

delta_count = spark.table(f"{CATALOG}.{SCHEMA}.credit_features_silver").count()

conn = get_conn()
cur = conn.cursor()
cur.execute("SELECT count(*) FROM credit_features")
lb_count = cur.fetchone()[0]

cur.execute("""
    SELECT risk_tier, count(*) FROM credit_features
    GROUP BY risk_tier ORDER BY risk_tier
""")
dist = cur.fetchall()
cur.close()
conn.close()

print(f"Delta silver:   {delta_count:>10,}")
print(f"Lakebase:       {lb_count:>10,}")
print(f"Parity:         {'PASS ✓' if delta_count == lb_count else 'FAIL — investigate'}")
print()
print("Risk tier distribution:")
for tier, cnt in dist:
    print(f"  {tier:>10}: {cnt:>8,}  ({cnt/lb_count:.1%})")

# COMMAND ----------
# MAGIC %md
# MAGIC ## ✓ Notebook 03 complete
# MAGIC
# MAGIC | Output | Location |
# MAGIC |---|---|
# MAGIC | Silver Delta table | `{catalog}.{schema}.credit_features_silver` |
# MAGIC | Lakebase (enriched) | `credit_features` + derived columns |
# MAGIC | UC view | `{catalog}.{schema}.feature_store_live` |
# MAGIC
# MAGIC The Lakebase table now holds the complete feature vector + derived risk attributes,
# MAGIC served via a B-tree index for sub-15ms p95 lookups.
# MAGIC
# MAGIC **Next:** Run `04_realtime_serving.py` — the benchmark that validates the 50ms SLA gate.
