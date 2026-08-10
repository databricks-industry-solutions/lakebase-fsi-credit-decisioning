#!/usr/bin/env bash
# teardown.sh — Remove everything setup.sh / the pipeline job created.
#
# Idempotent: every drop uses IF EXISTS / ignores "not found", so it can be
# re-run safely and a partial setup tears down cleanly.
#
# Removes:
#   - UC Delta tables: credit_features_bronze, credit_features_silver,
#     feature_store_live, decision_log
#   - UC registered models: credit_scoring_v1, credit_scoring_xgb, credit_scoring_lr
#   - the UC schema itself (only if --drop-schema is passed)
#   - the Databricks App: lakebase-credit-decisioning
#   - the pipeline job: lakebase-credit-pipeline
#
# Lakebase PostgreSQL tables (credit_features, credit_limits, decision_log) are
# dropped/recreated with DROP TABLE IF EXISTS inside notebooks 03/05, so they are
# already idempotent and are not touched here.
#
# USAGE:
#   bash scripts/teardown.sh --catalog <catalog> [--schema <schema>] \
#       [--profile <profile>] [--drop-schema]

set -euo pipefail

# ── Defaults (override via flags or env vars) ─────────────────────────────────
PROFILE="${DATABRICKS_PROFILE:-DEFAULT}"
CATALOG="${UC_CATALOG:-}"
SCHEMA="${UC_SCHEMA:-lakebase_credit_demo}"
APP_NAME="lakebase-credit-decisioning"
JOB_NAME="lakebase-credit-pipeline"
DROP_SCHEMA="false"

while [[ $# -gt 0 ]]; do
    case $1 in
        --profile)      PROFILE="$2"; shift 2 ;;
        --catalog)      CATALOG="$2"; shift 2 ;;
        --schema)       SCHEMA="$2";  shift 2 ;;
        --drop-schema)  DROP_SCHEMA="true"; shift ;;
        *)              echo "Unknown flag: $1"; exit 1 ;;
    esac
done

DB="databricks --profile $PROFILE"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
step() { echo -e "\n${YELLOW}── $* ──${NC}"; }

if [ -z "$CATALOG" ]; then
    echo "ERROR: --catalog is required. Example: --catalog my_catalog"
    echo "       or set UC_CATALOG env var."
    exit 1
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  lakebase-fsi-credit-decisioning — Teardown"
echo "═══════════════════════════════════════════════════════════"
echo "  Profile:     $PROFILE"
echo "  UC target:   $CATALOG.$SCHEMA"
echo "  Drop schema: $DROP_SCHEMA"
echo ""

# Verify auth
if ! $DB auth describe 2>/dev/null | grep -qi "host"; then
    warn "Profile '$PROFILE' not authenticated — nothing to tear down."
    exit 0
fi
ok "Authenticated (profile: $PROFILE)"

# ── Step 1: Drop UC tables ────────────────────────────────────────────────────
step "Dropping UC tables in $CATALOG.$SCHEMA"

for tbl in credit_features_bronze credit_features_silver feature_store_live decision_log; do
    $DB sql-query -q "DROP TABLE IF EXISTS \`$CATALOG\`.\`$SCHEMA\`.\`$tbl\`;" >/dev/null 2>&1 \
        && ok "  dropped table $tbl" \
        || warn "  could not drop $tbl (may not exist)"
done

# ── Step 2: Drop registered models ────────────────────────────────────────────
step "Deleting UC registered models"

for model in credit_scoring_v1 credit_scoring_xgb credit_scoring_lr; do
    $DB sql-query -q "DROP MODEL IF EXISTS \`$CATALOG\`.\`$SCHEMA\`.\`$model\`;" >/dev/null 2>&1 \
        && ok "  deleted model $model" \
        || warn "  could not delete $model (may not exist)"
done

# ── Step 3: Optionally drop the schema ────────────────────────────────────────
if [ "$DROP_SCHEMA" = "true" ]; then
    step "Dropping schema $CATALOG.$SCHEMA"
    $DB sql-query -q "DROP SCHEMA IF EXISTS \`$CATALOG\`.\`$SCHEMA\` CASCADE;" >/dev/null 2>&1 \
        && ok "  schema dropped" \
        || warn "  could not drop schema (may not exist)"
fi

# ── Step 4: Delete the App ────────────────────────────────────────────────────
step "Deleting App: $APP_NAME"

if $DB apps get "$APP_NAME" >/dev/null 2>&1; then
    $DB apps delete "$APP_NAME" >/dev/null 2>&1 \
        && ok "  app deleted" \
        || warn "  could not delete app"
else
    ok "  app not present — nothing to delete"
fi

# ── Step 5: Delete the pipeline job ───────────────────────────────────────────
step "Deleting job: $JOB_NAME"

JOB_IDS=$($DB jobs list -o json 2>/dev/null \
    | python3 -c "import sys,json; [print(j['job_id']) for j in json.load(sys.stdin) if j.get('settings',{}).get('name')=='$JOB_NAME']" \
    2>/dev/null || echo "")

if [ -n "$JOB_IDS" ]; then
    for jid in $JOB_IDS; do
        $DB jobs delete "$jid" >/dev/null 2>&1 \
            && ok "  deleted job $jid" \
            || warn "  could not delete job $jid"
    done
else
    ok "  no job named $JOB_NAME — nothing to delete"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  TEARDOWN COMPLETE"
echo "  Re-run scripts/setup.sh (or 'databricks bundle deploy') to rebuild."
echo "═══════════════════════════════════════════════════════════"
