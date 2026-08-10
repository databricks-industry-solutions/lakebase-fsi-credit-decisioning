# Databricks notebook source
# MAGIC %md
# MAGIC # Notebook 00 — Setup & Connection Verify
# MAGIC
# MAGIC **Start here.** Run this notebook once before notebooks 01–05 to confirm your
# MAGIC environment is ready and your Lakebase connection works.
# MAGIC
# MAGIC ### What you need
# MAGIC
# MAGIC | Requirement | How to get it |
# MAGIC |---|---|
# MAGIC | Databricks workspace with Unity Catalog | Any Databricks edition |
# MAGIC | Lakebase instance | Catalog Explorer → Lakebase → Create |
# MAGIC | Lakebase endpoint host | Instance detail page → copy "Host" |
# MAGIC | Lakebase credential | Instance detail page → "Generate credential" |
# MAGIC | UC catalog with USAGE + CREATE privileges | Ask your workspace admin |
# MAGIC
# MAGIC ### How to get a fresh credential
# MAGIC
# MAGIC **Option A — Workspace UI (simplest):**
# MAGIC 1. Open Catalog Explorer → Lakebase → click your instance
# MAGIC 2. Click **Generate credential** → copy the token
# MAGIC 3. Paste it into the `lakebase_pass` widget below
# MAGIC
# MAGIC **Option B — Databricks CLI:**
# MAGIC ```bash
# MAGIC databricks auth token --profile <your-profile> \
# MAGIC   | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))"
# MAGIC ```
# MAGIC
# MAGIC **Option C — Service Principal (recommended for jobs):**
# MAGIC See `docs/AUTHENTICATION.md` for the full SP setup guide.

# COMMAND ----------
# MAGIC %md ## 0. Parameters

# COMMAND ----------

dbutils.widgets.text("lakebase_host", "", "Lakebase Host (e.g. ep-xxxxx.database.us-east-1.cloud.databricks.com)")
dbutils.widgets.text("lakebase_pass", "", "Lakebase credential (token from 'Generate credential')")
dbutils.widgets.text("lakebase_user", "", "Lakebase user (your Databricks email, or SP client_id for jobs)")
dbutils.widgets.text("lakebase_port", "5432", "Port")
dbutils.widgets.text("lakebase_db",   "databricks_postgres", "Database")
dbutils.widgets.text("uc_catalog",    "", "UC Catalog (must exist with USAGE + CREATE SCHEMA)")
dbutils.widgets.text("uc_schema",     "lakebase_credit_demo", "UC Schema (will be created if missing)")

LB_HOST = dbutils.widgets.get("lakebase_host").strip()
LB_PASS = dbutils.widgets.get("lakebase_pass").strip()
LB_USER = dbutils.widgets.get("lakebase_user").strip()
LB_PORT = int(dbutils.widgets.get("lakebase_port"))
LB_DB   = dbutils.widgets.get("lakebase_db").strip()
CATALOG = dbutils.widgets.get("uc_catalog").strip()
SCHEMA  = dbutils.widgets.get("uc_schema").strip()

if not LB_HOST:
    raise ValueError("Fill the 'lakebase_host' widget before running.")
if not LB_USER:
    raise ValueError("Fill the 'lakebase_user' widget (your Databricks email or SP client_id).")
if not CATALOG:
    raise ValueError("Fill the 'uc_catalog' widget with your Unity Catalog name.")

print(f"Lakebase: {LB_HOST}:{LB_PORT}/{LB_DB}")
print(f"UC:       {CATALOG}.{SCHEMA}")
print(f"Password: {'set (' + str(len(LB_PASS)) + ' chars)' if LB_PASS else 'EMPTY — fill the widget (see instructions above)'}")

# COMMAND ----------
# MAGIC %md ## 1. Test Lakebase connection

# COMMAND ----------

import psycopg2

def test_connection(host, port, db, user, password):
    try:
        conn = psycopg2.connect(
            host=host, port=port, dbname=db,
            user=user, password=password,
            sslmode="require", connect_timeout=10,
        )
        cur = conn.cursor()
        cur.execute("SELECT version()")
        version = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        table_count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, version, table_count
    except Exception as e:
        return False, str(e), 0

if not LB_PASS:
    print("WARN: lakebase_pass widget is empty.")
    print("Notebooks 01-05 will attempt auto-detect your session token.")
    print("If that fails, come back here and fill the widget with a fresh credential.")
else:
    ok, msg, tables = test_connection(LB_HOST, LB_PORT, LB_DB, LB_USER, LB_PASS)
    if ok:
        print("✓ Lakebase connection OK")
        print(f"  Server:        {msg[:80]}")
        print(f"  Public tables: {tables}")
    else:
        print(f"✗ Connection failed: {msg}")
        print()
        print("Troubleshooting:")
        print("  1. Credential expired? Generate a new one from Catalog Explorer.")
        print("  2. Wrong user? Use your Databricks email (interactive) or SP client_id (jobs).")
        print("  3. Private endpoint? Fill the widget — auto-detect only works on public endpoints.")
        print("     See docs/AUTHENTICATION.md for private-endpoint setup.")

# COMMAND ----------
# MAGIC %md ## 2. Create UC schema (run once)

# COMMAND ----------

import re

def _validate_identifier(name: str, label: str):
    if not re.match(r'^[a-zA-Z0-9_]+$', name):
        raise ValueError(f"{label} '{name}' contains invalid characters. Use only letters, digits, underscores.")

_validate_identifier(CATALOG, "uc_catalog")
_validate_identifier(SCHEMA,  "uc_schema")

from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
print(f"Schema ready: {CATALOG}.{SCHEMA}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## ✓ If connection is OK, proceed with the notebooks in order:
# MAGIC
# MAGIC | # | Notebook | What it does | Time |
# MAGIC |---|---|---|---|
# MAGIC | 01 | `01_data_generation` | Generate 100K synthetic credit features → Delta + Lakebase | ~5 min |
# MAGIC | 02 | `02_model_training` | Train LightGBM credit scoring model → MLflow UC registry | ~3 min |
# MAGIC | 03 | `03_feature_store` | Feature engineering in Spark → sync enriched features to Lakebase | ~2 min |
# MAGIC | 04 | `04_realtime_serving` | Benchmark p50/p95/p99 at multiple concurrency levels | ~10 min |
# MAGIC | 05 | `05_decision_log` | Simulate 10K decisions → Delta + Lakebase audit table | ~3 min |
# MAGIC
# MAGIC **All notebooks use the same widgets.** Copy your `lakebase_host`, `lakebase_user`,
# MAGIC `lakebase_pass`, and `uc_catalog` values to each one.
# MAGIC
# MAGIC For automated runs (Databricks Jobs), store credentials in a secret scope named
# MAGIC `lakebase-demo` — see `docs/AUTHENTICATION.md` for details.
