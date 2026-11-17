# Failure drills

Break things on purpose against a local AWS emulator (Moto): nothing touches a real account.

**Setup:**

```bash
make lab-shell        # starts Moto and opens a shell with tests/e2e/lab.sh loaded
lab_apply             # terraform apply the stack (state lives in Moto's S3)
lab_check             # expect: ✅ app-storage/dev: clean
```

`lab_check` runs the same `scripts/detect-drift.sh` + `driftwatch` as the scheduled workflow
and prints the message. `aws_` is the AWS CLI pointed at Moto: your "console".
`make lab-down` deletes everything.

## Drill 1: Fix it in the console

**Scenario.** It's 2 a.m., an upload job fails, and someone suspends versioning on the bucket
"just for tonight":

```bash
aws_ s3api put-bucket-versioning --bucket code-clinic-dev-uploads --versioning-configuration Status=Suspended
lab_check
terraform -chdir=stacks/app-storage plan -lock=false | grep -E "will be updated|Plan:"
```

Then decide, and do one of: `terraform -chdir=stacks/app-storage apply -auto-approve` (Git
wins) or change `versioning_configuration` in `main.tf` (reality wins). Run `lab_check` again.

**What to observe.** `🔶 drift detected`:
`aws_s3_bucket_versioning.uploads: versioning_configuration [{"mfa_delete":"Disabled","status":"Enabled"}]
→ [{"mfa_delete":"Disabled","status":"Suspended"}]`. The plan lists **three** resources "will be
updated in-place", yet the report names only one. After `apply`, the check is clean again.

**Questions**

1. Why does the plan show three updates when only one thing was changed?
2. What would the naive version (`terraform plan` output pasted into Slack) have told the
   on-call engineer?
3. Who should decide between "apply" and "change the code"? What information do they need?

<details>
<summary>Explanation and fix</summary>

The other two updates are the emulator's perpetual diffs (bucket `tags_all`, the queue's
`redrive_policy` whitespace), recorded as accepted differences in `tests/e2e/drift.toml`. The
report shows only what someone changed outside Terraform **and** Terraform would change back
([ADR 0001](adr/0001-drift-is-what-apply-would-undo.md)), with the before and after values.

The naive version posts the whole plan text: three updates, no indication of which is drift and
which is noise, and every attribute of every resource, sensitive ones included.

Whoever owns the bucket decides. If the console change was a real need, it goes into the code
(a reviewed PR); otherwise `apply` restores the reviewed configuration. Leaving the drift in
place means the next unrelated apply silently reverts the 2 a.m. fix.
</details>

## Drill 2: Forget the accepted differences

**Scenario.** Run the check without `tests/e2e/drift.toml`, as a team would on day one:

```bash
touch /tmp/empty.toml
DRIFT_CONFIG=/tmp/empty.toml lab_check
terraform -chdir=stacks/app-storage plan -lock=false | grep -B2 -A2 redrive_policy
```

**What to observe.** On a stack nobody touched: `🕓 unapplied changes: aws_sqs_queue.jobs
(update)`. The plan shows `~ redrive_policy = jsonencode( # whitespace changes`.

**Questions**

1. Is this drift? Is it a pending code change? What is it really?
2. Why is it reported as "pending" and not "drift"?
3. What are the two ways to make it go away, and which one is better in the long run?
4. Why must every entry in `drift.toml` have a reason?

<details>
<summary>Explanation and fix</summary>

It's a **perpetual diff**: the emulator returns the redrive policy JSON with spaces, the
provider compares strings, and every plan wants to "fix" it. Real providers have these too
(JSON policies, default values, tags added by an external tool).

It isn't drift because `resource_drift` doesn't record the attribute as changed outside
Terraform; it's only in the plan's changes, so it lands in "pending".

Fix options: (1) record it in `drift.toml` with a reason (what we did for Moto), or (2) remove
the cause: normalise the value in code, use `lifecycle { ignore_changes = [...] }`, or upgrade
the provider. Prefer (2) when you control the cause: an ignore is a blind spot, and the reason
field is what lets a reviewer judge whether the blind spot is still acceptable.
</details>

## Drill 3: The check itself breaks

**Scenario.** The state bucket is renamed (or the drift role loses access):

```bash
sed -i 's/tfstate-lab/tfstate-gone/' .lab/backend.hcl
rm -rf stacks/app-storage/.terraform
scripts/detect-drift.sh stacks/app-storage app-storage/dev .lab/out
uv run driftwatch notify .lab/out/app-storage-dev.json --slack-webhook-env SLACK_URL --dry-run; echo "exit=$?"
```

**What to observe.** `🚨 Terraform drift: drift check FAILED`, the end of Terraform's error
(`NoSuchBucket: The specified bucket does not exist`), the line *"Until this is fixed, drift in
this stack is NOT being detected."*, and `exit=1`.

**Questions**

1. With the naive workflow (`terraform plan ... || true`), what would Slack have shown?
2. Why is "the check failed" ranked worse than "drift detected"?
3. Why does `detect-drift.sh` exit 0 here while `notify` exits 1?

<details>
<summary>Explanation and fix</summary>

The naive version swallows the error (`|| true`) and posts the error text as if it were a plan,
or posts an empty "drift report". Either way, nobody learns that detection stopped: the most
dangerous state for a monitoring system is "silently not monitoring".

An error means *unknown*: there could be any amount of drift. So it's the worst status, it is
always posted, and the notify job fails (`--fail-on error`, the default) so GitHub also flags
the run. `detect-drift.sh` exits 0 on purpose: its job is to always produce a report, so the
notify job (which runs `if: always()`) can tell everyone. Restore the bucket name, `lab_check`
is clean again.
</details>
