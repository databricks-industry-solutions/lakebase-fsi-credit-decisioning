# Databricks notebook source
# MAGIC %md
# MAGIC # Notebook 01 — Synthetic Credit Features
# MAGIC
# MAGIC Generates realistic synthetic credit features and writes them to:
# MAGIC - **Delta table** (Unity Catalog) — for model training and batch processing
# MAGIC - **Lakebase** (PostgreSQL) — as the real-time serving layer
# MAGIC
# MAGIC This is the foundation of the unified-platform narrative:
# MAGIC the same dataset lives in Delta (training) and Lakebase (serving) —
# MAGIC no ETL pipeline between two separate systems.
# MAGIC
# MAGIC **Runtime:** ~5 min for 100K rows. Re-run with `N_ROWS=1_000_000` for performance testing.

# COMMAND ----------
# MAGIC %md ## 0. Parameters

# COMMAND ----------

dbutils.widgets.text("lakebase_host", "",
    "Lakebase Host")
dbutils.widgets.text("lakebase_port",  "5432",  "Port")
dbutils.widgets.text("lakebase_db",    "databricks_postgres", "Database")
dbutils.widgets.text("lakebase_user", "", "User")
dbutils.widgets.text("lakebase_pass",  "", "Password (token from 'Generate credential')")
dbutils.widgets.text("uc_catalog",    "", "UC Catalog")
dbutils.widgets.text("uc_schema",      "lakebase_credit_demo", "UC Schema")
dbutils.widgets.text("n_rows",         "100000", "Number of rows")

LB_HOST  = dbutils.widgets.get("lakebase_host")
LB_PORT  = int(dbutils.widgets.get("lakebase_port"))
LB_DB    = dbutils.widgets.get("lakebase_db")
LB_USER  = dbutils.widgets.get("lakebase_user")
LB_PASS  = dbutils.widgets.get("lakebase_pass")




# COMMAND ----------
# MAGIC %run ./util_lakebase_auth

# COMMAND ----------

# ── Authentication ─────────────────────────────────────────────────────────────
if not LB_PASS:
    LB_PASS = _get_lakebase_credential(LB_HOST, LB_USER)
CATALOG  = dbutils.widgets.get("uc_catalog").strip()
SCHEMA   = dbutils.widgets.get("uc_schema").strip()
N_ROWS   = int(dbutils.widgets.get("n_rows"))

if not LB_HOST:
    raise ValueError("Fill the 'lakebase_host' widget.")
if not LB_USER:
    raise ValueError("Fill the 'lakebase_user' widget.")
if not CATALOG:
    raise ValueError("Fill the 'uc_catalog' widget.")

print(f"Lakebase: {LB_HOST}:{LB_PORT}/{LB_DB}")
print(f"UC:       {CATALOG}.{SCHEMA}")
print(f"Rows:     {N_ROWS:,}")
print(f"Password: {'set (' + str(len(LB_PASS)) + ' chars)' if LB_PASS else 'NOT SET — fill widget above'}")

# COMMAND ----------
# MAGIC %md ## 1. Dependencies

# COMMAND ----------

# psycopg2-binary ships with DBR 13+. If missing: Compute → Libraries → PyPI → psycopg2-binary
import psycopg2
import psycopg2.pool
from psycopg2.extras import execute_values
import random
import math
import time
from datetime import date, timedelta
from pyspark.sql import SparkSession
from pyspark.sql.types import (
    StructType, StructField, LongType, IntegerType,
    DoubleType, FloatType, StringType,
)
import pyspark.sql.functions as F

spark = SparkSession.builder.getOrCreate()
print("Spark version:", spark.version)
print("psycopg2 version:", psycopg2.__version__)

# COMMAND ----------
# MAGIC %md ## 2. Generate synthetic credit features
# MAGIC
# MAGIC Schema matches a realistic credit decisioning feature table:
# MAGIC
# MAGIC | Field | Type | Description |
# MAGIC |---|---|---|
# MAGIC | customer_id | BIGINT PK | Unique identifier |
# MAGIC | age | INT | Age in years |
# MAGIC | income_monthly | DECIMAL(12,2) | Gross monthly income |
# MAGIC | credit_score | INT | Internal credit score (300–850) |
# MAGIC | open_accounts | INT | Number of active credit lines |
# MAGIC | total_debt | DECIMAL(14,2) | Total outstanding debt |
# MAGIC | payment_history | FLOAT | % of on-time payments (0–1) |
# MAGIC | utilization | FLOAT | Credit utilization ratio (0–1) |
# MAGIC | months_employed | INT | Employment tenure |
# MAGIC | derogatory_marks | INT | Negative credit events (0–5) |
# MAGIC | last_updated | DATE | Feature timestamp |

# COMMAND ----------

random.seed(42)

def _beta_int(lo, hi, a=2.0, b=5.0):
    """Beta-distributed integer — skewed toward lower end (realistic for most credit features)."""
    return lo + int(random.betavariate(a, b) * (hi - lo))

def _generate_customer(i: int) -> tuple:
    age = _beta_int(18, 75, 3, 3)
    income = round(random.lognormvariate(math.log(5000), 0.8), 2)  # log-normal income
    credit_score = _beta_int(300, 850, 3, 1.5)  # right-skewed (more low scores)
    open_accounts = _beta_int(0, 20, 2, 5)
    total_debt = round(max(0, random.lognormvariate(math.log(15000), 1.2)), 2)
    payment_history = round(min(1.0, max(0.0, random.betavariate(7, 2))), 4)
    utilization = round(min(1.0, max(0.0, random.betavariate(2, 4))), 4)
    months_employed = _beta_int(0, 480, 2, 6)  # 0–40 years
    derogatory_marks = _beta_int(0, 5, 1, 8)  # most customers have 0
    last_updated = date.today() - timedelta(days=random.randint(0, 30))
    return (i, age, income, credit_score, open_accounts, total_debt,
            payment_history, utilization, months_employed, derogatory_marks, last_updated)

COLUMNS = [
    "customer_id", "age", "income_monthly", "credit_score", "open_accounts",
    "total_debt", "payment_history", "utilization", "months_employed",
    "derogatory_marks", "last_updated",
]

print(f"Generating {N_ROWS:,} rows...")
t0 = time.perf_counter()
rows = [_generate_customer(i) for i in range(N_ROWS)]
print(f"  Generated in {time.perf_counter() - t0:.1f}s")

# COMMAND ----------
# MAGIC %md ## 3. Write to Delta (Unity Catalog)

# COMMAND ----------

# Use DoubleType for income/debt (serverless compat — avoids Decimal/Date assertion errors)
# DateType is replaced by StringType to avoid Python date object coercion issues in serverless
SCHEMA_STRUCT = StructType([
    StructField("customer_id",      LongType(),      False),
    StructField("age",              IntegerType(),   True),
    StructField("income_monthly",   DoubleType(),    True),
    StructField("credit_score",     IntegerType(),   True),
    StructField("open_accounts",    IntegerType(),   True),
    StructField("total_debt",       DoubleType(),    True),
    StructField("payment_history",  FloatType(),     True),
    StructField("utilization",      FloatType(),     True),
    StructField("months_employed",  IntegerType(),   True),
    StructField("derogatory_marks", IntegerType(),   True),
    StructField("last_updated",     StringType(),    True),
])

# Convert date to string for schema compatibility
rows = [r[:10] + (str(r[10]),) for r in rows]

import re as _re
def _validate_identifier(name: str, label: str):
    if not _re.match(r'^[a-zA-Z0-9_]+$', name):
        raise ValueError(f"{label} '{name}' contains invalid characters.")

_validate_identifier(CATALOG, "uc_catalog")
_validate_identifier(SCHEMA,  "uc_schema")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")

df = spark.createDataFrame(rows, schema=SCHEMA_STRUCT)

(
    df.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(f"{CATALOG}.{SCHEMA}.credit_features_bronze")
)

count = spark.table(f"{CATALOG}.{SCHEMA}.credit_features_bronze").count()
print(f"Delta: {CATALOG}.{SCHEMA}.credit_features_bronze → {count:,} rows")

# COMMAND ----------
# MAGIC %md ## 4. Write to Lakebase (PostgreSQL)
# MAGIC
# MAGIC Same data, same schema — but served via a PostgreSQL-compatible endpoint.
# MAGIC This is the **real-time serving layer**: applications query Lakebase directly
# MAGIC during inference, getting sub-50ms feature lookups.
# MAGIC
# MAGIC **Why not just query Delta?** Delta/Parquet reads have ~100-500ms overhead for
# MAGIC single-row lookups. Lakebase gives you a proper OLTP index (B-tree on `customer_id`)
# MAGIC with consistent <15ms p95 at 100 concurrent requests.

# COMMAND ----------

def get_conn():
    return psycopg2.connect(
        host=LB_HOST, port=LB_PORT, dbname=LB_DB,
        user=LB_USER, password=LB_PASS,
        sslmode="require", connect_timeout=10,
    )

conn = get_conn()
conn.autocommit = True
cur = conn.cursor()

# Create table with primary key index for O(log n) point lookups
cur.execute("""
    DROP TABLE IF EXISTS credit_features CASCADE; -- ok: demo setup

    CREATE TABLE credit_features (
        customer_id      BIGINT       PRIMARY KEY,
        age              INT,
        income_monthly   DOUBLE PRECISION,
        credit_score     INT,
        open_accounts    INT,
        total_debt       DOUBLE PRECISION,
        payment_history  FLOAT,
        utilization      FLOAT,
        months_employed  INT,
        derogatory_marks INT,
        last_updated     TEXT
    );

    COMMENT ON TABLE credit_features IS
        'Synthetic credit features — demo for real-time credit decisioning SA';
""")
print("Table credit_features created.")
cur.close()
conn.close()

# Bulk insert in pages of 5,000 rows (optimal for Lakebase)
conn = get_conn()
conn.autocommit = False
cur = conn.cursor()

t0 = time.perf_counter()
PAGE = 5_000
total_inserted = 0

for start in range(0, N_ROWS, PAGE):
    page = rows[start:start + PAGE]
    execute_values(
        cur,
        """INSERT INTO credit_features
           (customer_id, age, income_monthly, credit_score, open_accounts,
            total_debt, payment_history, utilization, months_employed,
            derogatory_marks, last_updated)
           VALUES %s ON CONFLICT DO NOTHING""",
        page,
        page_size=PAGE,
    )
    total_inserted += len(page)
    if start % 25_000 == 0:
        print(f"  {total_inserted:,}/{N_ROWS:,} rows inserted...")

conn.commit()
cur.close()
conn.close()

elapsed = time.perf_counter() - t0
print(f"\nLakebase: credit_features → {total_inserted:,} rows in {elapsed:.1f}s "
      f"({total_inserted / elapsed:,.0f} rows/s)")

# COMMAND ----------
# MAGIC %md ## 5. Validate — cross-check row counts

# COMMAND ----------

# Delta count
delta_count = spark.table(f"{CATALOG}.{SCHEMA}.credit_features_bronze").count()

# Lakebase count
conn = get_conn()
cur = conn.cursor()
cur.execute("SELECT count(*) FROM credit_features")
lb_count = cur.fetchone()[0]
cur.close()
conn.close()

print(f"Delta count:    {delta_count:>10,}")
print(f"Lakebase count: {lb_count:>10,}")
print(f"Match:          {'YES ✓' if delta_count == lb_count else 'NO — investigate'}")

assert delta_count == lb_count == N_ROWS, \
    f"Row count mismatch: Delta={delta_count}, Lakebase={lb_count}, expected={N_ROWS}"

# COMMAND ----------
# MAGIC %md
# MAGIC ## 5b. Create Lakebase table: credit_limits
# MAGIC
# MAGIC Derived from `credit_features` — credit limit tier and available balance per customer.
# MAGIC This is the second Lakebase table: the real-time limit check step in the decision loop.

# COMMAND ----------

conn = get_conn()
conn.autocommit = True
cur = conn.cursor()
cur.execute("""
    DROP TABLE IF EXISTS credit_limits CASCADE; -- ok: demo setup

    CREATE TABLE credit_limits (
        customer_id   BIGINT           PRIMARY KEY,
        credit_limit  DOUBLE PRECISION,
        available     DOUBLE PRECISION,
        utilization   FLOAT,
        tier          TEXT,
        last_updated  TEXT
    );

    COMMENT ON TABLE credit_limits IS
        'Credit limit per customer — derived from features, updated nightly in production';
""")
print("Table credit_limits created.")
cur.close()
conn.close()

# Populate from credit_features (single INSERT … SELECT — no Python loop needed)
conn = get_conn()
conn.autocommit = False
cur = conn.cursor()
cur.execute("""
    INSERT INTO credit_limits
    SELECT
        customer_id,
        CASE
            WHEN credit_score >= 750 THEN income_monthly * 3.0
            WHEN credit_score >= 650 THEN income_monthly * 1.5
            ELSE                           income_monthly * 0.5
        END AS credit_limit,
        CASE
            WHEN credit_score >= 750 THEN income_monthly * 3.0 * (1.0 - utilization)
            WHEN credit_score >= 650 THEN income_monthly * 1.5 * (1.0 - utilization)
            ELSE                           income_monthly * 0.5 * (1.0 - utilization)
        END AS available,
        utilization,
        CASE
            WHEN credit_score >= 750 THEN 'PREMIUM'
            WHEN credit_score >= 650 THEN 'STANDARD'
            WHEN credit_score >= 550 THEN 'BASIC'
            ELSE                          'RESTRICTED'
        END AS tier,
        last_updated
    FROM credit_features;
""")
conn.commit()

cur.execute("SELECT count(*), avg(credit_limit), avg(available) FROM credit_limits")
lim_row = cur.fetchone()
cur.close()
conn.close()

print(f"credit_limits: {lim_row[0]:,} rows | "
      f"avg_limit=${lim_row[1]:,.0f} | avg_available=${lim_row[2]:,.0f}")

# COMMAND ----------
# MAGIC %md ## 6. Sample rows

# COMMAND ----------

display(spark.table(f"{CATALOG}.{SCHEMA}.credit_features_bronze").limit(10))

# COMMAND ----------
# MAGIC %md
# MAGIC ## ✓ Notebook 01 complete
# MAGIC
# MAGIC | Output | Location |
# MAGIC |---|---|
# MAGIC | Delta table | `{catalog}.{schema}.credit_features_bronze` |
# MAGIC | Lakebase table | `credit_features` ({n_rows} rows) |
# MAGIC
# MAGIC **Next:** Run `02_model_training.py` to train the credit scoring model.
