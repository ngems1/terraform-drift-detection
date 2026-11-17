# 0003. Plan with a read-only role, without locking, and never ship the plan file

## Status

Accepted · 2026-11-17

## Context

The drift job runs `terraform plan` against production every day, unattended. It needs cloud
credentials, it reads the state, and its output (the plan) contains every attribute of every
resource, sensitive values in clear text.

## Options considered

### Option A: reuse the deploy role and the normal plan (naive)

- ✅ Nothing new to set up.
- ❌ A scheduled job holding write access to production is a standing target; a compromised
  workflow (or a dependency in it) can change prod.
- ❌ `terraform plan` takes the state lock: a drift check running during a real apply fails, or
  makes the apply fail.

### Option B: a dedicated read-only role (OIDC), `-lock=false`, reports only leave the runner

- ✅ The worst a compromised drift job can do is read.
- ✅ `-lock=false` is safe for a read-only plan: it never writes state, and it never blocks
  an apply.
- ✅ Only the small JSON report (resource names, attribute names, shortened non-sensitive
  values) is uploaded; `detect-drift.sh` keeps the plan in a temp dir and deletes it.
- ❌ A read-only plan may race with an apply and report a half-applied state once; the next
  run corrects it.
- ❌ One more IAM role to maintain (ReadOnlyAccess + state bucket read, trusted for this repo's
  `drift.yml` on `main` only).

## Decision

**Option B.** `drift.yml` assumes `vars.DRIFT_ROLE_ARN` through GitHub OIDC (no stored keys),
runs `terraform plan -lock=false -detailed-exitcode`, and uploads only `out/*.json`.

## Consequences

- **Good:** no write credential and no plan file ever leave the runner.
- **Bad:** reports may show values (shortened to 60 characters) of non-sensitive attributes;
  if a provider forgets to mark a secret sensitive, it can reach the chat. THREAT-MODEL T3.
- **Bad:** read-only roles can't read some data sources that need write-like permissions;
  such stacks fail as `error` until the role is extended, which is visible, not silent.
