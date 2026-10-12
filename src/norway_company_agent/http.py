from __future__ import annotations

import hashlib
import json
import socket
import ssl
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


_NETWORK_ERRORS = (
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionRefusedError,
    TimeoutError,
    socket.timeout,
    socket.gaierror,
    ssl.SSLError,
    ssl.CertificateError,
    OSError,
    urllib.error.URLError,
)


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
                try:
                    raw = response.read()
                except _NETWORK_ERRORS as exc:
                    elapsed = int((time.monotonic() - started) * 1000)
                    err_name = type(exc).__name__
                    record_request(module=module, provider="BRREG" if "brreg.no" in url else "http", operation="GET", success=False, status=0, duration_ms=elapsed, organisation_number=organisation_number, error=err_name, attempt=attempt + 1, retry=attempt > 0, retry_reason=err_name if attempt > 0 else None)
                    last_error = err_name
                    if organisation_number and attempt + 1 < attempts:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] RETRY company {organisation_number} | module={module} | attempt={attempt + 1}/{attempts} | {err_name}", flush=True)
                    if attempt + 1 < attempts:
                        time.sleep(0.4 * (2**attempt))
                    continue
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
            try:
                raw = exc.read()
            except Exception:
                raw = b""
            record_request(module=module, provider="BRREG" if "brreg.no" in url else "http", operation="GET", success=False, status=exc.code, duration_ms=elapsed, organisation_number=organisation_number, error=f"HTTP {exc.code}", attempt=attempt + 1, retry=attempt > 0, retry_reason=f"HTTP {exc.code}" if attempt > 0 else None)
            if exc.code in {404, 410}:
                return FetchResult(url, exc.code, elapsed, len(raw), error=f"HTTP {exc.code}", content_sha256=hashlib.sha256(raw).hexdigest() if raw else None, retrieved_at=_utc_now(), failure_class=classify_request(success=False, status=exc.code, error=f"HTTP {exc.code}"))
            last_error = f"HTTP {exc.code}"
            if organisation_number and attempt + 1 < attempts:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] RETRY company {organisation_number} | module={module} | attempt={attempt + 1}/{attempts} | HTTP {exc.code}", flush=True)
        except _NETWORK_ERRORS as exc:
            err_name = type(exc).__name__
            record_request(module=module, provider="BRREG" if "brreg.no" in url else "http", operation="GET", success=False, status=0, duration_ms=int((time.monotonic() - started) * 1000), organisation_number=organisation_number, error=err_name, attempt=attempt + 1, retry=attempt > 0, retry_reason=err_name if attempt > 0 else None)
            last_error = err_name
            if organisation_number and attempt + 1 < attempts:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] RETRY company {organisation_number} | module={module} | attempt={attempt + 1}/{attempts} | {err_name}", flush=True)
        if attempt + 1 < attempts:
            time.sleep(0.4 * (2**attempt))
    if organisation_number:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] FAILED company {organisation_number} | module={module} | error={last_error}", flush=True)
    return FetchResult(url, 0, 0, 0, error=last_error, retrieved_at=_utc_now(), failure_class=classify_request(success=False, status=0, error=last_error))
