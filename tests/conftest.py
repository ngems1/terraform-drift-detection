"""Shared helpers: the real plans from the e2e test, and a builder for synthetic plans."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from driftwatch.plan import Ignore

ROOT = Path(__file__).resolve().parent.parent
PLANS = ROOT / "tests" / "fixtures" / "plans"
# The two Moto quirks recorded in tests/e2e/drift.toml.
MOTO_IGNORES = [
    Ignore("aws_s3_bucket.uploads", ("tags_all",), "moto"),
    Ignore("aws_sqs_queue.jobs", ("redrive_policy",), "moto"),
]


def real_plan(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((PLANS / f"{name}.json").read_text(encoding="utf-8"))
    return data


def entry(
    address: str, before: dict[str, Any] | None, after: dict[str, Any] | None, actions: list[str], **change: Any
) -> dict[str, Any]:
    return {
        "address": address,
        "mode": "managed",
        "type": address.split(".")[0],
        "change": {"actions": actions, "before": before, "after": after, **change},
    }


def plan(drift: list[dict[str, Any]] | None = None, changes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "format_version": "1.2",
        "terraform_version": "1.17.2",
        "resource_drift": drift or [],
        "resource_changes": changes or [],
    }
