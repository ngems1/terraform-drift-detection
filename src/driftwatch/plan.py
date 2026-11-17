"""Turn `terraform show -json <planfile>` into a drift report for one stack.

A normal `terraform plan` refreshes the state first, then compares it with the code. Its JSON
answers two different questions, and the report keeps them apart:

* ``resource_drift``: what changed **outside Terraform** since the last apply (someone used
  the console, a script, another tool). This is drift.
* ``resource_changes``: what ``terraform apply`` would do now. Part of it reverts the drift;
  the rest is **pending code**: merged changes nobody applied yet.

"Somebody clicked in the console" and "we forgot to apply a PR" need different people and
different fixes, so a message that lumps them together gets ignored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

MAX_VALUE_CHARS = 60
# Attributes that change on every refresh or only identify the object.
NOISE = {"id", "arn", "last_modified", "etag"}


class PlanError(Exception):
    """The input is not a usable plan (exit code 3, never 'no drift')."""


@dataclass(frozen=True)
class AttrChange:
    name: str
    before: str  # display values: already shortened and masked
    after: str


@dataclass(frozen=True)
class ResourceDrift:
    address: str
    type: str
    action: str  # "update", "delete" (deleted outside Terraform), "replace", ...
    attributes: tuple[AttrChange, ...]


@dataclass(frozen=True)
class PendingChange:
    address: str
    action: str
    reverts_drift: bool  # True: applying would undo an out-of-band change


@dataclass(frozen=True)
class Ignore:
    """A known, accepted difference: e.g. tags an external tagging tool adds."""

    address: str  # glob
    attributes: tuple[str, ...]  # empty = the whole resource
    reason: str

    def covers(self, address: str, attribute: str | None = None) -> bool:
        if not fnmatchcase(address, self.address):
            return False
        return not self.attributes or attribute in self.attributes


@dataclass
class StackReport:
    stack: str
    status: str  # "clean" | "drift" | "pending" | "error"
    terraform_version: str = ""
    drift: list[ResourceDrift] = field(default_factory=list)
    pending: list[PendingChange] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    error: str = ""
    monthly_cost_delta: str | None = None  # applying the plan changes the bill by this (USD)
    currency: str = "USD"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "stack": self.stack,
            "status": self.status,
            "terraform_version": self.terraform_version,
            "drift": [
                {
                    "address": d.address,
                    "type": d.type,
                    "action": d.action,
                    "attributes": [{"name": a.name, "before": a.before, "after": a.after} for a in d.attributes],
                }
                for d in self.drift
            ],
            "pending": [
                {"address": p.address, "action": p.action, "reverts_drift": p.reverts_drift} for p in self.pending
            ],
            "ignored": self.ignored,
            "error": self.error,
            "monthly_cost_delta": self.monthly_cost_delta,
            "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StackReport:
        if data.get("schema_version") != 1:
            raise PlanError(f"unsupported report schema_version {data.get('schema_version')!r}")
        return cls(
            stack=str(data["stack"]),
            status=str(data["status"]),
            terraform_version=str(data.get("terraform_version", "")),
            drift=[
                ResourceDrift(
                    d["address"],
                    d["type"],
                    d["action"],
                    tuple(AttrChange(a["name"], a["before"], a["after"]) for a in d["attributes"]),
                )
                for d in data.get("drift", [])
            ],
            pending=[
                PendingChange(p["address"], p["action"], bool(p["reverts_drift"])) for p in data.get("pending", [])
            ],
            ignored=list(data.get("ignored", [])),
            error=str(data.get("error", "")),
            monthly_cost_delta=data.get("monthly_cost_delta"),
            currency=str(data.get("currency", "USD")),
        )


def load(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PlanError(f"cannot read plan file {path}: {exc.strerror}") from exc
    if raw.startswith(b"PK"):
        raise PlanError(f"{path} is a binary plan; run `terraform show -json {path.name} > plan.json`")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PlanError(f"{path} is not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or str(data.get("format_version", "")).split(".")[0] != "1":
        raise PlanError(f"{path}: not a Terraform plan with format_version 1.x")
    if data.get("errored"):
        raise PlanError(f"{path}: the plan errored")
    return data


def analyse(stack: str, plan: dict[str, Any], ignores: list[Ignore] | None = None) -> StackReport:
    ignores = ignores or []
    report = StackReport(stack=stack, status="clean", terraform_version=str(plan.get("terraform_version", "")))

    # 1. What would `terraform apply` change, attribute by attribute (minus accepted differences)?
    planned: dict[str, tuple[str, set[str]]] = {}
    for item in plan.get("resource_changes") or []:
        if item.get("mode", "managed") != "managed":
            continue
        change = item.get("change") or {}
        actions = change.get("actions") or []
        if actions in (["no-op"], ["read"], []):
            continue
        address = item["address"]
        if any(i.covers(address) and not i.attributes for i in ignores):
            report.ignored.append(address)
            continue
        changed = _changed_keys(change.get("before") or {}, change.get("after") or {})
        accepted = {a for a in changed if any(i.covers(address, a) for i in ignores)}
        report.ignored.extend(f"{address} {a}" for a in sorted(accepted))
        changed -= accepted
        if actions == ["update"] and not changed:
            continue  # only accepted differences: e.g. a provider's perpetual diff
        planned[address] = (_action(actions), changed)

    # 2. Drift that matters: changed outside Terraform AND Terraform would change it back.
    #    resource_drift also lists harmless differences (attributes that another resource
    #    manages, null vs {} normalisation); if the plan doesn't touch them, reality already
    #    matches the code, and reporting them would only teach people to ignore the alert.
    for item in plan.get("resource_drift") or []:
        if item.get("mode", "managed") != "managed":
            continue
        address = item["address"]
        change = item.get("change") or {}
        action = _action(change.get("actions") or [])
        if address not in planned:
            continue
        if action == "delete":  # deleted outside Terraform; the plan re-creates it
            report.drift.append(ResourceDrift(address, item.get("type", ""), action, ()))
            continue
        attrs = tuple(a for a in _attribute_changes(change) if a.name in planned[address][1])
        if attrs:
            report.drift.append(ResourceDrift(address, item.get("type", ""), action, attrs))

    drifted = {d.address for d in report.drift}
    report.pending = [PendingChange(a, act, a in drifted) for a, (act, _) in planned.items()]

    unexplained = [p for p in report.pending if not p.reverts_drift]
    if report.drift:
        report.status = "drift"
    elif unexplained:
        report.status = "pending"
    return report


def error_report(stack: str, message: str) -> StackReport:
    """A stack whose plan could not run: reported loudly, never as 'clean'."""
    return StackReport(stack=stack, status="error", error=message)


def _action(actions: list[str]) -> str:
    if "delete" in actions and "create" in actions:
        return "replace"
    return "/".join(actions) if actions else "unknown"


def _changed_keys(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    return {k for k in set(before) | set(after) if k not in NOISE and before.get(k) != after.get(k)}


def _attribute_changes(change: dict[str, Any]) -> tuple[AttrChange, ...]:
    before = change.get("before") or {}
    after = change.get("after") or {}
    sensitive = _sensitive_keys(change.get("before_sensitive")) | _sensitive_keys(change.get("after_sensitive"))
    names = sorted(_changed_keys(before, after))
    if "tags" in names and "tags_all" in names:
        names.remove("tags_all")  # tags_all mirrors tags (+ default_tags): one line is enough
    return tuple(
        AttrChange(
            name,
            "(sensitive)" if name in sensitive else _show(before.get(name)),
            "(sensitive)" if name in sensitive else _show(after.get(name)),
        )
        for name in names
    )


def _sensitive_keys(marker: Any) -> set[str]:
    if not isinstance(marker, dict):
        return set()
    return {k for k, v in marker.items() if v is True or (isinstance(v, dict | list) and _any_true(v))}


def _any_true(marker: Any) -> bool:
    if marker is True:
        return True
    if isinstance(marker, dict):
        return any(_any_true(v) for v in marker.values())
    if isinstance(marker, list):
        return any(_any_true(v) for v in marker)
    return False


def _show(value: Any) -> str:
    """A short, single-line rendering of a value for a chat message."""
    if value is None:
        return "(none)"
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"))
    text = text.replace("\n", " ")
    if len(text) > MAX_VALUE_CHARS:
        return f"{text[: MAX_VALUE_CHARS - 1]}… ({len(text)} chars)"
    return text
