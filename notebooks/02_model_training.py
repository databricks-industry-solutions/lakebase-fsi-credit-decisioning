# Databricks notebook source
# MAGIC %md
# MAGIC # Notebook 02 — Credit Scoring Model (LightGBM + MLflow)
# MAGIC
# MAGIC Trains a binary credit scoring model on the synthetic features from Notebook 01,
# MAGIC logs everything to MLflow, and registers the model in Unity Catalog.
# MAGIC
# MAGIC **The unified-platform point**: training data lives in Delta, the model registry
# MAGIC lives in UC — the same catalog that governs the Lakebase feature table and the
# MAGIC decision log in Notebooks 03–05. One lineage graph across the full pipeline.
# MAGIC
# MAGIC **Runtime:** ~3 min on a single-node cluster (ML Runtime 15.4+).

# COMMAND ----------
# MAGIC %pip install lightgbm

# COMMAND ----------
# MAGIC %md ## 0. Parameters

# COMMAND ----------

dbutils.widgets.text("uc_catalog",  "", "UC Catalog")
dbutils.widgets.text("uc_schema",   "lakebase_credit_demo",  "UC Schema")
dbutils.widgets.text("model_name",  "credit_scoring_v1",     "Registered model name")

CATALOG    = dbutils.widgets.get("uc_catalog").strip()
SCHEMA     = dbutils.widgets.get("uc_schema").strip()
MODEL_NAME = dbutils.widgets.get("model_name").strip()
TABLE_NAME = f"{CATALOG}.{SCHEMA}.credit_features_bronze"

if not CATALOG:
    raise ValueError("Fill the 'uc_catalog' widget.")

print(f"Source table: {TABLE_NAME}")
print(f"Model:        {CATALOG}.{SCHEMA}.{MODEL_NAME}")

# COMMAND ----------
# MAGIC %md ## 1. Dependencies

# COMMAND ----------

import mlflow
import mlflow.lightgbm
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, classification_report
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment("/Shared/lakebase-fsi-credit-decisioning/credit_scoring")

print("LightGBM:", lgb.__version__)
print("MLflow:  ", mlflow.__version__)

# COMMAND ----------
# MAGIC %md ## 2. Load features from Delta

# COMMAND ----------

df = spark.table(TABLE_NAME).toPandas()
print(f"Loaded {len(df):,} rows from {TABLE_NAME}")
df.info()

# COMMAND ----------
# MAGIC %md ## 3. Feature engineering + label derivation
# MAGIC
# MAGIC We derive a binary label from the feature combination — this approximates
# MAGIC how a rules-based scorecard would have labelled historical applications.
# MAGIC
# MAGIC In production this label would come from actual default/repayment outcomes.

# COMMAND ----------

FEATURES = [
    "age", "income_monthly", "credit_score", "open_accounts",
    "total_debt", "payment_history", "utilization", "months_employed",
    "derogatory_marks",
]

def derive_label(row) -> int:
    """Deterministic label: 1 = good (approve), 0 = bad (decline)."""
    score = 0
    if row["credit_score"] >= 650:     score += 3
    elif row["credit_score"] >= 550:   score += 1
    if row["payment_history"] >= 0.9:  score += 2
    elif row["payment_history"] >= 0.7: score += 1
    if row["utilization"] <= 0.30:     score += 2
    elif row["utilization"] <= 0.60:   score += 1
    if row["derogatory_marks"] == 0:   score += 2
    elif row["derogatory_marks"] <= 1: score += 1
    debt_to_income = float(row["total_debt"]) / max(float(row["income_monthly"]) * 12, 1)
    if debt_to_income <= 0.3:          score += 1
    return int(score >= 5)

df["label"] = df.apply(derive_label, axis=1)
approval_rate = df["label"].mean()
print(f"Approval rate: {approval_rate:.1%}  (target: 55–70%)")

X = df[FEATURES].astype(float)
y = df["label"]

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
print(f"Train: {len(X_train):,}  Test: {len(X_test):,}")

# COMMAND ----------
# MAGIC %md ## 4. Train LightGBM + log to MLflow

# COMMAND ----------

PARAMS = {
    "objective":      "binary",
    "metric":         "auc",
    "learning_rate":  0.05,
    "num_leaves":     63,
    "min_child_samples": 20,
    "feature_fraction":  0.8,
    "bagging_fraction":  0.8,
    "bagging_freq":      5,
    "verbose":        -1,
}

with mlflow.start_run(run_name="credit_scoring_lgbm") as run:
    mlflow.log_params(PARAMS)
    mlflow.log_param("n_features", len(FEATURES))
    mlflow.log_param("n_train", len(X_train))
    mlflow.log_param("n_test", len(X_test))
    mlflow.log_param("approval_rate", round(approval_rate, 4))

    dtrain = lgb.Dataset(X_train, label=y_train, feature_name=FEATURES)
    dval   = lgb.Dataset(X_test,  label=y_test,  reference=dtrain)

    callbacks = [lgb.early_stopping(50, verbose=False), lgb.log_evaluation(50)]
    model = lgb.train(
        PARAMS,
        dtrain,
        num_boost_round=500,
        valid_sets=[dval],
        callbacks=callbacks,
    )

    y_pred = model.predict(X_test)
    auc = roc_auc_score(y_test, y_pred)
    mlflow.log_metric("test_auc", auc)
    print(f"\nTest AUC: {auc:.4f}  (target: >0.70)")

    report = classification_report(y_test, (y_pred >= 0.5).astype(int), output_dict=True)
    mlflow.log_metric("precision_1", report["1"]["precision"])
    mlflow.log_metric("recall_1",    report["1"]["recall"])
    mlflow.log_metric("f1_1",        report["1"]["f1-score"])

    # Feature importance
    fi = pd.DataFrame({
        "feature":    FEATURES,
        "importance": model.feature_importance(importance_type="gain"),
    }).sort_values("importance", ascending=False)
    mlflow.log_dict(fi.set_index("feature")["importance"].to_dict(), "feature_importance.json")

    # Log model with input schema
    signature = mlflow.models.infer_signature(X_train, model.predict(X_train))
    mlflow.lightgbm.log_model(
        model,
        artifact_path="model",
        signature=signature,
        input_example=X_train.iloc[:3],
    )

    run_id = run.info.run_id
    print(f"MLflow run:  {run_id}")

# COMMAND ----------
# MAGIC %md ## 5. Register in Unity Catalog

# COMMAND ----------

model_uri = f"runs:/{run_id}/model"
registered = mlflow.register_model(
    model_uri,
    name=f"{CATALOG}.{SCHEMA}.{MODEL_NAME}",
)
print(f"Registered: {registered.name}  version={registered.version}")

# Add alias for serving
client = mlflow.tracking.MlflowClient()
client.set_registered_model_alias(
    name=f"{CATALOG}.{SCHEMA}.{MODEL_NAME}",
    alias="champion",
    version=registered.version,
)
print(f"Alias 'champion' → version {registered.version}")

# COMMAND ----------
# MAGIC %md ## 6. Feature importance (visual)

# COMMAND ----------

display(
    spark.createDataFrame(fi)
    .orderBy("importance", ascending=False)
)

# COMMAND ----------
# MAGIC %md ## 7. Quick sanity check — predict 5 customers

# COMMAND ----------

sample = X_test.iloc[:5].copy()
scores = model.predict(sample)
decisions = ["APPROVE" if s >= 0.5 else "DECLINE" for s in scores]

print("Sample predictions:")
print(f"{'customer_id':>12}  {'score':>8}  {'decision':>10}")
print("-" * 36)
for i, (score, decision) in enumerate(zip(scores, decisions)):
    print(f"{i:>12}  {score:>8.4f}  {decision:>10}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## ✓ Notebook 02 complete
# MAGIC
# MAGIC | Output | Location |
# MAGIC |---|---|
# MAGIC | Trained model | MLflow run `{run_id}` |
# MAGIC | UC registry | `{catalog}.{schema}.{model_name}` (alias: champion) |
# MAGIC | Test AUC | logged in MLflow |
# MAGIC
# MAGIC **Next:** Run `03_feature_store.py` to set up the Lakebase feature store
# MAGIC and sync features to the real-time serving layer.
