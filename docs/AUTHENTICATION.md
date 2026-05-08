# Lakebase Authentication Guide

This document covers authentication patterns for connecting to Lakebase from notebooks,
automated jobs, and the Databricks App. It also includes the onboarding steps for new users
who want to run the notebooks or access the deployed App.

---

## New User Onboarding

If you received access to a workspace where this SA is already deployed (notebooks uploaded,
App running), follow these steps to get started.

### Step 1 — Get workspace access

Ask the workspace admin to grant you:
- **USAGE** on the UC catalog (e.g. `your_catalog`)
- **CAN_USE** on the Lakebase project (Catalog Explorer → Lakebase → your instance → Permissions)
- **CAN_USE** on the Databricks App (Apps → `lakebase-credit-decisioning` → Permissions)

### Step 2 — Create your Lakebase postgres role (one-time)

Before you can connect to Lakebase from a notebook, your identity needs a postgres role.
Run the following SQL **once** using any admin credential (ask a workspace admin if you don't
have one):

```sql
CREATE EXTENSION IF NOT EXISTS databricks_auth;

-- Replace <your-email> with your Databricks email
SELECT databricks_create_role('<your-email@company.com>', 'end_user');

GRANT CONNECT ON DATABASE databricks_postgres TO "<your-email@company.com>";
GRANT USAGE ON SCHEMA public TO "<your-email@company.com>";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "<your-email@company.com>";
```

> **How to run this SQL:** Connect to Lakebase using an admin credential (from notebook 00 or
> any PostgreSQL client), or ask the admin who set up the SA to run it for you.

### Step 3 — Open the App (no setup needed)

Open the App URL in your browser. Log in with your Databricks SSO credentials.
The App uses your browser session (`X-Forwarded-Access-Token`) to connect to Lakebase
on your behalf — no additional config needed after Step 2.

### Step 4 — Run notebooks (optional)

If you want to run the data pipeline yourself:
1. Open notebook `00_setup_verify` in the Databricks workspace
2. Fill the widgets: `lakebase_host`, `lakebase_user` (your email), `uc_catalog`
3. Leave `lakebase_pass` **empty** — it will auto-detect your session token
4. Run all cells to verify connectivity
5. Then run notebooks 01 → 05 in order

---

## Admin Checklist — Onboarding a New User

For workspace admins onboarding a new team member:

**Unity Catalog grants (run as admin in any notebook):**
```sql
-- UC catalog access
GRANT USE CATALOG ON CATALOG <your_catalog> TO `<user-email>`;
GRANT USE SCHEMA ON SCHEMA <your_catalog>.lakebase_credit_demo TO `<user-email>`;
GRANT SELECT ON SCHEMA <your_catalog>.lakebase_credit_demo TO `<user-email>`;
GRANT EXECUTE ON FUNCTION <your_catalog>.lakebase_credit_demo.credit_scoring_v1 TO `<user-email>`;
```

**Lakebase postgres role (run via psycopg2 or notebook 00):**
```sql
CREATE EXTENSION IF NOT EXISTS databricks_auth;
SELECT databricks_create_role('<user-email>', 'end_user');
GRANT CONNECT ON DATABASE databricks_postgres TO "<user-email>";
GRANT USAGE ON SCHEMA public TO "<user-email>";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "<user-email>";
```

**Databricks UI grants:**
- [ ] Catalog Explorer → Lakebase → instance → Permissions → add user with `CAN_USE`
- [ ] Apps → `lakebase-credit-decisioning` → Permissions → add user with `CAN_USE`

**Share with the user:**
- [ ] App URL
- [ ] Lakebase host (for notebooks: `ep-xxxxx.database.<region>.cloud.databricks.com`)

---

## App Service Principal — First-Time Setup

When deploying the App for the first time, the App's service principal needs:

**1. UC grants** (same as above, but use the SP's `client_id` UUID as the principal):
```sql
GRANT USE CATALOG ON CATALOG <your_catalog> TO `<sp-client-id-uuid>`;
GRANT USE SCHEMA ON SCHEMA <your_catalog>.lakebase_credit_demo TO `<sp-client-id-uuid>`;
GRANT SELECT ON SCHEMA <your_catalog>.lakebase_credit_demo TO `<sp-client-id-uuid>`;
GRANT EXECUTE ON FUNCTION <your_catalog>.lakebase_credit_demo.credit_scoring_v1 TO `<sp-client-id-uuid>`;
```

**2. Lakebase postgres role** for the SP:
```sql
SELECT databricks_create_role('<sp-client-id-uuid>', 'service_principal');
GRANT CONNECT ON DATABASE databricks_postgres TO "<sp-client-id-uuid>";
GRANT USAGE ON SCHEMA public TO "<sp-client-id-uuid>";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "<sp-client-id-uuid>";
```

**How to find the App's SP client_id:**
```bash
# Get the App's SP numeric ID
databricks apps get <app-name> --profile <profile> | grep service_principal_id

# Get the SP's client_id UUID
databricks --profile <profile> api get /api/2.0/preview/scim/v2/ServicePrincipals/<numeric-id> \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['applicationId'])"
```

> These grants must be applied **after** the App is deployed (the SP is created at deploy time).
> If the App shows "Model not loaded", the SP is missing the UC grants above.

---

## Quick Reference

| Scenario | Pattern | Setup required |
|---|---|---|
| Interactive notebook (browser) | Context token → `generate_database_credential` | Role creation (one-time) |
| Databricks Job (serverless) | Pass credential as job parameter | Role creation (one-time) |
| Databricks App (any user) | `X-Forwarded-Access-Token` → `generate_database_credential` | Role creation per user |
| Databricks App (SP background) | SDK `generate_database_credential` | SP role creation (one-time) |

## Prerequisites: Create a Lakebase Postgres Role (one-time per identity)

**This is the most common setup step people miss.**

Before any identity (human user or service principal) can use `generate_database_credential`,
that identity needs a Postgres role in the Lakebase instance. Granting `CAN_USE` in the
Databricks UI is **not sufficient** — the postgres role must be created explicitly via SQL.

### For a human user (interactive notebooks, jobs running as you)

Connect to the Lakebase instance using your own credential, then run:

```sql
-- Enable the Databricks auth extension (idempotent)
CREATE EXTENSION IF NOT EXISTS databricks_auth;

-- Create your postgres role
-- Replace <your-email> with your Databricks email address
SELECT databricks_create_role('<your-email@company.com>', 'end_user');

-- Grant permissions on the schema
GRANT CONNECT ON DATABASE databricks_postgres TO "<your-email@company.com>";
GRANT USAGE ON SCHEMA public TO "<your-email@company.com>";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "<your-email@company.com>";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "<your-email@company.com>";
```

### For a Service Principal (automated jobs, App background tasks)

The SP's `client_id` (UUID) is its postgres username:

```sql
CREATE EXTENSION IF NOT EXISTS databricks_auth;

-- Replace <sp-client-id> with the SP's UUID (not display name)
SELECT databricks_create_role('<sp-client-id-uuid>', 'service_principal');

GRANT CONNECT ON DATABASE databricks_postgres TO "<sp-client-id-uuid>";
GRANT USAGE ON SCHEMA public TO "<sp-client-id-uuid>";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "<sp-client-id-uuid>";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "<sp-client-id-uuid>";
```

> **How to get the SP's client_id:** Account Console → Service Principals → [SP] → copy the UUID.
> In the Databricks Apps runtime, this is the `DATABRICKS_CLIENT_ID` env var.

---

## Pattern 1: Interactive Notebooks

When running interactively in the browser, the notebooks auto-detect your session token and
call `generate_database_credential` (via the `util_lakebase_auth` utility notebook).

**No configuration needed** beyond having your postgres role created (see Prerequisites).

Fill the `lakebase_host` and `lakebase_user` widgets. Leave `lakebase_pass` empty — it will
be auto-filled.

---

## Pattern 2: Databricks Jobs (recommended approach)

For automated jobs, generate a fresh credential before triggering the run and pass it as a
job parameter. This avoids dependency on the SDK `generate_database_credential` inside
serverless compute (which requires additional NCC configuration in some workspaces).

```python
import os
os.environ["DATABRICKS_CONFIG_PROFILE"] = "<your-profile>"
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()

ENDPOINT = "projects/<your-project>/branches/<branch>/endpoints/primary"
cred = w.postgres.generate_database_credential(endpoint=ENDPOINT)
lb_pass = cred.token

# Update job parameters with fresh credential
w.jobs.update(job_id=<JOB_ID>, new_settings={
    "tasks": [...],  # existing tasks
    "base_parameters": {"lakebase_pass": lb_pass, ...}
})
w.jobs.run_now(job_id=<JOB_ID>)
```

**Credential TTL:** ~14 minutes. For a pipeline that takes 20+ minutes, trigger it immediately
after generating the credential. The notebooks themselves execute fast enough that a single
credential covers the full run.

**Alternative for longer pipelines:** Store SP credentials in the secret scope and use
SP M2M OAuth (see Pattern 4 below).

---

## Pattern 3: Databricks App

The App uses a two-tier auth approach:

### Tier 1: User identity (browser sessions)
When a user opens the App in their browser, the runtime injects `X-Forwarded-Access-Token`
with the user's own OAuth token. The App captures this on page load and exchanges it for a
postgres credential:

```python
# In app.py — captures user token on every page request
@server.before_request
def _capture_user_bearer():
    bearer = flask.request.headers.get("X-Forwarded-Access-Token", "")
    if bearer:
        # Exchange for postgres-specific credential
        tok = _exchange_for_pg_cred(bearer)
        # Pool rebuilt with this user's identity
```

Each user connects to Lakebase using their own identity. All users with `CAN_USE` on the
App can access Lakebase, as long as their postgres role has been created (see Prerequisites).

### Tier 2: SDK fallback (startup, background tasks)
If no user token is available (App startup, non-browser requests), the SDK uses the App's
built-in service principal:

```python
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
cred = w.postgres.generate_database_credential(endpoint=LAKEBASE_ENDPOINT_PATH)
```

This requires the App's SP to have its postgres role created (see Prerequisites for SP).

The App's SP `client_id` is available as `os.environ.get("DATABRICKS_CLIENT_ID")` in the
Apps runtime.

---

## Pattern 4: Service Principal M2M OAuth (optional fallback)

This pattern is a fallback for environments where Step 0 (SDK) cannot resolve workspace
credentials automatically — for example, specific NCC configurations or private-endpoint
setups. Most deployments do not need it.

**When you need it:** if `_get_lakebase_credential` logs `SDK generate_database_credential
skipped` and no other step succeeds.

**Setup (one-time):**
```bash
databricks secrets put-secret lakebase-demo sp_client_id --string-value "<sp-uuid>"
databricks secrets put-secret lakebase-demo sp_client_secret --string-value "<sp-secret>"
```

The `util_lakebase_auth` notebook uses these automatically as Step 3 if Steps 0–2 fail.

**Requirements:**
1. Create a Service Principal in Account Console
2. Generate client credentials (Account Console → Service Principals → [SP] → Add secret)
3. Grant `CAN_USE` on the Lakebase project (Catalog Explorer → Lakebase → Permissions)
4. Create the postgres role via SQL (see Prerequisites for SP)
5. Store credentials in `lakebase-demo` secret scope

---

## Secret Scope Reference

The notebooks and the App use a Databricks secret scope named `lakebase-demo`.

| Key | Purpose | Required |
|---|---|---|
| `endpoint_path` | Lakebase endpoint for `generate_database_credential` | **Yes** |
| `sp_client_id` | SP UUID for M2M OAuth — Step 3 fallback only | No |
| `sp_client_secret` | SP secret for M2M OAuth — Step 3 fallback only | No |

`sp_client_id` and `sp_client_secret` are used only if Steps 0–2 in `util_lakebase_auth` all
fail (rare: specific NCC or private-endpoint configurations). Interactive notebooks, serverless
jobs, and the Databricks App all work with `endpoint_path` alone.

**Create the scope (minimum required):**
```bash
databricks secrets create-scope lakebase-demo --profile <your-profile>
databricks secrets put-secret lakebase-demo endpoint_path \
  --string-value "projects/<name>/branches/<branch>/endpoints/primary"
```

---

## Troubleshooting

### `password authentication failed for user '<email>'`
The postgres role for your user does not exist. Run the `databricks_create_role` SQL from
the Prerequisites section.

### `generate_database_credential` fails in serverless job
The SDK inside serverless compute may not automatically resolve the workspace host in all
configurations. Use Pattern 2 (pass credential as job parameter) as the workaround.

### App shows `DB pool ready` but queries fail
The App's SP doesn't have the postgres role. Run the SP role creation SQL from Prerequisites,
using the App's `DATABRICKS_CLIENT_ID` as the `client_id`.

### Credential expires mid-pipeline
Credentials have a ~14-minute TTL. For pipelines longer than 14 minutes running sequentially,
the credential in `lakebase_pass` may expire between notebooks. Use SP M2M OAuth (Pattern 4)
for long pipelines — SP credentials do not expire and refresh automatically.
