#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
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
            "Accept-Language": "en-US,en;q=0.8,no;q=0.6",
            "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(2_000_000)


def parse_jobs_page(raw: bytes, expected_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(raw, "html.parser")
    jobs: list[dict[str, Any]] = []
    for card in soup.select("a[href*='/jobs/view/']"):
        href = str(card.get("href") or "")
        job_url = urllib.parse.urljoin("https://www.linkedin.com", href)
        if "/jobs/view/" not in job_url:
            continue
        title = " ".join(card.get_text(" ", strip=True).split())
        if not title:
            continue
        company_urls = {canonical_company_url(str(link.get("href") or "")) for link in card.parent.select("a[href*='linkedin.com/company/']") if link.get("href")}
        if expected_url not in company_urls:
            continue
        jobs.append({
            "title": title,
            "url": job_url,
            "company_url": expected_url,
            "location": "not_available",
        })
    return jobs


def main() -> int:
    parser = argparse.ArgumentParser(description="Experimental logged-out LinkedIn job extraction; non-publishable.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--cache-dir", default="cache/linkedin-jobs")
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
    observations: list[dict[str, Any]] = []
    report_rows = []
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for profile in profiles:
        org = str(profile.get("organisation_number") or "")
        legal_name = str(profile.get("name") or "").strip()
        company_handle = canonical_company_url(str(profile.get("linkedin_company_url") or profile.get("company_url") or ""))
        if not company_handle:
            company_handle = f"https://www.linkedin.com/company/{urllib.parse.quote(normalized_company(legal_name).replace(' ', '-'))}"
        if not company_handle:
            report_rows.append({"organisation_number": org, "status": "skipped", "reason": "No handle"})
            continue

        try:
            raw = fetch(company_handle, timeout=args.timeout)
            snapshot_path = cache_dir / f"{org}-linkedin-jobs.html"
            snapshot_path.write_bytes(raw)
            matches = parse_jobs_page(raw, company_handle)
            rows = [
                {
                    "id": "linkedin-guest-job-" + hashlib.sha256(f"{org}|{item['url']}".encode("utf-8")).hexdigest()[:24],
                    "organisation_number": org,
                    "platform": "linkedin",
                    "signal_type": "job_posting",
                    "source_url": item["url"],
                    "company_url": item["company_url"],
                    "title": item["title"],
                    "location": item["location"],
                    "retrieved_at": retrieved_at,
                    "rights_status": "experimental",
                    "publishable": False,
                    "claim_boundary": "Logged-out LinkedIn job activity only; exact-company gating remains mandatory and these records are not publishable without rights approval.",
                    "snapshot": str(snapshot_path),
                }
                for item in matches
            ]
            observations.extend(rows)
            report_rows.append({"organisation_number": org, "status": "ok", "jobs_found": len(rows)})
        except Exception as exc:
            report_rows.append({"organisation_number": org, "status": "error", "reason": f"{type(exc).__name__}: {str(exc)[:180]}"})

    report = {
        "connector": "linkedin_guest_exact_handle_jobs_v1",
        "publishable": False,
        "exact_jobs": len(observations),
        "claim_boundary": "Logged-out LinkedIn job activity only; exact-company gating remains mandatory and no output may enter strict publication without rights approval.",
        "rights_status": "experimental",
        "records": len(report_rows),
    }

    Path(args.output).write_text(json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
