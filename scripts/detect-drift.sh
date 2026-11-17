#!/usr/bin/env bash
# Plan ONE stack and write its drift report. Used by .github/workflows/drift.yml and by the
# local e2e test, so both exercise exactly the same steps.
#
#   scripts/detect-drift.sh <stack-dir> <stack-name> <out-dir>
#
# Environment:
#   BACKEND_CONFIG     path to a backend .hcl file for `terraform init` (optional)
#   TF_VAR_*           Terraform variables (e.g. TF_VAR_environment=prod)
#   DRIFT_CONFIG       drift.toml with accepted differences (default: ./drift.toml if present)
#   INFRACOST_API_KEY  if set, the cost impact is added to the report
#   DRIFTWATCH         command to run driftwatch (default: driftwatch)
#
# It always writes <out-dir>/<stack>.json and exits 0 when it did: a plan that fails becomes
# an "error" report, so a broken check can never look like "no drift". The plan files contain
# every attribute in clear text (secrets too): they stay in a temp dir and are deleted.
set -uo pipefail

dir="$1" name="$2" out="$3"
safe="${name//\//-}"
mkdir -p "$out"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
read -r -a driftwatch <<< "${DRIFTWATCH:-driftwatch}"
config=()
if [ -n "${DRIFT_CONFIG:-}" ]; then config=(--config "$DRIFT_CONFIG")
elif [ -f drift.toml ]; then config=(--config drift.toml); fi

report() { "${driftwatch[@]}" report --stack "$name" "${config[@]}" --out "$out/$safe.json" "$@"; }
# The last lines of a failed command, on one line, for the chat message.
tail_of() { tail -n 3 "$1" | tr -s '\n' ' ' | cut -c1-300; }

init_args=(-input=false -no-color)
[ -n "${BACKEND_CONFIG:-}" ] && init_args+=("-backend-config=$BACKEND_CONFIG")
if ! terraform -chdir="$dir" init "${init_args[@]}" > "$work/init.log" 2>&1; then
  report --error "terraform init failed: $(tail_of "$work/init.log")"
  exit 0
fi

# -lock=false: a read-only check must never block (or be blocked by) a real apply.
# -detailed-exitcode: 0 = no changes, 2 = changes, anything else = the plan failed.
terraform -chdir="$dir" plan -input=false -no-color -lock=false -detailed-exitcode \
  -out="$work/tfplan" > "$work/plan.log" 2>&1
code=$?
if [ "$code" -ne 0 ] && [ "$code" -ne 2 ]; then
  report --error "terraform plan failed (exit $code): $(tail_of "$work/plan.log")"
  exit 0
fi
terraform -chdir="$dir" show -json "$work/tfplan" > "$work/plan.json"

cost=()
if [ -n "${INFRACOST_API_KEY:-}" ] && command -v infracost > /dev/null; then
  if infracost breakdown --path "$work/plan.json" --format json --out-file "$work/cost.json" > /dev/null 2>&1; then
    cost=(--infracost "$work/cost.json")
  else
    echo "detect-drift: infracost failed; reporting without cost" >&2
  fi
fi

report --plan "$work/plan.json" "${cost[@]}"
if [ -n "${KEEP_PLAN_JSON:-}" ]; then cp "$work/plan.json" "$KEEP_PLAN_JSON"; fi  # tests only
