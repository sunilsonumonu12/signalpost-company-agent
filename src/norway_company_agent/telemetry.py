from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def classify_request(*, success: bool, status: int | None = None, error: str | None = None) -> str:
    if success:
        return "success"
    if status in {404, 410}:
        return "not_found"
    if status == 429 or status in {500, 502, 503, 504}:
        return "retryable_failure"
    if error in {"URLError", "TimeoutError", "timeout", "Timeout"}:
        return "retryable_failure"
    return "hard_failure"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def request_log_path() -> Path | None:
    value = os.environ.get("SIGNALPOST_REQUEST_LOG", "").strip()
    return Path(value) if value else None


def record_request(
    *,
    module: str,
    provider: str,
    operation: str,
    success: bool,
    status: int | None = None,
    duration_ms: int | None = None,
    organisation_number: str | None = None,
    cache_hit: bool = False,
    caller_module: str | None = None,
    error: str | None = None,
    attempt: int = 1,
    retry: bool = False,
    retry_reason: str | None = None,
) -> None:
    path = request_log_path()
    if path is None:
        return
    event = {
        "timestamp": utc_now(),
        "run_id": os.environ.get("SIGNALPOST_RUN_ID"),
        "organisation_number": organisation_number,
        "module": module,
        "provider": provider,
        "operation": operation,
        "event_type": "request",
        "caller_module": caller_module or os.environ.get("SIGNALPOST_CALLER_MODULE", "run_agent"),
        "success": bool(success),
        "http_status": status,
        "duration_ms": duration_ms,
        "cache_hit": bool(cache_hit),
        "cache_miss": not cache_hit,
        "attempt": attempt,
        "retry": bool(retry),
    }
    if error:
        event["error"] = error[:200]
    if retry_reason:
        event["retry_reason"] = retry_reason[:120]
    event["failure_class"] = classify_request(success=success, status=status, error=error)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def aggregate_company_metrics(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate request events without collapsing modules into one provider stage."""
    companies: dict[str, dict[str, Any]] = {}
    stage_by_module = {
        "registry_live": "registry",
        "financials": "registry",
        "financial_history": "registry",
        "roles": "registry",
        "group": "registry",
        "locations": "registry",
        "website": "registry",
        "website_discovery": "discovery",
        "crawler": "crawling",
    }
    for event in events:
        org = event.get("organisation_number")
        if not org:
            continue
        module = str(event.get("module") or "unknown")
        failure_class = str(event.get("failure_class") or ("success" if event.get("success") else "hard_failure"))
        company = companies.setdefault(org, {
            "duration_ms": 0,
            "sum_request_duration_ms": 0,
            "wall_clock_duration_ms": 0,
            "first_request_at": None,
            "last_request_at": None,
            "retry_attempt_count": 0,
            "retry_success_count": 0,
            "status": "completed",
            "api_request_count": 0,
            "cache_hit_count": 0,
            "error_count": 0,
            "stages_executed": [],
            "modules_executed": [],
            "modules_succeeded": [],
            "modules_not_found": [],
            "modules_failed": [],
            # Step 3: discovery attribution fields.
            "discovery_attempted": False,
            "discovery_provider": None,
            "discovery_outcome": None,
        })
        company["duration_ms"] += int(event.get("duration_ms") or 0)
        company["sum_request_duration_ms"] += int(event.get("duration_ms") or 0)
        timestamp = event.get("timestamp")
        if timestamp and (company["first_request_at"] is None or timestamp < company["first_request_at"]):
            company["first_request_at"] = timestamp
        if timestamp and (company["last_request_at"] is None or timestamp > company["last_request_at"]):
            company["last_request_at"] = timestamp
        company["retry_attempt_count"] += int(bool(event.get("retry")))
        company["retry_success_count"] += int(bool(event.get("retry") and event.get("success")))
        company["api_request_count"] += 1
        company["cache_hit_count"] += int(bool(event.get("cache_hit")))
        company["error_count"] += int(not event.get("success"))
        if module not in company["modules_executed"]:
            company["modules_executed"].append(module)
        if event.get("success") and module not in company["modules_succeeded"]:
            company["modules_succeeded"].append(module)
        if failure_class == "not_found" and module not in company["modules_not_found"]:
            company["modules_not_found"].append(module)
        if failure_class in {"retryable_failure", "hard_failure"} and module not in company["modules_failed"]:
            company["modules_failed"].append(module)
        stage = stage_by_module.get(module)
        if stage and stage not in company["stages_executed"]:
            company["stages_executed"].append(stage)
        if not event.get("success") and failure_class in {"retryable_failure", "hard_failure"}:
            if module == "registry_live":
                company["status"] = "failed"
            elif company["status"] != "failed":
                company["status"] = "completed_with_errors"
        elif failure_class == "not_found" and company["status"] == "completed":
            company["status"] = "completed_with_gaps"

        # Step 3: capture discovery provider and outcome from website_discovery events.
        if module == "website_discovery":
            company["discovery_attempted"] = True
            provider_name = str(event.get("provider") or "unknown")
            if company["discovery_provider"] is None:
                company["discovery_provider"] = provider_name
            if event.get("success"):
                if company["discovery_outcome"] not in {"verified", "quarantined"}:
                    company["discovery_outcome"] = "searched"
            else:
                if company["discovery_outcome"] is None:
                    company["discovery_outcome"] = "failed"
    for company in companies.values():
        if company["first_request_at"] and company["last_request_at"]:
            first = datetime.fromisoformat(company["first_request_at"].replace("Z", "+00:00"))
            last = datetime.fromisoformat(company["last_request_at"].replace("Z", "+00:00"))
            company["wall_clock_duration_ms"] = max(0, int((last - first).total_seconds() * 1000))
    return companies


def apply_discovery_report(
    per_company: dict[str, dict[str, Any]],
    discovery_report: dict[str, Any],
) -> None:
    """Merge per-company discovery outcomes from a connector report into per_company.

    Outcome precedence (highest wins):
      verified > quarantined > searched > failed > skipped
    """
    outcome_rank = {"verified": 5, "quarantined": 4, "searched": 3, "failed": 2, "skipped": 1}
    for entry in discovery_report.get("company_outcomes") or []:
        org = str(entry.get("organisation_number") or "")
        outcome = str(entry.get("outcome") or "")
        provider = str(entry.get("provider") or "")
        if not org or not outcome:
            continue
        company = per_company.get(org)
        if company is None:
            company = per_company.setdefault(org, {
                "discovery_attempted": False,
                "discovery_provider": None,
                "discovery_outcome": None,
            })
        current_rank = outcome_rank.get(str(company.get("discovery_outcome") or ""), 0)
        new_rank = outcome_rank.get(outcome, 0)
        if new_rank > current_rank:
            company["discovery_outcome"] = outcome
        if outcome not in {"skipped"}:
            company["discovery_attempted"] = True
        if provider and company.get("discovery_provider") is None:
            company["discovery_provider"] = provider


def build_discovery_metrics(
    events: list[dict[str, Any]],
    *,
    provider_name: str,
    companies_attempted: list[str] | None = None,
    eligible_count: int | None = None,
    triggered_count: int | None = None,
    successful_count: int | None = None,
    failed_count: int | None = None,
    abstained_count: int | None = None,
) -> dict[str, Any]:
    """Build a structured discovery metrics block for one provider.

    Explicit counts (from connector report) take precedence over event-derived counts.
    companies_attempted must contain only organisation numbers — no query text.
    """
    provider_events = [
        e for e in events
        if e.get("event_type") == "request"
        and str(e.get("provider") or "").lower() == provider_name.lower()
        and str(e.get("module") or "") == "website_discovery"
    ]
    derived_triggered = len({str(e.get("organisation_number") or "") for e in provider_events if e.get("organisation_number")})
    derived_successful = sum(1 for e in provider_events if e.get("success"))
    derived_failed = sum(1 for e in provider_events if not e.get("success"))

    return {
        "provider": provider_name,
        "eligible_count": eligible_count,
        "triggered_count": triggered_count if triggered_count is not None else derived_triggered,
        "successful_count": successful_count if successful_count is not None else derived_successful,
        "failed_count": failed_count if failed_count is not None else derived_failed,
        "abstained_count": abstained_count,
        "companies_attempted": sorted(companies_attempted) if companies_attempted is not None else None,
        "query_text_retained": False,
    }


def build_metrics(
    *,
    events: list[dict[str, Any]],
    run_id: str,
    input_file: str,
    input_count: int,
    started_at: str,
    completed_at: str,
    duration_ms: int,
    stage_metrics: list[dict[str, Any]],
    per_company: dict[str, dict[str, Any]],
    command: list[str],
    discovery_reports: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    request_events = [event for event in events if event.get("event_type") == "request"]
    providers: dict[str, Counter] = defaultdict(Counter)
    modules: Counter = Counter()
    failure_classes: Counter = Counter()
    retry_attempts = sum(1 for event in request_events if event.get("retry"))
    retry_successes = sum(1 for event in request_events if event.get("retry") and event.get("success"))
    for event in request_events:
        provider = str(event.get("provider") or "unknown")
        modules[str(event.get("module") or "unknown")] += 1
        failure_classes[str(event.get("failure_class") or ("success" if event.get("success") else "hard_failure"))] += 1
        providers[provider]["total"] += 1
        providers[provider]["successful"] += int(bool(event.get("success")))
        providers[provider]["failed"] += int(not event.get("success"))
        providers[provider]["cache_hits"] += int(bool(event.get("cache_hit")))
        providers[provider]["cache_misses"] += int(bool(event.get("cache_miss")))
    success_count = sum(1 for item in per_company.values() if item.get("status") == "completed")
    failed_count = sum(1 for item in per_company.values() if item.get("status") == "failed")

    # Step 3: build structured discovery blocks — one per provider.
    discovery_provider_names: set[str] = set()
    for event in request_events:
        if str(event.get("module") or "") == "website_discovery":
            discovery_provider_names.add(str(event.get("provider") or "unknown"))
    report_by_provider: dict[str, dict[str, Any]] = {}
    for report in (discovery_reports or []):
        pname = str(report.get("provider_name") or report.get("provider") or "unknown")
        report_by_provider[pname] = report
        discovery_provider_names.add(pname)

    discovery_blocks: list[dict[str, Any]] = []
    for pname in sorted(discovery_provider_names):
        report = report_by_provider.get(pname) or {}
        discovery_blocks.append(build_discovery_metrics(
            request_events,
            provider_name=pname,
            companies_attempted=report.get("companies_attempted"),
            eligible_count=report.get("eligible_count"),
            triggered_count=report.get("triggered_count"),
            successful_count=report.get("successful_count"),
            failed_count=report.get("failed_count"),
            abstained_count=report.get("abstained_count"),
        ))

    return {
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "command": command,
        "input": {
            "input_file": input_file,
            "input_count": input_count,
            "processed_count": len(per_company),
            "success_count": success_count,
            "failed_count": failed_count,
            "skipped_count": max(0, input_count - len(per_company)),
        },
        "requests": {
            "total_api_calls": len(request_events),
            "successful_calls": sum(1 for event in request_events if event.get("success")),
            "failed_calls": sum(1 for event in request_events if not event.get("success")),
            "cache_hits": sum(1 for event in request_events if event.get("cache_hit")),
            "cache_misses": sum(1 for event in request_events if event.get("cache_miss")),
            "retry_attempts": retry_attempts,
            "retry_successes": retry_successes,
            "by_failure_class": dict(sorted(failure_classes.items())),
            "by_provider": dict(sorted(providers.items())),
            "by_module": dict(sorted(modules.items())),
            "discovery": discovery_blocks,
        },
        "stages": stage_metrics,
        "companies": per_company,
        "request_log": "request-log.jsonl",
    }


def monotonic_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
