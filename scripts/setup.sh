#!/usr/bin/env bash
# setup.sh — Upload notebooks + create job for lakebase-fsi-credit-decisioning.
#
# PREREQUISITES:
#   1. Databricks CLI installed and authenticated:
#      databricks auth login --host https://<workspace>.cloud.databricks.com --profile <profile>
#   2. Lakebase instance created in Catalog Explorer.
#   3. Secret scope 'lakebase-demo' created with 'endpoint_path' key:
#      databricks secrets create-scope lakebase-demo --profile <profile>
#      databricks secrets put-secret lakebase-demo endpoint_path --string-value "<path>"
#
# USAGE:
#   bash scripts/setup.sh [--profile <profile>] [--catalog <catalog>] [--user <email>]
#
# EXAMPLE:
#   export DATABRICKS_PROFILE=my-workspace
#   bash scripts/setup.sh --catalog my_catalog --user me@company.com

set -euo pipefail

# ── Defaults (override via flags or env vars) ─────────────────────────────────
PROFILE="${DATABRICKS_PROFILE:-DEFAULT}"
CATALOG="${UC_CATALOG:-}"
SCHEMA="${UC_SCHEMA:-lakebase_credit_demo}"
USER_EMAIL="${LAKEBASE_USER:-}"
LAKEBASE_HOST="${LAKEBASE_HOST:-}"
N_ROWS="${N_ROWS:-100000}"
MODEL_NAME="${MODEL_NAME:-credit_scoring_v1}"
SECRET_SCOPE="lakebase-demo"
WS_PATH="/Shared/lakebase-fsi-credit-decisioning"
APP_NAME="lakebase-credit-decisioning"

# Parse flags
while [[ $# -gt 0 ]]; do
    case $1 in
        --profile)        PROFILE="$2";       shift 2 ;;
        --catalog)        CATALOG="$2";       shift 2 ;;
        --schema)         SCHEMA="$2";        shift 2 ;;
        --user)           USER_EMAIL="$2";    shift 2 ;;
        --lakebase-host)  LAKEBASE_HOST="$2"; shift 2 ;;
        *)                echo "Unknown flag: $1"; exit 1 ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="databricks --profile $PROFILE"

# Colors
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }
step() { echo -e "\n${YELLOW}── $* ──${NC}"; }

# ── Validate required inputs ──────────────────────────────────────────────────
if [ -z "$CATALOG" ]; then
    echo "ERROR: --catalog is required. Example: --catalog my_catalog"
    echo "       or set UC_CATALOG env var."
    exit 1
fi
if [ -z "$USER_EMAIL" ]; then
    echo "ERROR: --user is required. Example: --user me@company.com"
    echo "       or set LAKEBASE_USER env var."
    exit 1
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  lakebase-fsi-credit-decisioning — Setup"
echo "═══════════════════════════════════════════════════════════"
echo "  Profile:      $PROFILE"
echo "  UC Catalog:   $CATALOG.$SCHEMA"
echo "  User:         $USER_EMAIL"
echo "  Secret scope: $SECRET_SCOPE"
echo "  Workspace:    $WS_PATH"
echo ""

# Verify auth
AUTH_OUT=$($DB auth describe 2>/dev/null || true)
if ! echo "$AUTH_OUT" | grep -qi "host"; then
    fail "Profile '$PROFILE' not authenticated. Run:\n  databricks auth login --profile $PROFILE"
fi
ok "Authenticated (profile: $PROFILE)"

# ── Step 1: Upload notebooks ──────────────────────────────────────────────────
step "Uploading notebooks to $WS_PATH/notebooks"

$DB workspace mkdirs "$WS_PATH/notebooks" 2>/dev/null || true

for nb in "$REPO_ROOT/notebooks/"*.py; do
    name=$(basename "$nb" .py)
    $DB workspace import "$WS_PATH/notebooks/$name" \
        --file "$nb" --language PYTHON --format SOURCE --overwrite
    ok "  $name"
done

# ── Step 2: Create multi-task job ─────────────────────────────────────────────
step "Creating pipeline job"

BASE_PARAMS=$(python3 -c "
import json
params = {
    'uc_catalog':     '$CATALOG',
    'uc_schema':      '$SCHEMA',
    'lakebase_host':  '${LAKEBASE_HOST:-}',
    'lakebase_port':  '5432',
    'lakebase_db':    'databricks_postgres',
    'lakebase_user':  '$USER_EMAIL',
    'lakebase_pass':  '',
    'n_rows':         '$N_ROWS',
    'model_name':     '$MODEL_NAME',
    'n_decisions':    '10000',
    'target_p99_ms':  '50',
}
print(json.dumps(params))
")

JOB_JSON=$(python3 -c "
import json

base_params = $BASE_PARAMS
notebooks = [
    '01_data_generation',
    '02_model_training',
    '03_feature_store',
    '04_realtime_serving',
    '05_decision_log',
]
tasks = []
for nb in notebooks:
    task = {
        'task_key': nb,
        'notebook_task': {
            'notebook_path': '$WS_PATH/notebooks/' + nb,
            'base_parameters': base_params,
        },
        'depends_on': [{'task_key': tasks[-1]['task_key']}] if tasks else [],
    }
    tasks.append(task)

job = {
    'name': 'lakebase-credit-pipeline',
    'tasks': tasks,
    'description': 'End-to-end credit decisioning pipeline — lakebase-fsi-credit-decisioning SA',
}
print(json.dumps(job, indent=2))
")

JOB_ID=$($DB jobs create --json "$JOB_JSON" 2>/dev/null \
    | python3 -c "import sys, json; print(json.load(sys.stdin).get('job_id', ''))" \
    || echo "")

if [ -n "$JOB_ID" ]; then
    ok "Job created — ID: $JOB_ID"
else
    warn "Could not create job automatically — create it manually in the UI."
fi

# ── Step 3: Deploy App ────────────────────────────────────────────────────────
step "Deploying Databricks App: $APP_NAME"

if [ ! -f "$REPO_ROOT/06_credit_app/app.yaml" ]; then
    warn "app.yaml not found — skipping App deploy."
elif grep -q "<your-" "$REPO_ROOT/06_credit_app/app.yaml"; then
    warn "app.yaml still has placeholder values."
    warn "Edit 06_credit_app/app.yaml and replace all <placeholder> values, then run:"
    warn "  databricks apps deploy $APP_NAME --source-code-path 06_credit_app/ --profile $PROFILE"
else
    $DB apps create "$APP_NAME" \
        --description "Real-time credit decisioning — Lakebase feature store" \
        2>/dev/null || true

    $DB apps deploy "$APP_NAME" \
        --source-code-path "$REPO_ROOT/06_credit_app" \
        && ok "App deployed: $APP_NAME" \
        || warn "App deploy failed — run manually: databricks apps deploy $APP_NAME --source-code-path 06_credit_app/"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
WS_HOST=$($DB auth describe 2>/dev/null | grep "Host:" | awk '{print $2}' || echo "<your-workspace>")

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  SETUP COMPLETE"
echo ""
echo "  Notebooks: $WS_HOST/#workspace$WS_PATH/notebooks"
[ -n "$JOB_ID" ] && echo "  Job ID:    $JOB_ID"
echo ""
echo "  NEXT STEPS:"
echo "  1. Run notebook 00 to verify Lakebase connection:"
echo "     $WS_HOST/#workspace$WS_PATH/notebooks/00_setup_verify"
echo "  2. Run the pipeline job (or notebooks in order 01→05):"
[ -n "$JOB_ID" ] && echo "     databricks jobs run-now --job-id $JOB_ID --profile $PROFILE"
echo "  3. Open the App after deploy completes."
echo "═══════════════════════════════════════════════════════════"
