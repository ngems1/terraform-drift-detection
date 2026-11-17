"""Render stack reports for people: Slack Block Kit, Teams Adaptive Card, Markdown, text.

A drift message has one job: tell the reader in ten seconds whether to act, and what to look
at. So: a headline with the worst status, one section per stack that needs attention, the
attributes that changed (values shortened, sensitive values masked), and a link to the run.
Clean stacks are a single line. Every chat platform has size limits, so lists are capped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from driftwatch.plan import StackReport

MAX_STACKS = 15  # Slack allows 50 blocks per message
MAX_RESOURCES = 8
ORDER = {"error": 0, "drift": 1, "pending": 2, "clean": 3}
ICON = {"error": "🚨", "drift": "🔶", "pending": "🕓", "clean": "✅"}
COUNTED = {"error": "failed", "drift": "drifted", "pending": "with unapplied changes"}
TITLE = {
    "error": "drift check FAILED",
    "drift": "drift detected",
    "pending": "unapplied changes",
    "clean": "no drift",
}


@dataclass(frozen=True)
class Context:
    title: str = "Terraform drift"
    run_url: str = ""


def worst(reports: list[StackReport]) -> str:
    return min((r.status for r in reports), key=lambda s: ORDER.get(s, 0), default="clean")


def headline(reports: list[StackReport], ctx: Context) -> str:
    status = worst(reports)
    counts = {s: sum(1 for r in reports if r.status == s) for s in ORDER}
    parts = [f"{counts[s]} {COUNTED[s]}" for s in ("error", "drift", "pending") if counts[s]]
    detail = ", ".join(parts) if parts else f"{len(reports)} stack(s) checked"
    return f"{ICON[status]} {ctx.title}: {TITLE[status]} ({detail})"


def stack_lines(r: StackReport) -> list[str]:
    """Plain lines describing one stack (shared by every format)."""
    lines: list[str] = []
    if r.status == "error":
        lines.append(f"The plan could not run: {r.error}")
        lines.append("Until this is fixed, drift in this stack is NOT being detected.")
        return lines
    for d in r.drift[:MAX_RESOURCES]:
        if d.action == "delete":
            lines.append(f"{d.address}: deleted outside Terraform")
            continue
        changes = ", ".join(f"{a.name} {a.before} → {a.after}" for a in d.attributes[:4])
        more = f" (+{len(d.attributes) - 4} more)" if len(d.attributes) > 4 else ""
        lines.append(f"{d.address}: {changes or d.action}{more}")
    if len(r.drift) > MAX_RESOURCES:
        lines.append(f"… and {len(r.drift) - MAX_RESOURCES} more drifted resource(s)")
    code = [p for p in r.pending if not p.reverts_drift]
    if code:
        shown = ", ".join(f"{p.address} ({p.action})" for p in code[:MAX_RESOURCES])
        extra = f" and {len(code) - MAX_RESOURCES} more" if len(code) > MAX_RESOURCES else ""
        lines.append(f"Unapplied code changes: {shown}{extra}")
    if r.monthly_cost_delta is not None and r.monthly_cost_delta not in ("+0.00", "-0.00"):
        lines.append(f"terraform apply would change the monthly cost by {r.monthly_cost_delta} {r.currency}")
    if r.ignored:
        lines.append(f"{len(r.ignored)} accepted difference(s) ignored (drift.toml)")
    return lines


def sorted_reports(reports: list[StackReport]) -> list[StackReport]:
    return sorted(reports, key=lambda r: (ORDER.get(r.status, 0), r.stack))


def text(reports: list[StackReport], ctx: Context) -> str:
    out = [headline(reports, ctx)]
    for r in sorted_reports(reports):
        out.append(f"{ICON.get(r.status, '?')} {r.stack}: {r.status}")
        out.extend(f"    {line}" for line in stack_lines(r))
    if ctx.run_url:
        out.append(f"run: {ctx.run_url}")
    return "\n".join(out) + "\n"


def markdown(reports: list[StackReport], ctx: Context) -> str:
    out = [f"### {headline(reports, ctx)}", ""]
    for r in sorted_reports(reports):
        out.append(f"**{ICON.get(r.status, '?')} `{r.stack}`: {r.status}**")
        out.extend(f"- {_md(line)}" for line in stack_lines(r))
        out.append("")
    if ctx.run_url:
        out.append(f"[Workflow run]({ctx.run_url})")
    return "\n".join(out) + "\n"


def slack(reports: list[StackReport], ctx: Context) -> dict[str, Any]:
    """Slack incoming-webhook payload (Block Kit). `text` is the notification fallback."""
    title = headline(reports, ctx)
    blocks: list[dict[str, Any]] = [{"type": "header", "text": {"type": "plain_text", "text": title[:150]}}]
    attention = [r for r in sorted_reports(reports) if r.status != "clean"]
    for r in attention[:MAX_STACKS]:
        body = "\n".join(f"• {_slack_escape(line)}" for line in stack_lines(r))
        section = f"*{ICON.get(r.status, '?')} {_slack_escape(r.stack)}* ({r.status})\n{body}"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": section[:2900]}})
    if len(attention) > MAX_STACKS:
        blocks.append(_slack_context(f"… and {len(attention) - MAX_STACKS} more stack(s): see the run"))
    clean = [r.stack for r in reports if r.status == "clean"]
    if clean:
        blocks.append(_slack_context(f"✅ clean: {', '.join(clean)}"[:2900]))
    if ctx.run_url:
        blocks.append(_slack_context(f"<{ctx.run_url}|Workflow run>"))
    return {"text": title, "blocks": blocks}


def teams(reports: list[StackReport], ctx: Context) -> dict[str, Any]:
    """Microsoft Teams Workflows webhook payload: a message with one Adaptive Card."""
    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "wrap": True, "text": headline(reports, ctx)}
    ]
    attention = [r for r in sorted_reports(reports) if r.status != "clean"]
    for r in attention[:MAX_STACKS]:
        body.append(
            {
                "type": "TextBlock",
                "weight": "Bolder",
                "wrap": True,
                "spacing": "Medium",
                "color": "Attention" if r.status in ("error", "drift") else "Default",
                "text": f"{ICON.get(r.status, '?')} {r.stack} ({r.status})",
            }
        )
        body.extend(
            {"type": "TextBlock", "wrap": True, "spacing": "None", "text": f"- {line}"} for line in stack_lines(r)
        )
    clean = [r.stack for r in reports if r.status == "clean"]
    if clean:
        body.append({"type": "TextBlock", "wrap": True, "isSubtle": True, "text": f"✅ clean: {', '.join(clean)}"})
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": body,
    }
    if ctx.run_url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Workflow run", "url": ctx.run_url}]
    return {
        "type": "message",
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}],
    }


def _slack_context(text_: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text_}]}


def _slack_escape(s: str) -> str:
    # Slack mrkdwn: these three must be escaped, or "<x>" becomes a link/mention.
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md(s: str) -> str:
    return s.replace("|", "\\|")
