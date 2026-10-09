from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Literal

import extruct
import trafilatura
from bs4 import BeautifulSoup

from .evidence import evidence, utc_now
from .page_signals import extract_page_signals, feed_record, looks_like_feed, title_span
from .snapshot_store import save_snapshot
from .website import _extraction_state, _jsonld_organisations, _registered_domain, normalize_homepage


PageOutcome = Literal[
    "success",
    "not_found",
    "http_error",
    "robots_blocked",
    "timeout",
    "transport_error",
    "unsupported_content",
    "oversized",
    "redirect_outside_domain",
    "scheduler_error",
]

_TIMEOUT_NAMES = frozenset({
    "TimeoutError", "ConnectionTimeout", "ReadTimeout", "TCPTimedOutError",
    "ResponseNeverReceived", "socket.timeout", "TimeoutExpired",
})
_TRANSPORT_NAMES = frozenset({
    "ConnectionRefusedError", "ConnectionResetError", "BrokenPipeError",
    "DNSLookupError", "ConnectionLost", "TunnelError", "SSLError",
    "CertificateError", "OpenSSL.SSL.Error", "URLError", "gaierror",
})


def emit_crawl_event(event_name: str, **payload: Any) -> None:
    trace_path = os.environ.get("SIGNALPOST_CRAWL_TRACE_PATH")
    if not trace_path:
        trace_path = str(Path(os.environ.get("SIGNALPOST_OUTPUT_DIR", "out/latest-run")) / "crawl-trace.jsonl")
    trace_file = Path(trace_path)
    trace_file.parent.mkdir(parents=True, exist_ok=True)
    row = {"event": event_name, "timestamp": utc_now(), **payload}
    with trace_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def classify_failure_class(
    *,
    status_code: int = 0,
    error: str = "",
    robots_blocked: bool = False,
    oversized: bool = False,
    unsupported_content: bool = False,
    redirect_outside_domain: bool = False,
    scheduler_error: bool = False,
) -> PageOutcome:
    if scheduler_error:
        return "scheduler_error"
    if robots_blocked:
        return "robots_blocked"
    if oversized:
        return "oversized"
    if unsupported_content:
        return "unsupported_content"
    if redirect_outside_domain:
        return "redirect_outside_domain"
    if status_code in {404, 410}:
        return "not_found"
    if 400 <= status_code < 600:
        return "http_error"
    if 200 <= status_code < 300 and not error:
        return "success"
    if any(name.casefold() in error.casefold() for name in _TIMEOUT_NAMES):
        return "timeout"
    if any(name.casefold() in error.casefold() for name in _TRANSPORT_NAMES):
        return "transport_error"
    http_match = re.search(r"\bHTTP\s+(\d{3})\b", error, re.IGNORECASE)
    if http_match:
        code = int(http_match.group(1))
        if code in {404, 410}:
            return "not_found"
        if 400 <= code < 600:
            return "http_error"
    return "transport_error" if error else "success"


def extract_page_event(
    *,
    organisation_number: str,
    requested_url: str,
    final_url: str,
    status_code: int,
    content_type: str,
    body: bytes,
    page_kind: str,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    base = {
        "organisation_number": organisation_number,
        "requested_url": requested_url,
        "final_url": final_url,
        "status_code": status_code,
        "content_type": content_type,
        "page_kind": page_kind,
        "retrieved_at": retrieved_at or utc_now(),
        "bytes": len(body),
        "content_sha256": hashlib.sha256(body).hexdigest(),
    }
    if status_code < 200 or status_code >= 300:
        return {**base, "status": "not_found" if status_code in {404, 410} else "source_error", "error": f"HTTP {status_code}"}
    if "html" not in content_type.casefold():
        return {**base, "status": "source_error", "error": f"Unsupported content type: {content_type}"}
    page_html = body.decode("utf-8", errors="replace")
    soup = BeautifulSoup(page_html, "lxml")
    text = trafilatura.extract(page_html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
    title = soup.title.get_text(" ", strip=True)[:500] if soup.title else ""
    description_tag = soup.select_one('meta[name="description"], meta[property="og:description"]')
    description = str(description_tag.get("content") or "").strip()[:2000] if description_tag else ""
    identity_nodes = soup.select(
        'footer, address, [itemprop="legalName"], [itemprop="address"], '
        '[itemprop="telephone"], [itemprop="email"]'
    )
    identity_text = " ".join(node.get_text(" ", strip=True) for node in identity_nodes)
    identity_text = " ".join(identity_text.split())[:3000]
    signals = extract_page_signals(page_html, final_url)
    from .website import _extract_contact_signals
    from .website_forensics import build_page_forensics

    contact_signals = _extract_contact_signals(page_html, final_url)
    forensics = build_page_forensics(
        organisation_number=organisation_number,
        requested_url=requested_url,
        final_url=final_url,
        http_status=status_code,
        page_kind=page_kind,
        html=page_html,
        extraction_state=_extraction_state(text, soup),
        signals=signals,
    )
    event = {
        **base,
        "status": "available",
        "title": title,
        "description": description,
        "main_text_excerpt": text[:5000],
        "identity_text_excerpt": identity_text,
        "social_links": signals["social_links"],
        "signals": signals,
        "forensics": forensics,
        "contact_signals": {
            field: contact_signals.get(field, "not_available")
            for field in ("phones", "emails", "addresses", "locations")
        },
        "snapshot_path": save_snapshot(body, "html"),
        "extraction_state": _extraction_state(text, soup),
    }
    if forensics["careers_page_detected"]:
        emit_crawl_event(
            "careers_page_detected", organisation_number=organisation_number,
            url=final_url, status="available", job_links_found=forensics["job_links_found"],
        )
    if forensics["job_links_found"]:
        emit_crawl_event(
            "jobs_links_detected", organisation_number=organisation_number,
            url=final_url, count=forensics["job_links_found"],
        )
    for candidate in forensics["job_candidates"]:
        emit_crawl_event(
            "job_candidate_detected", organisation_number=organisation_number,
            url=final_url, candidate_url=candidate.get("url"),
            evidence_kind=candidate.get("evidence_kind"),
        )
    for rejection in forensics["job_candidate_rejections"]:
        emit_crawl_event(
            "job_candidate_rejected", organisation_number=organisation_number,
            url=final_url, candidate_url=rejection.get("url"),
            reason=rejection.get("rejection_reason"),
        )
    if forensics["news_page_detected"]:
        emit_crawl_event(
            "news_page_detected", organisation_number=organisation_number,
            url=final_url, status="available", article_links_found=forensics["article_links_found"],
        )
    if forensics["article_links_found"]:
        emit_crawl_event(
            "news_links_detected", organisation_number=organisation_number,
            url=final_url, count=forensics["article_links_found"],
        )
    for candidate in forensics["article_candidates"]:
        emit_crawl_event(
            "news_candidate_detected", organisation_number=organisation_number,
            url=final_url, candidate_url=candidate.get("url"),
            evidence_kind=candidate.get("evidence_kind"),
        )
    for rejection in forensics["article_candidate_rejections"]:
        emit_crawl_event(
            "news_candidate_rejected", organisation_number=organisation_number,
            url=final_url, candidate_url=rejection.get("url"),
            reason=rejection.get("rejection_reason"),
        )
    if page_kind == "homepage":
        structured = extruct.extract(page_html, base_url=final_url, syntaxes=["json-ld", "microdata", "opengraph"])
        event["structured_organisations"] = _jsonld_organisations(structured)
        event["registered_domain"] = _registered_domain(final_url)
        event["title_span"] = title_span(page_html)
    return event


def extract_feed_event(
    *,
    organisation_number: str,
    requested_url: str,
    final_url: str,
    status_code: int,
    content_type: str,
    body: bytes,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    stamp = retrieved_at or utc_now()
    base = {
        "organisation_number": organisation_number,
        "requested_url": requested_url,
        "final_url": final_url,
        "status_code": status_code,
        "content_type": content_type,
        "page_kind": "feed",
        "retrieved_at": stamp,
        "bytes": len(body),
        "content_sha256": hashlib.sha256(body).hexdigest(),
    }
    if status_code < 200 or status_code >= 300 or not looks_like_feed(body):
        return {**base, "status": "source_error", "error": f"HTTP {status_code}" if status_code >= 300 or status_code < 200 else "Not an RSS/Atom feed"}
    return {**base, "status": "available", "feed": feed_record(final_url, body, stamp)}


def error_page_event(
    *,
    organisation_number: str,
    requested_url: str,
    page_kind: str,
    error: str,
    failure_class: str | None = None,
    http_status: int | None = None,
    attempt_number: int = 1,
) -> dict[str, Any]:
    outcome = classify_failure_class(status_code=http_status or 0, error=error)
    organization = str(organisation_number)
    page_forensics = {
        "organisation_number": organization,
        "url": requested_url,
        "final_url": requested_url,
        "http_status": http_status or 0,
        "status": "source_error",
        "title": "not_available",
        "page_kind": page_kind,
        "full_cleaned_page_text": "not_available",
        "all_discovered_links": [],
        "job_like_links": [],
        "article_like_links": [],
        "headings": [],
        "relevant_meta_tags": [],
        "jsonld_structured_data": [],
        "relevant_jsonld": [],
        "relevant_html_fragments": [],
        "job_candidates": [],
        "job_candidate_acceptance_reasons": [],
        "job_candidate_rejections": [],
        "article_candidates": [],
        "article_candidate_acceptance_reasons": [],
        "article_candidate_rejections": [],
        "dates_found": [],
        "normalized_jobs": "not_available",
        "normalized_news": "not_available",
        "careers_page_detected": page_kind == "careers",
        "job_links_found": 0,
        "job_candidates_found": 0,
        "jobs_extracted": 0,
        "news_page_detected": page_kind == "news",
        "article_links_found": 0,
        "article_candidates_found": 0,
        "news_extracted": 0,
        "extraction_state": "failed",
        "errors": [error[:240]],
        "parser_outputs": {"jobs": "not_available", "news": "not_available"},
    }
    return {
        "organisation_number": organisation_number,
        "requested_url": requested_url,
        "final_url": requested_url,
        "status_code": http_status or 0,
        "content_type": "",
        "page_kind": page_kind,
        "retrieved_at": utc_now(),
        "bytes": 0,
        "content_sha256": hashlib.sha256(b"").hexdigest(),
        "status": "source_error",
        "error": error[:240],
        "failure_class": failure_class or outcome,
        "http_status": http_status,
        "attempt_number": attempt_number,
        "forensics": page_forensics,
    }


def missing_seed_error_events(profiles: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every crawlable seed a terminal ledger entry, including robots rejections."""
    event_orgs = {str(event.get("organisation_number") or "") for event in events}
    missing = []
    for profile in profiles:
        organisation_number = str(profile.get("organisation_number") or "")
        if not profile.get("website") or organisation_number in event_orgs:
            continue
        requested_url = normalize_homepage(str(profile["website"]))
        if requested_url:
            missing.append(error_page_event(
                organisation_number=organisation_number,
                requested_url=requested_url,
                page_kind="homepage",
                error="No page event emitted; request was rejected before download (for example by robots.txt).",
            ))
    return missing


def merge_profile_events(profile: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    homepage_events = [item for item in events if item.get("page_kind") == "homepage"]
    homepage_events.sort(key=lambda item: item.get("retrieved_at") or "")
    homepage = homepage_events[-1] if homepage_events else None
    if not homepage:
        return evidence(
            "website",
            "not_found",
            "registry_linked_company_website_scrapy",
            str(profile.get("website") or "https://data.brreg.no/enhetsregisteret/api/enheter"),
            note="No homepage crawl event was produced",
        )
    if homepage.get("status") != "available":
        return evidence(
            "website",
            homepage.get("status", "source_error"),
            "registry_linked_company_website_scrapy",
            homepage.get("final_url") or homepage.get("requested_url"),
            note=homepage.get("error") or "Homepage fetch failed",
            retrieved_at=homepage.get("retrieved_at"),
            content_sha256=homepage.get("content_sha256"),
        )
    available = [item for item in events if item.get("status") == "available"]
    unique_pages = {}
    social = {}
    feeds = []
    signal_values: dict[str, list[Any]] = {
        "jobs": [], "news": [], "phones": [], "emails": [], "addresses": [], "locations": [],
    }
    for item in sorted(available, key=lambda value: (value.get("page_kind") != "homepage", value.get("final_url") or "")):
        if item.get("page_kind") == "feed":
            feeds.append(item["feed"])
            continue
        page_signals = item.get("signals") or {}
        signal_values["jobs"].extend(page_signals.get("jobs") or [])
        signal_values["news"].extend(page_signals.get("articles") or [])
        for field in ("phones", "emails", "addresses", "locations"):
            signal = (item.get("contact_signals") or {}).get(field)
            if isinstance(signal, list):
                signal_values[field].extend(signal)
        page_signal_record = dict(item.get("signals") or {})
        page_signal_record.pop("social_links", None)
        page = {
            "requested_url": item.get("requested_url"),
            "url": item.get("final_url"),
            "final_url": item.get("final_url"),
            "title": item.get("title") or "",
            "main_text_excerpt": item.get("main_text_excerpt") or "",
            "text_excerpt": item.get("main_text_excerpt") or "not_available",
            "identity_text_excerpt": item.get("identity_text_excerpt") or "",
            "content_sha256": item.get("content_sha256"),
            "retrieved_at": item.get("retrieved_at"),
            "status": item.get("status_code") or "not_available",
            "duration_seconds": item.get("duration_seconds", "not_available"),
            "page_kind": item.get("page_kind") or "not_available",
            "extraction_state": item.get("extraction_state") or "not_available",
            "errors": [],
            "snapshot_path": item.get("snapshot_path"),
            "signals": page_signal_record,
        }
        unique_pages[item.get("final_url") or item.get("requested_url")] = page
        for link in item.get("social_links") or []:
            social[(link.get("platform"), link.get("url"))] = link
    profile_website = ((profile.get("evidence") or {}).get("website") or {})
    previous_value = profile_website.get("value") if isinstance(profile_website.get("value"), dict) else {}
    value = {
        "requested_url": homepage.get("requested_url"),
        "final_url": homepage.get("final_url"),
        "registered_domain": homepage.get("registered_domain"),
        "title": homepage.get("title") or "",
        "description": homepage.get("description") or "",
        "main_text_excerpt": homepage.get("main_text_excerpt") or "",
        "identity_text_excerpt": homepage.get("identity_text_excerpt") or "",
        "social_links": list(social.values()),
        "structured_organisations": homepage.get("structured_organisations") or [],
        "content_sha256": homepage.get("content_sha256"),
        "extraction_state": homepage.get("extraction_state"),
        "pages": list(unique_pages.values()),
        "feeds": feeds,
        **{
            field: _unique_signal_values(values) or "not_available"
            for field, values in signal_values.items()
        },
        "snapshot_path": homepage.get("snapshot_path"),
        "title_span": homepage.get("title_span"),
        "crawl_errors": [
            {"url": item.get("final_url") or item.get("requested_url"), "error": item.get("error")}
            for item in events
            if item.get("status") != "available"
        ],
        "scheduler": "scrapy_resumable_v1",
        "discovery": previous_value.get("discovery") or {
            "method": "direct", "providers_used": [], "candidates": [], "cost_usd": 0.0,
        },
        "cost_usd": previous_value.get("cost_usd", 0.0),
    }
    from .website import normalize_website_value

    value = normalize_website_value(value)
    jobs_count = len(value["jobs"]) if isinstance(value["jobs"], list) else 0
    news_count = len(value["news"]) if isinstance(value["news"], list) else 0
    emit_crawl_event("jobs_extracted", organisation_number=str(profile.get("organisation_number") or ""), url=homepage.get("final_url"), count=jobs_count)
    emit_crawl_event("news_extracted", organisation_number=str(profile.get("organisation_number") or ""), url=homepage.get("final_url"), count=news_count)
    record = evidence(
        "website",
        "available",
        "registry_linked_company_website_scrapy",
        homepage.get("final_url"),
        value=value,
        note="Company-controlled claim layer; not an official registry fact",
        retrieved_at=homepage.get("retrieved_at"),
        content_sha256=homepage.get("content_sha256"),
    )
    record["snapshot_path"] = homepage.get("snapshot_path")
    record["extraction_method"] = "company_page_html"
    record["discovery"] = value["discovery"]
    return record


def _unique_signal_values(values: list[Any]) -> list[Any]:
    unique = []
    seen = set()
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique