from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

import extruct
import trafilatura
from bs4 import BeautifulSoup

from .evidence import evidence, utc_now
from .website import _extraction_state, _jsonld_organisations, _registered_domain, _social_links, normalize_homepage, structured_social_links


# ---------------------------------------------------------------------------
# Step 2: Stable page-outcome taxonomy
# ---------------------------------------------------------------------------
# Every attempted page — successful or failed — must carry exactly one
# `failure_class` value from this vocabulary.  "success" is used for pages
# that returned usable HTML; all other values are deterministic failure labels
# that are machine-comparable without parsing the human-readable `error` field.
#
# Vocabulary is intentionally closed.  New causes must be added here rather
# than emitting ad-hoc strings.
# ---------------------------------------------------------------------------

PageOutcome = Literal[
    "success",                   # 2xx HTML, content extracted
    "not_found",                 # HTTP 404 or 410
    "http_error",                # other HTTP 4xx / 5xx
    "robots_blocked",            # robots.txt disallows this agent
    "timeout",                   # connection or read timeout
    "transport_error",           # DNS failure, connection refused, SSL error, etc.
    "unsupported_content",       # non-HTML content-type
    "oversized",                 # response body exceeds configured byte limit
    "redirect_outside_domain",   # followed redirect to a different registered domain
    "scheduler_error",           # pre-download rejection by the scheduler (e.g. no
                                 # crawl event produced before the run finished)
]

# Scrapy / urllib exception class names that map to timeout vs transport.
_TIMEOUT_NAMES = frozenset({
    "TimeoutError", "ConnectionTimeout", "ReadTimeout",
    "TCPTimedOutError", "ResponseNeverReceived",
    "socket.timeout", "TimeoutExpired",
})
_TRANSPORT_NAMES = frozenset({
    "ConnectionRefusedError", "ConnectionResetError", "BrokenPipeError",
    "DNSLookupError", "ConnectionLost", "TunnelError",
    "SSLError", "CertificateError", "OpenSSL.SSL.Error",
    "URLError", "gaierror",
})


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
    """Derive a stable `failure_class` value from structured failure signals.

    Precedence (highest → lowest):
      scheduler_error > robots_blocked > oversized > unsupported_content >
      redirect_outside_domain > HTTP status > exception name in error string >
      generic transport_error.

    For successful pages (status_code 2xx, no flags set) returns "success".
    """
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
    # No HTTP status available — classify by exception name in the error string.
    for exc_name in _TIMEOUT_NAMES:
        if exc_name.lower() in error.lower():
            return "timeout"
    for exc_name in _TRANSPORT_NAMES:
        if exc_name.lower() in error.lower():
            return "transport_error"
    # "HTTP NNN" emitted by Scrapy errback or urllib when the status code was
    # not passed as a numeric argument (e.g. in the free-form error string).
    http_match = re.search(r"\bHTTP\s+(\d{3})\b", error)
    if http_match:
        code = int(http_match.group(1))
        if code in {404, 410}:
            return "not_found"
        if 400 <= code < 600:
            return "http_error"
    if error:
        return "transport_error"
    return "success"


# ---------------------------------------------------------------------------
# Page ledger helpers — shared fields added to every event record in Step 2
# ---------------------------------------------------------------------------

def _page_outcome_fields(
    *,
    status_code: int,
    failure_class: PageOutcome,
    attempted_at: str,
    attempt_number: int = 1,
    http_status: int | None = None,
) -> dict[str, Any]:
    """Return the structured outcome fields every page ledger entry must carry."""
    return {
        "failure_class": failure_class,
        "http_status": http_status if http_status is not None else (status_code or None),
        "attempted_at": attempted_at,
        "attempt_number": attempt_number,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
    attempt_number: int = 1,
) -> dict[str, Any]:
    attempted_at = retrieved_at or utc_now()
    base = {
        "organisation_number": organisation_number,
        "requested_url": requested_url,
        "final_url": final_url,
        "status_code": status_code,
        "content_type": content_type,
        "page_kind": page_kind,
        "retrieved_at": attempted_at,
        "bytes": len(body),
        "content_sha256": hashlib.sha256(body).hexdigest(),
    }
    if status_code < 200 or status_code >= 300:
        fc = classify_failure_class(status_code=status_code)
        outcome = _page_outcome_fields(
            status_code=status_code, failure_class=fc,
            attempted_at=attempted_at, attempt_number=attempt_number,
            http_status=status_code,
        )
        # Preserve legacy `status` and `error` fields for backwards compatibility
        # while adding the new structured fields alongside them.
        legacy_status = "not_found" if status_code in {404, 410} else "source_error"
        return {**base, **outcome, "status": legacy_status, "error": f"HTTP {status_code}"}
    if "html" not in content_type.casefold():
        fc = classify_failure_class(unsupported_content=True)
        outcome = _page_outcome_fields(
            status_code=status_code, failure_class=fc,
            attempted_at=attempted_at, attempt_number=attempt_number,
            http_status=status_code,
        )
        return {**base, **outcome, "status": "source_error", "error": f"Unsupported content type: {content_type}"}
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
    outcome = _page_outcome_fields(
        status_code=status_code, failure_class="success",
        attempted_at=attempted_at, attempt_number=attempt_number,
        http_status=status_code,
    )
    event = {
        **base,
        **outcome,
        "status": "available",
        "title": title,
        "description": description,
        "main_text_excerpt": text[:5000],
        "identity_text_excerpt": identity_text,
        "social_links": _social_links(final_url, soup),
        "extraction_state": _extraction_state(text, soup),
    }
    if page_kind == "homepage":
        structured = extruct.extract(page_html, base_url=final_url, syntaxes=["json-ld", "microdata", "opengraph"])
        event["structured_organisations"] = _jsonld_organisations(structured)
        combined_social = event["social_links"] + structured_social_links(event["structured_organisations"])
        event["social_links"] = list({(item["platform"], item["url"]): item for item in combined_social}.values())
        event["registered_domain"] = _registered_domain(final_url)
    return event


def error_page_event(
    *,
    organisation_number: str,
    requested_url: str,
    page_kind: str,
    error: str,
    failure_class: PageOutcome | None = None,
    http_status: int | None = None,
    attempt_number: int = 1,
) -> dict[str, Any]:
    """Emit a terminal failure event for a page that could not be fetched.

    `failure_class` should be supplied by callers that know the structured cause
    (e.g. Scrapy errback, robots check).  When omitted it is derived from the
    `error` string and `http_status` via `classify_failure_class()` so that
    legacy call-sites continue to work without changes.
    """
    attempted_at = utc_now()
    derived_fc: PageOutcome = failure_class or classify_failure_class(
        status_code=http_status or 0,
        error=error,
    )
    outcome = _page_outcome_fields(
        status_code=http_status or 0,
        failure_class=derived_fc,
        attempted_at=attempted_at,
        attempt_number=attempt_number,
        http_status=http_status,
    )
    return {
        "organisation_number": organisation_number,
        "requested_url": requested_url,
        "final_url": requested_url,
        "status_code": http_status or 0,
        "content_type": "",
        "page_kind": page_kind,
        "retrieved_at": attempted_at,
        "bytes": 0,
        "content_sha256": hashlib.sha256(b"").hexdigest(),
        **outcome,
        # Legacy fields preserved for backwards compatibility.
        "status": "source_error",
        "error": error[:240],
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
                # Scheduler-level rejection: the crawler never issued an HTTP
                # request, so this is a scheduler_error, not robots_blocked.
                # robots_blocked is reserved for confirmed robots.txt denials
                # observed during an actual HTTP exchange.
                failure_class="scheduler_error",
            ))
    return missing


def _crawl_error_entry(event: dict[str, Any]) -> dict[str, Any]:
    """Build a structured crawl-error ledger entry from a terminal failed event.

    Preserves the legacy `error` string and adds `failure_class`, `http_status`,
    and `attempted_at` so consumers can aggregate by cause without parsing text.
    """
    return {
        "url": event.get("final_url") or event.get("requested_url"),
        "error": event.get("error") or "",           # legacy free-form string
        "failure_class": event.get("failure_class") or classify_failure_class(
            status_code=event.get("status_code") or 0,
            error=event.get("error") or "",
        ),
        "http_status": event.get("http_status") or event.get("status_code") or None,
        "attempted_at": event.get("attempted_at") or event.get("retrieved_at"),
    }


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
    for item in sorted(available, key=lambda value: (value.get("page_kind") != "homepage", value.get("final_url") or "")):
        page = {
            "url": item.get("final_url"),
            "title": item.get("title") or "",
            "main_text_excerpt": item.get("main_text_excerpt") or "",
            "identity_text_excerpt": item.get("identity_text_excerpt") or "",
            "content_sha256": item.get("content_sha256"),
            # Step 2: carry structured outcome fields into the page ledger entry.
            "failure_class": item.get("failure_class", "success"),
            "http_status": item.get("http_status") or item.get("status_code"),
            "attempted_at": item.get("attempted_at") or item.get("retrieved_at"),
            "attempt_number": item.get("attempt_number", 1),
        }
        unique_pages[item.get("final_url")] = page
        for link in item.get("social_links") or []:
            social[(link.get("platform"), link.get("url"))] = link
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
        # Step 2: structured crawl-error entries with failure_class, http_status,
        # attempted_at instead of just {url, error}.  Legacy `error` field is
        # preserved inside each entry for backwards compatibility.
        "crawl_errors": [
            _crawl_error_entry(item)
            for item in events
            if item.get("status") != "available"
        ],
        "scheduler": "scrapy_resumable_v1",
    }
    return evidence(
        "website",
        "available",
        "registry_linked_company_website_scrapy",
        homepage.get("final_url"),
        value=value,
        note="Company-controlled claim layer; not an official registry fact",
        retrieved_at=homepage.get("retrieved_at"),
        content_sha256=homepage.get("content_sha256"),
    )
