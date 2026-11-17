#!/usr/bin/env bash
# Helpers for the drills (docs/DRILLS.md). `make lab-shell` starts Moto and opens a shell in the
# course image with this file sourced. Every command is a thin wrapper around what CI runs.
#
#   lab_apply              terraform apply the stack (state in Moto's S3)
#   lab_check [args...]    scripts/detect-drift.sh, then print the report as text
#   lab_slack              show the Slack message the drift workflow would post (dry run)
#   aws_ <aws cli args>    the AWS CLI pointed at Moto ("the console")
# shellcheck disable=SC2034  # variables are for the interactive shell

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EP="${AWS_ENDPOINT:-http://localhost:5000}"
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=eu-west-1
export TF_IN_AUTOMATION=1 TF_VAR_environment=dev TF_VAR_aws_endpoint="$EP"
export DRIFTWATCH="uv run --project $repo driftwatch" DRIFT_CONFIG="${DRIFT_CONFIG:-$repo/tests/e2e/drift.toml}"
export BACKEND_CONFIG="$repo/.lab/backend.hcl"
stack="$repo/stacks/app-storage"
mkdir -p "$repo/.lab"

aws_() { aws --endpoint-url "$EP" "$@"; }

lab_apply() {
  aws_ s3 mb s3://tfstate-lab > /dev/null 2>&1 || true
  cat > "$BACKEND_CONFIG" <<EOF
bucket = "tfstate-lab"
key = "app-storage/dev.tfstate"
region = "eu-west-1"
use_path_style = true
skip_credentials_validation = true
skip_requesting_account_id = true
skip_metadata_api_check = true
skip_region_validation = true
access_key = "test"
secret_key = "test"
endpoints = { s3 = "$EP" }
EOF
  # A .terraform/ from another machine (e.g. your host, with a plugin cache) can't be reused here.
  rm -rf "$stack/.terraform"
  terraform -chdir="$stack" init -input=false -no-color -reconfigure -backend-config="$BACKEND_CONFIG" > /dev/null &&
    terraform -chdir="$stack" apply -input=false -no-color -auto-approve | tail -n 4
}

lab_check() {
  (cd "$repo" && scripts/detect-drift.sh "$stack" app-storage/dev "$repo/.lab/out" "$@" > /dev/null) &&
    uv run --project "$repo" driftwatch notify "$repo/.lab/out/app-storage-dev.json" \
      --slack-webhook-env SLACK_URL --dry-run --fail-on never | sed '/^--- slack payload/,$d'
}

lab_slack() {
  uv run --project "$repo" driftwatch notify "$repo/.lab/out/app-storage-dev.json" \
    --slack-webhook-env SLACK_URL --dry-run --heartbeat --fail-on never | sed -n '/^--- slack payload/,$p'
}

echo "lab ready: Moto at $EP. Try: lab_apply, then lab_check"
