"""Deliver a payload to a chat webhook, with retries, and without ever printing the URL.

A webhook URL IS the credential (anyone who has it can post to your channel), so it comes
from an environment variable and never appears in logs or error messages.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

RETRY_STATUSES = {429, 500, 502, 503, 504}


class DeliveryError(Exception):
    """The message could not be delivered (exit code 3)."""


def post(
    url: str,
    payload: dict[str, Any],
    *,
    channel: str,
    attempts: int = 4,
    timeout: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """POST JSON; retry on 429/5xx and network errors with backoff. Returns the HTTP status."""
    if not url.startswith("https://") and not url.startswith("http://localhost") and not url.startswith("http://127."):
        # Plain http would send the message (and the URL's secret path) in clear text.
        raise DeliveryError(f"{channel}: webhook URL must use https")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last = ""
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(  # noqa: S310 (scheme checked above)
            url, data=data, method="POST", headers={"Content-Type": "application/json", "User-Agent": "driftwatch"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (scheme checked above)
                return int(response.status)
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code not in RETRY_STATUSES:
                raise DeliveryError(f"{channel}: webhook rejected the message ({last})") from None
            delay = _retry_after(exc.headers.get("Retry-After"), attempt)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            last = f"network error ({type(reason).__name__})"
            delay = float(2 ** (attempt - 1))
        if attempt < attempts:
            sleep(delay)
    raise DeliveryError(f"{channel}: delivery failed after {attempts} attempts ({last})")


def _retry_after(header: str | None, attempt: int) -> float:
    if header:
        try:
            return min(float(header), 60.0)
        except ValueError:
            pass
    return float(2 ** (attempt - 1))
