#!/usr/bin/env bats
# scripts/detect-drift.sh with stub `terraform`, `infracost` and `driftwatch` commands:
# every exit-code path must end in a report, and the plan files must not be left behind.

setup() {
  repo="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  stub="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$stub" "$BATS_TEST_TMPDIR/run"
  cd "$BATS_TEST_TMPDIR/run" || exit 1

  # terraform stub: STUB_INIT_RC / STUB_PLAN_RC decide the outcome; `show -json` prints a plan.
  cat > "$stub/terraform" <<'EOF'
#!/usr/bin/env bash
cmd="$2"
case "$cmd" in
  init) echo "init log line"; [ "${STUB_INIT_RC:-0}" = 0 ] || { echo "Error: AccessDenied: token expired"; exit "$STUB_INIT_RC"; } ;;
  plan) for a in "$@"; do case "$a" in -out=*) echo plan > "${a#-out=}";; esac; done
        echo "plan log"; [ "${STUB_PLAN_RC:-0}" = 1 ] && echo "Error: provider crashed"; exit "${STUB_PLAN_RC:-0}" ;;
  show) echo '{"format_version":"1.2"}' ;;
esac
EOF
  # driftwatch stub: records its arguments and where the plan JSON was.
  cat > "$stub/driftwatch" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$BATS_TEST_TMPDIR/args"
for ((i = 1; i <= $#; i++)); do [ "${!i}" = --plan ] && { j=$((i + 1)); echo "${!j}" > "$BATS_TEST_TMPDIR/planpath"; }; done
exit 0
EOF
  cat > "$stub/infracost" <<'EOF'
#!/usr/bin/env bash
[ "${STUB_INFRACOST_RC:-0}" = 0 ] || exit 1
for ((i = 1; i <= $#; i++)); do [ "${!i}" = --out-file ] && { j=$((i + 1)); echo '{}' > "${!j}"; }; done
exit 0
EOF
  chmod +x "$stub"/*
  export PATH="$stub:$PATH" DRIFTWATCH="$stub/driftwatch"
  unset INFRACOST_API_KEY DRIFT_CONFIG BACKEND_CONFIG
}

run_detect() { run "$repo/scripts/detect-drift.sh" stacks/x app/prod out; }
arg() { grep -qxF -- "$1" "$BATS_TEST_TMPDIR/args"; }

@test "plan exit 0 (no changes): report from the plan JSON" {
  run_detect
  [ "$status" -eq 0 ]
  arg --plan
  arg --stack && arg app/prod && arg out/app-prod.json
}

@test "plan exit 2 (changes): report from the plan JSON, plan files deleted afterwards" {
  STUB_PLAN_RC=2 run_detect
  [ "$status" -eq 0 ]
  arg --plan
  [ ! -e "$(cat "$BATS_TEST_TMPDIR/planpath")" ]   # the temp dir with plan.json is gone
}

@test "plan exit 1: an error report with the end of the log, never 'clean'" {
  STUB_PLAN_RC=1 run_detect
  [ "$status" -eq 0 ]
  arg --error
  grep -q "terraform plan failed (exit 1):.*provider crashed" "$BATS_TEST_TMPDIR/args"
  ! arg --plan
}

@test "init fails (expired credentials): an error report" {
  STUB_INIT_RC=1 run_detect
  [ "$status" -eq 0 ]
  grep -q "terraform init failed:.*AccessDenied" "$BATS_TEST_TMPDIR/args"
}

@test "infracost runs only with an API key, and its failure doesn't lose the report" {
  run_detect
  ! arg --infracost
  INFRACOST_API_KEY=k run_detect
  arg --infracost
  INFRACOST_API_KEY=k STUB_INFRACOST_RC=1 run_detect
  [ "$status" -eq 0 ]
  ! arg --infracost
  [[ "$output" == *"infracost failed; reporting without cost"* ]]
}

@test "drift.toml in the working directory is used; DRIFT_CONFIG overrides it" {
  touch drift.toml
  run_detect
  arg --config && arg drift.toml
  DRIFT_CONFIG=/elsewhere/drift.toml run_detect
  arg /elsewhere/drift.toml
}
