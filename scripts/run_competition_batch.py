#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from norway_company_agent.batch import (  # noqa: E402
    backfill_from_registry_live,
    profiles_from_inputs,
    terminal_envelope,
)
from norway_company_agent.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.official import (  # noqa: E402
    accounting_obligation_assessment,
    extract_annual_report_workforce,
    fetch_google_news_mentions,
    fetch_official_modules,
)
from norway_company_agent.website import fetch_website  # noqa: E402


class ProgressTracker:
    def __init__(self, path: Path, total: int):
        self.path = path
        self.lock = threading.Lock()
        now = time.time()
        self.state = {
            "total": total,
            "started": 0,
            "completed": 0,
            "successful": 0,
            "failed": 0,
            "active": 0,
            "last_company": None,
            "last_completed_company": None,
            "last_progress_at": now,
            "root_error": None,
            "failed_orgs": [],
            "total_retries": 0,
        }
        self._write_locked()

    def _write_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(10):
            temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, self.path)
                return
            except PermissionError:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(0.05 * (attempt + 1))
            except OSError:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(0.05 * (attempt + 1))
        raise OSError(f"Could not write progress state to {self.path}")

    def started_company(self, organisation_number: str) -> None:
        with self.lock:
            self.state["started"] += 1
            self.state["active"] += 1
            self.state["last_company"] = organisation_number
            self._write_locked()

    def add_retries(self, count: int) -> None:
        if count <= 0:
            return
        with self.lock:
            self.state["total_retries"] = int(self.state.get("total_retries") or 0) + count
            self._write_locked()

    def finished_company(self, organisation_number: str, error: Exception | None = None) -> None:
        with self.lock:
            self.state["completed"] += 1
            self.state["active"] = max(0, self.state["active"] - 1)
            self.state["last_company"] = organisation_number
            self.state["last_completed_company"] = organisation_number
            self.state["last_progress_at"] = time.time()
            if error is None:
                self.state["successful"] += 1
            else:
                self.state["failed"] += 1
                failed_orgs = self.state.get("failed_orgs") or []
                failed_orgs.append(organisation_number)
                self.state["failed_orgs"] = failed_orgs
                if self.state["root_error"] is None:
                    self.state["root_error"] = f"{type(error).__name__}: {error}"
            self._write_locked()


class CompanyTimingTracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.records: list[tuple[str, float]] = []

    def record(self, organisation_number: str, duration: float) -> None:
        with self.lock:
            self.records.append((organisation_number, duration))

    def print_summary(self) -> None:
        with self.lock:
            records = list(self.records)
        if not records:
            print("AVERAGE company time: 0.0 sec", flush=True)
            print("FASTEST company: n/a | 0.0 sec", flush=True)
            print("SLOWEST company: n/a | 0.0 sec", flush=True)
            return

        average = sum(duration for _, duration in records) / len(records)
        fastest = min(records, key=lambda record: record[1])
        slowest = max(records, key=lambda record: record[1])
        print(f"AVERAGE company time: {average:.1f} sec", flush=True)
        print(f"FASTEST company: {fastest[0]} | {fastest[1]:.1f} sec", flush=True)
        print(f"SLOWEST company: {slowest[0]} | {slowest[1]:.1f} sec", flush=True)


def read_orgs(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if path.suffix.lower() == ".json" or stripped.startswith("["):
        data = json.loads(text)
        if isinstance(data, list):
            return [str(item) for item in data]
        if isinstance(data, dict):
            return [str(v) for v in data.values()]
        return []

    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                row = json.loads(line)
                org = row.get("organisation_number") or row.get("org") or row.get("id")
                if org:
                    rows.append(str(org))
            except json.JSONDecodeError:
                pass
        else:
            rows.append(line)
    return rows


def _build_failure_profile(organisation_number: str, modules: set[str], error_message: str) -> dict:
    """Build a minimal but valid profile when collection crashed entirely."""
    from norway_company_agent.evidence import evidence as _evidence
    from norway_company_agent.batch import profiles_from_inputs
    base = profiles_from_inputs([{"organisation_number": organisation_number}])[0]
    base["_batch_state"] = "failed"
    base["_batch_error"] = error_message[:500] if error_message else "collection_exception"
    ev = base.setdefault("evidence", {})
    # Ensure every requested module has a terminal evidence record
    module_source_map = {
        "registry_live": "official_registry_live",
        "financials": "official_annual_accounts",
        "financial_history": "official_annual_account_copies",
        "roles": "official_roles",
        "group": "official_group_structure",
        "locations": "official_subunits",
        "accounting_obligation": "official_rule_interpretation",
        "workforce": "official_annual_account_copy",
        "google_news": "google_news_rss",
        "website": "registry_linked_company_website",
        "registry": "official_registry_live",
    }
    for mod in modules:
        if mod in ev:
            continue
        src = module_source_map.get(mod, "source_error_fallback")
        note = error_message[:300] if error_message else "collection_exception"
        ev[mod] = _evidence(
            mod, "source_error", src,
            "https://data.brreg.no/enhetsregisteret/api/enheter/" + organisation_number,
            note=f"collect_profile crashed: {note}",
            source_row_key=organisation_number,
        )
    # Mark the registry hint record too so terminal_envelope sees terminal states
    if "registry" not in ev:
        ev["registry"] = _evidence(
            "registry", "source_error", "official_registry_live",
            "https://data.brreg.no/enhetsregisteret/api/enheter/" + organisation_number,
            note="collection crashed before registry fetch",
            source_row_key=organisation_number,
        )
    return base


def collect_profile(
    profile: dict,
    modules: set[str],
    progress: ProgressTracker | None = None,
    timings: CompanyTimingTracker | None = None,
) -> dict:
    organisation_number = str(profile["organisation_number"])
    if progress:
        progress.started_company(organisation_number)
    started = time.perf_counter()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] START company {organisation_number}", flush=True)
    try:
        evidence = profile.setdefault("evidence", {})
        official_modules = modules & {"registry_live", "financials", "financial_history", "roles", "group", "locations"}
        if official_modules:
            evidence.update(fetch_official_modules(organisation_number, official_modules)[0])
        backfill_from_registry_live(profile)
        if "accounting_obligation" in modules:
            evidence["accounting_obligation"] = accounting_obligation_assessment(profile)
        if "workforce" in modules:
            evidence["workforce"] = extract_annual_report_workforce(profile)
        if "google_news" in modules:
            evidence["google_news"] = fetch_google_news_mentions(profile)
        if "website" in modules:
            website_record, _ = fetch_website(profile.get("website"), organisation_number=organisation_number)
            gated = apply_website_identity_gate(profile, website_record)
            evidence["website"] = gated["website"]
        profile["_batch_state"] = "ok"
        if progress:
            progress.finished_company(organisation_number)
        duration = time.perf_counter() - started
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] END company {organisation_number} | {duration:.1f} sec | SUCCESS",
            flush=True,
        )
        if timings:
            timings.record(organisation_number, duration)
        return profile
    except Exception as exc:
        duration = time.perf_counter() - started
        error = f"{type(exc).__name__}: {exc}".replace("\r", " ").replace("\n", " ")
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] END company {organisation_number} | {duration:.1f} sec | FAILED | {error}",
            flush=True,
        )
        if timings:
            timings.record(organisation_number, duration)
        if progress:
            progress.finished_company(organisation_number, exc)
        profile["_batch_state"] = "failed"
        profile["_batch_error"] = error[:500]
        # Build out missing evidence modules so terminal_envelope produces a valid (failed) envelope
        from norway_company_agent.evidence import evidence as _evidence
        ev = profile.setdefault("evidence", {})
        module_source_map = {
            "registry_live": "official_registry_live",
            "financials": "official_annual_accounts",
            "financial_history": "official_annual_account_copies",
            "roles": "official_roles",
            "group": "official_group_structure",
            "locations": "official_subunits",
            "accounting_obligation": "official_rule_interpretation",
            "workforce": "official_annual_account_copy",
            "google_news": "google_news_rss",
            "website": "registry_linked_company_website",
            "registry": "official_registry_live",
        }
        for mod in modules:
            if mod in ev:
                continue
            src = module_source_map.get(mod, "source_error_fallback")
            ev[mod] = _evidence(
                mod, "source_error", src,
                "https://data.brreg.no/enhetsregisteret/api/enheter/" + organisation_number,
                note=f"exception: {error[:240]}",
                source_row_key=organisation_number,
            )
        if "registry" not in ev:
            ev["registry"] = _evidence(
                "registry", "source_error", "official_registry_live",
                "https://data.brreg.no/enhetsregisteret/api/enheter/" + organisation_number,
                note="collection_exception",
                source_row_key=organisation_number,
            )
        return profile


def main() -> int:
    p = argparse.ArgumentParser(description="Minimal registry batch for V1 submission")
    p.add_argument("--organisations", required=True)
    p.add_argument("--profiles-output", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--run-id", default="run-v1")
    p.add_argument("--expected-count", type=int, default=0)
    p.add_argument("--progress-file", type=Path)
    p.add_argument("--modules", default="registry")
    args = p.parse_args()

    org_file = Path(args.organisations)
    orgs = read_orgs(org_file)
    profiles = profiles_from_inputs([{"organisation_number": org} for org in orgs])
    modules = {module.strip() for module in args.modules.split(",") if module.strip()}
    progress = ProgressTracker(args.progress_file, len(profiles)) if args.progress_file else None
    timings = CompanyTimingTracker()

    collected: list[dict] = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, max(1, len(profiles)))) as executor:
            futures = [executor.submit(collect_profile, profile, modules, progress, timings) for profile in profiles]
            for profile, future in zip(profiles, futures):
                org = str(profile["organisation_number"])
                try:
                    collected.append(future.result())
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}".replace("\r", " ").replace("\n", " ")
                    print(
                        f"[{datetime.now().strftime('%H:%M:%S')}] WORKER CRASH company {org} | {error}",
                        flush=True,
                    )
                    if timings:
                        timings.record(org, 0.0)
                    if progress:
                        progress.finished_company(org, exc)
                    recovered = _build_failure_profile(org, modules, error)
                    collected.append(recovered)
    finally:
        timings.print_summary()

    # Count retries from the request log if available
    total_retries = 0
    try:
        request_log = Path(os.environ.get("SIGNALPOST_REQUEST_LOG", ""))
        if request_log.exists():
            lines = request_log.read_text(encoding="utf-8").splitlines()
            for line in lines:
                if not line.strip():
                    continue
                try:
                    evt = json.loads(line)
                    if evt.get("retry") and evt.get("success") is not False:
                        total_retries += 1
                except Exception:
                    pass
    except Exception:
        pass
    if progress:
        progress.add_retries(total_retries)

    Path(args.profiles_output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.profiles_output, "w", encoding="utf-8") as handle:
        for row in collected:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    envelope_rows = [
        terminal_envelope(
            row,
            run_id=args.run_id,
            modules=row.get("evidence", {}).keys(),
            started_at="",
            completed_at="",
        )
        for row in collected
    ]

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        for row in envelope_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Compute failed orgs via profile _batch_state (authoritative: pipeline crashed vs completed cleanly)
    expected = args.expected_count or len(collected)
    successful = sum(1 for row in collected if row.get("_batch_state", "ok") != "failed")
    failed_count = len(collected) - successful
    failed_orgs = sorted({
        str(row.get("organisation_number", ""))
        for row in collected
        if row.get("_batch_state") == "failed"
    })
    if not failed_orgs:
        # Fallback: also count envelopes where every module is source_error (indicates data didn't load)
        env_failed = []
        for env in envelope_rows:
            module_states = list((env.get("modules") or {}).values())
            if module_states and all(item.get("state") == "source_error" for item in module_states):
                org = str(env.get("organisation_number", ""))
                if org and org not in env_failed:
                    env_failed.append(org)
        failed_orgs = sorted(set(env_failed))
        failed_count = len(failed_orgs)
        successful = len(collected) - failed_count

    identity_decisions: dict[str, int] = {}
    for row in collected:
        website_value = (((row.get("evidence") or {}).get("website") or {}).get("value") or {})
        assessment = website_value.get("identity_assessment") if isinstance(website_value, dict) else None
        decision = str((assessment or {}).get("decision") or "")
        if decision:
            identity_decisions[decision] = identity_decisions.get(decision, 0) + 1

    # Concise failure summary print
    print("", flush=True)
    print("=" * 60, flush=True)
    print("BATCH FAILURE SUMMARY", flush=True)
    print("=" * 60, flush=True)
    print(f"  Total companies   : {len(collected)}/{expected}", flush=True)
    print(f"  Successful        : {successful}", flush=True)
    print(f"  Failed            : {failed_count}", flush=True)
    print(f"  Total retries     : {total_retries}", flush=True)
    if failed_orgs:
        preview = ", ".join(failed_orgs[:20])
        extra = "" if len(failed_orgs) <= 20 else f" (+{len(failed_orgs) - 20} more)"
        print(f"  Failed orgs       : {preview}{extra}", flush=True)
    if progress and not failed_orgs:
        progress_failed = progress.state.get("failed") or 0
        if progress_failed:
            print(f"  (progress-tracker failed: {progress_failed}; envelopes still marked complete)", flush=True)
    print("=" * 60, flush=True)
    print("", flush=True)

    report = {
        "run_id": args.run_id,
        "expected_count": expected,
        "profiles_written": len(collected),
        "envelopes_written": len(envelope_rows),
        "modules": sorted(modules),
        "status": "ok" if len(collected) == expected else "partial",
        "successful": successful,
        "failed": failed_count,
        "failed_orgs": failed_orgs,
        "total_retries": total_retries,
        "identity_decisions": identity_decisions,
        "validation": {
            "website_values_accepted": sum(
                1 for row in collected
                if ((row.get("evidence") or {}).get("website") or {}).get("status") == "available"
            ),
            "website_values_rejected": sum(
                1 for row in collected
                if "schema validation" in str((((row.get("evidence") or {}).get("website") or {}).get("note") or "")).casefold()
            ),
        },
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
