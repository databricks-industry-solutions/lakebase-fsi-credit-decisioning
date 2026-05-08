# Lakebase Authentication Guide

This document covers authentication patterns for connecting to Lakebase from different Databricks contexts.

## Quick Reference

| Scenario | Credential | Works from |
|---|---|---|
| Interactive notebook (browser) | OAuth session token (auto) | Cluster driver — public endpoint |
| Databricks Job (classic cluster) | OAuth JWT `all-apis` scope | Cluster driver — public endpoint |
| Databricks Job (serverless) | OAuth JWT `all-apis` scope | Private endpoint — same auth required |
| Databricks App | SDK WorkspaceClient (SP) | App runtime — public endpoint |
| External app / local dev | Generate credential (UI) | Public endpoint |

## Recommended Pattern: Pass OAuth Token via Job Parameters

The most reliable pattern for automated jobs is to obtain a fresh OAuth token with `all-apis` scope and pass it directly as the `lakebase_pass` notebook parameter.

```bash
# Get fresh token (requires: databricks auth login with all-apis scope)
FRESH_TOKEN=$(databricks auth token --profile <your-profile> --output json | \
    python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

# Pass directly to notebook job (not via {{secrets/...}} interpolation)
databricks jobs run-now <JOB_ID> --json '{
  "job_parameters": {
    "lakebase_pass": "'"$FRESH_TOKEN"'"
  }
}'
```

**Token expiry:** OAuth session tokens expire in ~1 hour. For longer pipelines, use a Service Principal with M2M OAuth (see below).

## Production Pattern: Service Principal M2M OAuth

For production workloads, use a Service Principal:

```python
import urllib.request, urllib.parse, json

def get_lakebase_token(ws_host, client_id, client_secret):
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "all-apis",
    }).encode()
    req = urllib.request.Request(
        f"{ws_host}/oidc/v1/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("access_token", "")

# Credentials from Databricks secrets scope
cid  = dbutils.secrets.get("lakebase-demo", "sp_client_id")
csec = dbutils.secrets.get("lakebase-demo", "sp_client_secret")
token = get_lakebase_token("https://<workspace>.cloud.databricks.com", cid, csec)
```

**Requirements:**
1. Create a Service Principal in the Account Console
2. Generate client credentials (Account Console → Service Principals → [SP] → Add secret)
3. Grant the SP access to the Lakebase instance (Catalog Explorer → Lakebase → Permissions)
4. Store client_id and client_secret in a Databricks secret scope

## Databricks Apps Authentication

Databricks Apps automatically inject `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET` for the App's built-in service principal. Use the SDK to get a token:

```python
from databricks.sdk import WorkspaceClient
headers = WorkspaceClient().config.authenticate()
token = headers.get("Authorization", "").replace("Bearer ", "")
```

**Requirement:** The App's service principal must be granted access to the Lakebase instance via Catalog Explorer → Lakebase → Permissions.

## Known Limitations

### Private vs Public Endpoint

Lakebase instances expose two endpoints:
- **Public endpoint** (`18.x.x.x`): accessible from outside Databricks, accepts OAuth JWT
- **Private endpoint** (`192.168.x.x`): used by Databricks serverless compute, requires specific NCC configuration

When connecting from serverless jobs or notebooks, the DNS resolves to the private endpoint. The credentials generated via "Generate credential" in the UI work on the public endpoint but may fail on the private endpoint.

**Workaround for serverless:** Pass an OAuth token with `all-apis offline_access` scope (obtained via `databricks auth token`) directly as the notebook parameter. This token is accepted by both endpoints.

**Production fix:** Configure Network Connectivity Configuration (NCC) for your serverless workspace. See Databricks documentation: "Configure private connectivity to resources in your VPC".

### `{{secrets/...}}` Interpolation

The `{{secrets/scope/key}}` syntax in job `base_parameters` may not be interpolated in all Databricks workspace configurations. If secrets are not being substituted, pass the actual token value directly instead.

### `resources:` section in App YAML

The `resources:` section in `app.yaml` for mapping secrets to environment variables may not be supported in all App runtime versions. Use direct `env.value` entries as a workaround.

## Open Questions

See `docs/OPEN_QUESTIONS.md` for pending questions to the Lakebase product team about:
- Correct `generate-credential` API endpoint for private endpoint auth
- NCC configuration requirements for serverless → Lakebase connectivity
