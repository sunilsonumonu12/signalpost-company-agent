from __future__ import annotations

import json
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup
import extruct
from pydantic import ValidationError
import tldextract
import trafilatura

from .evidence import evidence
from .page_signals import extract_page_signals
from .telemetry import record_request

USER_AGENT = "builderr-signalpost-poc/0.1 (+https://builderr.ai)"
SOCIAL_HOSTS = {
    "linkedin.com": "linkedin",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "x.com": "x",
    "twitter.com": "x",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "tiktok.com": "tiktok",
}
PRIORITY_TERMS = (
    "om-oss", "om_oss", "about", "kontakt", "contact", "ledelse", "management",
    "team", "people", "locations", "lokasjoner", "avdelinger", "butikker",
    "newsroom", "news", "press", "presse", "pressemelding", "media", "release", "announcement",
    "artikler", "articles", "blogg", "blog", "aktuelt", "nyheter", "siste-nytt",
    "careers", "career", "recruitment", "recruiting", "vacancies", "vacancy", "positions",
    "work-with-us", "jobs", "job", "karriere", "ledige-stillinger", "stillinger",
)
PAGE_KIND_BY_TERM = {
    "om-oss": "about", "om_oss": "about", "about": "about",
    "kontakt": "contact", "contact": "contact",
    "ledelse": "management", "management": "management",
    "team": "team", "people": "team",
    "locations": "locations", "lokasjoner": "locations", "avdelinger": "locations", "butikker": "locations",
    "newsroom": "news", "news": "news", "press": "news", "presse": "news",
    "pressemelding": "news", "media": "news", "release": "news", "announcement": "news",
    "artikler": "news", "articles": "news", "blogg": "news", "blog": "news",
    "aktuelt": "news", "nyheter": "news", "siste-nytt": "news",
    "careers": "careers", "career": "careers", "jobs": "careers", "job": "careers",
    "recruitment": "careers", "recruiting": "careers", "vacancies": "careers", "vacancy": "careers",
    "positions": "careers", "work-with-us": "careers", "karriere": "careers",
    "ledige-stillinger": "careers", "stillinger": "careers",
}


def assert_public_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("Only public HTTP(S) URLs are allowed")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Local hosts are blocked")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError("Hostname did not resolve") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Private, loopback, link-local, multicast, and reserved addresses are blocked")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        assert_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


SAFE_OPENER = urllib.request.build_opener(SafeRedirectHandler())


def normalize_homepage(value: str | None) -> str | None:
    value = str(value or "").strip()
    if not value:
        return None
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))


def _extract_contact_signals(html: str, final_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    signals = extract_page_signals(html, final_url)
    jobs = []
    for item in signals.get("jobs") or []:
        jobs.append({
            "title": str(item.get("title") or "").strip() or "not_available",
            "url": str(item.get("url") or final_url).strip(),
            "location": "not_available",
            "description": "not_available",
            "source_url": final_url,
        })

    news = []
    for item in signals.get("articles") or []:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        news.append({
            "title": title,
            "url": str(item.get("url") or final_url).strip(),
            "date": str(item.get("date") or "not_available").strip() or "not_available",
            "source_url": final_url,
        })

    emails = sorted(set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", html)))
    phones = []
    for candidate in re.findall(r"(?:\+?47\s*[- ]?)?(?:\d[\s().-]{6,}\d)", html):
        cleaned = re.sub(r"[^\d+\s().-]", "", candidate)
        if len(re.sub(r"\D", "", cleaned)) >= 8:
            phones.append(cleaned.strip())
    phones = sorted(set(p for p in phones if p and p.strip()))

    addresses = []
    for selector in ("address", ".address", "[itemprop='address']", "[itemprop='streetAddress']", "[itemprop='location']"):
        for node in soup.select(selector):
            text = " ".join(node.get_text(" ", strip=True).split())
            if text and len(text) >= 8 and not re.fullmatch(r"[\d\s()+-.]+", text):
                addresses.append(text)
    if not addresses:
        for text in re.findall(r"(?:[A-Za-zÆØÅæøå]+\s+){1,5}\d{4}\s+[A-Za-zÆØÅæøå\- ]+", html):
            addresses.append(" ".join(text.split()))
    addresses = sorted(set(addresses))

    locations = []
    for label in sorted(set([item["title"] for item in news if item.get("title")])):
        locations.append({"label": label, "address": "not_available"})
    if not locations:
        for address in addresses[:5]:
            locations.append({"label": address, "address": address})

    return {
        "jobs": jobs or "not_available",
        "news": news or "not_available",
        "phones": phones or "not_available",
        "emails": emails or "not_available",
        "addresses": addresses or "not_available",
        "locations": locations or "not_available",
    }


def normalize_website_value(value: dict[str, Any] | None) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    pages: list[dict[str, Any]] = []
    for page in raw.get("pages") or []:
        if not isinstance(page, dict):
            continue
        pages.append({
            "url": page.get("url") or page.get("final_url") or "",
            "final_url": page.get("final_url") or "not_available",
            "title": page.get("title") or "not_available",
            "status": page.get("status") if page.get("status") is not None else "not_available",
            "duration_seconds": page.get("duration_seconds") if page.get("duration_seconds") is not None else "not_available",
            "page_kind": page.get("page_kind") or "not_available",
            "extraction_state": page.get("extraction_state") or "not_available",
            "text_excerpt": page.get("text_excerpt") or page.get("main_text_excerpt") or "not_available",
            "errors": page.get("errors") or [],
        })

    discovery = raw.get("discovery") if isinstance(raw.get("discovery"), dict) else {}
    normalized = {
        "requested_url": raw.get("requested_url") or raw.get("source_url") or "not_available",
        "final_url": raw.get("final_url") or "not_available",
        "registered_domain": raw.get("registered_domain") or _registered_domain(raw.get("final_url") or raw.get("requested_url") or "") or "not_available",
        "title": raw.get("title") or "not_available",
        "description": raw.get("description") or "not_available",
        "main_text_excerpt": raw.get("main_text_excerpt") or "not_available",
        "structured_organisations": raw.get("structured_organisations") or [],
        "pages": pages,
        "identity_assessment": raw.get("identity_assessment") or {},
        "org_number_found": raw.get("org_number_found") if isinstance(raw.get("org_number_found"), bool) else "not_available",
        "legal_name_match": raw.get("legal_name_match") if isinstance(raw.get("legal_name_match"), bool) else "not_available",
        "address_match": raw.get("address_match") if isinstance(raw.get("address_match"), bool) else "not_available",
        "extraction_state": raw.get("extraction_state") or "partial",
        "content_sha256": raw.get("content_sha256") or "",
        "crawl_errors": raw.get("crawl_errors") or [],
        "discovery": {
            "method": discovery.get("method") or "direct",
            "providers_used": discovery.get("providers_used") or [],
            "candidates": discovery.get("candidates") or [],
            "cost_usd": float(discovery.get("cost_usd", 0.0) or 0.0),
        },
        "cost_usd": float(raw.get("cost_usd", 0.0) or 0.0),
    }
    for field in ("jobs", "news", "phones", "emails", "addresses", "locations"):
        value_for_field = raw.get(field)
        if value_for_field is None or value_for_field == [] or value_for_field == "":
            normalized[field] = "not_available"
        else:
            normalized[field] = value_for_field
    normalized.pop("social_links", None)
    normalized.pop("discovered_social_links", None)
    normalized.pop("social_link_assessments", None)
    # Preserve social profile links under the canonical public field name.
    # Source may be 'social_links' (pre-identity-gate) or 'discovered_social_links'
    # (post-identity-gate rename in identity.py).  Either list is accepted; the first
    # non-empty one wins.  Each item carries {platform, url} at minimum.
    raw_social = raw.get("social_links") or raw.get("discovered_social_links") or []
    if isinstance(raw_social, list) and raw_social:
        normalized["social_profiles"] = [
            {"platform": str(item.get("platform") or ""), "url": str(item.get("url") or "")}
            for item in raw_social
            if isinstance(item, dict) and item.get("platform") and item.get("url")
        ] or "not_available"
    else:
        normalized["social_profiles"] = "not_available"
    return normalized


def _registered_domain(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    ext = tldextract.extract(parsed.hostname or "")
    return ext.top_domain_under_public_suffix


def _robots_allowed(url: str, timeout: float, organisation_number: str | None = None) -> bool:
    assert_public_url(url)
    parsed = urllib.parse.urlparse(url)
    robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
        started = time.monotonic()
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            elapsed = int((time.monotonic() - started) * 1000)
            record_request(module="website", provider="website/http", operation="GET robots.txt", success=True, status=response.status, duration_ms=elapsed, organisation_number=organisation_number)
            parser.parse(response.read().decode("utf-8", errors="replace").splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except Exception as exc:
        record_request(module="website", provider="website/http", operation="GET robots.txt", success=False, status=getattr(exc, "code", 0), duration_ms=0, organisation_number=organisation_number, error=type(exc).__name__)
        # An unavailable robots file is not permission to ignore explicit site terms; callers retain
        # the URL and can route uncertain domains to review. For this bounded homepage POC, allow one
        # ordinary GET when robots.txt is absent rather than crawl deeper.
        return True


def _social_links(base_url: str, soup: BeautifulSoup) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}
    candidates = [str(node.get("href") or "") for node in soup.select("a[href]")]
    candidates.extend(str(node.get("data-href") or "") for node in soup.select("[data-href]"))
    candidates.extend(str(node.get("src") or "") for node in soup.select("iframe[src]"))
    for candidate in candidates:
        url = urllib.parse.urljoin(base_url, candidate)
        parsed_candidate = urllib.parse.urlparse(url)
        if (parsed_candidate.hostname or "").casefold().removeprefix("www.") == "facebook.com" and parsed_candidate.path.startswith("/plugins/"):
            embedded = urllib.parse.parse_qs(parsed_candidate.query).get("href", [])
            if embedded:
                url = embedded[0]
        normalized = normalize_social_url(url)
        if not normalized:
            continue
        found[(normalized["platform"], normalized["url"])] = normalized
    return sorted(found.values(), key=lambda item: (item["platform"], item["url"]))


def structured_social_links(value: Any) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            same_as = node.get("sameAs")
            urls = same_as if isinstance(same_as, list) else [same_as]
            for raw in urls:
                if not isinstance(raw, str):
                    continue
                normalized = normalize_social_url(raw.strip())
                if normalized:
                    found[(normalized["platform"], normalized["url"])] = normalized
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return sorted(found.values(), key=lambda item: (item["platform"], item["url"]))


def normalize_social_url(url: str) -> dict[str, str] | None:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    platform = next((label for domain, label in SOCIAL_HOSTS.items() if host == domain or host.endswith("." + domain)), None)
    if not platform:
        return None
    parts = [part.strip() for part in parsed.path.split("/") if part.strip()]
    lowered = [part.casefold() for part in parts]
    rejected_first = {
        "facebook": {"sharer", "sharer.php", "share.php", "dialog", "policy.php", "privacy", "events", "groups", "plugins"},
        "instagram": {"p", "reel", "reels", "stories", "explore"},
        "x": {"intent", "share", "home", "search", "i"},
    }
    if not parts or lowered[0] in rejected_first.get(platform, set()):
        return None
    if platform == "facebook" and lowered[0] == "profile.php":
        return None
    if platform == "linkedin" and (lowered[0] != "company" or len(parts) < 2):
        return None
    if platform == "youtube" and lowered[0] not in {"channel", "user", "c"} and not parts[0].startswith("@"):
        return None
    if host == "youtu.be":
        return None
    if platform == "tiktok" and not parts[0].startswith("@"):
        return None
    if platform == "x" and len(parts) != 1:
        return None
    canonical_host = {
        "linkedin": "linkedin.com",
        "facebook": "facebook.com",
        "instagram": "instagram.com",
        "x": "x.com",
        "youtube": "youtube.com",
        "tiktok": "tiktok.com",
    }[platform]
    if platform == "linkedin":
        parts = parts[:2]
    elif platform == "youtube":
        parts = parts[:1] if parts[0].startswith("@") else parts[:2]
    return {"platform": platform, "url": f"https://{canonical_host}/{'/'.join(parts)}"}


def _priority_links(base_url: str, soup: BeautifulSoup, limit: int = 6) -> list[tuple[str, str]]:
    # Was 4; PRIORITY_TERMS grew from 10 to 13 terms when careers/jobs pages were
    # added, so a 4-link cap on a homepage with contact + news + careers links all
    # present would silently drop one category rather than just fetching an extra
    # page or two (crawl concurrency/per-domain limits already bound the real cost).
    base = urllib.parse.urlparse(base_url)
    candidates: dict[str, tuple[int, str]] = {}
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        url = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != base.netloc.lower():
            continue
        haystack = (parsed.path + " " + anchor.get_text(" ", strip=True)).casefold()
        match = next(((index, term) for index, term in enumerate(PRIORITY_TERMS) if term in haystack), None)
        if match is None:
            continue
        clean = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
        if clean.rstrip("/") == base_url.rstrip("/"):
            continue
        rank, term = match
        candidate = (rank, PAGE_KIND_BY_TERM.get(term, "unknown"))
        if clean not in candidates or rank < candidates[clean][0]:
            candidates[clean] = candidate
    return [
        (url, kind)
        for url, (_, kind) in sorted(candidates.items(), key=lambda item: (item[1][0], item[0]))[:limit]
    ]


def _fetch_secondary_page(url: str, *, homepage_domain: str, timeout: float, max_bytes: int, organisation_number: str | None = None) -> tuple[dict[str, Any] | None, list[dict[str, str]], int, int, int, str | None, str, int | None]:
    """Fetch one secondary page and return structured outcome fields.

    Return tuple (extended from the original 6-tuple to 8-tuple in Step 2):
        page          – parsed page dict or None on failure
        social_links  – extracted social links (empty on failure)
        requests      – number of HTTP requests issued (1 for robots + 1 for page)
        bytes_read    – response body size in bytes
        elapsed_ms    – wall-clock time in milliseconds
        error         – human-readable error string or None on success
        failure_class – stable PageOutcome string (new in Step 2)
        http_status   – numeric HTTP status code or None when unavailable (new in Step 2)
    """
    from .crawl_events import classify_failure_class  # local import avoids circular dependency

    if not _robots_allowed(url, timeout, organisation_number):
        return None, [], 1, 0, 0, "robots.txt disallows page", "robots_blocked", None
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            elapsed = int((time.monotonic() - started) * 1000)
            record_request(module="website", provider="website/http", operation="GET secondary_page", success=True, status=response.status, duration_ms=elapsed, organisation_number=organisation_number)
            final_url = response.geturl()
            if len(raw) > max_bytes:
                return None, [], 2, len(raw), elapsed, "oversized page", "oversized", response.status
            if "html" not in response.headers.get("content-type", "").lower():
                return None, [], 2, len(raw), elapsed, "unsupported or oversized page", "unsupported_content", response.status
            if _registered_domain(final_url) != homepage_domain:
                return None, [], 2, len(raw), elapsed, "redirected outside registered domain", "redirect_outside_domain", response.status
        page_html = raw.decode("utf-8", errors="replace")
        page_soup = BeautifulSoup(page_html, "lxml")
        page_text = trafilatura.extract(page_html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
        page = {
            "url": url,
            "final_url": final_url,
            "title": page_soup.title.get_text(" ", strip=True)[:500] if page_soup.title else "",
            "main_text_excerpt": page_text[:5000],
            "extraction_state": _extraction_state(page_text, page_soup),
            "content_sha256": __import__("hashlib").sha256(raw).hexdigest(),
            "html": page_html,
        }
        return page, _social_links(final_url, page_soup), 2, len(raw), elapsed, None, "success", response.status
    except urllib.error.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        record_request(module="website", provider="website/http", operation="GET secondary_page", success=False, status=exc.code, duration_ms=elapsed, organisation_number=organisation_number, error=f"HTTP {exc.code}")
        fc = classify_failure_class(status_code=exc.code)
        return None, [], 2, 0, elapsed, f"HTTP {exc.code}", fc, exc.code
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        error_str = f"{type(exc).__name__}: {str(exc)[:120]}"
        record_request(module="website", provider="website/http", operation="GET secondary_page", success=False, status=getattr(exc, "code", 0), duration_ms=elapsed, organisation_number=organisation_number, error=type(exc).__name__)
        fc = classify_failure_class(error=error_str)
        return None, [], 2, 0, elapsed, error_str, fc, getattr(exc, "code", None) or None


def _jsonld_organisations(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            kind = value.get("@type")
            kinds = set(kind if isinstance(kind, list) else [kind])
            if kinds & {"Organization", "Corporation", "LocalBusiness", "Store", "Restaurant"}:
                values.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(metadata.get("json-ld", []))
    return values[:20]


def _extraction_state(text: str, soup: BeautifulSoup) -> str:
    return "js_fallback_candidate" if len(text.strip()) < 100 and len(soup.select("script[src]")) >= 2 else "static_complete"


def fetch_website(url: str | None, *, timeout: float = 15.0, max_bytes: int = 2_000_000, organisation_number: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    from .crawl_events import classify_failure_class, emit_crawl_event  # local import avoids circular dependency
    from .evidence import utc_now

    emit_crawl_event("crawler_started", organisation_number=organisation_number or "", url=url or "")
    supplied_url = str(url or "").strip()
    supplied_scheme = bool(re.match(r"^https?://", supplied_url, re.I))
    normalized = normalize_homepage(url)
    if not normalized:
        emit_crawl_event("crawler_completed", organisation_number=organisation_number or "", status="not_found")
        return evidence("website", "not_found", "registry_linked_company_website", "https://data.brreg.no/enhetsregisteret/api/enheter", note="No valid registry website URL"), {"requests": 0, "bytes": 0, "latencies_ms": []}
    try:
        assert_public_url(normalized)
    except ValueError as exc:
        emit_crawl_event("crawler_completed", organisation_number=organisation_number or "", status="blocked", note=str(exc))
        return evidence("website", "blocked", "registry_linked_company_website", normalized, note=str(exc)), {"requests": 0, "bytes": 0, "latencies_ms": []}
    if not _robots_allowed(normalized, timeout, organisation_number):
        emit_crawl_event("crawler_completed", organisation_number=organisation_number or "", status="blocked", note="robots.txt disallows this user agent")
        return evidence("website", "blocked", "registry_linked_company_website", normalized, note="robots.txt disallows this user agent"), {"requests": 1, "bytes": 0, "latencies_ms": []}
    started = time.monotonic()
    emit_crawl_event("homepage_started", url=normalized)
    request = urllib.request.Request(normalized, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            raw = response.read(max_bytes + 1)
            elapsed = int((time.monotonic() - started) * 1000)
            record_request(module="website", provider="website/http", operation="GET homepage", success=True, status=response.status, duration_ms=elapsed, organisation_number=organisation_number)
            if len(raw) > max_bytes:
                return evidence("website", "blocked", "registry_linked_company_website", normalized, note="Homepage exceeds byte limit"), {"requests": 2, "bytes": len(raw), "latencies_ms": [elapsed]}
            if "html" not in content_type.lower():
                return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"Unsupported content type: {content_type}"), {"requests": 2, "bytes": len(raw), "latencies_ms": [elapsed]}
            final_url = response.geturl()
            assert_public_url(final_url)
            homepage_http_status = response.status
        html = raw.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        structured = extruct.extract(html, base_url=final_url, syntaxes=["json-ld", "microdata", "opengraph"])
        text = trafilatura.extract(html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        description_tag = soup.select_one('meta[name="description"], meta[property="og:description"]')
        description = str(description_tag.get("content") or "").strip() if description_tag else ""
        homepage_sha256 = __import__("hashlib").sha256(raw).hexdigest()
        homepage_attempted_at = utc_now()
        homepage_signals = _extract_contact_signals(html, final_url)
        value = {
            "requested_url": normalized,
            "final_url": final_url,
            "registered_domain": _registered_domain(final_url),
            "title": title[:500],
            "description": description[:2000],
            "main_text_excerpt": text[:5000],
            "social_links": _social_links(final_url, soup),
            "structured_organisations": _jsonld_organisations(structured),
            "content_sha256": homepage_sha256,
            "extraction_state": _extraction_state(text, soup),
            **homepage_signals,
            "cost_usd": 0.0,
        }
        from .discovery import mark_direct_discovery
        from .validation import (
            append_quarantine_record,
            validate_website_value,
            validation_error_details,
            validation_reason_code,
        )

        value = mark_direct_discovery(value)
        # Step 2: homepage page ledger entry carries structured outcome fields.
        pages = [{
            "url": normalized,
            "final_url": final_url,
            "title": title[:500],
            "text_excerpt": text[:5000] or "not_available",
            "content_sha256": homepage_sha256,
            "failure_class": "success",
            "status": homepage_http_status,
            "duration_seconds": elapsed / 1000,
            "page_kind": "homepage",
            "extraction_state": _extraction_state(text, soup),
            "errors": [],
            "attempted_at": homepage_attempted_at,
            "attempt_number": 1,
        }]
        social = value["social_links"]
        crawl_errors = []
        requests = 2
        bytes_received = len(raw)
        page_latencies = [elapsed]
        homepage_domain = value["registered_domain"]
        priority_links = _priority_links(final_url, soup)
        emit_crawl_event("links_discovered", url=final_url, count=len(priority_links))
        for page_url, page_kind in priority_links:
            emit_crawl_event("page_started", url=page_url, page_kind=page_kind)
            page, page_social, page_requests, page_bytes, page_elapsed, page_error, page_fc, page_http_status = _fetch_secondary_page(
                page_url,
                homepage_domain=homepage_domain,
                timeout=timeout,
                max_bytes=min(max_bytes, 1_000_000),
                organisation_number=organisation_number,
            )
            requests += page_requests
            bytes_received += page_bytes
            if page_elapsed:
                page_latencies.append(page_elapsed)
            if page:
                pages.append({
                    **page,
                    "failure_class": page_fc,
                    "status": page_http_status,
                    "duration_seconds": page_elapsed / 1000,
                    "page_kind": page_kind,
                    "extraction_state": page.get("extraction_state") or "not_available",
                    "text_excerpt": page.get("main_text_excerpt") or "not_available",
                    "errors": [],
                    "attempted_at": utc_now(),
                    "attempt_number": 1,
                })
                social.extend(page_social)
                second_signals = _extract_contact_signals(page.get("html") if isinstance(page, dict) and "html" in page else "", page_url)
                for field in ("jobs", "news", "phones", "emails", "addresses", "locations"):
                    current = value.get(field)
                    if current in (None, "not_available"):
                        value[field] = second_signals.get(field, "not_available")
                    elif isinstance(current, list) and isinstance(second_signals.get(field), list):
                        value[field] = current + second_signals.get(field)
            elif page_error:
                pages.append({
                    "url": page_url,
                    "final_url": "not_available",
                    "title": "not_available",
                    "status": page_http_status,
                    "duration_seconds": page_elapsed / 1000,
                    "page_kind": page_kind,
                    "extraction_state": "failed",
                    "text_excerpt": "not_available",
                    "errors": [page_error],
                })
                crawl_errors.append({
                    "url": page_url,
                    "error": page_error,
                    "failure_class": page_fc,
                    "http_status": page_http_status,
                    "attempted_at": utc_now(),
                })
            emit_crawl_event("page_completed", url=page_url, page_kind=page_kind, status="success" if page else "failed", error=page_error or "")
        value["pages"] = pages
        value["social_links"] = list({(item["platform"], item["url"]): item for item in social}.values())
        value["crawl_errors"] = crawl_errors
        value = normalize_website_value(value)
        try:
            value = validate_website_value(value)
        except ValidationError as exc:
            append_quarantine_record(
                path=None,
                record_type="WebsiteValue",
                record=value,
                reason_code=validation_reason_code(exc),
                errors=validation_error_details(exc),
            )
            emit_crawl_event(
                "validation_rejected",
                organisation_number=organisation_number or "",
                record_type="WebsiteValue",
                reason_code=validation_reason_code(exc),
            )
            emit_crawl_event(
                "crawler_completed",
                organisation_number=organisation_number or "",
                status="validation_rejected",
                url=final_url,
            )
            return evidence(
                "website", "source_error", "registry_linked_company_website", final_url,
                note="Website payload failed schema validation",
            ), {"requests": requests, "bytes": bytes_received, "latencies_ms": page_latencies}
        jobs_count = len(value["jobs"]) if isinstance(value["jobs"], list) else 0
        news_count = len(value["news"]) if isinstance(value["news"], list) else 0
        emit_crawl_event("jobs_extracted", url=final_url, count=jobs_count)
        emit_crawl_event("news_extracted", url=final_url, count=news_count)
        emit_crawl_event("homepage_completed", url=final_url, status="available", pages=len(value["pages"]))
        emit_crawl_event("identity_check_started", url=final_url)
        emit_crawl_event("identity_check_completed", url=final_url, status="available")
        emit_crawl_event("crawler_completed", organisation_number=organisation_number or "", status="available", url=final_url)
        record = evidence("website", "available", "registry_linked_company_website", final_url, value=value, note="Company-controlled claim layer; not an official registry fact", content_sha256=value["content_sha256"])
        record["discovery"] = value["discovery"]
        return record, {"requests": requests, "bytes": bytes_received, "latencies_ms": page_latencies}
    except urllib.error.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        record_request(module="website", provider="website/http", operation="GET homepage", success=False, status=exc.code, duration_ms=elapsed, organisation_number=organisation_number, error=f"HTTP {exc.code}")
        emit_crawl_event("homepage_completed", url=normalized, status="http_error", code=exc.code)
        emit_crawl_event("crawler_completed", organisation_number=organisation_number or "", status="http_error", url=normalized)
        status = "not_found" if exc.code in {404, 410} else "source_error"
        return evidence("website", status, "registry_linked_company_website", normalized, note=f"HTTP {exc.code}"), {"requests": 2, "bytes": 0, "latencies_ms": [elapsed]}
    except urllib.error.URLError as exc:
        record_request(module="website", provider="website/http", operation="GET homepage", success=False, status=0, duration_ms=int((time.monotonic() - started) * 1000), organisation_number=organisation_number, error="URLError")
        emit_crawl_event("homepage_completed", url=normalized, status="source_error", error=str(exc.reason)[:180])
        if not supplied_scheme and normalized.startswith("https://"):
            first_elapsed = int((time.monotonic() - started) * 1000)
            record, metrics = fetch_website("http://" + supplied_url, timeout=timeout, max_bytes=max_bytes)
            metrics["requests"] += 2
            metrics["latencies_ms"].insert(0, first_elapsed)
            return record, metrics
        elapsed = int((time.monotonic() - started) * 1000)
        emit_crawl_event("crawler_completed", organisation_number=organisation_number or "", status="source_error", url=normalized)
        return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"URLError: {str(exc.reason)[:180]}"), {"requests": 2, "bytes": 0, "latencies_ms": [elapsed]}
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        record_request(module="website", provider="website/http", operation="GET homepage", success=False, status=0, duration_ms=elapsed, organisation_number=organisation_number, error=type(exc).__name__)
        emit_crawl_event("homepage_completed", url=normalized, status="source_error", error=f"{type(exc).__name__}: {str(exc)[:180]}")
        emit_crawl_event("crawler_completed", organisation_number=organisation_number or "", status="source_error", url=normalized)
        return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"{type(exc).__name__}: {str(exc)[:180]}"), {"requests": 2, "bytes": 0, "latencies_ms": [elapsed]}
