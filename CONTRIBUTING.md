# Contributing

We welcome contributions! Please follow these guidelines.

## Before you start

1. Open an issue describing the change or bug fix.
2. For significant changes, discuss the approach before writing code.

## Development setup

```bash
git clone https://github.com/databricks-industry-solutions/lakebase-fsi-credit-decisioning.git
cd lakebase-fsi-credit-decisioning
pip install -r 06_credit_app/requirements.txt
```

## Notebook changes

- Test notebooks end-to-end on a real Databricks workspace before submitting.
- Widget defaults must be empty strings — never commit workspace-specific values.
- The `_get_lakebase_credential` function lives in `util_lakebase_auth.py` only. Do not copy it into other notebooks.

## App changes

- The App runs in Databricks Apps (serverless). No local filesystem writes, no `pip install` at runtime.
- Test with `python app.py` locally (set `LAKEBASE_PASS` env var) before deploying.

## Pull request checklist

- [ ] No hardcoded workspace hosts, catalog names, or email addresses
- [ ] No credentials, tokens, or secret values
- [ ] `app.yaml` uses `<placeholder>` values only — never real endpoints
- [ ] Documentation updated if behavior changes
- [ ] Notebooks tested on DBR 15.4 ML LTS or later
