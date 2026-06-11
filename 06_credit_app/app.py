"""Credit Decision Engine — Databricks App (serverless) v3.

Real-time credit decisioning: customer_id → Lakebase → model → APPROVE/DECLINE.
Solution Accelerator for FSI — Databricks Lakebase unified feature store.

Auth: SP M2M OAuth (auto-injected by Databricks Apps runtime).
      Falls back to LAKEBASE_PASS env var for local dev.
"""
from __future__ import annotations

from typing import Optional
import json
import os
import random
import time
import urllib.parse
import urllib.request

import dash
from dash import dcc, html, Input, Output, State
import dash_bootstrap_components as dbc
import mlflow
import mlflow.lightgbm
import pandas as pd
import psycopg2
import psycopg2.pool
import plotly.graph_objects as go

# ── Config ────────────────────────────────────────────────────────────────────
LB_HOST           = os.environ.get("LAKEBASE_HOST", "")
LB_PORT           = int(os.environ.get("LAKEBASE_PORT", "5432"))
LB_DB             = os.environ.get("LAKEBASE_DB", "databricks_postgres")
LB_USER           = os.environ.get("LAKEBASE_USER", "")
CATALOG           = os.environ.get("UC_CATALOG", "")
SCHEMA            = os.environ.get("UC_SCHEMA", "lakebase_credit_demo")
MODEL_NM          = os.environ.get("MODEL_NAME", "credit_scoring_v1")
N_ROWS            = int(os.environ.get("N_ROWS", "100000"))
WS_HOST           = os.environ.get("DATABRICKS_HOST", "")
LB_ENDPOINT_PATH  = os.environ.get("LAKEBASE_ENDPOINT_PATH", "")
BUREAU_PROVIDER   = os.environ.get("BUREAU_PROVIDER",   "Experian")
CASHFLOW_PROVIDER = os.environ.get("CASHFLOW_PROVIDER", "Prism Data")

# ── Color palette ─────────────────────────────────────────────────────────────
_RED    = "#FF3621"
_DARK   = "#0F1623"
_DARKER = "#080C14"
_CARD   = "#141E30"
_CARD2  = "#1A2538"
_BORDER = "#253047"
_BORDER2= "#1C2940"
_GREEN  = "#00C851"
_WARN   = "#FFB900"
_BLUE   = "#4DA6FF"
_MUTED  = "#6B7A9C"
_TEXT   = "#D4DCF0"
_LTEXT  = "#EEF2FF"

TIER_COLORS = {
    "PREMIUM":    "#FFD700",
    "STANDARD":   "#A8B4CC",
    "BASIC":      "#6B7A9C",
    "RESTRICTED": "#C0392B",
    "UNKNOWN":    "#4A5568",
}

# Demo customer IDs — loaded at startup for the sidebar quick-select buttons
DEMO_SCENARIOS = {
    "approve":  62850,   # score 849, derogatory=0 → APPROVE
    "decline":  13298,   # score 513, RC01+RC03+RC05 → DECLINE
    "kyc_fail": 941,     # derogatory_marks=3 → KYC FAIL → DECLINE
}

FEATURES = [
    "age", "income_monthly", "credit_score", "open_accounts",
    "total_debt", "payment_history", "utilization", "months_employed",
    "derogatory_marks",
]
FEATURE_LABELS = {
    "age":              "Age",
    "income_monthly":   "Monthly Income",
    "credit_score":     "Credit Score",
    "open_accounts":    "Open Accounts",
    "total_debt":       "Total Debt",
    "payment_history":  "Payment History",
    "utilization":      "Utilization",
    "months_employed":  "Employment",
    "derogatory_marks": "Derogatory Marks",
}
REASON_CODES = {
    "credit_score":     (lambda v: v < 600,  "RC01 — Credit score below 600"),
    "payment_history":  (lambda v: v < 0.75, "RC02 — Payment history below 75%"),
    "utilization":      (lambda v: v > 0.75, "RC03 — Utilization above 75%"),
    "derogatory_marks": (lambda v: v >= 2,   "RC04 — Multiple derogatory marks"),
    "debt_to_income":   (lambda v: v > 0.50, "RC05 — Debt-to-income above 50%"),
}

# 5 Cs of Credit — feature groupings for the Technical Analyst view
_5C_GROUPS = [
    ("Character",  ["payment_history", "derogatory_marks"]),
    ("Capacity",   ["income_monthly", "months_employed", "debt_to_income"]),
    ("Capital",    ["total_debt", "open_accounts"]),
    ("Conditions", ["credit_score", "utilization"]),
    ("Profile",    ["age"]),
]
_5C_FORMAT = {
    "age":              lambda v: f"{int(v)} yrs",
    "income_monthly":   lambda v: f"${v:,.0f}",
    "credit_score":     lambda v: f"{int(v)}",
    "open_accounts":    lambda v: f"{int(v)}",
    "total_debt":       lambda v: f"${v:,.0f}",
    "payment_history":  lambda v: f"{v*100:.1f}%",
    "utilization":      lambda v: f"{v*100:.1f}%",
    "months_employed":  lambda v: f"{int(v)} mo",
    "derogatory_marks": lambda v: f"{int(v)}",
    "debt_to_income":   lambda v: f"{v*100:.1f}%",
}


# ── Mock external APIs ────────────────────────────────────────────────────────
def mock_bureau_call(customer_id: int, internal_credit_score: float) -> dict:
    t0 = time.perf_counter()
    time.sleep(random.uniform(0.040, 0.060))
    bureau_score = int(max(300, min(850, internal_credit_score + random.gauss(0, 25))))
    return {"bureau_score": bureau_score,
            "bureau_latency_ms": round((time.perf_counter() - t0) * 1000, 1)}


def mock_cashflow_call(customer_id: int, income_monthly: float) -> dict:
    t0 = time.perf_counter()
    time.sleep(random.uniform(0.030, 0.055))
    cashflow_score = int(max(0, min(100, income_monthly / 200.0 + random.gauss(0, 8))))
    return {"cashflow_score": cashflow_score,
            "cashflow_latency_ms": round((time.perf_counter() - t0) * 1000, 1)}


def mock_kyc_check(customer_id: int, derogatory_marks: float, payment_history: float) -> dict:
    t0 = time.perf_counter()
    time.sleep(random.uniform(0.008, 0.015))
    if derogatory_marks >= 3:
        return {"status": "FAIL",
                "flag": "RC07: Multiple derogatory marks — KYC review required",
                "kyc_latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
    if payment_history < 0.5:
        return {"status": "FAIL",
                "flag": "RC08: Payment history below 50% — AML flag",
                "kyc_latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
    return {"status": "PASS", "flag": None,
            "kyc_latency_ms": round((time.perf_counter() - t0) * 1000, 1)}


# ── Auth ──────────────────────────────────────────────────────────────────────
def _jwt_sub(token: str) -> str:
    """Extract 'sub' claim from a JWT without verifying signature."""
    try:
        import base64
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # pad to 4-byte boundary
        return json.loads(base64.urlsafe_b64decode(payload)).get("sub", "")
    except Exception:
        return ""


_lb_pg_user: str = ""  # resolved identity (email or SP app-id)


def _get_pg_cred_via_sdk() -> tuple[str, str]:
    """Return (token, postgres_user) via SDK generate_database_credential.

    Official pattern per docs.databricks.com/oltp/projects/external-apps-connect:
      - postgres user  = DATABRICKS_CLIENT_ID (SP's UUID, set by Apps runtime)
      - postgres pass  = short-lived credential from w.postgres.generate_database_credential()
      - SP postgres role must be pre-created with databricks_create_role()
    """
    global _lb_pg_tok, _lb_pg_tok_ts, _lb_pg_user
    if _lb_pg_tok and time.time() - _lb_pg_tok_ts < 840:
        return _lb_pg_tok, _lb_pg_user
    if not LB_ENDPOINT_PATH:
        return "", ""
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        # Use the SDK's dedicated method for postgres credentials
        cred = w.postgres.generate_database_credential(endpoint=LB_ENDPOINT_PATH)
        tok = cred.token if hasattr(cred, "token") else (cred or {}).get("token", "")
        if tok:
            # SP's client_id is both the postgres user and JWT sub
            client_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
            _lb_pg_user = client_id or _jwt_sub(tok) or LB_USER
            _lb_pg_tok, _lb_pg_tok_ts = tok, time.time()
            print(f"Auth: SDK generate_database_credential (auto-refresh) user={_lb_pg_user}")
        return tok, _lb_pg_user
    except AttributeError:
        # SDK version doesn't have w.postgres — fall back to raw API
        try:
            from databricks.sdk import WorkspaceClient
            w = WorkspaceClient()
            result = w.api_client.do(
                "POST", "/api/2.0/postgres/credentials",
                body={"endpoint": LB_ENDPOINT_PATH},
            )
            tok = (result or {}).get("token", "")
            if tok:
                client_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
                _lb_pg_user = client_id or _jwt_sub(tok) or LB_USER
                _lb_pg_tok, _lb_pg_tok_ts = tok, time.time()
                print(f"Auth: SDK api_client fallback user={_lb_pg_user}")
            return tok, _lb_pg_user
        except Exception as e2:
            print(f"Auth: SDK fallback failed ({e2.__class__.__name__}: {e2})")
            return "", ""
    except Exception as e:
        print(f"Auth: SDK failed ({e.__class__.__name__}: {e})")
        return "", ""


def _exchange_for_pg_cred(bearer: str) -> str:
    global _lb_pg_tok, _lb_pg_tok_ts
    if _lb_pg_tok and time.time() - _lb_pg_tok_ts < 840:
        return _lb_pg_tok
    if not (LB_ENDPOINT_PATH and bearer and WS_HOST):
        return ""
    try:
        req = urllib.request.Request(
            f"{WS_HOST}/api/2.0/postgres/credentials",
            data=json.dumps({"endpoint": LB_ENDPOINT_PATH}).encode(),
            headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            tok = json.loads(r.read()).get("token", "")
        if tok:
            _lb_pg_tok, _lb_pg_tok_ts = tok, time.time()
            print("Auth: urllib exchange")
        return tok
    except Exception as e:
        print(f"Auth: urllib exchange failed ({e.__class__.__name__})")
        return ""


def _get_lakebase_token() -> tuple[str, str]:
    """Return (token, postgres_user).

    Priority order:
    1. Cached user bearer (captured from X-Forwarded-Access-Token on page loads) — works
       for any user with CAN_USE on the app; auto-rotates when user reloads the page.
    2. SDK OAuth via /api/2.0/postgres/credentials — fallback for startup / local dev.
    3. LAKEBASE_PASS env / secret — explicit local dev override.
    """
    # 1. Cached bearer from page-load hook (covers all users; no PAT rotation needed)
    if _user_bearer and time.time() - _user_bearer_ts < 2700 and LB_ENDPOINT_PATH:
        tok = _exchange_for_pg_cred(_user_bearer)
        if tok:
            user = _jwt_sub(tok) or LB_USER
            return tok, user

    # 2. SDK OAuth (startup / non-browser contexts)
    if LB_ENDPOINT_PATH:
        tok, user = _get_pg_cred_via_sdk()
        if tok:
            return tok, user

    # 3. Explicit credential (local dev only)
    pass_tok = os.environ.get("LAKEBASE_PASS", "").strip()
    if pass_tok:
        print(f"Auth: LAKEBASE_PASS user={LB_USER}")
        return pass_tok, LB_USER

    print("Auth: no credential — set LAKEBASE_ENDPOINT_PATH (app) or LAKEBASE_PASS (local)")
    return "", ""


def _make_pool():
    tok, pg_user = _get_lakebase_token()
    if not tok:
        raise RuntimeError("No Lakebase credential — check LAKEBASE_PASS or LAKEBASE_ENDPOINT_PATH")
    return psycopg2.pool.ThreadedConnectionPool(
        minconn=2, maxconn=10,
        host=LB_HOST, port=LB_PORT, dbname=LB_DB,
        user=pg_user, password=tok,
        sslmode="require", connect_timeout=5,
    )


_pool = None  # type: Optional[psycopg2.pool.ThreadedConnectionPool]
_pool_ts: float = 0.0
_lb_pg_tok: str = ""
_lb_pg_tok_ts: float = 0.0
# Cached user bearer captured from X-Forwarded-Access-Token on page-load requests
_user_bearer: str = ""
_user_bearer_ts: float = 0.0
_model = None
_model_lock = __import__("threading").Lock()

def _load_model_if_needed() -> None:
    """Lazy model load on first request using available token (user or SP)."""
    global _model, _feature_importances, _model_version
    if _model is not None:
        return
    token = _user_bearer or os.getenv("DATABRICKS_TOKEN", "")
    if not token:
        return
    with _model_lock:
        if _model is not None:
            return
        try:
            old = os.environ.get("DATABRICKS_TOKEN", "")
            os.environ["DATABRICKS_TOKEN"] = token
            mlflow.set_registry_uri("databricks-uc")
            model = mlflow.lightgbm.load_model(f"models:/{CATALOG}.{SCHEMA}.{MODEL_NM}@champion")
            os.environ["DATABRICKS_TOKEN"] = old
            _model = model
            raw = _model.feature_importance(importance_type="gain")
            total = float(raw.sum()) or 1.0
            _feature_importances = {
                FEATURES[i]: round(float(raw[i]) / total, 4)
                for i in range(len(FEATURES))
            }
            try:
                _v = mlflow.MlflowClient().get_model_version_by_alias(
                    f"{CATALOG}.{SCHEMA}.{MODEL_NM}", "champion")
                _model_version = str(_v.version)
            except Exception:
                pass
            print(f"Model loaded lazily ({len(_feature_importances)} features)")
        except Exception as e:
            os.environ["DATABRICKS_TOKEN"] = old if 'old' in dir() else ""
            print(f"WARN model lazy load: {e}")

_feature_importances: dict[str, float] = {}
_model_version: str = "—"

# Try eager load at startup (works if SP has EXECUTE ON FUNCTION for the model)
try:
    mlflow.set_registry_uri("databricks-uc")
    _model = mlflow.lightgbm.load_model(f"models:/{CATALOG}.{SCHEMA}.{MODEL_NM}@champion")
    raw = _model.feature_importance(importance_type="gain")
    total = float(raw.sum()) or 1.0
    _feature_importances = {FEATURES[i]: round(float(raw[i]) / total, 4) for i in range(len(FEATURES))}
    try:
        _v = mlflow.MlflowClient().get_model_version_by_alias(
            f"{CATALOG}.{SCHEMA}.{MODEL_NM}", "champion")
        _model_version = str(_v.version)
    except Exception:
        pass
    print("Model loaded at startup.")
except Exception as e:
    print(f"WARN model startup: {e} — will retry on first user request")

try:
    _pool = _make_pool()
    _pool_ts = time.time()
    print("DB pool ready.")
except Exception as e:
    print(f"WARN pool: {e}")

_system_status = {
    "model": "loaded"    if _model is not None else "error",
    # Pool starts as "connecting" if startup failed; first real request will build it
    "db":    "connected" if _pool  is not None else "connecting",
}


def _reset_pool():
    global _pool, _pool_ts, _lb_pg_tok, _lb_pg_tok_ts
    if _pool is not None:
        try:
            _pool.closeall()
        except Exception:
            pass
        _pool = None
    _lb_pg_tok, _lb_pg_tok_ts = "", 0.0  # force token refresh on next call
    _pool = _make_pool()
    _pool_ts = time.time()


def get_pool():
    global _pool, _pool_ts
    if _pool is None or time.time() - _pool_ts > 2700:
        _reset_pool()
    return _pool


# ── DB queries ────────────────────────────────────────────────────────────────
def query_portfolio_stats() -> dict:
    """Portfolio-level stats for the Risk Officer view: credit exposure by tier + decline reasons."""
    try:
        pool = get_pool()
        conn = pool.getconn()
        try:
            cur = conn.cursor()
            # Credit exposure by tier
            cur.execute(
                "SELECT tier, COUNT(*) as customers, "
                "SUM(credit_limit) as total_limit, AVG(credit_limit) as avg_limit "
                "FROM credit_limits GROUP BY tier ORDER BY avg_limit DESC"
            )
            tier_rows = cur.fetchall()
            # Decline reason frequency
            cur.execute(
                "SELECT unnest(string_to_array(reason_codes, ',')) AS reason, COUNT(*) AS freq "
                "FROM decision_log WHERE decision = %s AND reason_codes IS NOT NULL "
                "GROUP BY reason ORDER BY freq DESC LIMIT 8",
                ("DECLINE",),
            )
            reason_rows = cur.fetchall()
            cur.close()
        finally:
            pool.putconn(conn)
        total_limit = sum(float(r[2] or 0) for r in tier_rows)
        return {
            "tiers":        [(r[0], int(r[1]), float(r[2] or 0), float(r[3] or 0)) for r in tier_rows],
            "total_limit":  total_limit,
            "reason_freq":  [(r[0].strip(), int(r[1])) for r in reason_rows if r[0]],
        }
    except Exception as e:
        print(f"WARN portfolio stats: {e}")
        return {"tiers": [], "total_limit": 0, "reason_freq": []}


def query_executive_stats() -> dict:
    try:
        pool = get_pool()
        conn = pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT customer_id, decision, score, reason_codes, "
                "to_char(decision_ts, 'HH24:MI:SS'), latency_ms "
                "FROM decision_log ORDER BY decision_ts DESC LIMIT 200",
            )
            rows = cur.fetchall()
            cur.close()
        finally:
            pool.putconn(conn)
        total       = len(rows)
        approved    = sum(1 for r in rows if r[1] == "APPROVE")
        kyc_flagged = sum(1 for r in rows if r[3] and ("RC07" in r[3] or "RC08" in r[3]))
        latencies   = [float(r[5]) for r in rows if r[5] is not None]
        avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else None
        return {
            "total":         total,
            "approval_rate": round(approved / total * 100, 1) if total else 0,
            "kyc_flag_rate": round(kyc_flagged / total * 100, 1) if total else 0,
            "avg_latency":   avg_latency,
            "rows":          rows,
        }
    except Exception as e:
        print(f"WARN exec stats: {e}")
        return {"total": 0, "approval_rate": 0, "kyc_flag_rate": 0, "avg_latency": None, "rows": []}


def query_review_queue(n: int = 10) -> list:
    try:
        pool = get_pool()
        conn = pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT customer_id, score, reason_codes, "
                "to_char(decision_ts, 'HH24:MI:SS') "
                "FROM decision_log "
                "WHERE decision = %s AND score BETWEEN 0.40 AND 0.50 "
                "ORDER BY score DESC LIMIT %s",
                ("DECLINE", n),
            )
            rows = cur.fetchall()
            cur.close()
        finally:
            pool.putconn(conn)
        return rows
    except Exception as e:
        print(f"WARN review queue: {e}")
        return []


def query_decision_history(n: int = 20) -> list:
    try:
        pool = get_pool()
        conn = pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT customer_id, decision, score, reason_codes, latency_ms, "
                "to_char(decision_ts, 'HH24:MI:SS') AS ts "
                "FROM decision_log ORDER BY decision_ts DESC LIMIT %s",
                (n,),
            )
            rows = cur.fetchall()
            cur.close()
        finally:
            pool.putconn(conn)
        return [
            {"customer_id": r[0], "decision": r[1], "score": round(float(r[2]), 3),
             "reasons": (r[3] or "").split(",")[0] or "—",
             "latency_ms": r[4], "ts": r[5]}
            for r in rows
        ]
    except Exception as e:
        print(f"WARN history: {e}")
        return []


def run_decision(customer_id: int) -> dict:
    pool = get_pool()
    t0 = time.perf_counter()
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT age, income_monthly, credit_score, open_accounts, total_debt, "
            "payment_history, utilization, months_employed, derogatory_marks, debt_to_income "
            "FROM credit_features WHERE customer_id = %s",
            (customer_id,),
        )
        row = cur.fetchone()
        if row is None:
            cur.close()
            pool.putconn(conn)
            return {"error": f"Customer {customer_id} not found (range: 0–{N_ROWS-1})"}
        cur.execute(
            "SELECT credit_limit, available, tier FROM credit_limits WHERE customer_id = %s",
            (customer_id,),
        )
        limit_row = cur.fetchone()
        cur.close()
    finally:
        pool.putconn(conn)

    feat = dict(zip(FEATURES + ["debt_to_income"],
                    [float(v) if v is not None else 0.0 for v in row]))
    credit_limit = float(limit_row[0]) if limit_row else 0.0
    available    = float(limit_row[1]) if limit_row else 0.0
    tier         = limit_row[2]        if limit_row else "UNKNOWN"
    limit_block  = available < 100.0

    bureau   = mock_bureau_call(int(customer_id), feat.get("credit_score", 650))
    cashflow = mock_cashflow_call(int(customer_id), feat.get("income_monthly", 0))
    kyc      = mock_kyc_check(int(customer_id),
                               feat.get("derogatory_marks", 0),
                               feat.get("payment_history", 1.0))

    if _model is None:
        _load_model_if_needed()
    if _model is None:
        return {"error": "Model not loaded — check UC registry"}

    X = pd.DataFrame([{f: feat[f] for f in FEATURES}])
    score = float(_model.predict(X)[0])

    kyc_blocked = kyc["status"] == "FAIL"
    decision    = "APPROVE" if (score >= 0.5 and not limit_block and not kyc_blocked) else "DECLINE"
    reasons     = [msg for field, (check, msg) in REASON_CODES.items()
                   if field in feat and check(feat[field])]
    if limit_block:
        reasons.append("RC06: Insufficient available credit")
    if kyc_blocked and kyc["flag"]:
        reasons.append(kyc["flag"])

    latency = round((time.perf_counter() - t0) * 1000, 1)

    try:
        conn2 = pool.getconn()
        cur2 = conn2.cursor()
        cur2.execute(
            """INSERT INTO decision_log
               (customer_id, features_snapshot, model_version, score,
                decision, reason_codes, latency_ms, decision_ts)
               VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
               ON CONFLICT DO NOTHING""",
            (customer_id, json.dumps({f: round(feat[f], 4) for f in FEATURES}),
             "champion", round(score, 6), decision,
             ",".join(reasons) or None, latency),
        )
        conn2.commit()
        cur2.close()
        pool.putconn(conn2)
    except Exception as _audit_exc:
        print(f"WARN audit write: {_audit_exc.__class__.__name__}: {_audit_exc}")  # never blocks decision

    return {
        "customer_id":         customer_id,
        "score":               round(score, 4),
        "decision":            decision,
        "reasons":             reasons,
        "features":            feat,
        "latency_ms":          latency,
        "credit_limit":        round(credit_limit, 2),
        "available":           round(available, 2),
        "tier":                tier,
        "bureau_score":        bureau["bureau_score"],
        "bureau_latency_ms":   bureau["bureau_latency_ms"],
        "bureau_provider":     BUREAU_PROVIDER,
        "cashflow_score":      cashflow["cashflow_score"],
        "cashflow_latency_ms": cashflow["cashflow_latency_ms"],
        "cashflow_provider":   CASHFLOW_PROVIDER,
        "kyc_status":          kyc["status"],
        "kyc_flag":            kyc["flag"],
        "kyc_latency_ms":      kyc["kyc_latency_ms"],
    }


# ── Feature contribution helpers ──────────────────────────────────────────────
def _good_score(feature: str, value: float) -> float:
    """Normalize feature value to [0, 1] where 1 = best for creditworthiness."""
    if feature == "credit_score":
        return max(0.0, min(1.0, (value - 300) / 550))
    if feature == "payment_history":
        return max(0.0, min(1.0, value))
    if feature == "utilization":
        return max(0.0, min(1.0, 1.0 - value))
    if feature == "income_monthly":
        return max(0.0, min(1.0, value / 12000))
    if feature == "months_employed":
        return max(0.0, min(1.0, value / 84))
    if feature == "derogatory_marks":
        return max(0.0, min(1.0, 1.0 - value / 4))
    if feature == "open_accounts":
        return max(0.0, min(1.0, 1.0 - abs(value - 5) / 10))
    if feature == "total_debt":
        return max(0.0, min(1.0, 1.0 - value / 80000))
    if feature == "age":
        return 0.8 if 28 <= value <= 58 else 0.55
    return 0.5


def _compute_contributions(feat: dict) -> list[tuple[str, float]]:
    """Per-customer approximate feature contribution (positive → APPROVE, negative → DECLINE)."""
    result = []
    for f in FEATURES:
        if f not in feat or f not in _feature_importances:
            continue
        good = _good_score(f, feat[f])
        contrib = (good - 0.5) * 2.0 * _feature_importances[f]
        result.append((FEATURE_LABELS.get(f, f), round(contrib, 4)))
    return sorted(result, key=lambda x: x[1])  # most negative first → top


# ── Chart builders ────────────────────────────────────────────────────────────
def _pipeline_fig(lakebase_ms, kyc_ms, bureau_ms, cashflow_ms, bureau_prov, cashflow_prov):
    labels = [
        "LightGBM inference",
        "KYC / AML gate",
        "Lakebase lookup",
        f"{cashflow_prov} — cash flow",
        f"{bureau_prov} — bureau",
    ]
    values = [0.5, kyc_ms, lakebase_ms, cashflow_ms, bureau_ms]
    colors = [_GREEN, _GREEN, _GREEN, _WARN, _WARN]
    texts  = ["<1ms", f"{kyc_ms}ms", f"{lakebase_ms}ms", f"{cashflow_ms}ms", f"{bureau_ms}ms"]

    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker_color=colors, marker_line_width=0,
        text=texts, textposition="outside",
        textfont={"size": 11, "color": _MUTED},
        cliponaxis=False,
    ))
    fig.update_layout(
        height=190,
        margin={"t": 4, "b": 4, "l": 170, "r": 55},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis={"showgrid": False, "showticklabels": False, "range": [0, max(values) * 1.4]},
        yaxis={"tickfont": {"size": 11, "color": _MUTED},
               "categoryorder": "array", "categoryarray": labels},
        font={"family": "DM Sans"},
        showlegend=False,
    )
    return fig


def _contribution_fig(contributions: list[tuple[str, float]]):
    if not contributions:
        return go.Figure()
    labels  = [c[0] for c in contributions]
    values  = [c[1] for c in contributions]
    max_abs = max(abs(v) for v in values) or 0.1
    colors  = [_GREEN if v >= 0 else _RED for v in values]
    texts   = [f"+{v*100:.1f}%" if v >= 0 else f"{v*100:.1f}%" for v in values]

    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker_color=colors, marker_line_width=0,
        text=texts, textposition="outside",
        textfont={"size": 10, "color": _MUTED},
        cliponaxis=False,
    ))
    fig.add_vline(x=0, line_width=1, line_color=_BORDER)
    fig.update_layout(
        height=230,
        margin={"t": 4, "b": 4, "l": 130, "r": 65},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis={"showgrid": False, "showticklabels": False,
               "range": [-max_abs * 1.6, max_abs * 1.6]},
        yaxis={"tickfont": {"size": 11, "color": _MUTED}},
        font={"family": "DM Sans"},
        showlegend=False,
    )
    return fig


def _score_gauge_fig(score: float, decision: str):
    d_color = _GREEN if decision == "APPROVE" else _RED
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=round(score * 100),
        domain={"x": [0, 1], "y": [0, 1]},
        title={"text": "Model Score", "font": {"size": 12, "color": _MUTED}},
        number={"suffix": "%", "font": {"size": 32, "color": d_color}},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 0, "tickcolor": _BORDER,
                     "tickvals": [0, 50, 100], "ticktext": ["0", "50%", "100"],
                     "tickfont": {"size": 9, "color": _MUTED}},
            "bar": {"color": d_color, "thickness": 0.65},
            "bgcolor": _BORDER,
            "steps": [
                {"range": [0,  30], "color": "#1C0808"},
                {"range": [30, 50], "color": "#1A1408"},
                {"range": [50, 70], "color": "#081C10"},
                {"range": [70, 100], "color": "#061A08"},
            ],
            "threshold": {
                "line": {"color": _MUTED, "width": 2},
                "thickness": 0.8,
                "value": 50,
            },
        },
    ))
    fig.update_layout(
        height=185,
        margin={"t": 30, "b": 0, "l": 16, "r": 16},
        paper_bgcolor="rgba(0,0,0,0)",
        font={"color": _MUTED, "family": "DM Sans"},
    )
    return fig


def _score_dist_fig(rows: list):
    if len(rows) < 3:
        return None
    # Approximate FICO from model score: score ∈ [0,1] → FICO_est ∈ [300, 850]
    # Cutoff 0.500 → FICO ≈ 575
    to_fico = lambda s: round(300 + float(s) * 550)
    approve_fico = [to_fico(r[2]) for r in rows if r[1] == "APPROVE"]
    decline_fico = [to_fico(r[2]) for r in rows if r[1] != "APPROVE"]
    fig = go.Figure()
    if approve_fico:
        fig.add_trace(go.Histogram(
            x=approve_fico, name="APPROVE", nbinsx=12,
            marker_color=_GREEN, opacity=0.65, marker_line_width=0,
        ))
    if decline_fico:
        fig.add_trace(go.Histogram(
            x=decline_fico, name="DECLINE", nbinsx=12,
            marker_color=_RED, opacity=0.65, marker_line_width=0,
        ))
    fig.add_vline(x=575, line_width=2, line_dash="dash", line_color=_MUTED,
                  annotation_text="cutoff ~575", annotation_position="top right",
                  annotation_font={"size": 9, "color": _MUTED})
    fig.update_layout(
        barmode="overlay",
        height=195,
        margin={"t": 8, "b": 30, "l": 30, "r": 10},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis={
            "tickfont": {"size": 9, "color": _MUTED}, "gridcolor": _BORDER2,
            "range": [300, 850],
            "tickvals": [300, 400, 500, 575, 650, 750, 850],
            "title": {"text": "est. FICO (from model score)", "font": {"size": 8, "color": _MUTED}},
        },
        yaxis={"tickfont": {"size": 9, "color": _MUTED}, "gridcolor": _BORDER2},
        legend={"font": {"size": 10, "color": _MUTED}, "bgcolor": "rgba(0,0,0,0)",
                "x": 0.02, "y": 0.98},
        font={"family": "DM Sans"},
        bargap=0.04,
    )
    return fig


# ── Business chart builders ───────────────────────────────────────────────────
TIER_ORDER = ["PREMIUM", "STANDARD", "BASIC", "RESTRICTED", "UNKNOWN"]
TIER_PALETTE = {
    "PREMIUM":    "#FFD700",
    "STANDARD":   "#4DA6FF",
    "BASIC":      "#6B7A9C",
    "RESTRICTED": "#FF3621",
    "UNKNOWN":    "#374151",
}

REASON_LABELS = {
    "RC01": "Low credit score",
    "RC02": "Poor payment history",
    "RC03": "High utilization",
    "RC04": "Derogatory marks",
    "RC05": "Debt-to-income exceeded",
    "RC06": "Insufficient available credit",
    "RC07": "KYC — multiple derogatory marks",
    "RC08": "KYC — AML flag",
}


def _decline_reasons_fig(reason_freq: list):
    if not reason_freq:
        return None
    labels = [REASON_LABELS.get(r[0], r[0]) for r in reason_freq]
    values = [r[1] for r in reason_freq]
    max_v  = max(values) or 1
    colors = [_RED if v == max_v else _WARN if v >= max_v * 0.6 else _MUTED for v in values]
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker_color=colors, marker_line_width=0,
        text=[str(v) for v in values], textposition="outside",
        textfont={"size": 11, "color": _MUTED},
        cliponaxis=False,
    ))
    fig.update_layout(
        height=max(180, len(labels) * 32 + 20),
        margin={"t": 4, "b": 4, "l": 5, "r": 50},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis={"showgrid": False, "showticklabels": False,
               "range": [0, max(values) * 1.25]},
        yaxis={"tickfont": {"size": 11, "color": _MUTED},
               "categoryorder": "total ascending"},
        font={"family": "DM Sans"},
        showlegend=False,
    )
    return fig


def _tier_distribution_fig(tiers: list):
    if not tiers:
        return None
    labels = [t[0] for t in tiers]
    counts = [t[1] for t in tiers]
    colors = [TIER_PALETTE.get(t[0], TIER_PALETTE["UNKNOWN"]) for t in tiers]
    fig = go.Figure(go.Bar(
        x=labels, y=counts,
        marker_color=colors, marker_line_width=0,
        text=[f"{c:,}" for c in counts], textposition="auto",
        textfont={"size": 11, "color": "#0F1623"},
    ))
    fig.update_layout(
        height=180,
        margin={"t": 4, "b": 20, "l": 30, "r": 10},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis={"tickfont": {"size": 11, "color": _MUTED}, "showgrid": False},
        yaxis={"tickfont": {"size": 9, "color": _MUTED}, "gridcolor": _BORDER2},
        font={"family": "DM Sans"},
        showlegend=False,
    )
    return fig


# ── UI helpers ────────────────────────────────────────────────────────────────
def _card(children, className="db-card", style=None, **kw):
    return dbc.Card(children, className=className, style={
        "backgroundColor": _CARD, "border": f"1px solid {_BORDER}",
        "borderRadius": "12px", **(style or {}),
    }, **kw)


def _section_label(text, right=None):
    return html.Div(style={"display": "flex", "alignItems": "center", "marginBottom": "12px"}, children=[
        html.Div(text, style={
            "fontSize": "11px", "fontWeight": "600", "color": _MUTED,
            "textTransform": "uppercase", "letterSpacing": "0.9px",
        }),
        *([] if right is None else [html.Div(right, style={"marginLeft": "auto"})]),
    ])


def _stat(label, value, color=_TEXT, sub=None):
    return html.Div([
        html.Div(label, style={"fontSize": "11px", "color": _MUTED, "textTransform": "uppercase",
                               "letterSpacing": "0.8px", "marginBottom": "3px"}),
        html.Div(value, style={"fontSize": "22px", "fontWeight": "700", "color": color,
                               "letterSpacing": "-0.5px"}),
        *([] if sub is None else [html.Div(sub, style={"fontSize": "11px", "color": _MUTED,
                                                        "marginTop": "2px"})]),
    ])


def _badge(text, color):
    return html.Span(text, style={
        "display": "inline-block", "padding": "2px 10px", "borderRadius": "20px",
        "fontSize": "11px", "fontWeight": "700", "letterSpacing": "0.4px",
        "backgroundColor": f"{color}22", "border": f"1px solid {color}55", "color": color,
    })


def _tier_badge(tier: str):
    color = TIER_COLORS.get((tier or "UNKNOWN").upper(), TIER_COLORS["UNKNOWN"])
    return html.Span((tier or "UNKNOWN").upper(), style={
        "display": "inline-block", "padding": "2px 10px", "borderRadius": "20px",
        "fontSize": "11px", "fontWeight": "700", "letterSpacing": "0.4px",
        "backgroundColor": f"{color}22", "border": f"1px solid {color}55", "color": color,
    })


def _labeled_chip(label: str, value: str, color: str):
    """Badge with visible label prefix — e.g. 'Tier: PREMIUM', 'KYC: PASS'."""
    return html.Span([
        html.Span(f"{label}: ", style={
            "fontWeight": "500", "fontSize": "9px", "opacity": "0.75", "letterSpacing": "0.3px",
        }),
        html.Span(value.upper(), style={"fontWeight": "700"}),
    ], style={
        "display": "inline-flex", "alignItems": "center", "gap": "1px",
        "padding": "2px 9px", "borderRadius": "20px", "fontSize": "11px",
        "letterSpacing": "0.3px",
        "backgroundColor": f"{color}22", "border": f"1px solid {color}55", "color": color,
    })


def _credit_bar(available: float, credit_limit: float):
    pct = round(available / credit_limit * 100) if credit_limit > 0 else 0
    bar_color = _GREEN if pct > 50 else _WARN if pct > 20 else _RED
    return html.Div([
        html.Div(style={"display": "flex", "justifyContent": "space-between",
                        "marginBottom": "5px"}, children=[
            html.Span("Available Credit", style={"fontSize": "12px", "color": _MUTED}),
            html.Span(f"${available:,.0f}  /  ${credit_limit:,.0f}",
                      style={"fontSize": "12px", "color": _TEXT, "fontWeight": "500"}),
        ]),
        html.Div(style={"backgroundColor": _BORDER, "borderRadius": "5px", "height": "7px"}, children=[
            html.Div(style={
                "backgroundColor": bar_color, "borderRadius": "5px",
                "height": "7px", "width": f"{pct}%",
            }),
        ]),
        html.Div(f"{pct}% available", style={"fontSize": "10px", "color": _MUTED, "marginTop": "4px"}),
    ])


def _live_badge():
    return html.Span([
        html.Span(className="live-dot"),
        html.Span("LIVE · Lakebase", style={"fontSize": "11px", "color": _GREEN, "fontWeight": "600"}),
    ])


def _status_pill(label, ok):
    color = _GREEN if ok else _RED
    icon  = "✓" if ok else "✗"
    return html.Span(f"{icon} {label}", style={
        "fontSize": "10px", "color": color, "fontWeight": "600",
        "padding": "1px 7px", "borderRadius": "10px",
        "backgroundColor": f"{color}18", "border": f"1px solid {color}44",
        "marginRight": "6px",
    })


# ── App init ──────────────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.CYBORG,
        "https://fonts.googleapis.com/css2?family=DM+Sans:ital,wght@0,300;0,400;0,500;0,600;0,700&display=swap",
    ],
    title="Credit Decision Engine · Databricks",
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
    suppress_callback_exceptions=True,
)
server = app.server


@server.before_request
def _capture_user_bearer():
    """Cache X-Forwarded-Access-Token on any request that carries it (initial page loads)."""
    global _user_bearer, _user_bearer_ts, _lb_pg_tok, _lb_pg_tok_ts
    import flask as _fl
    bearer = _fl.request.headers.get("X-Forwarded-Access-Token", "")
    if bearer:
        _user_bearer, _user_bearer_ts = bearer, time.time()
        _lb_pg_tok, _lb_pg_tok_ts = "", 0.0  # force pool to use fresh token on next call
        print(f"Auth: captured X-Forwarded-Access-Token user={_jwt_sub(bearer) or 'unknown'}")


_PAGE  = {"fontFamily": "'DM Sans', -apple-system, sans-serif",
          "backgroundColor": _DARK, "minHeight": "100vh", "color": _TEXT}
_BOLD  = {"fontWeight": "700"}
_UPPER = {"textTransform": "uppercase", "letterSpacing": "0.8px"}


# ── Persistent header ─────────────────────────────────────────────────────────
def _header():
    return html.Div(style={
        "background": f"linear-gradient(180deg, {_DARKER} 0%, #0C1420 100%)",
        "borderBottom": f"1px solid {_BORDER2}",
        "padding": "0 36px", "position": "sticky", "top": "0", "zIndex": "100",
    }, children=[
        dbc.Row(align="center", style={"height": "60px"}, children=[
            dbc.Col(width="auto", children=[
                html.Div(style={"display": "flex", "alignItems": "center", "gap": "10px"}, children=[
                    html.Span("◆", style={"color": _RED, "fontSize": "20px"}),
                    html.Span("Credit Decision Engine", style={
                        "fontSize": "16px", "fontWeight": "600", "color": _LTEXT,
                        "letterSpacing": "-0.3px",
                    }),
                    html.Span("· Lakebase", style={"fontSize": "13px", "color": _MUTED}),
                ]),
            ]),
            dbc.Col(width="auto", children=[html.Div(id="nav-links")]),
            dbc.Col(children=[
                html.Div(id="header-stats", style={
                    "display": "flex", "gap": "28px", "justifyContent": "flex-end",
                    "alignItems": "center",
                }),
            ]),
        ]),
    ])


# ── Landing page ──────────────────────────────────────────────────────────────
_LANDING = html.Div(id="page-landing", children=[
    html.Div(style={"padding": "60px 40px 40px", "maxWidth": "900px", "margin": "0 auto"}, children=[

        # Hero
        html.Div(style={"textAlign": "center", "marginBottom": "48px"}, children=[
            html.Div("Real-time Credit Decisioning", style={
                "fontSize": "30px", "fontWeight": "700", "color": _LTEXT,
                "letterSpacing": "-0.8px", "marginBottom": "10px",
            }),
            html.Div("One platform. Train · Store · Serve · Log · Audit.", style={
                "fontSize": "15px", "color": _MUTED, "marginBottom": "32px",
            }),
            # Benchmark bar
            dbc.Row(justify="center", style={"marginBottom": "8px"}, children=[
                dbc.Col(width="auto", children=[
                    _card(dbc.CardBody(style={"padding": "14px 24px"}, children=[
                        html.Div(style={"display": "flex", "gap": "40px", "alignItems": "center"}, children=[
                            html.Div([
                                html.Div("p50", style={"fontSize": "10px", "color": _MUTED, **_UPPER}),
                                html.Div("4 ms", style={"fontSize": "22px", "fontWeight": "700",
                                                         "color": _GREEN}),
                            ]),
                            html.Div(style={"width": "1px", "height": "32px",
                                            "backgroundColor": _BORDER}),
                            html.Div([
                                html.Div("p95", style={"fontSize": "10px", "color": _MUTED, **_UPPER}),
                                html.Div("14 ms", style={"fontSize": "22px", "fontWeight": "700",
                                                          "color": _GREEN}),
                            ]),
                            html.Div(style={"width": "1px", "height": "32px",
                                            "backgroundColor": _BORDER}),
                            html.Div([
                                html.Div("p99", style={"fontSize": "10px", "color": _MUTED, **_UPPER}),
                                html.Div("22 ms", style={"fontSize": "22px", "fontWeight": "700",
                                                          "color": _GREEN}),
                            ]),
                            html.Div(style={"width": "1px", "height": "32px",
                                            "backgroundColor": _BORDER}),
                            html.Div([
                                html.Div("Customers", style={"fontSize": "10px", "color": _MUTED, **_UPPER}),
                                html.Div("100K", style={"fontSize": "22px", "fontWeight": "700",
                                                         "color": _TEXT}),
                            ]),
                            html.Div(style={"width": "1px", "height": "32px",
                                            "backgroundColor": _BORDER}),
                            html.Div([
                                html.Div("Tables", style={"fontSize": "10px", "color": _MUTED, **_UPPER}),
                                html.Div("3", style={"fontSize": "22px", "fontWeight": "700",
                                                      "color": _BLUE}),
                                html.Div("1 endpoint", style={"fontSize": "9px", "color": _MUTED}),
                            ]),
                        ]),
                    ]), className="db-card", style={
                        "backgroundColor": _CARD2, "borderRadius": "14px",
                        "border": f"1px solid {_BORDER}",
                    }),
                ]),
            ]),
            html.Div("@ 100 concurrent · us-east-1 · Databricks Serverless", style={
                "fontSize": "11px", "color": _MUTED, "marginTop": "6px",
            }),
        ]),

        # Persona cards
        dbc.Row(justify="center", style={"gap": "0px"}, children=[
            dbc.Col(width=5, children=[
                html.A(href="/technical", style={"textDecoration": "none"}, children=[
                    _card(dbc.CardBody(style={"padding": "32px 28px"}, children=[
                        html.Div("⬡", style={"fontSize": "32px", "color": _RED, "marginBottom": "14px"}),
                        html.Div("Technical Analyst", style={
                            "fontSize": "17px", "fontWeight": "600", "color": _LTEXT,
                            "marginBottom": "10px",
                        }),
                        html.Div([
                            html.Div("▸ Customer lookup by ID", style={"fontSize": "13px", "color": _MUTED,
                                                                         "marginBottom": "5px"}),
                            html.Div("▸ Feature vector from Lakebase", style={"fontSize": "13px",
                                                                                "color": _MUTED, "marginBottom": "5px"}),
                            html.Div("▸ Decision pipeline trace", style={"fontSize": "13px", "color": _MUTED,
                                                                           "marginBottom": "5px"}),
                            html.Div("▸ Model explainability", style={"fontSize": "13px", "color": _MUTED}),
                        ]),
                        html.Div(style={"marginTop": "20px"}, children=[
                            html.Span("Open →", style={"fontSize": "12px", "color": _RED, "fontWeight": "600"}),
                        ]),
                    ]), className="persona-card persona-card-tech", style={
                        "border": f"1px solid {_BORDER}",
                    }),
                ]),
            ]),
            dbc.Col(width=5, children=[
                html.A(href="/executive", style={"textDecoration": "none"}, children=[
                    _card(dbc.CardBody(style={"padding": "32px 28px"}, children=[
                        html.Div("◈", style={"fontSize": "32px", "color": _GREEN, "marginBottom": "14px"}),
                        html.Div("Risk Officer", style={
                            "fontSize": "17px", "fontWeight": "600", "color": _LTEXT,
                            "marginBottom": "10px",
                        }),
                        html.Div([
                            html.Div("▸ Portfolio KPIs & approval rate", style={"fontSize": "13px",
                                                                                  "color": _MUTED, "marginBottom": "5px"}),
                            html.Div("▸ Score distribution", style={"fontSize": "13px", "color": _MUTED,
                                                                      "marginBottom": "5px"}),
                            html.Div("▸ Live decision feed", style={"fontSize": "13px", "color": _MUTED,
                                                                      "marginBottom": "5px"}),
                            html.Div("▸ Borderline review queue", style={"fontSize": "13px", "color": _MUTED}),
                        ]),
                        html.Div(style={"marginTop": "20px"}, children=[
                            html.Span("Open →", style={"fontSize": "12px", "color": _GREEN, "fontWeight": "600"}),
                        ]),
                    ]), className="persona-card persona-card-exec", style={
                        "border": f"1px solid {_BORDER}",
                    }),
                ]),
            ]),
        ]),

        # Platform table strip
        html.Div(style={
            "marginTop": "40px", "padding": "16px 24px",
            "backgroundColor": _CARD2, "borderRadius": "10px",
            "border": f"1px solid {_BORDER2}",
            "display": "flex", "alignItems": "center", "justifyContent": "center", "gap": "24px",
        }, children=[
            html.Span("credit_features", style={"fontSize": "12px", "color": _BLUE,
                                                 "fontFamily": "monospace", "fontWeight": "600"}),
            html.Span("·", style={"color": _MUTED}),
            html.Span("credit_limits", style={"fontSize": "12px", "color": _BLUE,
                                               "fontFamily": "monospace", "fontWeight": "600"}),
            html.Span("·", style={"color": _MUTED}),
            html.Span("decision_log", style={"fontSize": "12px", "color": _BLUE,
                                              "fontFamily": "monospace", "fontWeight": "600"}),
            html.Span("—", style={"color": _BORDER}),
            html.Span("1 PostgreSQL endpoint", style={"fontSize": "12px", "color": _MUTED}),
            html.Span("·", style={"color": _MUTED}),
            _live_badge(),
        ]),
    ]),
])


# ── Technical page ────────────────────────────────────────────────────────────
_TECH_SIDEBAR = dbc.Col(width=3, children=[
    _card(dbc.CardBody(style={"padding": "20px"}, children=[
        _section_label("Customer Lookup"),

        # ID input
        dcc.Input(
            id="customer-id-input",
            type="number", min=0, max=N_ROWS - 1,
            placeholder=f"ID  0 – {N_ROWS-1:,}",
            n_submit=0,
            style={
                "backgroundColor": _BORDER2, "border": f"1px solid {_BORDER}",
                "color": _LTEXT, "borderRadius": "8px", "fontSize": "16px",
                "padding": "9px 13px", "width": "100%", "outline": "none",
                "fontFamily": "'DM Sans', sans-serif", "marginBottom": "10px",
            },
        ),
        dbc.Button(
            "Run Decision", id="run-btn", color="danger", n_clicks=0,
            style={"width": "100%", "fontWeight": "600", "borderRadius": "8px",
                   "marginBottom": "6px", "fontSize": "14px"},
        ),
        html.Div("↵ Enter to run", style={"fontSize": "10px", "color": _MUTED,
                                           "textAlign": "center", "marginBottom": "16px"}),

        html.Hr(style={"borderColor": _BORDER, "margin": "0 0 14px 0"}),

        # Demo scenarios
        html.Div("Demo Scenarios", style={"fontSize": "11px", "color": _MUTED,
                                           "marginBottom": "8px", **_UPPER}),
        html.Div(style={"display": "flex", "flexDirection": "column", "gap": "6px",
                        "marginBottom": "16px"}, children=[
            dbc.Button(
                "✓  Approve  — #62850",
                id={"type": "quick-btn", "index": -1},
                size="sm", color="success", outline=True,
                className="scenario-btn",
                style={"borderRadius": "7px", "fontSize": "12px", "textAlign": "left",
                       "fontFamily": "'DM Sans', monospace"},
            ),
            dbc.Button(
                "✗  Decline  — #13298",
                id={"type": "quick-btn", "index": -2},
                size="sm", color="danger", outline=True,
                className="scenario-btn",
                style={"borderRadius": "7px", "fontSize": "12px", "textAlign": "left",
                       "fontFamily": "'DM Sans', monospace"},
            ),
            dbc.Button(
                "⚠  KYC Flag — #941",
                id={"type": "quick-btn", "index": -3},
                size="sm", color="warning", outline=True,
                className="scenario-btn",
                style={"borderRadius": "7px", "fontSize": "12px", "textAlign": "left",
                       "fontFamily": "'DM Sans', monospace"},
            ),
        ]),

        html.Hr(style={"borderColor": _BORDER, "margin": "0 0 14px 0"}),

        html.Div("Quick Select", style={"fontSize": "11px", "color": _MUTED,
                                         "marginBottom": "8px", **_UPPER}),
        html.Div([
            dbc.Button(str(cid), id={"type": "quick-btn", "index": cid},
                       size="sm", outline=True, color="secondary",
                       style={"borderRadius": "6px", "fontSize": "12px", "margin": "2px"})
            for cid in sorted(random.sample(range(N_ROWS), 6))
        ], style={"marginBottom": "14px"}),

        dbc.Button("↻  Random customer", id="random-btn",
                   outline=True, color="secondary", size="sm",
                   style={"width": "100%", "borderRadius": "8px", "fontSize": "12px",
                          "marginBottom": "16px"}),

        html.Hr(style={"borderColor": _BORDER, "margin": "0 0 12px 0"}),

        # System status
        html.Div([
            _status_pill("Model", _system_status["model"] == "loaded"),
            _status_pill("Lakebase", _system_status["db"] == "connected"),
        ]),

        html.Div(id="search-error", style={"marginTop": "10px"}),
    ])),
])

_TECH_PLACEHOLDER = _card(dbc.CardBody(style={"padding": "72px 0"}, children=[
    html.Div([
        html.Div("◆", style={"fontSize": "44px", "color": _BORDER, "textAlign": "center",
                              "marginBottom": "14px"}),
        html.Div("Select a scenario or enter a Customer ID",
                 style={"textAlign": "center", "color": _MUTED, "fontSize": "15px"}),
        html.Div("Lakebase lookup → KYC → Bureau → Cash Flow → LightGBM → Decision",
                 style={"textAlign": "center", "color": _BORDER, "fontSize": "12px",
                        "marginTop": "6px", "fontFamily": "monospace"}),
    ]),
]))

_TECH = html.Div(id="page-technical", style={"display": "none"}, children=[
    html.Div(style={"padding": "28px 36px 0", "maxWidth": "1500px", "margin": "0 auto"}, children=[
        dbc.Row(style={"marginBottom": "24px"}, children=[
            _TECH_SIDEBAR,
            dbc.Col(width=9, children=[
                dcc.Loading(id="loading-result", type="circle", color=_RED, children=[
                    html.Div(id="result-area", children=[_TECH_PLACEHOLDER]),
                ]),
            ]),
        ]),
    ]),
    # Audit log
    html.Div(style={"padding": "0 36px 36px", "maxWidth": "1500px", "margin": "0 auto"}, children=[
        _card(dbc.CardBody(style={"padding": "20px"}, children=[
            _section_label("Decision Audit Log",
                           right=_live_badge()),
            html.Div(id="audit-log-table"),
        ])),
    ]),
])


# ── Executive page ────────────────────────────────────────────────────────────
_EXEC = html.Div(id="page-executive", style={"display": "none"}, children=[
    html.Div(style={"padding": "28px 36px", "maxWidth": "1500px", "margin": "0 auto"}, children=[

        # Section: Decisioning KPIs
        html.Div(_section_label("Decisioning Performance"), style={"marginBottom": "8px"}),
        dbc.Row(style={"marginBottom": "20px"}, children=[
            dbc.Col(_card(dbc.CardBody(
                style={"padding": "18px 20px"},
                children=[html.Div(id="exec-kpi-total")]
            ), className="kpi-card"), width=3),
            dbc.Col(_card(dbc.CardBody(
                style={"padding": "18px 20px"},
                children=[html.Div(id="exec-kpi-approval")]
            ), className="kpi-card"), width=3),
            dbc.Col(_card(dbc.CardBody(
                style={"padding": "18px 20px"},
                children=[html.Div(id="exec-kpi-kyc")]
            ), className="kpi-card"), width=3),
            dbc.Col(_card(dbc.CardBody(
                style={"padding": "18px 20px"},
                children=[html.Div(id="exec-kpi-latency")]
            ), className="kpi-card"), width=3),
        ]),

        # Section: Portfolio Exposure (business-focused)
        html.Div(_section_label("Portfolio Exposure"), style={"marginBottom": "8px"}),
        dbc.Row(style={"marginBottom": "20px"}, children=[
            dbc.Col(_card(dbc.CardBody(
                style={"padding": "18px 20px"},
                children=[html.Div(id="exec-kpi-portfolio")]
            ), className="kpi-card"), width=3),
            dbc.Col(width=4, children=[
                _card(dbc.CardBody(style={"padding": "18px 20px"}, children=[
                    _section_label("Credit Tier Distribution"),
                    html.Div(id="exec-tier-chart"),
                ])),
            ]),
            dbc.Col(width=5, children=[
                _card(dbc.CardBody(style={"padding": "18px 20px"}, children=[
                    _section_label("Top Decline Reasons",
                                   right=html.Span("last 200 decisions",
                                                   style={"fontSize": "11px", "color": _MUTED})),
                    html.Div(id="exec-reasons-chart"),
                ])),
            ]),
        ]),

        # Section: Decision analytics
        dbc.Row(style={"marginBottom": "20px"}, children=[
            dbc.Col(width=3, children=[
                _card(dbc.CardBody(style={"padding": "18px 20px"}, children=[
                    _section_label("Decision Mix",
                                   right=html.Span("click to drill", style={
                                       "fontSize": "9px", "color": _MUTED,
                                       "letterSpacing": "0.4px",
                                   })),
                    html.Div(id="exec-donut"),
                    html.Div(id="exec-donut-drilldown", style={"marginTop": "10px"}),
                ])),
            ]),
            dbc.Col(width=5, children=[
                _card(dbc.CardBody(style={"padding": "18px 20px"}, children=[
                    _section_label("Approval Rate — rolling 100 decisions"),
                    html.Div(id="exec-trend"),
                ])),
            ]),
            dbc.Col(width=4, children=[
                _card(dbc.CardBody(style={"padding": "18px 20px"}, children=[
                    _section_label("Score Distribution"),
                    html.Div(id="exec-score-dist"),
                ])),
            ]),
        ]),

        # Live feed
        _card(dbc.CardBody(style={"padding": "20px"}, children=[
            _section_label("Live Decision Feed", right=_live_badge()),
            html.Div(id="exec-feed"),
        ]), style={"marginBottom": "20px"}),

        # Review queue
        _card(dbc.CardBody(style={"padding": "20px"}, children=[
            _section_label("Manual Review Queue",
                           right=html.Span("Borderline declines — score 0.40 – 0.50 — recommend manual underwriting review",
                                           style={"fontSize": "11px", "color": _WARN})),
            html.Div(id="exec-review-queue"),
        ])),
    ]),
])


# ── Layout ────────────────────────────────────────────────────────────────────
app.layout = html.Div(style=_PAGE, children=[
    dcc.Location(id="_location", refresh=False),
    _header(),
    _LANDING,
    _TECH,
    _EXEC,
    dcc.Interval(id="audit-interval", interval=10_000, n_intervals=0),
    dcc.Store(id="result-store"),
    dcc.Store(id="session-stats", data={"total": 0, "approved": 0, "avg_ms": 0}),
    dcc.Store(id="exec-portfolio-data", data={}),
])


# ── Callbacks ─────────────────────────────────────────────────────────────────
@app.callback(
    Output("page-landing",   "style"),
    Output("page-technical", "style"),
    Output("page-executive", "style"),
    Output("nav-links",      "children"),
    Input("_location", "pathname"),
)
def route(pathname):
    show, hide = {"display": "block"}, {"display": "none"}
    back = html.A("← Home", href="/", style={
        "color": _MUTED, "fontSize": "13px", "textDecoration": "none",
        "padding": "5px 12px", "borderRadius": "6px",
        "border": f"1px solid {_BORDER}", "transition": "border-color 0.2s ease",
    })
    if pathname == "/technical":
        return hide, show, hide, back
    if pathname == "/executive":
        return hide, hide, show, back
    return show, hide, hide, ""


@app.callback(
    Output("customer-id-input", "value"),
    Input("random-btn", "n_clicks"),
    Input({"type": "quick-btn", "index": dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def set_customer_id(rnd, quick):
    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update
    triggered = ctx.triggered[0]["prop_id"]
    if "quick-btn" in triggered:
        try:
            idx = json.loads(triggered.split(".")[0])["index"]
            if idx == -1: return DEMO_SCENARIOS["approve"]
            if idx == -2: return DEMO_SCENARIOS["decline"]
            if idx == -3: return DEMO_SCENARIOS["kyc_fail"]
            return idx
        except Exception:
            pass
    return random.randint(0, N_ROWS - 1)


@app.callback(
    Output("result-area",    "children"),
    Output("header-stats",   "children"),
    Output("search-error",   "children"),
    Output("session-stats",  "data"),
    Input("run-btn",             "n_clicks"),
    Input("customer-id-input",   "n_submit"),
    State("customer-id-input",   "value"),
    State("session-stats",       "data"),
    prevent_initial_call=True,
)
def run_and_display(n_clicks, n_submit, customer_id, stats):
    if customer_id is None or str(customer_id).strip() == "":
        return dash.no_update, dash.no_update, \
               dbc.Alert("Enter a Customer ID first.", color="warning", className="mt-2"), stats

    try:
        cid = int(customer_id)
    except (ValueError, TypeError):
        return dash.no_update, dash.no_update, \
               dbc.Alert("Invalid ID — enter a number.", color="danger", className="mt-2"), stats

    try:
        result = run_decision(cid)
    except Exception as exc:
        return dash.no_update, dash.no_update, \
               dbc.Alert(f"Connection error: {exc}", color="danger", className="mt-2"), stats

    if "error" in result:
        return dash.no_update, dash.no_update, \
               dbc.Alert(result["error"], color="warning", className="mt-2"), stats

    decision          = result["decision"]
    score             = result["score"]
    latency_ms        = result["latency_ms"]
    reasons           = result["reasons"]
    feat              = result["features"]
    credit_limit      = result["credit_limit"]
    available         = result["available"]
    tier              = result["tier"]
    bureau_score      = result["bureau_score"]
    bureau_latency_ms = result["bureau_latency_ms"]
    bureau_provider   = result["bureau_provider"]
    cashflow_score    = result["cashflow_score"]
    cashflow_latency_ms = result["cashflow_latency_ms"]
    cashflow_provider = result["cashflow_provider"]
    kyc_status        = result["kyc_status"]
    kyc_flag          = result["kyc_flag"]
    kyc_latency_ms    = result["kyc_latency_ms"]

    # Session stats
    stats["total"] += 1
    if decision == "APPROVE":
        stats["approved"] += 1
    stats["avg_ms"] = round(
        (stats["avg_ms"] * (stats["total"] - 1) + latency_ms) / stats["total"], 1
    )

    d_color   = _GREEN if decision == "APPROVE" else _RED
    lat_color = _GREEN if latency_ms < 20 else _WARN if latency_ms < 50 else _RED

    contributions = _compute_contributions(feat)

    # ── Decision banner ────────────────────────────────────────────────────────
    banner = html.Div(className="decision-banner", style={
        "background": f"linear-gradient(135deg, {d_color}18 0%, {_CARD} 60%)",
        "border": f"1px solid {d_color}44",
        "borderRadius": "12px", "padding": "18px 24px",
        "marginBottom": "16px",
        "display": "flex", "alignItems": "center", "gap": "20px", "flexWrap": "wrap",
    }, children=[
        html.Div(decision, style={
            "fontSize": "36px", "fontWeight": "800", "color": d_color,
            "letterSpacing": "-1px", "lineHeight": "1",
        }),
        html.Div(style={"display": "flex", "flexDirection": "column", "gap": "4px"}, children=[
            html.Div(style={"display": "flex", "alignItems": "center", "gap": "8px"}, children=[
                html.Span(f"Customer #{cid}", style={"fontSize": "14px", "color": _TEXT,
                                                       "fontWeight": "500"}),
                _labeled_chip("Tier", tier or "UNKNOWN",
                               TIER_COLORS.get((tier or "UNKNOWN").upper(), TIER_COLORS["UNKNOWN"])),
                _labeled_chip("KYC", kyc_status, _GREEN if kyc_status == "PASS" else _RED),
            ]),
            html.Div(
                f"{MODEL_NM} · v{_model_version} champion  ·  "
                f"score {score:.3f} vs cutoff 0.500  ·  {latency_ms}ms",
                style={"fontSize": "12px", "color": _MUTED},
            ),
        ]),
        html.Div(style={"marginLeft": "auto", "minWidth": "220px"}, children=[
            _credit_bar(available, credit_limit),
        ]),
    ])

    # ── Score gauge card ───────────────────────────────────────────────────────
    score_card = _card(dbc.CardBody(style={"padding": "16px"}, children=[
        dcc.Graph(figure=_score_gauge_fig(score, decision), config={"displayModeBar": False}),

        # Reason codes
        html.Div(style={"marginTop": "8px"}, children=[
            html.Div("Reason Codes", style={"fontSize": "10px", "color": _MUTED,
                                             "marginBottom": "6px", **_UPPER}),
            html.Div([
                html.Div(r, style={
                    "fontSize": "11px", "color": _WARN,
                    "borderLeft": f"2px solid {_RED}",
                    "paddingLeft": "8px", "marginBottom": "4px", "lineHeight": "1.4",
                }) for r in reasons
            ]) if reasons else html.Div(
                "No adverse factors.", style={"fontSize": "11px", "color": _GREEN}
            ),
        ]),

        # KYC flag detail
        html.Div(style={"marginTop": "10px"} if kyc_flag else {"display": "none"}, children=[
            html.Hr(style={"borderColor": _BORDER, "margin": "10px 0"}),
            html.Div(kyc_flag or "", style={
                "fontSize": "11px", "color": _RED, "fontWeight": "600",
                "borderLeft": f"2px solid {_RED}", "paddingLeft": "8px",
            }),
        ]),

        # Model governance metadata
        html.Hr(style={"borderColor": _BORDER, "margin": "12px 0 8px 0"}),
        html.Div("Model Governance", style={
            "fontSize": "10px", "color": _MUTED, "textTransform": "uppercase",
            "letterSpacing": "0.8px", "fontWeight": "600", "marginBottom": "6px",
        }),
        html.Div([
            html.Div(
                f"model: {MODEL_NM} · v{_model_version} · champion",
                style={"fontSize": "11px", "color": _MUTED, "fontFamily": "monospace",
                       "marginBottom": "3px"},
            ),
            html.Div("features: 9 · algorithm: LightGBM",
                     style={"fontSize": "11px", "color": _MUTED, "marginBottom": "3px"}),
            html.Div("MRM status: Active · production",
                     style={"fontSize": "11px", "color": _GREEN}),
        ]),
    ]))

    # ── Pipeline waterfall card ────────────────────────────────────────────────
    pipeline_card = _card(dbc.CardBody(style={"padding": "16px"}, children=[
        _section_label("Decision Pipeline"),
        dcc.Graph(
            figure=_pipeline_fig(latency_ms, kyc_latency_ms, bureau_latency_ms,
                                 cashflow_latency_ms, bureau_provider, cashflow_provider),
            config={"displayModeBar": False},
        ),
        html.Div(style={"display": "flex", "gap": "16px", "marginTop": "8px"}, children=[
            html.Span([html.Span("■ ", style={"color": _GREEN}),
                       html.Span("Internal (Lakebase)", style={"fontSize": "10px", "color": _MUTED})]),
            html.Span([html.Span("■ ", style={"color": _WARN}),
                       html.Span("External API", style={"fontSize": "10px", "color": _MUTED})]),
        ]),
    ]))

    # ── External enrichment card ───────────────────────────────────────────────
    bureau_color   = _GREEN if bureau_score >= 700 else _WARN if bureau_score >= 600 else _RED
    cashflow_color = _GREEN if cashflow_score >= 60 else _WARN if cashflow_score >= 40 else _RED

    enrich_card = _card(dbc.CardBody(style={"padding": "16px"}, children=[
        _section_label("External Enrichment"),

        # Bureau
        html.Div(style={"marginBottom": "16px"}, children=[
            html.Div(style={"display": "flex", "justifyContent": "space-between",
                            "alignItems": "flex-end", "marginBottom": "6px"}, children=[
                html.Div([
                    html.Div(f"Bureau ({bureau_provider})", style={"fontSize": "10px",
                                                                     "color": _MUTED, **_UPPER}),
                    html.Div(f"{bureau_score} FICO", style={"fontSize": "22px", "fontWeight": "700",
                                                              "color": bureau_color}),
                ]),
                html.Div([
                    html.Div("API latency", style={"fontSize": "10px", "color": _MUTED,
                                                    "textAlign": "right"}),
                    html.Div(f"{bureau_latency_ms}ms", style={"fontSize": "16px", "fontWeight": "600",
                                                               "color": _WARN, "textAlign": "right"}),
                    html.Div(f"vs {latency_ms}ms Lakebase", style={"fontSize": "10px", "color": _MUTED,
                                                                     "textAlign": "right"}),
                ]),
            ]),
            # FICO bar
            html.Div(style={"backgroundColor": _BORDER, "borderRadius": "4px", "height": "5px"}, children=[
                html.Div(style={
                    "backgroundColor": bureau_color, "borderRadius": "4px", "height": "5px",
                    "width": f"{(bureau_score - 300) / 550 * 100:.0f}%",
                }),
            ]),
            html.Div(style={"display": "flex", "justifyContent": "space-between",
                            "marginTop": "3px"}, children=[
                html.Span("300", style={"fontSize": "9px", "color": _MUTED}),
                html.Span("FICO range", style={"fontSize": "9px", "color": _MUTED}),
                html.Span("850", style={"fontSize": "9px", "color": _MUTED}),
            ]),
        ]),

        html.Hr(style={"borderColor": _BORDER, "margin": "0 0 14px 0"}),

        # Cashflow
        html.Div([
            html.Div(style={"display": "flex", "justifyContent": "space-between",
                            "alignItems": "flex-end", "marginBottom": "6px"}, children=[
                html.Div([
                    html.Div(f"Cash Flow ({cashflow_provider})", style={"fontSize": "10px",
                                                                          "color": _MUTED, **_UPPER}),
                    html.Div(f"{cashflow_score}/100", style={"fontSize": "22px", "fontWeight": "700",
                                                               "color": cashflow_color}),
                ]),
                html.Div([
                    html.Div("API latency", style={"fontSize": "10px", "color": _MUTED,
                                                    "textAlign": "right"}),
                    html.Div(f"{cashflow_latency_ms}ms", style={"fontSize": "16px", "fontWeight": "600",
                                                                  "color": _WARN, "textAlign": "right"}),
                ]),
            ]),
            html.Div(style={"backgroundColor": _BORDER, "borderRadius": "4px", "height": "5px"}, children=[
                html.Div(style={
                    "backgroundColor": cashflow_color, "borderRadius": "4px", "height": "5px",
                    "width": f"{cashflow_score}%",
                }),
            ]),
        ]),
    ]))

    # ── Feature vector card — grouped by 5 Cs of Credit ──────────────────────
    td_k = {"color": _MUTED, "fontSize": "11px", "paddingRight": "12px", "paddingBottom": "5px",
             "whiteSpace": "nowrap"}
    td_v = {"fontSize": "12px", "fontWeight": "500", "paddingBottom": "5px", "color": _TEXT}
    grp_label_style = {
        "fontSize": "9px", "color": _MUTED, "textTransform": "uppercase",
        "letterSpacing": "0.9px", "fontWeight": "600",
        "paddingTop": "8px", "paddingBottom": "4px", "paddingRight": "12px",
        "borderTop": f"1px solid {_BORDER2}",
    }

    def _5c_rows(group_features):
        rows = []
        for f in group_features:
            val = feat.get(f, feat.get("debt_to_income", 0) if f == "debt_to_income" else 0)
            fmt = _5C_FORMAT.get(f, lambda v: str(round(v, 3)))
            rows.append(html.Tr([
                html.Td(FEATURE_LABELS.get(f, f), style=td_k),
                html.Td(fmt(float(val)), style=td_v),
            ]))
        return rows

    # Build columns: left gets Character + Capacity, right gets Capital + Conditions + Profile
    left_groups  = _5C_GROUPS[:2]
    right_groups = _5C_GROUPS[2:]

    def _render_groups(groups):
        out = []
        for i, (name, fields) in enumerate(groups):
            header_style = {**grp_label_style}
            if i == 0:
                header_style.pop("borderTop")
                header_style.pop("paddingTop")
            out.append(html.Tr([html.Td(name, colSpan=2, style=header_style)]))
            out.extend(_5c_rows(fields))
        return out

    vector_card = _card(dbc.CardBody(style={"padding": "16px"}, children=[
        _section_label("Feature Vector — Lakebase",
                       right=html.Span("5 Cs of Credit", style={"fontSize": "10px", "color": _MUTED})),
        dbc.Row([
            dbc.Col(width=6, children=[
                html.Table(_render_groups(left_groups), style={"width": "100%"}),
            ]),
            dbc.Col(width=6, children=[
                html.Table(_render_groups(right_groups), style={"width": "100%"}),
            ]),
        ]),
    ]))

    # ── Feature contribution card ──────────────────────────────────────────────
    contribs = _compute_contributions(feat)
    contrib_card = _card(dbc.CardBody(style={"padding": "16px"}, children=[
        _section_label("Feature Contributions — this customer",
                       right=html.Span("← DECLINE  ·  APPROVE →",
                                       style={"fontSize": "10px", "color": _MUTED})),
        dcc.Graph(figure=_contribution_fig(contribs), config={"displayModeBar": False})
        if contribs else html.Div("Model not loaded.", style={"color": _MUTED, "fontSize": "12px"}),
    ]))

    result_layout = html.Div(className="decision-result", children=[
        banner,
        dbc.Row(style={"marginBottom": "14px"}, children=[
            dbc.Col(width=4, children=[score_card]),
            dbc.Col(width=4, children=[pipeline_card]),
            dbc.Col(width=4, children=[enrich_card]),
        ]),
        dbc.Row(children=[
            dbc.Col(width=4, children=[vector_card]),
            dbc.Col(width=8, children=[contrib_card]),
        ]),
    ])

    # Header stats
    approval_rate = round(stats["approved"] / stats["total"] * 100) if stats["total"] else 0
    header = html.Div(style={"display": "flex", "gap": "28px", "alignItems": "center"}, children=[
        _stat("Decisions", f"{stats['total']:,}"),
        _stat("Approval Rate", f"{approval_rate}%"),
        _stat("Avg Latency", f"{stats['avg_ms']}ms", lat_color),
        _live_badge(),
    ])

    return result_layout, header, "", stats


@app.callback(
    Output("exec-kpi-total",       "children"),
    Output("exec-kpi-approval",    "children"),
    Output("exec-kpi-kyc",         "children"),
    Output("exec-kpi-latency",     "children"),
    Output("exec-kpi-portfolio",   "children"),
    Output("exec-tier-chart",      "children"),
    Output("exec-reasons-chart",   "children"),
    Output("exec-donut",           "children"),
    Output("exec-trend",           "children"),
    Output("exec-score-dist",      "children"),
    Output("exec-feed",            "children"),
    Output("exec-review-queue",    "children"),
    Output("exec-portfolio-data",  "data"),
    Input("audit-interval", "n_intervals"),
    Input("_location", "pathname"),
)
def refresh_executive(_, pathname):
    if pathname != "/executive":
        return [dash.no_update] * 13

    stats     = query_executive_stats()
    portfolio = query_portfolio_stats()
    queue     = query_review_queue(10)
    rows      = stats["rows"]

    # ── Decisioning KPIs ──────────────────────────────────────────────────────
    kpi_total    = _stat("Applications Processed", f"{stats['total']:,}",
                         sub="last 200 decisions")
    kpi_approval = _stat("Approval Rate", f"{stats['approval_rate']}%",
                         color=_GREEN if stats["approval_rate"] >= 60 else _RED,
                         sub="auto-approved · last 200")
    kpi_kyc      = _stat("AML / KYC Flags", f"{stats['kyc_flag_rate']}%",
                         color=_RED if stats["kyc_flag_rate"] > 20 else _WARN,
                         sub="flagged for review")
    avg_lat = stats["avg_latency"]
    sla_ok  = avg_lat is not None and avg_lat < 50
    kpi_lat = _stat(
        "SLA Compliance",
        "WITHIN SLA" if sla_ok else ("—" if avg_lat is None else "BREACHED"),
        color=_GREEN if sla_ok else (_MUTED if avg_lat is None else _RED),
        sub=f"p99 < 50ms · avg {avg_lat}ms" if avg_lat else "p99 < 50ms target",
    )

    # ── Portfolio KPI ─────────────────────────────────────────────────────────
    total_limit = portfolio["total_limit"]
    premium_pct = 0
    if portfolio["tiers"]:
        premium_row = next((t for t in portfolio["tiers"] if t[0] == "PREMIUM"), None)
        if premium_row:
            total_customers = sum(t[1] for t in portfolio["tiers"])
            premium_pct = round(premium_row[1] / total_customers * 100) if total_customers else 0
    kpi_portfolio = _stat(
        "Total Credit Portfolio",
        f"${total_limit / 1_000_000:.1f}M" if total_limit >= 1_000_000 else f"${total_limit:,.0f}",
        color=_BLUE,
        sub=f"{premium_pct}% PREMIUM tier",
    )

    # ── Credit tier chart ─────────────────────────────────────────────────────
    tier_fig = _tier_distribution_fig(portfolio["tiers"])
    tier_chart = (
        dcc.Graph(figure=tier_fig, config={"displayModeBar": False})
        if tier_fig
        else html.Div("Run decisions to see tier distribution.",
                      style={"color": _MUTED, "fontSize": "12px", "padding": "48px 0",
                             "textAlign": "center"})
    )

    # ── Decline reasons chart ─────────────────────────────────────────────────
    reasons_fig = _decline_reasons_fig(portfolio["reason_freq"])
    reasons_chart = (
        dcc.Graph(figure=reasons_fig, config={"displayModeBar": False})
        if reasons_fig
        else html.Div("No declines logged yet.",
                      style={"color": _MUTED, "fontSize": "12px", "padding": "48px 0",
                             "textAlign": "center"})
    )

    # Donut
    n_approve = sum(1 for r in rows if r[1] == "APPROVE")
    n_decline = len(rows) - n_approve
    donut_fig = go.Figure(go.Pie(
        values=[max(n_approve, 1), max(n_decline, 1)],
        labels=["APPROVE", "DECLINE"],
        marker_colors=[_GREEN, _RED],
        hole=0.58, textinfo="none",
    ))
    donut_fig.update_layout(
        height=200, margin={"t": 8, "b": 8, "l": 8, "r": 8},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=True,
        legend={"font": {"color": _MUTED, "size": 11}, "bgcolor": "rgba(0,0,0,0)",
                "orientation": "h", "x": 0.5, "xanchor": "center", "y": -0.05},
        font={"family": "DM Sans"},
    )
    # Center annotation
    donut_fig.add_annotation(
        text=f"{round(n_approve / max(len(rows), 1) * 100)}%",
        x=0.5, y=0.5, showarrow=False,
        font={"size": 22, "color": _GREEN, "family": "DM Sans"},
    )
    donut = dcc.Graph(figure=donut_fig, id="exec-donut-graph",
                      config={"displayModeBar": False})

    # Trend
    if len(rows) >= 2:
        chrono = list(reversed(rows[:100]))
        rolling, approved_so_far = [], 0
        for i, r in enumerate(chrono, 1):
            if r[1] == "APPROVE":
                approved_so_far += 1
            rolling.append(round(approved_so_far / i * 100, 1))
        trend_fig = go.Figure(go.Scatter(
            x=list(range(1, len(rolling) + 1)), y=rolling,
            mode="lines", line={"color": _GREEN, "width": 2},
            fill="tozeroy", fillcolor="rgba(0,200,81,0.07)",
        ))
        trend_fig.update_layout(
            height=200, margin={"t": 4, "b": 20, "l": 44, "r": 10},
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            xaxis={"showgrid": False, "tickfont": {"size": 9, "color": _MUTED},
                   "title": {"text": "decisions", "font": {"size": 9, "color": _MUTED}}},
            yaxis={"range": [0, 105], "ticksuffix": "%", "gridcolor": _BORDER2,
                   "tickfont": {"size": 9, "color": _MUTED}},
            font={"family": "DM Sans"},
        )
        # Flag declining trend: drop > 5pp over last 10 decisions
        if len(rolling) >= 15:
            delta = rolling[-1] - rolling[-11]
            if delta < -5:
                trend_fig.add_annotation(
                    x=len(rolling), y=min(rolling[-1] + 8, 100),
                    text=f"⚠ {abs(delta):.0f}pp drop",
                    showarrow=True, arrowhead=2, arrowcolor=_WARN, arrowwidth=1,
                    font={"size": 9, "color": _WARN, "family": "DM Sans"},
                    bgcolor=_CARD, bordercolor=_WARN, borderwidth=1,
                    borderpad=3,
                )
        trend = dcc.Graph(figure=trend_fig, config={"displayModeBar": False})
    else:
        trend = html.Div("Run decisions in Technical view to build the trend.",
                         style={"color": _MUTED, "fontSize": "13px", "padding": "72px 0",
                                "textAlign": "center"})

    # Score distribution
    dist_fig = _score_dist_fig(rows)
    if dist_fig:
        score_dist = dcc.Graph(figure=dist_fig, config={"displayModeBar": False})
    else:
        score_dist = html.Div("Run more decisions to see distribution.",
                              style={"color": _MUTED, "fontSize": "12px",
                                     "padding": "72px 0", "textAlign": "center"})

    # Live feed
    hdr_s  = {"fontSize": "11px", "color": _MUTED, "textTransform": "uppercase",
               "letterSpacing": "0.6px", "paddingBottom": "8px", "paddingRight": "14px",
               "fontWeight": "600"}
    cell_s = {"fontSize": "12px", "paddingBottom": "7px", "paddingRight": "14px",
               "borderTop": f"1px solid {_BORDER2}", "paddingTop": "7px"}

    if rows:
        feed = html.Table(style={"width": "100%", "borderCollapse": "collapse"}, children=[
            html.Thead(html.Tr([
                html.Th("Customer",  style=hdr_s),
                html.Th("Decision",  style=hdr_s),
                html.Th("Score",     style=hdr_s),
                html.Th("KYC",       style=hdr_s),
                html.Th("Latency",   style=hdr_s),
                html.Th("Time", style={**hdr_s, "textAlign": "right"}),
            ])),
            html.Tbody([
                html.Tr(className="audit-row", children=[
                    html.Td(f"#{r[0]}", style=cell_s),
                    html.Td(r[1], style={**cell_s,
                                          "color": _GREEN if r[1] == "APPROVE" else _RED,
                                          "fontWeight": "700"}),
                    html.Td(f"{float(r[2]):.3f}", style=cell_s),
                    html.Td(
                        "⚠ FLAG" if r[3] and ("RC07" in r[3] or "RC08" in r[3]) else "✓ PASS",
                        style={**cell_s,
                               "color": _RED if r[3] and ("RC07" in r[3] or "RC08" in r[3])
                                             else _GREEN,
                               "fontSize": "11px", "fontWeight": "700"},
                    ),
                    html.Td(
                        f"{float(r[5]):.0f}ms" if r[5] is not None else "—",
                        style={**cell_s, "color": _MUTED},
                    ),
                    html.Td(r[4], style={**cell_s, "textAlign": "right", "color": _MUTED}),
                ]) for r in rows[:25]
            ]),
        ])
    else:
        feed = html.Div("No decisions logged yet — run decisions in Technical view.",
                        style={"color": _MUTED, "fontSize": "13px", "padding": "12px 0"})

    # Review queue
    if queue:
        def _priority_label(score):
            pct = float(score)
            if pct >= 0.48: return ("HIGH",  _RED)
            if pct >= 0.44: return ("MED",   _WARN)
            return ("LOW", _MUTED)

        rq = html.Div(style={"display": "flex", "flexDirection": "column", "gap": "8px"}, children=[
            html.Div(style={
                "backgroundColor": _CARD2,
                "border": f"1px solid {_RED}33",
                "borderRadius": "10px", "padding": "14px 18px",
                "display": "flex", "alignItems": "flex-start", "gap": "16px",
            }, children=[
                # Priority indicator
                html.Div(style={"flexShrink": "0", "width": "44px", "textAlign": "center"}, children=[
                    html.Div(_priority_label(r[1])[0], style={
                        "fontSize": "10px", "fontWeight": "700", "color": _priority_label(r[1])[1],
                        "textTransform": "uppercase",
                    }),
                    html.Div(f"{float(r[1]):.0%}", style={
                        "fontSize": "16px", "fontWeight": "700", "color": _RED, "marginTop": "2px",
                    }),
                ]),
                # Details
                html.Div(style={"flex": "1"}, children=[
                    html.Div(style={"display": "flex", "alignItems": "center",
                                    "gap": "8px", "marginBottom": "4px"}, children=[
                        html.Span(f"Customer #{r[0]}", style={"fontSize": "13px", "fontWeight": "600",
                                                                "color": _TEXT}),
                        html.Span("DECLINE", style={"fontSize": "10px", "color": _RED,
                                                      "fontWeight": "700", "marginLeft": "4px"}),
                    ]),
                    html.Div(
                        f"Score {float(r[1]):.0%} — borderline. "
                        f"{(r[2] or '').split(',')[0] or 'No primary reason code'}. "
                        "Recommend manual review.",
                        style={"fontSize": "12px", "color": _MUTED, "lineHeight": "1.5"},
                    ),
                ]),
                # Timestamp
                html.Div(r[3], style={"fontSize": "11px", "color": _MUTED,
                                       "flexShrink": "0", "marginLeft": "auto"}),
            ]) for r in queue
        ])
    else:
        rq = html.Div("No borderline cases in queue.",
                      style={"color": _MUTED, "fontSize": "13px", "padding": "12px 0"})

    portfolio_store = {
        "reason_freq": portfolio["reason_freq"],
        "n_approve": sum(1 for r in rows if r[1] == "APPROVE"),
        "n_decline": len(rows) - sum(1 for r in rows if r[1] == "APPROVE"),
    }
    return (kpi_total, kpi_approval, kpi_kyc, kpi_lat,
            kpi_portfolio, tier_chart, reasons_chart,
            donut, trend, score_dist, feed, rq, portfolio_store)


@app.callback(
    Output("audit-log-table", "children"),
    Input("audit-interval", "n_intervals"),
    Input("run-btn", "n_clicks"),
)
def refresh_audit_log(_, __):
    rows = query_decision_history(20)
    if not rows:
        return html.Div("No decisions logged yet — run a decision above.",
                        style={"color": _MUTED, "fontSize": "13px", "padding": "12px 0"})

    hdr_s  = {"fontSize": "11px", "color": _MUTED, "textTransform": "uppercase",
               "letterSpacing": "0.6px", "paddingBottom": "8px", "paddingRight": "14px",
               "fontWeight": "600"}
    cell_s = {"fontSize": "12px", "paddingBottom": "7px", "paddingRight": "14px",
               "borderTop": f"1px solid {_BORDER2}", "paddingTop": "7px"}

    return html.Table(style={"width": "100%", "borderCollapse": "collapse"}, children=[
        html.Thead(html.Tr([
            html.Th("Customer",   style=hdr_s),
            html.Th("Decision",   style=hdr_s),
            html.Th("Score",      style=hdr_s),
            html.Th("Top Reason", style=hdr_s),
            html.Th("Latency",    style=hdr_s),
            html.Th("Time", style={**hdr_s, "textAlign": "right"}),
        ])),
        html.Tbody([
            html.Tr(className="audit-row", children=[
                html.Td(f"#{r['customer_id']}", style=cell_s),
                html.Td(r["decision"], style={**cell_s,
                                               "color": _GREEN if r["decision"] == "APPROVE" else _RED,
                                               "fontWeight": "700"}),
                html.Td(f"{r['score']:.3f}", style=cell_s),
                html.Td(r["reasons"], style={**cell_s, "color": _MUTED, "fontSize": "11px"}),
                html.Td(f"{r['latency_ms']}ms" if r["latency_ms"] is not None else "—",
                        style={**cell_s, "color": _MUTED}),
                html.Td(r["ts"], style={**cell_s, "textAlign": "right", "color": _MUTED}),
            ]) for r in rows
        ]),
    ])


@app.callback(
    Output("exec-donut-drilldown", "children"),
    Input("exec-donut-graph", "clickData"),
    State("exec-portfolio-data", "data"),
    prevent_initial_call=True,
)
def drilldown_donut(click_data, portfolio_data):
    if not click_data or not portfolio_data:
        return dash.no_update
    segment = click_data["points"][0].get("label", "")
    reason_freq = portfolio_data.get("reason_freq", [])

    if segment == "APPROVE":
        n = portfolio_data.get("n_approve", 0)
        return html.Div([
            html.Hr(style={"borderColor": _BORDER, "margin": "8px 0"}),
            html.Div(f"{n} approvals", style={"fontSize": "11px", "color": _GREEN,
                                               "fontWeight": "600", "marginBottom": "4px"}),
            html.Div("No adverse reason codes recorded.",
                     style={"fontSize": "11px", "color": _MUTED}),
        ])

    if segment == "DECLINE":
        n = portfolio_data.get("n_decline", 0)
        top = reason_freq[:4] if reason_freq else []
        rows = [
            html.Div(style={
                "display": "flex", "justifyContent": "space-between",
                "marginBottom": "4px",
            }, children=[
                html.Span(REASON_LABELS.get(r[0], r[0]),
                          style={"fontSize": "11px", "color": _MUTED}),
                html.Span(str(r[1]), style={"fontSize": "11px", "color": _WARN,
                                             "fontWeight": "600"}),
            ]) for r in top
        ]
        return html.Div([
            html.Hr(style={"borderColor": _BORDER, "margin": "8px 0"}),
            html.Div(f"{n} declines — top reasons",
                     style={"fontSize": "11px", "color": _RED,
                            "fontWeight": "600", "marginBottom": "6px"}),
            *rows,
        ])
    return dash.no_update


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 8050)))
