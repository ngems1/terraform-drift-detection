"""The monthly cost impact of the plan, from `infracost breakdown --path plan.json --format json`.

Infracost prices both sides of the plan: `pastTotalMonthlyCost` (the infrastructure as it is
now, drift included) and `totalMonthlyCost` (after `terraform apply`). The difference tells
you what drift costs: a console-resized instance shows up as "applying the code would save
$X a month". Amounts are strings; we keep them as Decimal and print two decimals.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from driftwatch.plan import PlanError


def monthly_delta(path: Path) -> tuple[str | None, str]:
    """(signed delta with 2 decimals or None if unknown, currency)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PlanError(f"cannot read Infracost file {path}: {exc.strerror}") from exc
    except json.JSONDecodeError as exc:
        raise PlanError(f"{path} is not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or "projects" not in data:
        raise PlanError(f"{path}: not Infracost JSON (no `projects`)")
    currency = str(data.get("currency", "USD"))
    total, past = data.get("totalMonthlyCost"), data.get("pastTotalMonthlyCost")
    if total in (None, "") or past in (None, ""):
        return None, currency
    try:
        delta = Decimal(str(total)) - Decimal(str(past))
    except InvalidOperation as exc:
        raise PlanError(f"{path}: invalid amount") from exc
    return f"{delta.quantize(Decimal('0.01')):+}", currency
