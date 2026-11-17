# Threat model

**Scope:** the scheduled `drift.yml` workflow, `scripts/detect-drift.sh`, `driftwatch`, the
drift IAM role, the Terraform state it reads, and the Slack / Teams webhooks.
Trust boundaries: *GitHub Actions → AWS (OIDC)*, *the runner → the chat platforms*, *the plan
(sensitive data) → the report (shared)*.

**Out of scope:** the security of the stacks themselves, the apply pipeline and its role
(project #4), Slack/Teams/GitHub as platforms.

## Assets

- **The drift role**: read access to the accounts and the state.
- **Webhook URLs**: anyone holding one can post into the channel.
- **Plan contents**: every attribute, secrets included.
- **The signal itself**: people rely on silence meaning "no drift".

## Threats

| # | Asset | Threat | Likelihood / Impact | Mitigation in this repo | Residual risk / NOT protected against |
|---|---|---|---|---|---|
| T1 | drift role | **A write-capable role in a scheduled job** (the naive version reuses deploy keys) | M / H | dedicated read-only role via GitHub OIDC, no stored keys (ADR 0003); `id-token: write` only on the plan job | the role can still READ everything, including the state with its secrets; restrict the trust to `drift.yml` on `main` |
| T2 | webhook URLs | **Webhook leaked** in logs, errors or the repo | M / M | URLs only from secrets via env vars; never printed (tested: errors don't contain the URL); https enforced | anyone with repo admin can read Actions secrets; rotate on staff changes |
| T3 | plan contents | **Secrets posted to chat** or uploaded as artifacts | M / H | plan kept in a temp dir and deleted; only the JSON report is uploaded; values truncated to 60 chars; sensitive attributes masked (tested) | a provider that doesn't mark an attribute sensitive: its (shortened) value can reach the chat |
| T4 | the signal | **Silent failure**: expired role, deleted state, disabled workflow, broken webhook | H / H | failed plans become `error` reports, always posted, notify exits 1; a missing report becomes an error; Monday heartbeat (ADR 0002) | a workflow disabled by GitHub (60 days without commits on public repos) stops the heartbeat too: someone must notice a missing Monday message |
| T5 | the signal | **Alert fatigue** hides real drift | H / M | drift = what apply would undo (ADR 0001); pending code separate; accepted differences need a reason; clean runs are silent | an over-broad `drift.toml` entry hides real drift; review it like code |
| T6 | state | **Locking conflicts** with a real apply | M / M | `-lock=false` on a read-only plan | a report taken mid-apply may be wrong once |
| T7 | chat | **Message injection**: resource names or values containing Slack markup (`<!channel>`) | L / L | Slack mrkdwn `&`, `<`, `>` escaped (tested) | Teams Adaptive Card text isn't escaped for Markdown (cosmetic only) |
| T8 | drift role | **Workflow tampering**: a PR changes `drift.yml` to exfiltrate with the role | L / H | OIDC trust can be pinned to `ref:refs/heads/main`; PR workflows get no OIDC token for this role | requires branch protection and CODEOWNERS on `.github/` (GitHub settings) |

## Assumptions

- The drift role's trust policy allows only this repository's `main` branch.
- `main` is protected; `drift.yml`, `drift.toml` and `drift-targets.json` changes are reviewed.
- Webhook secrets are scoped to this repository (or an environment), not the organisation.

## How to verify

- `make test`: `test_sensitive_values_are_masked`, `test_long_values_are_shortened` (T3),
  `test_gives_up_and_never_prints_the_url`, `test_plain_http_is_refused` (T2),
  `test_broken_input_becomes_an_error_report_never_clean`, `test_error_reports_fail_by_default...` (T4),
  `test_slack_escapes_angle_brackets` (T7); bats: plan files deleted, every exit path reports (T3, T4).
- `make e2e`: step 5 (broken backend → error message, exit 1).
- [Drills](DRILLS.md) 1-3.
