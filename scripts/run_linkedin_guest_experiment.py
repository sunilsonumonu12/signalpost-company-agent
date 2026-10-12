#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; SignalpostResearch/1.0)"
LEGAL_SUFFIXES = {"as", "asa", "ba", "da", "enk", "nuf", "sa", "stiftelsen", "aksjeselskap"}


def normalized_company(value: str) -> str:
    words = re.findall(r"[a-z0-9æøå]+", urllib.parse.unquote(str(value or "")).casefold())
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def company_slug(value: str) -> str:
    cleaned = normalized_company(value).replace(" ", "-")
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned.strip("-")


def canonical_company_url(value: str) -> str | None:
    parsed = urllib.parse.urlparse(str(value or "").strip())
    host = (parsed.hostname or "").casefold()
    if host not in {"linkedin.com", "www.linkedin.com"} and not host.endswith(".linkedin.com"):
        return None
    parts = [urllib.parse.unquote(item).strip() for item in parsed.path.split("/") if item.strip()]
    if len(parts) < 2 or parts[0].casefold() != "company":
        return None
    return f"https://www.linkedin.com/company/{parts[1].casefold()}"


def fetch(url: str, timeout: float = 20.0) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "no,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(2_000_000)


def json_ld_graph(soup: BeautifulSoup) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.get_text("", strip=True))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            if isinstance(payload.get("@graph"), list):
                rows.extend(item for item in payload["@graph"] if isinstance(item, dict))
            else:
                rows.append(payload)
        elif isinstance(payload, list):
            rows.extend(item for item in payload if isinstance(item, dict))
    return rows


def _text_from_select(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    for selector in selectors:
        node = soup.select_one(selector)
        if node is not None:
            text = node.get_text(" ", strip=True)
            if text:
                return text
    return None


def extract_profile(raw: bytes) -> dict[str, Any]:
    soup = BeautifulSoup(raw, "html.parser")
    graph = json_ld_graph(soup)
    organisations = [item for item in graph if item.get("@type") == "Organization"]
    if not organisations:
        raise RuntimeError("LinkedIn guest page returned no structured organization profile")

    organisation = organisations[-1]
    name = str(organisation.get("name") or "").strip()
    website = str(organisation.get("sameAs") or "").strip() or _text_from_select(soup, [
        '[data-test-id="about-us__website"] dd a',
        'a[href*="linkedin.com/company/"]',
    ])
    industry = str(organisation.get("industry") or "").strip() or _text_from_select(soup, ['[data-test-id="about-us__industry"] dd'])
    headquarters = _text_from_select(soup, ['[data-test-id="about-us__headquarters"] dd'])
    size_label = _text_from_select(soup, ['[data-test-id="about-us__size"] dd'])
    description = str(organisation.get("description") or "").strip()

    return {
        "name": name,
        "page_url": canonical_company_url(str(organisation.get("url") or "")) or None,
        "website": website or None,
        "industry": industry or None,
        "headquarters": headquarters or None,
        "employee_size_label": size_label or None,
        "description": description or None,
        "posts": [
            {
                "title": str(item.get("name") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "date_published": str(item.get("datePublished") or "").strip() or None,
                "text": str(item.get("text") or "").strip() or None,
            }
            for item in graph
            if item.get("@type") == "DiscussionForumPosting"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Experimental LinkedIn guest company-page extraction; never publishable.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--cache-dir", default="cache/linkedin")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    profiles = []
    for line in Path(args.profiles).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            profiles.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for profile in profiles:
        org = str(profile.get("organisation_number") or "")
        company_name = str(profile.get("name") or "").strip()
        slug = company_slug(company_name)
        candidate_url = canonical_company_url(str(profile.get("linkedin_company_url") or profile.get("company_url") or ""))
        if not candidate_url and slug:
            candidate_url = f"https://www.linkedin.com/company/{slug}"

        row: dict[str, Any] = {
            "organisation_number": org,
            "connector": "linkedin_guest_structured_company_v2",
            "publishable": False,
            "rights_status": "experimental",
            "source_class": "public_logged_out_company_page",
            "claim_boundary": "Public logged-out company-page fields only; exact-company gating still required and no output may be promoted without LinkedIn source-rights approval.",
            "retrieved_at": retrieved_at,
            "candidate_url": candidate_url,
            "observed": None,
            "errors": [],
        }

        if not candidate_url:
            row["errors"].append("No deterministic LinkedIn company URL available from profile data.")
            rows.append(row)
            continue

        try:
            raw = fetch(candidate_url, timeout=args.timeout)
            snapshot_path = cache_dir / f"{org}-linkedin-profile.bin"
            snapshot_path.write_bytes(raw)
            data = extract_profile(raw)
            row["observed"] = {
                "name": data.get("name"),
                "page_url": data.get("page_url"),
                "website": data.get("website"),
                "industry": data.get("industry"),
                "headquarters": data.get("headquarters"),
                "employee_size_label": data.get("employee_size_label"),
                "description": data.get("description"),
                "post_count": len(data.get("posts") or []),
                "snapshot": str(snapshot_path),
            }
            rows.append(row)
        except Exception as exc:
            row["errors"].append(f"{type(exc).__name__}: {str(exc)[:180]}")
            rows.append(row)

    report = {
        "connector": "linkedin_guest_structured_company_v2",
        "publishable": False,
        "observations": len(rows),
        "errors": sum(1 for row in rows if row.get("errors")),
        "claim_boundary": "Public logged-out company-page fields only; these are experimental and non-publishable without approved LinkedIn rights.",
        "rights_status": "experimental",
    }

    Path(args.output).write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
