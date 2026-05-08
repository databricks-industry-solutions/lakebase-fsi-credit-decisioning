# Databricks notebook source
# MAGIC %md
# MAGIC # Real-time Credit Decisioning with Lakebase — Setup
# MAGIC
# MAGIC This notebook creates the companion multi-task Workflow to run the full solution
# MAGIC accelerator pipeline. Run all cells to set up the environment and create the job.
# MAGIC
# MAGIC **Steps:**
# MAGIC 1. Attach this notebook to a Serverless cluster (or any DBR 15.4 ML LTS+)
# MAGIC 2. Fill the widgets below with your Lakebase and Unity Catalog details
# MAGIC 3. Run all cells — a multi-task Workflow will be created automatically
# MAGIC 4. Navigate to the Workflow link printed at the end and click **Run Now**
# MAGIC
# MAGIC **Prerequisites:**
# MAGIC - Databricks workspace with Unity Catalog enabled
# MAGIC - Lakebase instance created (Catalog Explorer → Lakebase → Create)
# MAGIC - Secret scope `lakebase-demo` with key `endpoint_path` (see `docs/AUTHENTICATION.md`)
# MAGIC - UC catalog with USAGE + CREATE SCHEMA privileges

# COMMAND ----------
# MAGIC %md ## 0. Parameters

# COMMAND ----------

dbutils.widgets.text("lakebase_host", "", "Lakebase Host (ep-xxxxx.database.<region>.cloud.databricks.com)")
dbutils.widgets.text("lakebase_user", "", "Lakebase user (your Databricks email)")
dbutils.widgets.text("uc_catalog",    "", "UC Catalog")
dbutils.widgets.text("uc_schema",     "lakebase_credit_demo", "UC Schema")
dbutils.widgets.text("n_rows",        "100000", "Dataset size (rows)")

LB_HOST  = dbutils.widgets.get("lakebase_host").strip()
LB_USER  = dbutils.widgets.get("lakebase_user").strip()
CATALOG  = dbutils.widgets.get("uc_catalog").strip()
SCHEMA   = dbutils.widgets.get("uc_schema").strip()
N_ROWS   = dbutils.widgets.get("n_rows").strip()

if not LB_HOST:
    raise ValueError("Fill 'lakebase_host' widget before running.")
if not LB_USER:
    raise ValueError("Fill 'lakebase_user' widget before running.")
if not CATALOG:
    raise ValueError("Fill 'uc_catalog' widget before running.")

import re
for name, val in [("uc_catalog", CATALOG), ("uc_schema", SCHEMA)]:
    if not re.match(r'^[a-zA-Z0-9_]+$', val):
        raise ValueError(f"{name} '{val}' contains invalid characters.")

# COMMAND ----------
# MAGIC %md ## 1. Create Workflow

# COMMAND ----------

import json
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import Task, NotebookTask, JobSettings

w = WorkspaceClient()

WS_PATH    = "/Shared/lakebase-fsi-credit-decisioning/notebooks"
MODEL_NAME = "credit_scoring_v1"

base_params = {
    "lakebase_host": LB_HOST,
    "lakebase_user": LB_USER,
    "lakebase_pass": "",
    "uc_catalog":    CATALOG,
    "uc_schema":     SCHEMA,
    "n_rows":        N_ROWS,
    "model_name":    MODEL_NAME,
    "n_decisions":   "10000",
    "target_p99_ms": "50",
}

notebooks = [
    "01_data_generation",
    "02_model_training",
    "03_feature_store",
    "04_realtime_serving",
    "05_decision_log",
]

tasks = []
for nb in notebooks:
    task = Task(
        task_key=nb,
        notebook_task=NotebookTask(
            notebook_path=f"{WS_PATH}/{nb}",
            base_parameters=base_params,
        ),
        depends_on=[{"task_key": tasks[-1].task_key}] if tasks else [],
    )
    tasks.append(task)

job = w.jobs.create(
    name="lakebase-credit-pipeline",
    tasks=tasks,
    description="Real-time credit decisioning with Lakebase — databricks-industry-solutions SA",
)

displayHTML(f"""
<h3>✓ Workflow created</h3>
<p><a href='/#job/{job.job_id}' target='_blank'>
  Open lakebase-credit-pipeline (Job ID: {job.job_id})
</a></p>
<p>Click <b>Run Now</b> to execute all notebooks in sequence (~23 min).</p>
""")

print(f"Job ID: {job.job_id}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## ✓ Setup complete
# MAGIC
# MAGIC After the Workflow completes:
# MAGIC 1. Deploy the App: see `docs/AUTHENTICATION.md` → Step 7
# MAGIC 2. Open `06_credit_app/app.yaml`, fill `<placeholder>` values, deploy
# MAGIC 3. Visit the App URL and run the three demo scenarios from the sidebar
