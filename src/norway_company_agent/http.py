from __future__ import annotations

import json
import hashlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .telemetry import classify_request, record_request


@dataclass
class FetchResult:
    url: str
    status: int
    elapsed_ms: int
    bytes_received: int
    body: Any = None
    error: str | None = None
    content_sha256: str | None = None
    retrieved_at: str | None = None
    effective_at: str | None = None
    failure_class: str = "success"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_json(
    url: str,
    *,
    timeout: float = 20.0,
    attempts: int = 3,
    module: str = "http",
    organisation_number: str | None = None,
) -> FetchResult:
    last_error = "request failed"
    for attempt in range(attempts):
        started = time.monotonic()
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "builderr-signalpost-poc/0.1 (+https://builderr.ai)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                elapsed = int((time.monotonic() - started) * 1000)
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    failure_class = classify_request(success=False, status=response.status, error="JSONDecodeError")
                    record_request(module=module, provider="BRREG" if "brreg.no" in url else "http", operation="GET", success=False, status=response.status, duration_ms=elapsed, organisation_number=organisation_number, error="JSONDecodeError", attempt=attempt + 1, retry=attempt > 0, retry_reason="JSONDecodeError" if attempt > 0 else None)
                    return FetchResult(url, response.status, elapsed, len(raw), error="JSONDecodeError", content_sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=_utc_now(), failure_class=failure_class)
                result = FetchResult(url, response.status, elapsed, len(raw), body, content_sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=_utc_now(), failure_class="success")
                record_request(module=module, provider="BRREG" if "brreg.no" in url else "http", operation="GET", success=True, status=response.status, duration_ms=elapsed, organisation_number=organisation_number, attempt=attempt + 1, retry=attempt > 0, retry_reason=last_error if attempt > 0 else None)
                return result
        except urllib.error.HTTPError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            raw = exc.read()
            record_request(module=module, provider="BRREG" if "brreg.no" in url else "http", operation="GET", success=False, status=exc.code, duration_ms=elapsed, organisation_number=organisation_number, error=f"HTTP {exc.code}", attempt=attempt + 1, retry=attempt > 0, retry_reason=f"HTTP {exc.code}" if attempt > 0 else None)
            if exc.code in {404, 410}:
                return FetchResult(url, exc.code, elapsed, len(raw), error=f"HTTP {exc.code}", content_sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=_utc_now(), failure_class=classify_request(success=False, status=exc.code, error=f"HTTP {exc.code}"))
            last_error = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError) as exc:
            record_request(module=module, provider="BRREG" if "brreg.no" in url else "http", operation="GET", success=False, status=0, duration_ms=int((time.monotonic() - started) * 1000), organisation_number=organisation_number, error=type(exc).__name__, attempt=attempt + 1, retry=attempt > 0, retry_reason=type(exc).__name__ if attempt > 0 else None)
            last_error = type(exc).__name__
        if attempt + 1 < attempts:
            time.sleep(0.4 * (2**attempt))
    return FetchResult(url, 0, 0, 0, error=last_error, retrieved_at=_utc_now(), failure_class=classify_request(success=False, status=0, error=last_error))
