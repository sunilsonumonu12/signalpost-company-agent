#!/usr/bin/env python3
"""Single evaluator entrypoint for the full Signalpost agent pipeline.

Chains every stage of the pipeline into one command, as required by the
"reproducible setup ... one evaluator command" hard gate:

  1. registry batch (identity, financials, financial_history, roles, group,
     locations, single-page website fetch) -- required, no optional deps.
  2. website discovery (Tavily, then Exa) for companies still missing a site --
     runs only if TAVILY_API_KEY / EXA_API_KEY is set (server-side secret, per
     the locked evaluator budget's own requirement).
  3. deep multi-page site crawl (scrapy) for every company with a website
     candidate -- best-effort: skipped cleanly if the `scrapy` package isn't
     installed in this environment, rather than failing the whole run.
  4. company-owned activity/news extraction from the crawl -- pure-Python,
     no optional dependency, always attempted.
  5. official annual-report OCR workforce extraction -- best-effort per
     company already (see run_annual_report_workforce_connector.py's own
     broad except); still guarded here so a totally missing tesseract/poppler
     install degrades to "no workforce data" rather than noisy per-company
     errors for the whole batch.
  6. claims/evidence envelope conversion, folding in every observation file
     collected above.

Every optional stage is best-effort: a failure is logged and the pipeline
moves on rather than losing everything already collected. The registry batch
(step 1) and the final conversion (step 6) are the only required stages --
without them there is no submission at all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
ARCHIVE_ROOT = PROJECT_ROOT / "archive" / "v2-crawler"
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from norway_company_agent.env import load_local_env  # noqa: E402
from norway_company_agent.telemetry import aggregate_company_metrics, apply_discovery_report, build_metrics, read_events  # noqa: E402
from norway_company_agent.vocabulary import SCHEMA_VERSION  # noqa: E402


def run(cmd: list[str], *, optional: bool = False) -> bool:
    print("+ " + " ".join(cmd), file=sys.stderr)
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        if not optional:
            raise
        print(f"  (optional stage failed, continuing: {exc})", file=sys.stderr)
        return False


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: dict) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def path_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.name.encode("utf-8", errors="replace"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    if path.is_dir():
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            digest.update(child.relative_to(path).as_posix().encode("utf-8", errors="replace"))
            with child.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()
    digest.update(("missing:" + str(path)).encode("utf-8", errors="replace"))
    return digest.hexdigest()


def stage_fingerprint(cmd: list[str], stage_name: str) -> tuple[str, list[str]]:
    input_options = {"--organisations", "--input", "--profiles", "--cache", "--observations"}
    output_options = {
        "--output", "--profiles-output", "--report", "--progress-file",
        "--jobdir", "--events", "--forensics",
    }
    inputs: list[tuple[str, Path]] = []
    flags: list[str] = []
    index = 2  # Skip interpreter and script path.
    ignored_value_options = {"--started-at", "--completed-at"}
    while index < len(cmd):
        token = cmd[index]
        if token == "--cache" and stage_name == "workforce_ocr" and index + 1 < len(cmd):
            # This stage writes to the cache itself. Hashing it pre-run would
            # make its marker invalid immediately after a successful run.
            index += 2
            continue
        if token in ignored_value_options and index + 1 < len(cmd):
            index += 2
            continue
        if token in input_options and index + 1 < len(cmd):
            inputs.append((token, Path(cmd[index + 1])))
            index += 2
            continue
        if token in output_options and index + 1 < len(cmd):
            index += 2
            continue
        flags.append(token)
        index += 1

    digest = hashlib.sha256()
    for option, raw_path in inputs:
        input_path = raw_path.resolve()
        digest.update(option.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(input_path).encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(path_sha256(input_path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), flags


def stage_output_paths(cmd: list[str]) -> list[Path]:
    output_options = {"--output", "--profiles-output", "--report"}
    result: list[Path] = []
    index = 2
    while index < len(cmd):
        if cmd[index] in output_options and index + 1 < len(cmd):
            result.append(Path(cmd[index + 1]))
            index += 2
        else:
            index += 1
    return result


def merge_profiles(base: list[dict], updated: list[dict], *, only_if_has: str | None = None) -> int:
    """Merge updated rows into base by organisation_number. Returns count merged."""
    updated_by_org = {row["organisation_number"]: row for row in updated}
    merged = 0
    for index, row in enumerate(base):
        candidate = updated_by_org.get(row["organisation_number"])
        if not candidate:
            continue
        if only_if_has and not (candidate.get("evidence") or {}).get(only_if_has):
            continue
        base[index] = candidate
        merged += 1
    return merged


def backfill_top_level_website(profiles: list[dict]) -> None:
    for row in profiles:
        if row.get("website"):
            continue
        web = row.get("evidence", {}).get("website") or {}
        if web.get("status") == "available":
            value = web.get("value") or {}
            row["website"] = value.get("final_url") or web.get("source_url")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full Signalpost agent end to end.")
    parser.add_argument("--organisations", required=True, help="JSONL/JSON/text list of organisation numbers")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--discovery-limit", type=int, default=None, help="Cap discovery queries; default is expected-count")
    parser.add_argument("--skip-deep-crawl", dest="skip_deep_crawl", action="store_true", default=False, help="Skip the scrapy multi-page crawl stage")
    parser.add_argument("--include-deep-crawl", dest="skip_deep_crawl", action="store_false", help="Enable the crawler (default)")
    parser.add_argument("--skip-workforce-ocr", dest="skip_workforce_ocr", action="store_true", default=True, help="Skip the annual-report OCR workforce stage (default for V1 submission)")
    parser.add_argument("--include-workforce-ocr", dest="skip_workforce_ocr", action="store_false", help="Enable annual-report OCR for V2 work")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()

    load_local_env(PROJECT_ROOT)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.run_id) or args.run_id in {".", ".."}:
        parser.error("--run-id may contain only letters, digits, dot, underscore, and hyphen")
    output_dir = Path(args.output_dir).resolve()
    # The launcher owns destructive cleanup via --fresh. This entrypoint never
    # wipes an existing run directory, so direct invocations also resume safely.
    output_dir.mkdir(parents=True, exist_ok=True)
    request_log = output_dir / "request-log.jsonl"
    workforce_cache_dir = PROJECT_ROOT / "cache" / "workforce-cache"
    crawl_jobdir_root = PROJECT_ROOT / "cache" / args.run_id / "crawl-jobdir"
    os.environ["SIGNALPOST_REQUEST_LOG"] = str(request_log)
    os.environ["SIGNALPOST_SNAPSHOT_DIR"] = str((output_dir / "snapshots").resolve())
    os.environ["SIGNALPOST_OUTPUT_DIR"] = str(output_dir.resolve())
    os.environ["SIGNALPOST_CRAWL_TRACE_PATH"] = str((output_dir / "crawl-trace.jsonl").resolve())
    os.environ["SIGNALPOST_QUARANTINE_PATH"] = str((output_dir / "validation-quarantine.jsonl").resolve())
    os.environ["SIGNALPOST_RUN_ID"] = args.run_id
    os.environ["SIGNALPOST_CALLER_MODULE"] = "run_agent"
    discovery_limit = args.discovery_limit or args.expected_count
    started_monotonic = time.monotonic()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stages_run: list[str] = []
    stage_metrics: list[dict] = []
    discovery_reports: list[dict] = []   # Step 3: one entry per provider that ran

    def run_stage(name: str, cmd: list[str], *, optional: bool = False) -> bool:
        marker_dir = output_dir / "stages"
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker_name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-_") or "stage"
        marker_path = marker_dir / f"{marker_name}.done"
        input_sha256, flags = stage_fingerprint(cmd, name)
        outputs = stage_output_paths(cmd)
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            marker = {}
        outputs_ready = bool(outputs) and all(path.exists() for path in outputs)
        if (
            marker.get("input_sha256") == input_sha256
            and marker.get("flags") == flags
            and outputs_ready
        ):
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            print(f"[{name}] skipped (done)", flush=True)
            stage_metrics.append({
                "stage": name,
                "started_at": now,
                "completed_at": now,
                "duration_ms": 0,
                "requests": 0,
                "errors": 0,
                "skipped": True,
            })
            return True

        atomic_write_text(output_dir / "progress-stage.txt", name + "\n")
        stage_started = time.monotonic()
        stage_started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ok = run(cmd, optional=optional)
        completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        stage_metrics.append({
            "stage": name,
            "started_at": stage_started_at,
            "completed_at": completed_at,
            "duration_ms": int((time.monotonic() - stage_started) * 1000),
            "requests": 0,
            "errors": 0 if ok else 1,
            "skipped": False,
        })
        if ok:
            atomic_write_json(marker_path, {
                "input_sha256": input_sha256,
                "flags": flags,
                "completed_at": completed_at,
            })
        return ok

    # 1. Registry batch: identity, financials, financial_history, roles, group,
    # locations, single-page website. Required. Keep this artifact immutable so
    # later merged profiles cannot invalidate the registry-stage marker.
    profiles_path = output_dir / "profiles-stage1.jsonl"
    run_stage("registry", [
        args.python, str(ROOT / "run_competition_batch.py"),
        "--organisations", args.organisations,
        "--profiles-output", str(profiles_path),
        "--output", str(output_dir / "registry-envelopes.jsonl"),
        "--report", str(output_dir / "registry-report.json"),
        "--run-id", args.run_id,
        "--expected-count", str(args.expected_count),
        "--progress-file", str(output_dir / "progress.json"),
        "--modules", "registry,accounting_obligation,registry_live,financials,financial_history,roles,group,locations,website",
    ])
    stages_run.append("registry_batch")

    # 2. Website discovery: Tavily, then Exa, for whatever's still missing.
    for name, script, env_var in (("tavily", "run_tavily_discovery.py", "TAVILY_API_KEY"), ("exa", "run_exa_discovery.py", "EXA_API_KEY")):
        if not os.environ.get(env_var, "").strip():
            print(f"No {env_var} set -- skipping {name} discovery.", file=sys.stderr)
            continue
        discovered_path = output_dir / f"profiles-with-{name}.jsonl"
        discovery_report_path = output_dir / f"{name}-discovery-report.json"
        ok = run_stage(f"{name}_discovery", [
            args.python, str(ARCHIVE_ROOT / script),
            "--input", str(profiles_path),
            "--output", str(discovered_path),
            "--report", str(discovery_report_path),
            "--limit", str(discovery_limit),
            "--promote-verified",
        ], optional=True)
        if ok:
            profiles_path = discovered_path
            stages_run.append(f"{name}_discovery")
            # Step 3: read the connector report and merge per-company discovery
            # outcomes into the telemetry companies dict so run-metrics.json
            # reflects verified/quarantined/abstained rather than just "searched".
            # API key values and query text are never present in these reports.
            if discovery_report_path.exists():
                try:
                    discovery_report = json.loads(discovery_report_path.read_text(encoding="utf-8"))
                    discovery_reports.append(discovery_report)
                except (json.JSONDecodeError, OSError):
                    pass  # Non-fatal: telemetry will fall back to request-log counts.

    profiles = read_jsonl(profiles_path)
    backfill_top_level_website(profiles)
    write_jsonl(output_dir / "profiles.jsonl", profiles)
    profiles_path = output_dir / "profiles.jsonl"

    # 3. Deep multi-page crawl (scrapy) of every company with a website
    # candidate. Best-effort: scrapy may not be installed everywhere.
    activity_obs_path = output_dir / "activity-observations.jsonl"
    news_obs_path = output_dir / "news-observations.jsonl"
    careers_obs_path = output_dir / "careers-observations.jsonl"
    has_scrapy = False
    if not args.skip_deep_crawl:
        try:
            import scrapy  # noqa: F401
            has_scrapy = True
        except ImportError:
            has_scrapy = False

    if has_scrapy:
        crawl_input = output_dir / "crawl-input.jsonl"
        write_jsonl(crawl_input, [row for row in profiles if row.get("website")])
        crawl_jobdir = crawl_jobdir_root / path_sha256(crawl_input)
        crawled_path = output_dir / "profiles-crawled.jsonl"
        ok = run_stage("deep_crawl", [
            args.python, str(ARCHIVE_ROOT / "run_scrapy_websites.py"),
            "--input", str(crawl_input),
            "--output", str(crawled_path),
            "--events", str(output_dir / "crawl-events.jsonl"),
            "--jobdir", str(crawl_jobdir),
            "--report", str(output_dir / "crawl-report.json"),
            "--forensics", str(output_dir / "website-forensics.jsonl"),
        ], optional=True)
        if ok:
            crawled = read_jsonl(crawled_path)
            merge_profiles(profiles, crawled)
            write_jsonl(profiles_path, profiles)
            stages_run.append("deep_crawl")

            # 4. Activity/news extraction from the deepened crawl. Pure Python,
            # always attempted once there's a crawl to extract from.
            run_stage("site_activity", [args.python, str(ARCHIVE_ROOT / "extract_company_site_activity.py"), "--profiles", str(profiles_path), "--output", str(activity_obs_path), "--report", str(output_dir / "activity-report.json")], optional=True)
            run_stage("site_news", [args.python, str(ARCHIVE_ROOT / "extract_company_site_news.py"), "--profiles", str(profiles_path), "--output", str(news_obs_path), "--report", str(output_dir / "news-report.json")], optional=True)
            run_stage("site_careers", [args.python, str(ARCHIVE_ROOT / "extract_company_site_careers.py"), "--profiles", str(profiles_path), "--output", str(careers_obs_path), "--report", str(output_dir / "careers-report.json")], optional=True)
            if activity_obs_path.exists():
                stages_run.append("site_activity")
            if news_obs_path.exists():
                stages_run.append("site_news")
            if careers_obs_path.exists():
                stages_run.append("site_careers")
    elif args.skip_deep_crawl:
        print("Deep website crawl skipped (--skip-deep-crawl).", file=sys.stderr)
    else:
        print("scrapy not installed -- skipping deep crawl and activity/news extraction.", file=sys.stderr)

    # 5. Official annual-report OCR workforce extraction. Already degrades
    # per-company internally; skip the whole stage only if asked to.
    workforce_obs_path = output_dir / "workforce-observations.jsonl"
    if not args.skip_workforce_ocr:
        orgs_path = output_dir / "all-orgs.txt"
        atomic_write_text(orgs_path, "\n".join(row["organisation_number"] for row in profiles))
        ok = run_stage("workforce_ocr", [
            args.python, str(ARCHIVE_ROOT / "run_annual_report_workforce_connector.py"),
            "--profiles", str(profiles_path),
            "--organisations", str(orgs_path),
            "--output", str(workforce_obs_path),
            "--cache", str(workforce_cache_dir),
            "--report", str(output_dir / "workforce-report.json"),
        ], optional=True)
        if ok:
            stages_run.append("workforce_ocr")

    # 5b. Prior-year comparative financial figures, recovered from the same
    # annual-report OCR cache the workforce stage just populated -- no new
    # downloads or OCR, so this only runs if that cache exists.
    prior_year_obs_path = output_dir / "prior-year-financials-observations.jsonl"
    if not args.skip_workforce_ocr and workforce_cache_dir.exists():
        ok = run_stage("prior_year_financials", [
            args.python, str(ARCHIVE_ROOT / "extract_prior_year_financials.py"),
            "--profiles", str(profiles_path),
            "--cache", str(workforce_cache_dir),
            "--output", str(prior_year_obs_path),
            "--report", str(output_dir / "prior-year-financials-report.json"),
        ], optional=True)
        if ok:
            stages_run.append("prior_year_financials")

    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 6. Claims/evidence conversion, folding in every observation file collected.
    envelopes_path = output_dir / "envelopes.jsonl"
    convert_cmd = [
        args.python, str(ROOT / "build_output_contract.py"),
        "--profiles", str(profiles_path),
        "--output", str(envelopes_path),
        "--report", str(output_dir / "contract-report.json"),
        "--run-id", args.run_id,
        "--started-at", started_at,
        "--completed-at", completed_at,
    ]
    for obs_path in (activity_obs_path, news_obs_path, careers_obs_path, workforce_obs_path, prior_year_obs_path):
        if obs_path.exists():
            convert_cmd += ["--observations", str(obs_path)]
    run_stage("claims_conversion", convert_cmd)
    stages_run.append("claims_conversion")

    # 6b. Validate the envelopes just written. Optional: a failed validation is reported
    # in validation-report.json and on stderr, it does not delete the envelopes.
    validation_report_path = output_dir / "validation-report.json"
    ok = run_stage("envelope_validation", [
        args.python, str(ROOT / "validate_envelopes.py"),
        "--input", str(envelopes_path),
        "--report", str(validation_report_path),
        "--run-id", args.run_id,
        "--expected-count", str(args.expected_count),
    ], optional=True)
    if ok:
        stages_run.append("envelope_validation")

    summary = {
        "schema_version": SCHEMA_VERSION,
        "run_id": args.run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "stages_run": stages_run,
        "profiles": str(profiles_path),
        "envelopes": str(envelopes_path),
        "validation_report": str(validation_report_path),
        "metrics": str(output_dir / "run-metrics.json"),
    }
    atomic_write_json(output_dir / "run-summary.json", summary)
    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    all_events = read_events(request_log)
    foreign_run_ids = sorted({str(event.get("run_id")) for event in all_events if event.get("run_id") != args.run_id})
    if foreign_run_ids:
        print(
            f"Warning: request log contains events for other run_id values: {', '.join(foreign_run_ids)}; "
            f'filtering metrics to run_id "{args.run_id}".',
            file=sys.stderr,
        )
    events = [event for event in all_events if event.get("run_id") == args.run_id]
    companies = aggregate_company_metrics(events)
    # Step 3: merge per-company discovery outcomes from connector reports into
    # the companies dict so run-metrics.json shows verified/quarantined/abstained
    # rather than relying solely on the request-log-derived "searched" placeholder.
    for discovery_report in discovery_reports:
        apply_discovery_report(companies, discovery_report)
    metrics = build_metrics(events=events, run_id=args.run_id, input_file=args.organisations, input_count=args.expected_count, started_at=started_at, completed_at=completed_at, duration_ms=int((time.monotonic() - started_monotonic) * 1000), stage_metrics=stage_metrics, per_company=companies, command=sys.argv, discovery_reports=discovery_reports)
    metrics["stages"] = [{**stage, "requests": sum(1 for event in events if event.get("timestamp", "") >= stage["started_at"] and event.get("timestamp", "") <= stage["completed_at"]), "errors": sum(1 for event in events if not event.get("success") and event.get("timestamp", "") >= stage["started_at"] and event.get("timestamp", "") <= stage["completed_at"])} for stage in stage_metrics]
    metrics["schema_version"] = SCHEMA_VERSION
    atomic_write_json(output_dir / "run-metrics.json", metrics)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
