"""sp_auth.py — Service Principal M2M OAuth helper for Lakebase.

Best practice for Solution Accelerators: never use user tokens or PATs for automated
pipelines. Service Principals with OAuth M2M client credentials flow are the standard.

Usage (in notebooks):
    import sys; sys.path.insert(0, '/Workspace/Shared/lakebase-demo-sa/scripts')
    from sp_auth import get_lakebase_token
    token = get_lakebase_token()

Or inline (copy the function):
    token = get_lakebase_token()

The token is automatically refreshed on each call — safe for long-running jobs.
Cache it for the duration of a single notebook cell if needed.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.parse


def get_lakebase_token(
    workspace_host: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> str:
    """Return a valid OAuth M2M access token for Lakebase.

    Credential resolution priority:
      1. Explicit args (for testing / override)
      2. Databricks secrets via dbutils (standard SA pattern)
      3. Environment variables (local dev / CI)
      4. Databricks SDK WorkspaceClient (Apps runtime — auto-injected SP credentials)

    Security: credentials are never logged or stored in plaintext.
    Token is short-lived (~1h) and automatically refreshed on each call.
    """
    # Priority 4: Databricks Apps runtime (SP credentials auto-injected)
    if not (client_id and client_secret):
        try:
            from databricks.sdk import WorkspaceClient
            headers = WorkspaceClient().config.authenticate()
            tok = headers.get("Authorization", "").replace("Bearer ", "")
            if tok:
                return tok
        except Exception:
            pass

    # Priority 3: Environment variables (local dev, CI)
    host = workspace_host or os.environ.get("DATABRICKS_HOST", "")
    cid = client_id or os.environ.get("LAKEBASE_SP_CLIENT_ID", "")
    csec = client_secret or os.environ.get("LAKEBASE_SP_CLIENT_SECRET", "")

    # Priority 2: Databricks secrets (standard pattern for notebooks/jobs)
    if not (cid and csec):
        try:
            from pyspark.dbutils import DBUtils  # type: ignore
            from pyspark.sql import SparkSession  # type: ignore
            dbutils = DBUtils(SparkSession.builder.getOrCreate())
            cid = cid or dbutils.secrets.get("lakebase-demo", "sp_client_id")
            csec = csec or dbutils.secrets.get("lakebase-demo", "sp_client_secret")
        except Exception:
            pass

    if not (host and cid and csec):
        raise RuntimeError(
            "Cannot obtain SP credentials. Set one of:\n"
            "  1. Databricks secrets: lakebase-demo/sp_client_id + sp_client_secret\n"
            "  2. Env vars: DATABRICKS_HOST, LAKEBASE_SP_CLIENT_ID, LAKEBASE_SP_CLIENT_SECRET\n"
            "  3. Run in Databricks Apps (auto-injected SP credentials)"
        )

    return _fetch_m2m_token(host.rstrip("/"), cid, csec)


def _fetch_m2m_token(host: str, client_id: str, client_secret: str) -> str:
    """Fetch OAuth M2M token via client_credentials grant."""
    data = urllib.parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     client_id,
        "client_secret": client_secret,
        "scope":         "all-apis",
    }).encode()
    req = urllib.request.Request(
        f"{host}/oidc/v1/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        body = json.loads(resp.read())
    tok = body.get("access_token", "")
    if not tok:
        raise RuntimeError(f"Token endpoint returned no access_token: {list(body.keys())}")
    return tok


if __name__ == "__main__":
    print("Testing SP token generation...")
    try:
        tok = get_lakebase_token()
        print(f"Token obtained ({len(tok)} chars): {tok[:20]}...")
    except Exception as e:
        print(f"FAILED: {e}")
