"""Read drift.toml: accepted differences ("ignores"), each with a mandatory reason.

    [[ignore]]
    address    = "aws_s3_bucket.*"
    attributes = ["tags"]            # omit to ignore the whole resource
    reason     = "the security team's tagging Lambda adds compliance tags"

Strict like every policy file in this course: unknown keys or a missing reason are errors,
so an ignore can't silently swallow more than intended.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from driftwatch.plan import Ignore, PlanError


def load(path: Path | None) -> list[Ignore]:
    if path is None:
        return []
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise PlanError(f"cannot read {path}: {exc.strerror}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PlanError(f"{path}: invalid TOML: {exc}") from exc
    return parse(data, str(path))


def parse(data: dict[str, Any], source: str = "drift.toml") -> list[Ignore]:
    unknown = set(data) - {"ignore"}
    if unknown:
        raise PlanError(f"{source}: unknown key(s): {', '.join(sorted(unknown))}")
    ignores = []
    for i, raw in enumerate(data.get("ignore", [])):
        where = f"{source}: [[ignore]] #{i + 1}"
        if not isinstance(raw, dict):
            raise PlanError(f"{where}: expected a table")
        extra = set(raw) - {"address", "attributes", "reason"}
        if extra:
            raise PlanError(f"{where}: unknown key(s): {', '.join(sorted(extra))}")
        if not str(raw.get("address", "")).strip():
            raise PlanError(f"{where}: `address` is required")
        if not str(raw.get("reason", "")).strip():
            raise PlanError(f"{where}: `reason` is required: say why this difference is acceptable")
        attributes = raw.get("attributes", [])
        if not isinstance(attributes, list) or not all(isinstance(a, str) for a in attributes):
            raise PlanError(f"{where}: `attributes` must be a list of strings")
        ignores.append(Ignore(str(raw["address"]), tuple(attributes), str(raw["reason"])))
    return ignores
