# LEARN: catching infrastructure drift before it catches you

A guide for juniors. Terms in *italics* link to the [course glossary](https://github.com/ngems1/code-clinic/blob/main/GLOSSARY.md).

## 1. The problem, in plain words

Terraform describes your infrastructure, but it isn't the only thing that changes it. At 2 a.m.
someone raises a queue timeout in the console. A script adds tags. A teammate "temporarily"
suspends versioning. Now the code and reality disagree: that's
*[drift](https://github.com/ngems1/code-clinic/blob/main/GLOSSARY.md#drift)*.

Drift hurts twice. First, the code no longer documents production. Second, the next unrelated
`terraform apply` silently reverts the console fix, at the worst possible moment. This project
checks every stack every weekday morning and tells the team, in one readable message, what
changed, where, and what applying would do.

## 2. Concepts from zero

### State, plan, refresh

Terraform keeps a *[state](https://github.com/ngems1/code-clinic/blob/main/GLOSSARY.md#terraform-state)*
file: what it believes exists. `terraform plan` first **refreshes** that belief by reading the
real resources, then compares reality with the code. `-detailed-exitcode` returns 0 (nothing to
do), 2 (changes) or 1 (error).

### Plan JSON: two sections that answer two questions

`terraform show -json tfplan` (*[plan JSON](https://github.com/ngems1/code-clinic/blob/main/GLOSSARY.md#plan-json)*):

- `resource_drift`: what the refresh found changed **since the last apply** (outside Terraform);
- `resource_changes`: what **apply** would do now.

The e2e test shows why neither is enough on its own. On a stack nobody touched,
`resource_drift` already lists the bucket's legacy `versioning` attribute (set by a separate
resource) and `tags: null → {}`; and `resource_changes` mixes "revert the console change" with
"apply the PR merged yesterday". So this tool combines them:

| Report status | Rule | Who acts |
|---|---|---|
| **drift** | an attribute changed in `resource_drift` **and** apply would change it back | the resource owner: keep (PR) or revert (apply) |
| **pending** | apply would change something that didn't drift | whoever owns the deploy pipeline |
| **clean** | nothing to do | nobody (and no message) |
| **error** | the plan couldn't run | the platform team, today: detection is blind |

### Perpetual diffs and accepted differences

Some differences come back on every plan even though nobody changes anything: a provider
compares JSON as text, an external tool adds tags. Here the emulator has two. They go into
`drift.toml`, each with a **reason**, so they're visible and reviewable instead of becoming
noise.

### Infracost

*[Infracost](https://github.com/ngems1/code-clinic/blob/main/GLOSSARY.md#infracost)* prices both
sides of the plan. The difference (`totalMonthlyCost - pastTotalMonthlyCost`) is "what apply
would do to the bill": a console-upsized instance shows up as a saving.

### OIDC and a read-only role

The workflow gets AWS credentials through
*[GitHub OIDC](https://github.com/ngems1/code-clinic/blob/main/GLOSSARY.md#github-oidc)*: no
stored keys, short-lived tokens, and a role limited to reading
(*[least privilege](https://github.com/ngems1/code-clinic/blob/main/GLOSSARY.md#least-privilege)*).

## 3. The diagram, step by step

Watch [`architecture.svg`](images/architecture.svg) (10-second loop):

1. **0-1.4 s:** the clock reaches 06:17 on a weekday; the workflow starts.
2. **1.2-3 s:** one plan job per target lights up, each with the read-only role and
   `-lock=false`; the plan JSON stays on its runner.
3. **3-4.7 s:** the reports appear: staging ✅ clean, app-storage/prod 🔶 drift, billing/prod 🚨
   error (its state bucket can't be read).
4. **6.2 s:** the notify job combines them into **one** message: the failure first, then the
   drift with the exact attribute (`visibility_timeout_seconds 60 → 300`), clean stacks on one
   line, and a link to the run.
5. **7.2 s:** the rule of the channel: post only when something needs attention, plus a Monday
   heartbeat, so silence means clean.

## 4. Naive vs production

The naive version is on the `naive` branch (tag `v0.1-naive`): a nightly `terraform plan` whose
text output is pasted into Slack with `curl`.

| Concern | Naive (`v0.1-naive`) | What goes wrong | Production (`v1.0.0`) |
|---|---|---|---|
| Errors | `terraform plan ... \|\| true` | expired credentials post an error text, or nothing; detection silently stops | a failed plan is an `error` report, always posted, the job fails |
| What "drift" means | anything in the plan | console changes, unapplied PRs and perpetual diffs look the same | drift / pending / clean / error, attribute-level (ADR 0001) |
| Noise | one message every night | the channel gets muted | one message only when needed + Monday heartbeat (ADR 0002) |
| Secrets | the whole plan text in Slack | sensitive values and internal names in chat history | only addresses, attribute names, shortened non-sensitive values |
| Message format | JSON built by string concatenation | a `"` in the plan breaks the payload | Block Kit / Adaptive Card built as data, escaped |
| Credentials | long-lived access keys in secrets | stealable keys with deploy rights | OIDC + read-only role (ADR 0003) |
| Locking | `plan` takes the lock | collides with real applies | `-lock=false` (read-only) |
| Webhook URL | `${{ secrets.X }}` inside `run:` | ends up in shell history and logs if misused | env var, never printed |
| Scope | one stack, hard-coded | adding a stack = editing the workflow | `drift-targets.json` matrix; one failure doesn't hide the others |
| Teams | not supported | — | Adaptive Card payload |
| Testing | none | — | Moto e2e, 64 tests, bats |

## 5. Common mistakes

- **`|| true` on the plan.** It turns "the check is broken" into "no drift".
- **Alerting on `plan` exit code 2.** It can't tell console changes from unapplied PRs.
- **Alerting on raw `resource_drift`.** Harmless refresh noise; the channel gets muted.
- **Posting the plan text.** Plans contain sensitive values and are unreadable in chat anyway.
- **Using the deploy role.** A scheduled job with write access to prod is a standing target.
- **Letting the drift check take the state lock.** It will collide with a real apply one day.
- **No heartbeat.** Silence then means either "clean" or "broken".
- **Ignores without reasons.** In six months nobody knows why `tags` is ignored on every bucket.

## 6. Try it on your laptop

```bash
make setup
make e2e          # ~3-4 minutes: the whole story on Moto, with assertions
make demo         # reports for the three real plans + the Slack payload
make lab-shell    # an interactive lab: lab_apply, aws_ ..., lab_check
```

Then do the [drills](DRILLS.md).

## 7. Build exercises

1. **A second stack.** Add `stacks/network` (a VPC + one subnet), a target in
   `drift-targets.json`, and extend the e2e test to drift it (e.g. change a tag).
   <details><summary>Hint</summary>Moto supports EC2/VPC. Copy the provider block with the endpoint variable; add <code>ec2 = var.aws_endpoint</code> to <code>endpoints</code>.</details>
2. **Who did it?** Add the CloudTrail event (user, time) for each drifted resource to the
   report, using `aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceName,...`.
   <details><summary>Hint</summary>Do it in <code>detect-drift.sh</code> after the report, so <code>driftwatch</code> stays free of AWS calls; Moto has a CloudTrail mock to test with.</details>
3. **Tickets instead of chat.** Open (or update) one GitHub issue per drifted stack, and close it
   when the stack is clean again.
   <details><summary>Hint</summary>Use the report JSON and <code>gh issue list --label drift --search "stack: app-storage/prod"</code>; keep the issue title stable so you can find it again.</details>
4. **Expiring ignores.** Give `drift.toml` entries an `expires` date (like project #8's waivers).
   <details><summary>Hint</summary>TOML has real dates; report an expired entry as a warning line in the message.</details>
5. **CircleCI.** Port `drift.yml` to a CircleCI scheduled pipeline (the challenge allows either).
   <details><summary>Hint</summary>The heavy lifting is in <code>detect-drift.sh</code> and <code>driftwatch</code>; only the scheduling, matrix and artifact passing change. CircleCI supports OIDC to AWS too.</details>

## 8. 🎯 Interview questions

<details>
<summary>1. What is infrastructure drift, and how do you detect it with Terraform?</summary>

Differences between the declared configuration and the real infrastructure, caused by changes
made outside the IaC workflow. Detect it with a scheduled `terraform plan` (or `plan
-refresh-only`), using the plan JSON: `resource_drift` for changes found during refresh and
`resource_changes` for what apply would do. Report only drift that conflicts with the code,
separately from unapplied changes.
</details>

<details>
<summary>2. Drift is detected. Do you always run `terraform apply`?</summary>

No. Find out why it changed (CloudTrail, the on-call log). If the change was a legitimate fix,
encode it in the code through a reviewed PR; if it was a mistake, apply. Reverting blindly can
undo an incident fix. Either way, the goal is code and reality agreeing again, deliberately.
</details>

<details>
<summary>3. How do you make a monitoring alert that people don't mute?</summary>

Every message must be actionable: send only when something needs attention, aggregate into one
message, say exactly what changed and who should act, separate kinds of problems, record
accepted exceptions with reasons, and add a heartbeat so silence is trustworthy. Never let the
monitor fail silently.
</details>

<details>
<summary>4. What credentials should a scheduled drift job have?</summary>

Short-lived credentials via OIDC (no stored keys), a role with read-only access to the resources
and read access to the state, trusted only for that workflow on the protected branch. It must
not be able to apply. Plan with `-lock=false` so it never blocks real deployments.
</details>

<details>
<summary>5. How would you test a drift detection pipeline without a cloud account?</summary>

Use an emulator (Moto server) with the provider's custom endpoints and the S3 backend pointed at
it; apply the stack, make out-of-band changes with the AWS CLI, run the real detection scripts,
and capture the webhook payloads with a local HTTP sink. Keep the produced plans as fixtures for
fast unit tests. Know the emulator's fidelity gaps (here, two perpetual diffs).
</details>

## 9. ✅ Self-check quiz

1. Which plan JSON section lists changes made outside Terraform?
2. A merged PR raised a timeout but nobody applied it. What status does this tool report?
3. Why does the clean plan's `resource_drift` contain entries, yet the report says "clean"?
4. The state bucket is deleted. What does the channel see, and what is the job's result?
5. Why does Monday's run post even when everything is clean?

<details>
<summary>Answers</summary>

1. `resource_drift`.
2. `pending`: apply would change something that didn't drift.
3. Those differences (legacy attributes managed by companion resources, `null` vs `{}`) aren't
   changed back by the plan: reality already matches the code.
4. `🚨 drift check FAILED` with the error and "drift in this stack is NOT being detected"; the
   notify job exits 1 (red run).
5. It's the heartbeat: it proves the pipeline works, so silence on other days means "clean".
</details>

## 10. Further reading

- Terraform: [JSON output format](https://developer.hashicorp.com/terraform/internals/json-format)
  (`resource_drift`, `resource_changes`), [`plan -detailed-exitcode` and `-refresh-only`](https://developer.hashicorp.com/terraform/cli/commands/plan).
- GitHub Actions: [OIDC with AWS](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services),
  [scheduled events](https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#schedule).
- Slack: [incoming webhooks](https://api.slack.com/messaging/webhooks), [Block Kit](https://api.slack.com/block-kit).
- Microsoft Teams: [Workflows webhooks](https://support.microsoft.com/office/create-incoming-webhooks-with-workflows-for-microsoft-teams-8ae491c7-0394-4861-ba59-055e33f75498),
  [Adaptive Cards](https://adaptivecards.io/).
- Infracost: [CLI and JSON output](https://www.infracost.io/docs/features/cli_commands/).
- Moto: [server mode](https://docs.getmoto.org/en/latest/docs/server_mode.html).
- Course: #8 (policy checks on the plan), #18 (detect public buckets after deployment), #4
  (the cross-account CI/CD role).
