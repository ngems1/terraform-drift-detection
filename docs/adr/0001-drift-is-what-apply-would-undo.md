# 0001. Drift is an out-of-band change that `terraform apply` would undo

## Status

Accepted · 2026-11-17

## Context

"Drift" must mean something a human should act on, or the channel gets muted. Terraform
offers three raw signals:

- `terraform plan -refresh-only`: every difference between the state file and reality;
- the `resource_drift` section of a normal plan's JSON: the same differences, recorded during
  the refresh;
- the plan itself (`-detailed-exitcode` = 2): anything apply would change, drift **or** merged
  code that nobody applied.

Running the e2e test on a freshly applied stack showed the problem with the first two:
`resource_drift` already lists differences on resources nobody touched: the bucket's legacy
`versioning` / encryption attributes (set by the separate `aws_s3_bucket_versioning` and
encryption resources) and `tags: null → {}`. Terraform itself hides these in its human output
(only "relevant" attributes are shown). The third signal mixes two different problems.

## Options considered

### Option A: alert on `plan -detailed-exitcode` = 2

- ✅ One number; trivial to implement (the naive version).
- ❌ "Someone changed prod in the console" and "a merged PR was never applied" look identical;
  they need different people.

### Option B: alert on every `resource_drift` entry (or `plan -refresh-only`)

- ✅ Catches every out-of-band change.
- ❌ Noise on every run (computed attributes, companion resources, normalisation). Teams learn
  to ignore the alert within a week.

### Option C: drift = attributes changed in `resource_drift` **and** changed back by the plan

- ✅ Reports exactly the out-of-band changes that conflict with the code, attribute by
  attribute, with the old and new value.
- ✅ Everything else the plan would change is reported separately as **pending code**.
- ❌ An out-of-band change to an attribute the code doesn't manage is not reported (Terraform
  wouldn't touch it either). That is consistent with "the code is the source of truth".
- ❌ Two plans' worth of logic to explain to juniors (this ADR, LEARN.md).

## Decision

**Option C**, plus an explicit list of **accepted differences** (`drift.toml`, each with a
reason) for perpetual diffs a provider or an external tool causes. The tests run on real plans
produced by the e2e test, including the noisy "clean" plan.

## Consequences

- **Good:** a "drift detected" message lists only changes someone made outside Terraform that
  `terraform apply` would revert, e.g. `visibility_timeout_seconds 60 → 300`.
- **Good:** "unapplied code" gets its own status (`pending`) and wording.
- **Bad:** perpetual diffs (here: two Moto quirks; in production: provider bugs, external
  taggers) surface as `pending` until someone records them in `drift.toml`, which is the
  intended prompt to look at them once.
