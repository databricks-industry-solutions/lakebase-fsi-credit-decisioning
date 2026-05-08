# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this Solution Accelerator, please report it via the [Databricks Responsible Disclosure Program](https://www.databricks.com/trust/security-disclosure).

**Do not** open a public GitHub issue for security vulnerabilities.

## Security Considerations

- All credentials must be stored in Databricks Secrets (never hardcoded)
- The `lakebase_pass` widget accepts short-lived credentials (~14 min TTL) — never commit tokens
- The App's service principal accesses Lakebase via `generate_database_credential()` — no static passwords
- See `docs/AUTHENTICATION.md` for the full credential management guide
