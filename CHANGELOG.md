# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions: [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-11-17

### Added

- `drift.yml`: weekday schedule, one read-only plan job per target (GitHub OIDC,
  `-lock=false`), one notify job that always runs; Monday heartbeat; missing reports become
  error reports.
- `scripts/detect-drift.sh`: init → plan → (Infracost) → report for one stack; every failure
  becomes an `error` report; the plan never leaves a temp dir.
- `driftwatch` (stdlib Python): reports with four statuses (clean, drift, pending, error); drift =
  attributes changed outside Terraform that apply would change back; accepted differences in
  `drift.toml` with mandatory reasons; sensitive values masked, long values shortened; Slack
  Block Kit, Teams Adaptive Card, Markdown and text output; HTTPS-only delivery with retries and
  `Retry-After`; the webhook URL is never printed.
- Example stack `stacks/app-storage` (S3 + SQS + DLQ) that runs on AWS or on Moto, with
  `terraform test`.
- End-to-end test on Moto (apply, out-of-band changes, Slack/Teams payloads, pending code,
  broken backend) and a drill lab (`make lab-shell`).

### Fixed (found while building)

- Raw `resource_drift` reports harmless differences on untouched stacks (legacy attributes set
  by companion resources, `null` vs `{}`): drift is now limited to what the plan changes back.
- `make e2e` took ~14 minutes on Docker Desktop because each `terraform init` wrote the AWS
  provider into the bind-mounted repository; `TF_DATA_DIR` and the plugin cache now live in the
  container's `/tmp` (~3.5 minutes).
