"""driftwatch: one command per pipeline stage.

    driftwatch report --stack app-storage/prod --plan plan.json [--infracost cost.json] \\
                      [--config drift.toml] --out out/app-storage-prod.json
    driftwatch report --stack app-storage/prod --error "terraform plan failed (exit 1)" --out ...
    driftwatch notify out/*.json [--slack-webhook-env SLACK_WEBHOOK_URL] \\
                      [--teams-webhook-env TEAMS_WEBHOOK_URL] [--run-url URL] [--heartbeat]

Exit codes
    report  0 report written (even for a broken plan: it becomes an "error" report) · 3 cannot write
    notify  0 delivered or nothing to say · 1 a --fail-on condition was met · 3 delivery or input error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TextIO

from driftwatch import __version__, config, cost, notify, plan, render

EXIT_OK, EXIT_FAIL_ON, EXIT_ERROR = 0, 1, 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="driftwatch", description="Terraform drift reports for Slack and Teams.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("report", help="turn one stack's plan JSON into a report file")
    r.add_argument("--stack", required=True, help="stack name shown in messages, e.g. app-storage/prod")
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--plan", type=Path, help="terraform show -json output")
    src.add_argument("--error", help="the plan could not run: this message goes into an error report")
    r.add_argument("--infracost", type=Path, help="infracost breakdown --format json of the same plan")
    r.add_argument("--config", type=Path, help="drift.toml with accepted differences")
    r.add_argument("--out", type=Path, required=True, help="report file to write (JSON)")

    n = sub.add_parser("notify", help="combine reports and post one message")
    n.add_argument("reports", nargs="+", type=Path)
    n.add_argument("--slack-webhook-env", metavar="VAR", help="environment variable holding a Slack webhook URL")
    n.add_argument("--teams-webhook-env", metavar="VAR", help="environment variable holding a Teams webhook URL")
    n.add_argument("--run-url", default="", help="link to the workflow run")
    n.add_argument("--title", default="Terraform drift")
    n.add_argument("--heartbeat", action="store_true", help="post even when everything is clean")
    n.add_argument(
        "--fail-on",
        choices=["error", "drift", "pending", "never"],
        default="error",
        help="exit 1 when a stack is at least this bad (default: error)",
    )
    n.add_argument("--markdown", type=Path, help="also write a Markdown summary here (e.g. $GITHUB_STEP_SUMMARY)")
    n.add_argument("--dry-run", action="store_true", help="print the payloads instead of posting them")
    return p


def cmd_report(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    if args.error is not None:
        report = plan.error_report(args.stack, args.error)
    else:
        try:
            ignores = config.load(args.config)
            report = plan.analyse(args.stack, plan.load(args.plan), ignores)
            if args.infracost:
                report.monthly_cost_delta, report.currency = cost.monthly_delta(args.infracost)
        except plan.PlanError as exc:
            # A broken input must never look like "no drift": it becomes an error report.
            err.write(f"driftwatch: {exc}\n")
            report = plan.error_report(args.stack, str(exc))
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        err.write(f"driftwatch: cannot write {args.out}: {exc.strerror}\n")
        return EXIT_ERROR
    out.write(render.text([report], render.Context()))
    return EXIT_OK


def cmd_notify(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    try:
        reports = [plan.StackReport.from_dict(json.loads(p.read_text(encoding="utf-8"))) for p in args.reports]
    except (OSError, json.JSONDecodeError, KeyError, plan.PlanError) as exc:
        err.write(f"driftwatch: cannot read reports: {exc}\n")
        return EXIT_ERROR

    ctx = render.Context(title=args.title, run_url=args.run_url)
    out.write(render.text(reports, ctx))
    if args.markdown:
        with args.markdown.open("a", encoding="utf-8") as fh:
            fh.write(render.markdown(reports, ctx))

    status = render.worst(reports)
    code = EXIT_OK
    if args.fail_on != "never" and render.ORDER[status] <= render.ORDER[args.fail_on]:
        code = EXIT_FAIL_ON

    if status == "clean" and not args.heartbeat:
        out.write("driftwatch: everything is clean, no message sent\n")
        return code

    channels = []
    for name, var, build in (
        ("slack", args.slack_webhook_env, render.slack),
        ("teams", args.teams_webhook_env, render.teams),
    ):
        if var:
            channels.append((name, var, build(reports, ctx)))
    if not channels:
        err.write("driftwatch: no channel configured (--slack-webhook-env / --teams-webhook-env)\n")
        return EXIT_ERROR

    for name, var, payload in channels:
        if args.dry_run:
            out.write(f"--- {name} payload (dry run)\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n")
            continue
        url = os.environ.get(var, "")
        if not url:
            err.write(f"driftwatch: {name}: environment variable {var} is empty\n")
            return EXIT_ERROR
        try:
            status_code = notify.post(url, payload, channel=name)
        except notify.DeliveryError as exc:
            err.write(f"driftwatch: {exc}\n")
            return EXIT_ERROR
        out.write(f"driftwatch: {name}: delivered (HTTP {status_code})\n")
    return code


def main(argv: list[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out, err = stdout or sys.stdout, stderr or sys.stderr
    args = build_parser().parse_args(argv)
    if args.command == "report":
        return cmd_report(args, out, err)
    return cmd_notify(args, out, err)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
