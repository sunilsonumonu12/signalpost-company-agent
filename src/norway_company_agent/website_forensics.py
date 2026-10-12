from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

import trafilatura
from bs4 import BeautifulSoup

from .page_signals import (
    CAREERS_PAGE,
    JOB_DETAIL,
    NEWS_URL,
    _ats_host,
    _jsonld_blocks,
    _walk,
    _types,
    extract_page_signals,
    parse_date,
)

JOB_TERMS = re.compile(r"job|career|vacan|recruit|stilling|karriere|position|apply|arbeid|ledige", re.I)
NEWS_TERMS = re.compile(r"news|press|media|newsroom|release|announcement|blog|article|aktuelt|nyhet|presse", re.I)
ARTICLE_TYPES = {"Article", "NewsArticle", "BlogPosting", "ReportageNewsArticle", "TechArticle", "PressRelease"}
DATE_FIELDS = {"datePublished", "datePosted", "dateCreated", "dateModified", "validThrough", "publishedTime"}


def _absolute(base_url: str, value: str) -> str:
    return urllib.parse.urljoin(base_url, value.strip()) if value.strip() else ""


def _structured_scripts(soup: BeautifulSoup) -> list[dict[str, Any]]:
    blocks = []
    parsed_by_raw = {}
    for raw, data in _jsonld_blocks(str(soup)):
        parsed_by_raw[raw.strip()] = data
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        cleaned = raw.strip()
        data = parsed_by_raw.get(cleaned)
        if data is None:
            try:
                data = json.loads(cleaned)
                parse_error = None
            except (TypeError, json.JSONDecodeError) as exc:
                parse_error = type(exc).__name__
        else:
            parse_error = None
        if data is not None:
            kinds = sorted({kind for node in _walk(data) if isinstance(node, dict) for kind in _types(node)})
        else:
            kinds = []
        blocks.append({"json": data, "types": kinds, "raw": cleaned, "parse_error": parse_error})
    return blocks


def _page_date_candidates(soup: BeautifulSoup, structured: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = []
    for node in soup.select("time[datetime]"):
        raw = str(node.get("datetime") or "").strip()
        found.append({"source": "time.datetime", "raw": raw, "parsed": parse_date(raw)})
    for node in soup.select("meta[name], meta[property], meta[itemprop]"):
        key = str(node.get("property") or node.get("name") or node.get("itemprop") or "")
        if re.search(r"date|published|modified", key, re.I):
            raw = str(node.get("content") or "").strip()
            found.append({"source": f"meta:{key}", "raw": raw, "parsed": parse_date(raw)})
    for block in structured:
        data = block.get("json")
        for node in _walk(data):
            if not isinstance(node, dict):
                continue
            for key in DATE_FIELDS:
                raw = node.get(key)
                if raw:
                    found.append({"source": f"jsonld:{key}", "raw": str(raw), "parsed": parse_date(raw)})
    return found


def _link_records(soup: BeautifulSoup, final_url: str) -> list[dict[str, Any]]:
    links = []
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        text = " ".join(anchor.get_text(" ", strip=True).split())
        url = _absolute(final_url, href)
        job_like = bool(JOB_TERMS.search(f"{href} {text}"))
        article_like = bool(NEWS_TERMS.search(f"{href} {text}") or NEWS_URL.search(urllib.parse.urlparse(url).path))
        links.append({
            "url": url,
            "href": href,
            "anchor_text": text,
            "title": str(anchor.get("title") or ""),
            "rel": list(anchor.get("rel") or []),
            "job_like": job_like,
            "article_like": article_like,
            "html_fragment": str(anchor)[:4000] if job_like or article_like else None,
        })
    return links


def build_page_forensics(
    *,
    organisation_number: str,
    requested_url: str,
    final_url: str,
    http_status: int,
    page_kind: str,
    html: str,
    extraction_state: str,
    signals: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    full_text = trafilatura.extract(html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
    signals = signals or extract_page_signals(html, final_url)
    links = _link_records(soup, final_url)
    job_links = [link for link in links if link["job_like"]]
    article_links = [link for link in links if link["article_like"]]
    headings = [" ".join(node.get_text(" ", strip=True).split()) for node in soup.select("h1, h2, h3, h4, h5, h6")]
    meta_tags = []
    for node in soup.select("meta[name], meta[property], meta[itemprop]"):
        key = str(node.get("property") or node.get("name") or node.get("itemprop") or "")
        if re.search(r"og:|article:|description|title|date|published|modified|keywords", key, re.I):
            meta_tags.append({"key": key, "content": str(node.get("content") or "")})
    structured = _structured_scripts(soup)
    parsed_jobs = list(signals.get("jobs") or [])
    parsed_news = list(signals.get("articles") or [])
    parsed_job_urls = {str(item.get("url") or "") for item in parsed_jobs + (signals.get("apply_actions") or [])}
    parsed_article_urls = {str(item.get("url") or "") for item in parsed_news}
    page_path = urllib.parse.urlparse(final_url).path
    is_careers_page = bool(CAREERS_PAGE.search(page_path) or JOB_DETAIL.search(page_path) or re.search(r"recruit|vacan", page_path, re.I) or page_kind == "careers")
    is_news_page = bool(NEWS_URL.search(page_path) or re.search(r"newsroom|media|release|announcement", page_path, re.I) or page_kind == "news")

    rejected_job_links = []
    for link in job_links:
        if link["url"] in parsed_job_urls:
            continue
        target_path = urllib.parse.urlparse(link["url"]).path
        if not (CAREERS_PAGE.search(page_path) or JOB_DETAIL.search(page_path)):
            reason = "existing job parser only evaluates links when the fetched page URL matches its careers/job path gate"
        elif not (urllib.parse.urlparse(link["url"]).netloc == urllib.parse.urlparse(final_url).netloc or _ats_host(link["url"])):
            reason = "link host is neither same-site nor a recognized ATS host"
        elif not (JOB_DETAIL.search(target_path) or re.search(r"(?:ledig|stilling|vacan|apply)", target_path, re.I) or _ats_host(link["url"])):
            reason = "link URL does not match the parser's supported individual-job path/slug rules"
        else:
            reason = "job-like link did not satisfy the existing parser's title/card acceptance rules"
        rejected_job_links.append({**link, "rejection_reason": reason})

    rejected_article_links = []
    for link in article_links:
        if link["url"] in parsed_article_urls:
            continue
        reason = "no accepted dated article candidate matched this link; parser requires recognized article structure and a valid publication date"
        if is_news_page:
            reason = "news-like page/link was visible, but parser found no accepted article with a valid date and title"
        rejected_article_links.append({**link, "rejection_reason": reason})

    text_candidates = [
        line.strip() for line in full_text.splitlines()
        if (JOB_TERMS.search(line) or NEWS_TERMS.search(line)) and line.strip()
    ]
    relevant_jsonld = [block for block in structured if set(block.get("types") or []) & (ARTICLE_TYPES | {"JobPosting"})]
    return {
        "organisation_number": organisation_number,
        "url": requested_url,
        "final_url": final_url,
        "http_status": http_status,
        "status": "available" if 200 <= http_status < 300 else "source_error",
        "title": soup.title.get_text(" ", strip=True) if soup.title else "not_available",
        "page_kind": page_kind,
        "full_cleaned_page_text": full_text,
        "all_discovered_links": links,
        "job_like_links": job_links,
        "article_like_links": article_links,
        "headings": headings,
        "relevant_meta_tags": meta_tags,
        "jsonld_structured_data": structured,
        "relevant_jsonld": relevant_jsonld,
        "relevant_html_fragments": [link["html_fragment"] for link in job_links + article_links if link.get("html_fragment")],
        "job_candidates": parsed_jobs,
        "job_candidate_acceptance_reasons": [
            {
                "url": candidate.get("url"),
                "reason": {
                    "json_ld_jobposting": "existing parser found a JobPosting structured-data node with a non-empty title",
                    "role_card": "existing parser accepted a job-like detail link with a meaningful role title",
                }.get(candidate.get("evidence_kind"), "existing page_signals parser returned this candidate"),
            }
            for candidate in parsed_jobs
        ],
        "apply_actions": list(signals.get("apply_actions") or []),
        "job_like_text_candidates": text_candidates,
        "job_candidate_rejections": rejected_job_links,
        "article_candidates": parsed_news,
        "article_candidate_acceptance_reasons": [
            {
                "url": candidate.get("url"),
                "reason": {
                    "json_ld_article": "existing parser accepted supported article structured data with a valid publication date",
                    "time_element": "existing parser accepted a dated time element with a meaningful card title",
                    "meta_published_time": "existing parser accepted publication metadata with a meaningful headline",
                    "feed_item": "existing parser accepted a dated item from the company-owned feed",
                }.get(candidate.get("evidence_kind"), "existing page_signals parser returned this article candidate"),
            }
            for candidate in parsed_news
        ],
        "article_candidate_rejections": rejected_article_links,
        "dates_found": _page_date_candidates(soup, structured),
        "normalized_jobs": "not_available",
        "normalized_news": "not_available",
        "careers_page_detected": is_careers_page,
        "job_links_found": len(job_links),
        "job_candidates_found": len(parsed_jobs),
        "jobs_extracted": 0,
        "news_page_detected": is_news_page,
        "article_links_found": len(article_links),
        "article_candidates_found": len(parsed_news),
        "news_extracted": 0,
        "extraction_state": extraction_state,
        "errors": list(errors or []),
        "parser_outputs": {"jobs": parsed_jobs or "not_available", "news": parsed_news or "not_available"},
    }
