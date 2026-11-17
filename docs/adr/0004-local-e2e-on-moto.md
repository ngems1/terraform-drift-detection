# 0004. Test the whole pipeline locally on Moto, recording its quirks as accepted differences

## Status

Accepted · 2026-11-17

## Context

A drift detector can only be tested by creating real drift: apply a stack, change it behind
Terraform's back, and check the report and the message. Doing that against a real AWS account
costs money, needs credentials in CI, and can't run on a junior's laptop.

## Options considered

### Option A: LocalStack

- ✅ The most complete AWS emulator; widely documented.
- ❌ Since version 2026.03.0 the image requires an account and an auth token, even for the free
  tier: a dependency on a vendor account in every CI run and every laptop.

### Option B: Moto in server mode (`motoserver/moto`)

- ✅ Open source (Apache-2.0), no account, one container; S3 and SQS are well covered.
- ✅ Terraform's AWS provider talks to it through custom endpoints; the S3 backend works too,
  so even the state lives in the emulator.
- ❌ Lower fidelity: two perpetual diffs appear right after apply (bucket tags not returned at
  creation, the redrive policy JSON returned with spaces).

### Option C: a real sandbox AWS account

- ✅ Real behaviour.
- ❌ Cost, credentials in CI, cleanup, and no laptop story.

## Decision

**Option B.** The stack takes an `aws_endpoint` variable (null for real AWS). `tests/e2e/run.sh`
drives the real `scripts/detect-drift.sh` and `driftwatch` against Moto and a fake webhook. The
two Moto quirks are recorded in `tests/e2e/drift.toml`, with reasons, exactly as a team would
record a real provider's perpetual diff.

## Consequences

- **Good:** the pipeline runs end to end in about two minutes, offline except for image and
  provider downloads, on a laptop and in CI (Moto as a service container).
- **Good:** the quirks became a teaching example for `drift.toml`.
- **Bad:** behaviour that only real AWS has (eventual consistency, IAM evaluation, real
  pricing) is not covered; the scheduled workflow on GitHub is the first real-account run.
