#!/usr/bin/env bash
# End-to-end test against a local AWS emulator (Moto server): no account, no cost.
#
#   1. apply the app-storage stack                     → report "clean", nothing posted
#   2. --heartbeat                                     → one "no drift" message
#   3. change 3 things out of band with the AWS CLI    → "drift", Slack + Teams payloads checked
#   4. re-apply, then change the code without applying → "pending" (not drift)
#   5. break the backend                               → "error" report, notify exits 1
#
# Needs: terraform, aws CLI, uv, curl, and a Moto server at $AWS_ENDPOINT
# (default http://localhost:5000). `make e2e` starts Moto in Docker and runs this in the
# course image; CI runs it with Moto as a service container.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
EP="${AWS_ENDPOINT:-http://localhost:5000}"
state="${E2E_STATE:-$repo/.e2e}"
rm -rf "$state" && mkdir -p "$state/sink" "$state/out"
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=eu-west-1
export TF_IN_AUTOMATION=1 TF_VAR_environment=dev TF_VAR_aws_endpoint="$EP"
# Keep .terraform/ (the ~700 MB AWS provider) off the repository: on a bind mount (Docker on
# Windows/macOS) writing it there takes minutes per init. The cache shares one download.
export TF_DATA_DIR="${TF_DATA_DIR:-${TMPDIR:-/tmp}/driftwatch-tfdata}"
export TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-${TMPDIR:-/tmp}/driftwatch-plugins}"
mkdir -p "$TF_PLUGIN_CACHE_DIR"
export DRIFTWATCH="uv run --project $repo driftwatch" DRIFT_CONFIG="$here/drift.toml"
stack="$repo/stacks/app-storage"
aws_() { aws --endpoint-url "$EP" "$@"; }

pass() { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m  ✗ %s\033[0m\n' "$*"; exit 1; }
log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
field() { uv run --project "$repo" python -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))" "$@"; }

log "setup: Moto at $EP, state bucket, webhook sink"
for _ in $(seq 1 30); do curl -fsS -o /dev/null "$EP/moto-api/" && break; sleep 1; done
curl -fsS -o /dev/null "$EP/moto-api/" || fail "no Moto server at $EP"
curl -fsS -X POST -o /dev/null "$EP/moto-api/reset"   # start from an empty "account"
aws_ s3 mb s3://tfstate-e2e > /dev/null
cat > "$state/backend.hcl" <<EOF
bucket                      = "tfstate-e2e"
key                         = "app-storage/dev.tfstate"
region                      = "eu-west-1"
use_path_style              = true
skip_credentials_validation = true
skip_requesting_account_id  = true
skip_metadata_api_check     = true
skip_region_validation      = true
access_key                  = "test"
secret_key                  = "test"
endpoints                   = { s3 = "$EP" }
EOF
export BACKEND_CONFIG="$state/backend.hcl"
uv run --project "$repo" python "$here/webhook_sink.py" "$state/sink" 8099 &
sink_pid=$!
trap 'kill $sink_pid 2>/dev/null || true' EXIT
export SLACK_URL=http://127.0.0.1:8099/slack TEAMS_URL=http://127.0.0.1:8099/teams
notify() { uv run --project "$repo" driftwatch notify "$state/out/app-storage-dev.json" \
  --slack-webhook-env SLACK_URL --teams-webhook-env TEAMS_URL --run-url https://example.invalid/run/1 "$@"; }
sent() { find "$state/sink" -name '*.json' | wc -l; }
detect() { (cd "$repo" && KEEP_PLAN_JSON="${1:-}" scripts/detect-drift.sh "$stack" app-storage/dev "$state/out" > /dev/null); }
pass "ready"

log "1/5 apply, then check: clean, nothing posted"
rm -rf "$TF_DATA_DIR"
terraform -chdir="$stack" init -input=false -no-color -backend-config="$BACKEND_CONFIG" > /dev/null
terraform -chdir="$stack" apply -input=false -no-color -auto-approve > /dev/null
detect "$state/plan-clean.json"
[ "$(field "$state/out/app-storage-dev.json" 'd["status"]')" = clean ] || fail "expected clean"
notify > "$state/notify.log"
[ "$(sent)" = 0 ] || fail "a clean run must not post"
pass "clean, no message"

log "2/5 heartbeat"
notify --heartbeat > /dev/null
[ "$(sent)" = 2 ] || fail "expected 2 messages (Slack + Teams), got $(sent)"
grep -q 'no drift' "$state"/sink/01-slack.json || fail "heartbeat text"
pass "heartbeat posted to Slack and Teams"

log "3/5 out-of-band changes"
queue="$(terraform -chdir="$stack" output -raw queue_url)"
aws_ sqs set-queue-attributes --queue-url "$queue" --attributes VisibilityTimeout=300
aws_ s3api put-bucket-versioning --bucket code-clinic-dev-uploads --versioning-configuration Status=Suspended
aws_ s3api put-bucket-tagging --bucket code-clinic-dev-uploads \
  --tagging 'TagSet=[{Key=owner,Value=someone-in-the-console}]'
detect "$state/plan-drift.json"
r="$state/out/app-storage-dev.json"
[ "$(field "$r" 'd["status"]')" = drift ] || fail "expected drift"
[ "$(field "$r" 'sorted(x["address"] for x in d["drift"])')" = \
  "['aws_s3_bucket.uploads', 'aws_s3_bucket_versioning.uploads', 'aws_sqs_queue.jobs']" ] || fail "drifted resources"
pass "drift: 3 resources"
rm -f "$state"/sink/*.json
set +e; notify --fail-on drift > /dev/null; code=$?; set -e
[ "$code" = 1 ] || fail "--fail-on drift must exit 1, got $code"
grep -q 'visibility_timeout_seconds 60 → 300' "$state"/sink/*-slack.json || fail "Slack payload lacks the attribute change"
grep -q 'application/vnd.microsoft.card.adaptive' "$state"/sink/*-teams.json || fail "Teams payload is not an Adaptive Card"
grep -q 'aws_s3_bucket_versioning.uploads' "$state"/sink/*-teams.json || fail "Teams payload lacks a resource"
pass "Slack + Teams payloads name the 3 resources and the changed attributes"

log "4/5 re-apply, then an unapplied code change: pending, not drift"
terraform -chdir="$stack" apply -input=false -no-color -auto-approve > /dev/null
TF_VAR_job_timeout_seconds=90 detect "$state/plan-pending.json"
[ "$(field "$r" 'd["status"]')" = pending ] || fail "expected pending"
[ "$(field "$r" 'd["drift"]')" = "[]" ] || fail "an unapplied code change is not drift"
pass "pending: aws_sqs_queue.jobs, no drift"

log "5/5 the check itself breaks (wrong state bucket)"
sed 's/tfstate-e2e/does-not-exist/' "$BACKEND_CONFIG" > "$state/broken.hcl"
rm -rf "$TF_DATA_DIR"
BACKEND_CONFIG="$state/broken.hcl" detect
[ "$(field "$r" 'd["status"]')" = error ] || fail "expected an error report"
rm -f "$state"/sink/*.json
set +e; notify > /dev/null; code=$?; set -e
[ "$code" = 1 ] || fail "an error report must make notify exit 1 (default --fail-on error)"
grep -q 'drift check FAILED' "$state"/sink/*-slack.json || fail "error headline"
pass "error report posted and the job fails"
rm -rf "$TF_DATA_DIR"

log "e2e PASSED"
