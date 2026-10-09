#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.crawl_events import merge_profile_events, missing_seed_error_events  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.operations import domain_request_summary, latency_summary, peak_rss_bytes  # noqa: E402
from norway_company_agent.validation import (  # noqa: E402
    append_quarantine_record,
    validate_crawl_page_event,
    validate_website_value,
    validation_error_details,
    validation_reason_code,
)
from pydantic import ValidationError  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def terminal_events_for_run(profiles: list[dict], events: list[dict], crawl_complete: bool) -> list[dict]:
    return missing_seed_error_events(profiles, events) if crawl_complete else []


def _page_outcome_summary(events: list[dict]) -> dict:
    """Aggregate per-page outcome counts from the event ledger (Step 2).

    Returns a dict with:
      attempted        – total page events (success + all failure classes)
      available        – pages that returned usable HTML
      by_failure_class – count per failure_class for non-success events
      missing_class    – events that have no failure_class field (legacy events
                         loaded from a previous run before Step 2 was deployed)
    """
    from collections import Counter
    from norway_company_agent.crawl_events import classify_failure_class

    total = 0
    available = 0
    by_fc: Counter[str] = Counter()
    missing_class = 0

    for event in events:
        total += 1
        status = event.get("status")
        fc = event.get("failure_class")
        if status == "available":
            available += 1
            # success events after Step 2 carry failure_class="success"; older
            # events may not have the field at all.
            if fc is None:
                missing_class += 1
        else:
            if fc is None:
                # Legacy event: derive from whatever fields are present.
                fc = classify_failure_class(
                    status_code=event.get("status_code") or 0,
                    error=event.get("error") or "",
                )
                missing_class += 1
            by_fc[fc] += 1

    return {
        "attempted": total,
        "available": available,
        "by_failure_class": dict(sorted(by_fc.items())),
        "missing_failure_class_legacy_events": missing_class,
    }


def main() -> None:
    try:
        from scrapy.crawler import CrawlerProcess
        from scrapy.utils.project import get_project_settings
        from scrapy_crawler import SignalpostWebsiteSpider
    except ImportError as exc:
        raise SystemExit(f'Install Scrapy using this interpreter: "{sys.executable}" -m pip install scrapy') from exc

    parser = argparse.ArgumentParser(description="Resumable, robots-aware Scrapy scheduler for registry-linked company sites.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--jobdir", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--forensics", required=True)
    parser.add_argument("--quarantine", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--organisation-number", action="append", dest="organisation_numbers")
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--per-domain", type=int, default=2)
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()

    input_path = Path(args.input)
    input_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
    jobdir = Path(args.jobdir)
    jobdir.mkdir(parents=True, exist_ok=True)
    metadata_path = jobdir / "signalpost-input.json"
    metadata = {"input_sha256": input_hash, "input_path": str(input_path.resolve())}
    if metadata_path.exists() and json.loads(metadata_path.read_text(encoding="utf-8")) != metadata:
        raise SystemExit("Job directory belongs to a different input. Choose a new --jobdir.")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    events_path = Path(args.events)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_before = read_jsonl(events_path) if events_path.exists() else []
    settings = get_project_settings()
    settings.update({
        "CONCURRENT_REQUESTS": args.concurrency,
        "CONCURRENT_REQUESTS_PER_DOMAIN": args.per_domain,
        "JOBDIR": str(jobdir),
        "LOG_LEVEL": args.log_level,
        "FEEDS": {str(events_path.resolve()): {"format": "jsonlines", "encoding": "utf8", "overwrite": False}},
    })
    process = CrawlerProcess(settings)
    crawler = process.create_crawler(SignalpostWebsiteSpider)
    selected_numbers = set(args.organisation_numbers or []) or None
    process.crawl(
        crawler,
        profiles_path=str(input_path),
        limit=args.limit,
        organisation_numbers=",".join(sorted(selected_numbers)) if selected_numbers else None,
    )
    run_started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    started = time.monotonic()
    process.start()
    elapsed = time.monotonic() - started
    run_finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    stats = crawler.stats.get_stats()
    crawl_complete = stats.get("finish_reason") == "finished"

    rows = read_jsonl(input_path)
    crawl_targets = [
        row for row in rows
        if row.get("website") and (selected_numbers is None or row["organisation_number"] in selected_numbers)
    ][: args.limit if args.limit else None]
    events = read_jsonl(events_path) if events_path.exists() else []
    missing_seed_events = terminal_events_for_run(crawl_targets, events, crawl_complete)
    if missing_seed_events:
        with events_path.open("a", encoding="utf-8") as handle:
            for event in missing_seed_events:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        events.extend(missing_seed_events)
    quarantine_path = args.quarantine or Path(args.report).parent / "validation-quarantine.jsonl"
    valid_events = []
    rejected_events = 0
    for event in events:
        try:
            valid_events.append(validate_crawl_page_event(event))
        except ValidationError as exc:
            rejected_events += 1
            append_quarantine_record(
                path=quarantine_path,
                record_type="CrawlPageEvent",
                record=event,
                reason_code=validation_reason_code(exc),
                errors=validation_error_details(exc),
            )
    events = valid_events
    by_org: dict[str, list[dict]] = defaultdict(list)
    dedupe = set()
    for event in events:
        key = (event.get("organisation_number"), event.get("page_kind"), event.get("final_url"), event.get("content_sha256"), event.get("status"))
        if key in dedupe:
            continue
        dedupe.add(key)
        by_org[str(event.get("organisation_number") or "")].append(event)
    touched = 0
    website_statuses: Counter[str] = Counter()
    identity_statuses: Counter[str] = Counter()
    identity_decisions: Counter[str] = Counter()
    published_socials = 0
    website_values_accepted = 0
    website_values_rejected = 0
    for row in rows:
        profile_events = by_org.get(row["organisation_number"])
        if not profile_events:
            continue
        website = merge_profile_events(row, profile_events)
        if website.get("status") == "available":
            try:
                website["value"] = validate_website_value(website.get("value"))
                website_values_accepted += 1
            except ValidationError as exc:
                website_values_rejected += 1
                append_quarantine_record(
                    path=quarantine_path,
                    record_type="WebsiteValue",
                    record=website.get("value"),
                    reason_code=validation_reason_code(exc),
                    errors=validation_error_details(exc),
                )
                website = {
                    **website,
                    "status": "source_error",
                    "value": None,
                    "note": "Website payload failed schema validation",
                }
        gated = apply_website_identity_gate(row, website)
        row.setdefault("evidence", {})["website"] = gated["website"]
        website_statuses[gated["website"].get("status", "invalid")] += 1
        if gated["assessment"]:
            identity_statuses[gated["assessment"].get("status", "invalid")] += 1
            identity_decisions[gated["assessment"].get("decision", "UNKNOWN")] += 1
        published_socials += len((gated["website"].get("value") or {}).get("social_links") or [])
        touched += 1
    write_jsonl(Path(args.output), rows)
    output_hash = hashlib.sha256(Path(args.output).read_bytes()).hexdigest()

    forensic_pages_by_org: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        forensic = event.get("forensics")
        if isinstance(forensic, dict):
            forensic_pages_by_org[str(event.get("organisation_number") or "")].append(forensic)

    forensic_rows = []
    forensic_totals = Counter()
    rejection_reasons: Counter[str] = Counter()
    visible_data_without_candidate = []
    for row in rows:
        org = str(row.get("organisation_number") or "")
        website = (row.get("evidence") or {}).get("website") or {}
        website_value = website.get("value") if isinstance(website.get("value"), dict) else {}
        jobs = website_value.get("jobs") if isinstance(website_value.get("jobs"), list) else []
        news = website_value.get("news") if isinstance(website_value.get("news"), list) else []
        pages = forensic_pages_by_org.get(org, [])
        for page in pages:
            job_urls = {str(candidate.get("url") or "") for candidate in page.get("job_candidates") or []}
            news_urls = {str(candidate.get("url") or "") for candidate in page.get("article_candidates") or []}
            page_jobs = [item for item in jobs if str(item.get("url") or "") in job_urls]
            page_news = [item for item in news if str(item.get("url") or "") in news_urls]
            page["normalized_jobs"] = page_jobs or "not_available"
            page["normalized_news"] = page_news or "not_available"
            page["jobs_extracted"] = len(page_jobs)
            page["news_extracted"] = len(page_news)
            for rejection in page.get("job_candidate_rejections") or []:
                rejection_reasons["jobs: " + str(rejection.get("rejection_reason") or "unspecified")] += 1
            for rejection in page.get("article_candidate_rejections") or []:
                rejection_reasons["news: " + str(rejection.get("rejection_reason") or "unspecified")] += 1

        careers_pages = [page for page in pages if page.get("careers_page_detected")]
        news_pages = [page for page in pages if page.get("news_page_detected")]
        profile_events = by_org.get(org, [])
        failed_careers_pages = [event for event in profile_events if event.get("page_kind") == "careers" and event.get("status") != "available"]
        failed_news_pages = [event for event in profile_events if event.get("page_kind") == "news" and event.get("status") != "available"]
        job_links_found = sum(int(page.get("job_links_found") or 0) for page in pages)
        job_candidates_found = sum(int(page.get("job_candidates_found") or 0) for page in pages)
        article_links_found = sum(int(page.get("article_links_found") or 0) for page in pages)
        article_candidates_found = sum(int(page.get("article_candidates_found") or 0) for page in pages)
        unselected_careers_links = [
            link for page in pages for link in page.get("priority_links_not_selected", [])
            if link.get("page_kind") == "careers"
        ]
        if not careers_pages and not failed_careers_pages:
            jobs_loss_stage = (
                "page discovery: careers link was found but omitted by the bounded priority-page crawl"
                if unselected_careers_links else
                "candidate detection: job-like links or text were visible but no existing parser candidate was accepted"
                if job_links_found or any(page.get("job_like_text_candidates") for page in pages) else
                "page discovery: no careers page or job-like link was found on crawled pages"
            )
        elif failed_careers_pages:
            jobs_loss_stage = "page crawl: a selected careers page failed to fetch"
        elif job_candidates_found and not jobs:
            jobs_loss_stage = "normalization: parser candidates existed but normalized jobs are absent"
        elif job_links_found and not job_candidates_found:
            jobs_loss_stage = "candidate detection: job-like links were visible but parser candidates were absent"
        elif any(not page.get("full_cleaned_page_text") and (page.get("relevant_jsonld") or page.get("relevant_html_fragments")) for page in careers_pages):
            jobs_loss_stage = "HTML extraction: structured or relevant markup exists but cleaned text is empty"
        else:
            jobs_loss_stage = "none observed: parser candidates propagated to normalized jobs" if jobs else "page discovery: no job data was observed"

        if failed_news_pages:
            news_loss_stage = "page crawl: a selected news page failed to fetch"
        elif article_candidates_found and not news:
            news_loss_stage = "normalization: article candidates existed but normalized news are absent"
        elif article_links_found and not article_candidates_found:
            news_loss_stage = "candidate detection: article-like links were visible but no dated article candidate was accepted"
        elif any(not page.get("full_cleaned_page_text") and (page.get("relevant_jsonld") or page.get("relevant_html_fragments")) for page in news_pages):
            news_loss_stage = "HTML extraction: structured or relevant markup exists but cleaned text is empty"
        elif news:
            news_loss_stage = "none observed: article candidates propagated to normalized news"
        else:
            news_loss_stage = "page discovery: no news page or article-like link was found on crawled pages"

        for page in pages:
            if page.get("job_like_links") and not page.get("job_candidates"):
                visible_data_without_candidate.append({"organisation_number": org, "url": page.get("final_url"), "signal": "jobs", "stage": jobs_loss_stage})
            if page.get("article_like_links") and not page.get("article_candidates"):
                visible_data_without_candidate.append({"organisation_number": org, "url": page.get("final_url"), "signal": "news", "stage": news_loss_stage})
        forensic_totals["companies_with_careers_pages"] += bool(careers_pages)
        forensic_totals["companies_with_job_links"] += job_links_found > 0
        forensic_totals["total_job_links"] += job_links_found
        forensic_totals["total_job_candidates"] += job_candidates_found
        forensic_totals["total_normalized_jobs"] += len(jobs)
        forensic_totals["companies_with_news_pages"] += bool(news_pages)
        forensic_totals["total_article_links"] += article_links_found
        forensic_totals["total_article_candidates"] += article_candidates_found
        forensic_totals["total_normalized_news"] += len(news)
        forensic_rows.append({
            "organisation_number": org,
            "careers_page": "available" if careers_pages else "not_available",
            "jobs_data_loss_stage": jobs_loss_stage,
            "job_links_found": job_links_found,
            "job_candidates_found": job_candidates_found,
            "jobs_extracted": len(jobs),
            "normalized_jobs": jobs or "not_available",
            "news_page": "available" if news_pages else "not_available",
            "news_data_loss_stage": news_loss_stage,
            "article_links_found": article_links_found,
            "article_candidates_found": article_candidates_found,
            "news_extracted": len(news),
            "normalized_news": news or "not_available",
            "pages": pages,
        })
    forensics_path = Path(args.forensics)
    forensics_path.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(forensics_path, forensic_rows)
    forensic_report_path = forensics_path.with_name("website-forensic-summary.json")
    forensic_report_path.write_text(json.dumps({
        "companies": len(forensic_rows),
        **dict(forensic_totals),
        "candidate_rejection_reasons": dict(sorted(rejection_reasons.items())),
        "pages_with_visible_data_but_no_candidate": visible_data_without_candidate,
        "forensics_path": str(forensics_path),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    request_latencies = getattr(crawler, "signalpost_request_latency_ms", [])
    response_download_latencies = getattr(crawler, "signalpost_response_download_latency_ms", [])
    failure_attempt_latencies = getattr(crawler, "signalpost_failure_attempt_latency_ms", [])
    selected_orgs = {row["organisation_number"] for row in crawl_targets}
    company_completion_by_org = dict(getattr(crawler, "signalpost_company_completion_ms_by_org", {}))
    synthetic_completion_orgs = selected_orgs - set(company_completion_by_org)
    company_completion_latencies = list(company_completion_by_org.values())
    company_completion_latencies.extend([elapsed * 1000] * len(synthetic_completion_orgs))
    request_domains = getattr(crawler, "signalpost_request_domains", Counter())
    response_statuses = getattr(crawler, "signalpost_response_statuses", Counter())
    error_buckets = getattr(crawler, "signalpost_error_buckets", Counter())
    events_added = max(0, len(events) - len(events_before))
    terminal_orgs = selected_orgs & {str(event.get("organisation_number") or "") for event in events}
    # Step 2: structured page-outcome counts derived from the full event ledger.
    page_outcome_counts = _page_outcome_summary(events)
    report = {
        "input_sha256": input_hash,
        "profiles": len(rows),
        "website_seeds_selected": len(crawl_targets),
        "profiles_with_events": touched,
        "unique_events": len(dedupe),
        "events_added": events_added,
        "events_present_before_run": len(events_before),
        "synthetic_terminal_events": len(missing_seed_events),
        "website_statuses": dict(website_statuses),
        "identity_statuses": dict(identity_statuses),
        "identity_decisions": dict(identity_decisions),
        "page_outcome_counts": page_outcome_counts,
        "validation": {
            "crawl_page_events_accepted": len(events),
            "crawl_page_events_rejected": rejected_events,
            "website_values_accepted": website_values_accepted,
            "website_values_rejected": website_values_rejected,
            "quarantined_records": rejected_events + website_values_rejected,
            "quarantine_path": str(quarantine_path),
        },
        "published_social_profiles": published_socials,
        "elapsed_seconds": round(elapsed, 3),
        "run_started_at": run_started_at,
        "run_finished_at": run_finished_at,
        "events_per_second": round(events_added / elapsed, 3) if elapsed else None,
        "requests": stats.get("downloader/request_count", 0),
        "responses": stats.get("downloader/response_count", 0),
        "response_bytes": stats.get("downloader/response_bytes", 0),
        "retries": sum(value for key, value in stats.items() if str(key).startswith("retry/reason_count/")),
        "robots_forbidden": stats.get("robotstxt/forbidden", 0),
        "request_wall_latency_including_queue": latency_summary(request_latencies),
        "response_download_latency": latency_summary(response_download_latencies),
        "failure_attempt_wall_latency": latency_summary(failure_attempt_latencies),
        "company_completion_latency": latency_summary(company_completion_latencies),
        "company_completion_synthetic_upper_bounds": len(synthetic_completion_orgs),
        "request_domain_fairness": domain_request_summary(request_domains),
        "response_statuses": dict(sorted(response_statuses.items())),
        "error_buckets": dict(sorted(error_buckets.items())),
        "request_amplification_per_seed": round(stats.get("downloader/request_count", 0) / len(crawl_targets), 3) if crawl_targets else None,
        "terminal_coverage": round(len(terminal_orgs) / len(selected_orgs), 6) if selected_orgs else None,
        "crawl_complete": crawl_complete,
        "peak_rss_bytes": peak_rss_bytes(),
        "output_sha256": output_hash,
        "finish_reason": stats.get("finish_reason"),
        "resume_noop": bool(events_before and stats.get("downloader/request_count", 0) == 0),
        "concurrency": args.concurrency,
        "per_domain": args.per_domain,
        "jobdir": str(jobdir),
        "events_path": str(events_path),
        "forensics_path": str(forensics_path),
        "forensic_summary_path": str(forensic_report_path),
        "forensic_totals": dict(forensic_totals),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
