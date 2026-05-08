# Databricks notebook source
# MAGIC %md
# MAGIC # Lakebase Auth Utility
# MAGIC
# MAGIC **Do not run this notebook directly.** Include it from other notebooks with:
# MAGIC ```python
# MAGIC # MAGIC %run ./util_lakebase_auth
# MAGIC ```
# MAGIC
# MAGIC Defines `_get_lakebase_credential(lb_host, lb_user)` — returns a valid Lakebase token
# MAGIC for the current execution context (interactive, job, or Databricks App).
# MAGIC
# MAGIC ### Authentication priority
# MAGIC | Priority | Context | Mechanism |
# MAGIC |---|---|---|
# MAGIC | 1 | Interactive notebook | Notebook context token → credential exchange |
# MAGIC | 2 | Serverless job / App | `DATABRICKS_TOKEN` env var → credential exchange |
# MAGIC | 3 | SP-based automation | SP M2M OAuth → credential exchange |
# MAGIC | 4 | Manual override | `databricks_pat` secret → credential exchange |
# MAGIC
# MAGIC The credential exchange (`/api/2.0/postgres/credentials`) is required for
# MAGIC private-endpoint Lakebase instances (the default for Databricks workspaces).

# COMMAND ----------

def _get_lakebase_credential(lb_host: str, lb_user: str) -> str:
    """Return a valid Lakebase credential for the current execution context.

    Works in interactive notebooks, serverless jobs, and Databricks Apps.
    Credential has a ~15-minute TTL — safe to call once per notebook run.
    """
    import json, os, urllib.request, urllib.parse

    # Workspace host: injected by Databricks runtime in jobs/apps.
    # In interactive notebooks it is not set, but the direct-connect path
    # (step 2b below) covers that case.
    WS_HOST = os.environ.get("DATABRICKS_HOST", "").rstrip("/")

    def _exchange(bearer: str) -> str:
        """Exchange a workspace bearer token for a Lakebase-specific credential."""
        if not WS_HOST:
            return ""
        try:
            endpoint_path = dbutils.secrets.get("lakebase-demo", "endpoint_path")
            req = urllib.request.Request(
                f"{WS_HOST}/api/2.0/postgres/credentials",
                data=json.dumps({"endpoint": endpoint_path}).encode(),
                headers={"Authorization": f"Bearer {bearer}",
                         "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                tok = json.loads(r.read()).get("token", "")
            if tok:
                print("Auth: credential exchange OK")
            return tok
        except Exception as xe:
            print(f"Auth: credential exchange skipped ({xe.__class__.__name__}: {xe})")
            return ""

    # ── Step 1: DATABRICKS_TOKEN env var (jobs, Apps, serverless clusters) ──────
    bearer = os.environ.get("DATABRICKS_TOKEN", "")
    if bearer:
        print(f"Auth: bearer from DATABRICKS_TOKEN (len={len(bearer)})")

    # ── Step 2: Notebook context token (interactive browser session) ─────────────
    if not bearer:
        try:
            ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()
            if ctx and len(ctx) > 100:
                bearer = ctx
                print("Auth: bearer from notebook context token")
        except Exception:
            pass

    if bearer:
        # 2a. Try direct connect first — works when Lakebase has a public endpoint
        try:
            import psycopg2 as _pg
            _c = _pg.connect(host=lb_host, port=5432, dbname="databricks_postgres",
                             user=lb_user, password=bearer,
                             sslmode="require", connect_timeout=5)
            _c.close()
            print("Auth: direct bearer connect OK (public endpoint)")
            return bearer
        except Exception:
            pass
        # 2b. Private endpoint → exchange bearer for Lakebase-specific token
        tok = _exchange(bearer)
        if tok:
            return tok

    # ── Step 3: Service Principal M2M OAuth ──────────────────────────────────────
    try:
        cid  = dbutils.secrets.get("lakebase-demo", "sp_client_id")
        csec = dbutils.secrets.get("lakebase-demo", "sp_client_secret")
        data = urllib.parse.urlencode({
            "grant_type":    "client_credentials",
            "client_id":     cid,
            "client_secret": csec,
            "scope":         "all-apis",
        }).encode()
        req = urllib.request.Request(
            f"{WS_HOST}/oidc/v1/token", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            sp_bearer = json.loads(r.read()).get("access_token", "")
        if sp_bearer:
            tok = _exchange(sp_bearer)
            if tok:
                return tok
    except Exception as e:
        print(f"Auth: SP M2M skipped ({e.__class__.__name__})")

    # ── Step 4: Stored PAT in Databricks Secrets (fallback) ──────────────────────
    try:
        pat = dbutils.secrets.get("lakebase-demo", "databricks_pat").strip()
        if pat:
            tok = _exchange(pat)
            if tok:
                return tok
            print("Auth: stored PAT (direct)")
            return pat
    except Exception:
        pass

    raise RuntimeError(
        "Could not obtain a Lakebase credential.\n"
        "Options:\n"
        "  1. Run interactively in the Databricks workspace browser (context token auto-used)\n"
        "  2. Fill the 'lakebase_pass' widget with a token from:\n"
        "     databricks auth token --profile <your-profile>\n"
        "  3. Store a Service Principal in secret scope 'lakebase-demo':\n"
        "     Keys: sp_client_id, sp_client_secret, endpoint_path\n"
        "  See docs/AUTHENTICATION.md for the full setup guide."
    )
