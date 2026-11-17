# 0002. One message per run, only when something needs attention, plus a weekly heartbeat

## Status

Accepted · 2026-11-17

## Context

The pipeline checks every stack in every environment every weekday. How often it speaks
decides whether people listen. Two failure modes are common: a channel flooded with "no
changes" messages that nobody reads, and a silent channel that nobody notices has been silent
because the job broke three weeks ago.

## Options considered

### Option A: one message per stack, every run

- ✅ Simple.
- ❌ 2 stacks × 2 environments × 5 days = 20 messages a week that say nothing.

### Option B: one aggregated message, only when a stack is not clean

- ✅ Silence means "clean"; a message always deserves a look.
- ❌ Silence also means "the workflow was disabled", "the schedule broke", "the webhook was
  rotated": indistinguishable from clean.

### Option C: B, plus a heartbeat every Monday even when clean, and errors always posted

- ✅ Silence on Tuesday-Friday means clean **because** Monday proved the pipeline works.
- ✅ A stack whose plan fails (expired role, deleted state bucket) is an **error** report, the
  worst status, with "drift in this stack is NOT being detected".
- ❌ One "all clean" message a week.

### Option D: post only when the drift set changes since the last run

- ✅ No repeats for drift nobody fixes.
- ❌ Needs state between runs (a cache or an issue); unfixed drift becomes invisible, which is
  the opposite of what we want.

## Decision

**Option C.** The workflow aggregates all reports into one message, posts it when any stack is
`error`, `drift` or `pending`, and always on Mondays (`--heartbeat`). The job's own exit code
is red only for `error` by default (`--fail-on error`): drift is a finding, not a pipeline
failure.

## Consequences

- **Good:** the team knows the check is alive, and every message is actionable.
- **Bad:** unfixed drift is repeated every weekday. That's deliberate pressure; if drift is
  accepted, it belongs in `drift.toml` with a reason.
- **Follow-up:** a team that prefers tickets can open/update one GitHub issue per stack from
  the same JSON reports (LEARN exercise 3).
