from __future__ import annotations

import hashlib
import io
import re
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

from .crawl_events import emit_crawl_event
from .evidence import evidence
from .http import FetchResult, fetch_json

try:  # pragma: no cover - optional dependency; runtime falls back to a clean source_error
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None

OCR_NUMBER = r"(?-i:\b[0-9O][0-9O .,-]{0,8})"
YEAR_NOUN = r"(?:regnskaps[aå]ret|rekneskaps[aå]ret)"
ARSVERK = r"(?:aarsverk|arsverk|årsverk)"
PATTERNS = (
    (0, "full_time_equivalents", re.compile(rf"(?i)(?:antall|tal(?:et)?\s+p[aå])\s+{ARSVERK}\s+i\s+{YEAR_NOUN}\s*(?:er|:|=)?\s*({OCR_NUMBER})")),
    (0, "full_time_equivalents", re.compile(rf"(?i)antall\s+{ARSVERK}(?:\s+sysselsatt\s+i\s+{YEAR_NOUN})?\s*(?:er|:|=)?\s*({OCR_NUMBER})")),
    (0, "full_time_equivalents", re.compile(rf"(?i)selskapet\s+har(?:\s+[i1]\s+\d{{4}})?\s+sysselsatt\s+({OCR_NUMBER})\s+{ARSVERK}")),
    (0, "full_time_equivalents", re.compile(rf"(?i)selskapet\s+har\s+({OCR_NUMBER})\s+{ARSVERK}")),
    (0, "full_time_equivalents", re.compile(rf"(?i)antall\s+{ARSVERK}\s+(?:sysselsatt|syssetsatt)\s+i\s+{YEAR_NOUN}\s*(?:er|:|=)?\s*({OCR_NUMBER})")),
    (0, "full_time_equivalents", re.compile(rf"(?i)tal(?:et)?\s+p[aå]\s+{ARSVERK}\s+sysselsett\s+i\s+{YEAR_NOUN}\s*(?:er|:|=)?\s*({OCR_NUMBER})")),
    (1, "employees", re.compile(rf"(?i)gjennomsnittlig(?:e)?\s+antall\s+ansatte(?:\s+i\s+{YEAR_NOUN})?\s*(?:er|:|=)?\s*({OCR_NUMBER})")),
    (1, "employees", re.compile(rf"(?i)antall\s+ansatte\s*(?:er|:|=)?\s*({OCR_NUMBER})")),
    (2, "employees", re.compile(rf"(?i)({OCR_NUMBER})\s+(?:heltids)?ansatte\b")),
)
WORD_COUNTS = {"ingen": 0, "en": 1, "ett": 1, "to": 2, "tre": 3, "fire": 4, "fem": 5}
WORD_EMPLOYEE_PATTERN = re.compile(r"(?i)\b(?:det\s+er|selskapet\s+har)\s+(ingen|en|ett|to|tre|fire|fem)\s+ansatte\b")
ZERO_WORKFORCE_PATTERN = re.compile(rf"(?i)\b(?:selskapet|stiftelsen|legatet|sameiet|borettslaget|borettslag|det)\s+(?:har\s+ingen\s+(ansatte|{ARSVERK})|har\s+ikke\s+hatt\s+(?:noen\s+)?ansatte|hadde\s+ingen\s+ansatte|ikke\s+har\s+ansatte)\b")
WORKFORCE_TERMS = re.compile(r"(?i)ansatt|aarsverk|arsverk|årsverk|sysselsatt")

ACCOUNTING_OBLIGATION_SOURCE = "https://www.brreg.no/en/submission-of-annual-accounts/reporting-obligations-to-the-register-of-company-accounts/who-has-an-accounting-obligation/"
ACCOUNTING_RULESET_VERSION = "brreg_accounting_obligation_rules_2024-08-09_v1"
ALWAYS_ACCOUNTING_OBLIGED_FORMS = {"AS", "ASA", "BRL", "BBL", "STI", "SF", "VPFO"}
THRESHOLD_OR_ACTIVITY_FORMS = {"ENK", "ANS", "DA", "SA", "FLI", "ESEK", "NUF", "UTLA", "ORGL", "SAM", "SPA", "KS", "BO"}

BRREG_ENTITY = "https://data.brreg.no/enhetsregisteret/api/enheter/{org}"
BRREG_ROLES = BRREG_ENTITY + "/roller"
BRREG_GROUP = "https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{org}"
BRREG_SUBUNITS = "https://data.brreg.no/enhetsregisteret/api/underenheter?overordnetEnhet={org}&size=1000"
BRREG_ACCOUNTS = "https://data.brreg.no/regnskapsregisteret/regnskap/{org}"
BRREG_ACCOUNT_YEARS = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/aar"
BRREG_ACCOUNT_PDF = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/{year}"

_history_lock = threading.Lock()
_history_last_request = 0.0


def accounting_obligation_assessment(profile: dict[str, Any]) -> dict[str, Any]:
    """Classify the rule path without inventing a definitive exemption.

    Brreg's public rule is categorical for some forms and threshold/activity dependent for others.
    Registry employee counts are not treated as equivalent to statutory man-years or asset/revenue tests.
    """
    legal_form = str(profile.get("legal_form") or "").upper()
    latest = profile.get("latest_submitted_accounts")
    if latest:
        classification = "filing_observed"
        reason = "The live BRREG registry reports a latest submitted annual-account year."
    elif legal_form in ALWAYS_ACCOUNTING_OBLIGED_FORMS:
        classification = "required_by_legal_form"
        reason = f"Brreg lists organisation form {legal_form} in an always-obliged category."
    elif legal_form in THRESHOLD_OR_ACTIVITY_FORMS:
        classification = "threshold_or_activity_dependent"
        reason = "Obligation depends on statutory size, partner, activity, tax or supervision tests not fully present in the open entity row."
    else:
        classification = "special_rule_or_review_required"
        reason = "The open entity row is insufficient for a definitive accounting-obligation decision."
    ruleset_hash = __import__("hashlib").sha256(ACCOUNTING_RULESET_VERSION.encode()).hexdigest()
    return evidence(
        "accounting_obligation",
        "available",
        "official_rule_interpretation",
        ACCOUNTING_OBLIGATION_SOURCE,
        value={
            "classification": classification,
            "legal_form": legal_form or None,
            "latest_submitted_accounts": latest or None,
            "reason": reason,
            "ruleset_version": ACCOUNTING_RULESET_VERSION,
        },
        as_of="2024-08-09",
        content_sha256=ruleset_hash,
        source_row_key=str(profile.get("organisation_number") or "") or None,
    )


def _get(value: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def normalize_financials(body: Any) -> dict[str, Any]:
    records = body if isinstance(body, list) else []
    if not records:
        return {"records": []}
    normalized = []
    for item in records[:3]:
        normalized.append({
            "record_id": item.get("id"),
            "account_type": item.get("regnskapstype"),
            "period": item.get("regnskapsperiode"),
            "currency": item.get("valuta"),
            "revenue": _get(item, "resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter"),
            "operating_result": _get(item, "resultatregnskapResultat", "driftsresultat", "driftsresultat"),
            "profit_before_tax": _get(item, "resultatregnskapResultat", "ordinaertResultatFoerSkattekostnad"),
            "annual_result": _get(item, "resultatregnskapResultat", "aarsresultat"),
            "assets": _get(item, "eiendeler", "sumEiendeler"),
            "equity": _get(item, "egenkapitalGjeld", "egenkapital", "sumEgenkapital"),
            "debt": _get(item, "egenkapitalGjeld", "gjeldOversikt", "sumGjeld"),
        })
    return {"records": normalized}


def normalize_financial_history(body: Any, org: str) -> dict[str, Any]:
    years = sorted({str(year) for year in body if str(year).isdigit()}) if isinstance(body, list) else []
    return {
        "years": years,
        "pdfs": [
            {"year": year, "url": BRREG_ACCOUNT_PDF.format(org=org, year=year)}
            for year in reversed(years)
        ],
    }


def _reserve_history_slot(clock: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep) -> None:
    """Reserve starts at least 2.1 seconds apart without serializing response time."""
    global _history_last_request
    with _history_lock:
        delay = 2.1 - (clock() - _history_last_request)
        if delay > 0:
            sleeper(delay)
        _history_last_request = clock()


def _fetch_history(url: str, org: str) -> FetchResult:
    """Keep this endpoint below its observed 30-request-starts/minute allowance."""
    _reserve_history_slot()
    return fetch_json(url, module="financial_history", organisation_number=org)


def normalize_roles(body: Any) -> dict[str, Any]:
    roles = []
    for group in body.get("rollegrupper", []) if isinstance(body, dict) else []:
        changed = group.get("sistEndret")
        for item in group.get("roller", []):
            person = item.get("person") or {}
            name = person.get("navn") or {}
            entity = item.get("enhet") or {}
            display_name = " ".join(filter(None, [name.get("fornavn"), name.get("mellomnavn"), name.get("etternavn")])) or entity.get("navn")
            roles.append({
                "name": display_name or None,
                "organisation_number": entity.get("organisasjonsnummer"),
                "role_code": _get(item, "type", "kode"),
                "role": _get(item, "type", "beskrivelse"),
                "group_code": _get(group, "type", "kode"),
                "group": _get(group, "type", "beskrivelse"),
                "last_changed": changed,
                "inactive": bool(item.get("avregistrert")),
            })
    return {"roles": roles}


def normalize_locations(body: Any) -> dict[str, Any]:
    rows = _get(body, "_embedded", "underenheter") or []
    return {"locations": [{
        "organisation_number": item.get("organisasjonsnummer"),
        "name": item.get("navn"),
        "address": item.get("beliggenhetsadresse") or item.get("postadresse"),
        "industry": item.get("naeringskode1"),
        "employees": item.get("antallAnsatte"),
    } for item in rows]}


def normalize_entity(body: Any) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    return {
        "organisation_number": body.get("organisasjonsnummer"),
        "name": body.get("navn"),
        "legal_form": _get(body, "organisasjonsform", "kode"),
        "employees": body.get("antallAnsatte"),
        "bankrupt": body.get("konkurs"),
        "liquidating": body.get("underAvvikling"),
        "website": body.get("hjemmeside"),
        "industry": body.get("naeringskode1"),
        "business_address": body.get("forretningsadresse"),
        "postal_address": body.get("postadresse"),
        "latest_submitted_accounts": body.get("sisteInnsendteAarsregnskap"),
    }


def _classified(field: str, source_type: str, result: FetchResult, value: Any = None) -> dict[str, Any]:
    if result.status == 200:
        return evidence(field, "available", source_type, result.url, value=result.body if value is None else value, content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, effective_at=result.effective_at)
    if result.status in {404, 410}:
        return evidence(field, "not_found", source_type, result.url, note=result.error, content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, effective_at=result.effective_at)
    return evidence(field, "source_error", source_type, result.url, note=result.error, content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, effective_at=result.effective_at)


def __number_value(value: str) -> int | float | None:
    cleaned = value.replace(" ", "").replace("O", "0").strip(".,-")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif re.search(r"\.\d{1,2}$", cleaned):
        pass
    else:
        cleaned = cleaned.replace(".", "")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    if not 0 <= number <= 100_000:
        return None
    return int(number) if number.is_integer() else round(number, 2)


def _needs_ocr(text: str) -> bool:
    return len(text.strip()) < 100 or not WORKFORCE_TERMS.search(text)


def _extract_workforce_candidate(text: str) -> tuple[int | float | None, str | None, str, str | None]:
    compact = re.sub(r"[\t\r ]+", " ", text)
    matches: list[tuple[int, int | float, str, str]] = []
    for priority, measure, pattern in PATTERNS:
        for match in pattern.finditer(compact):
            start = max(0, compact.rfind("\n", 0, match.start()) + 1)
            end_pos = compact.find("\n", match.end())
            end = len(compact) if end_pos < 0 else end_pos
            span = compact[start:end].strip()[:500]
            if re.search(r"(?i)konsern|group", span):
                continue
            count = __number_value(match.group(1))
            if count is not None:
                matches.append((priority, count, span, measure))
    for match in WORD_EMPLOYEE_PATTERN.finditer(compact):
        start = max(0, compact.rfind("\n", 0, match.start()) + 1)
        end_pos = compact.find("\n", match.end())
        end = len(compact) if end_pos < 0 else end_pos
        span = compact[start:end].strip()[:500]
        if not re.search(r"(?i)konsern|group", span):
            matches.append((2, WORD_COUNTS[match.group(1).casefold()], span, "employees"))
    for match in ZERO_WORKFORCE_PATTERN.finditer(compact):
        start = max(0, compact.rfind("\n", 0, match.start()) + 1)
        end_pos = compact.find("\n", match.end())
        end = len(compact) if end_pos < 0 else end_pos
        span = compact[start:end].strip()[:500]
        if not re.search(r"(?i)konsern|group", span):
            measure = "full_time_equivalents" if match.group(1) and re.search(r"(?i)verk", match.group(1)) else "employees"
            matches.append((0, 0, span, measure))
    if not matches:
        return None, None, "no_employee_phrase", None
    best_priority = min(item[0] for item in matches)
    best = [item for item in matches if item[0] == best_priority]
    values = {item[1] for item in best}
    if len(values) != 1:
        return None, None, "conflicting_employee_counts", None
    chosen = sorted(best, key=lambda item: item[3] != "full_time_equivalents")[0]
    return chosen[1], chosen[2], "accepted", chosen[3]


def _ocr_pdf(pdf_path: Path, *, pages: int, dpi: int) -> str:
    with tempfile.TemporaryDirectory(prefix="signalpost-annual-ocr-") as temporary:
        prefix = Path(temporary) / "page"
        subprocess.run(["pdftoppm", "-f", "1", "-l", str(pages), "-jpeg", "-r", str(dpi), str(pdf_path), str(prefix)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
        text: list[str] = []
        for image_path in sorted(Path(temporary).glob("page-*.jpg")):
            completed = subprocess.run(["tesseract", str(image_path), "stdout", "-l", "nor", "--psm", "6"], check=True, capture_output=True, text=True, encoding="utf-8", timeout=60)
            text.append(completed.stdout)
        return "\n".join(text)


def extract_annual_report_workforce(profile: dict[str, Any], *, cache_dir: str | Path | None = None, ocr_pages: int = 15, ocr_dpi: int = 130) -> dict[str, Any]:
    organisation_number = str(profile.get("organisation_number") or "")
    if not organisation_number:
        return evidence("workforce", "source_error", "official_annual_account_copy", "", note="Missing organisation number for annual-report workforce lookup")

    registry = ((profile.get("evidence") or {}).get("registry_live") or {}).get("value") or {}
    if str(registry.get("employees") or registry.get("antallAnsatte") or "").strip() and str(registry.get("employees") or registry.get("antallAnsatte") or "").strip().isdigit():
        return evidence("workforce", "not_applicable", "official_annual_account_copy", "https://data.brreg.no/enhetsregisteret/api/enheter/" + organisation_number, note="Employee count already available in the registry; annual-report snapshot is intentionally skipped.")

    history = ((profile.get("evidence") or {}).get("financial_history") or {}).get("value") or {}
    pdfs = history.get("pdfs") or []
    if not pdfs:
        return evidence("workforce", "not_found", "official_annual_account_copy", "https://data.brreg.no/regnskapsregisteret/regnskap/" + organisation_number, note="No annual-report PDF links were available from the official financial-history record.")

    latest = sorted(pdfs, key=lambda item: str(item.get("year") or ""), reverse=True)[0]
    url = str(latest.get("url") or "")
    if not url:
        return evidence("workforce", "not_found", "official_annual_account_copy", "https://data.brreg.no/regnskapsregisteret/regnskap/" + organisation_number, note="Financial-history entry had no PDF URL.")

    cache_root = Path(cache_dir) if cache_dir else None
    if cache_root is not None:
        cache_root.mkdir(parents=True, exist_ok=True)
        cache_path = cache_root / f"{organisation_number}-{latest.get('year')}.pdf"
    else:
        cache_path = None

    try:
        raw = cache_path.read_bytes() if cache_path and cache_path.exists() else None
        if raw is None:
            request = urllib.request.Request(url, headers={"User-Agent": "SignalpostResearchPOC/1.0 (+https://builderr.ai)"})
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read(20_000_001)
            if cache_path is not None:
                cache_path.write_bytes(raw)
        if len(raw) > 20_000_000 or not raw.startswith(b"%PDF"):
            return evidence("workforce", "source_error", "official_annual_account_copy", url, note="Unsupported or oversized annual-report document.")
        if PdfReader is None:
            return evidence("workforce", "source_error", "official_annual_account_copy", url, note="pypdf is not installed; annual-report text extraction is unavailable.")
        reader = PdfReader(io.BytesIO(raw), strict=False)
        pages = [page.extract_text() or "" for page in reader.pages[:120]]
        text = "\n".join(pages)
        ocr_used = False
        if _needs_ocr(text) and ocr_pages > 0:
            if cache_path is not None:
                ocr_cache = cache_path.with_suffix(f"-{ocr_pages}-{ocr_dpi}.txt")
                if ocr_cache.exists():
                    text = text + "\n" + ocr_cache.read_text(encoding="utf-8", errors="replace")
                else:
                    try:
                        ocr_text = _ocr_pdf(cache_path, pages=min(ocr_pages, len(reader.pages)), dpi=ocr_dpi)
                        ocr_cache.write_text(ocr_text, encoding="utf-8")
                        text = text + "\n" + ocr_text
                        ocr_used = True
                    except Exception:
                        text = text
            else:
                try:
                    with tempfile.TemporaryDirectory(prefix="signalpost-workforce-") as directory:
                        temp_pdf = Path(directory) / "annual_report.pdf"
                        temp_pdf.write_bytes(raw)
                        text = text + "\n" + _ocr_pdf(temp_pdf, pages=min(ocr_pages, len(reader.pages)), dpi=ocr_dpi)
                        ocr_used = True
                except Exception:
                    text = text
        if organisation_number not in re.sub(r"\D", "", text):
            return evidence("workforce", "not_found", "official_annual_account_copy", url, note="Organisation number was not found in the downloaded annual report.")

        count, span, status, measure = _extract_workforce_candidate(text)
        if count is None:
            return evidence("workforce", "not_found", "official_annual_account_copy", url, value={"status": status, "measure": measure, "ocr_used": ocr_used}, note="No valid company-scope workforce phrase was found in the annual report.")
        metrics = {"workforce_value": count, "measure": measure, "year": str(latest.get("year") or ""), "scope": "company_phrase", "ocr_used": ocr_used}
        metrics[measure] = count
        return evidence(
            "workforce",
            "available",
            "official_annual_account_copy",
            url,
            value=metrics,
            content_sha256=hashlib.sha256(raw).hexdigest(),
            effective_at=str(latest.get("year") or ""),
            note=span or "company-scope workforce count extracted from the annual report",
        )
    except Exception as exc:  # pragma: no cover - safety fallback for next-phase integration
        return evidence("workforce", "source_error", "official_annual_account_copy", url if 'url' in locals() else "", note=f"{type(exc).__name__}: {str(exc)[:180]}")


def exact_title_match(company_name: str, title: str) -> bool:
    company_tokens = re.findall(r"[a-z0-9æøå]+", str(company_name or "").casefold())
    title_tokens = re.findall(r"[a-z0-9æøå]+", str(title or "").rsplit(" - ", 1)[0].casefold())
    if not company_tokens or not title_tokens or len(company_tokens) > len(title_tokens):
        return False

    allowed_predecessors = {"av", "for", "fra", "hos", "i", "med", "om", "på", "til", "og", "kjøper", "velger"}
    for index in range(len(title_tokens) - len(company_tokens) + 1):
        if title_tokens[index:index + len(company_tokens)] != company_tokens:
            continue
        if index == 0 or title_tokens[index - 1] in allowed_predecessors:
            return True
    return False


def fetch_google_news_mentions(profile: dict[str, Any], *, limit: int = 5, years: int = 2) -> dict[str, Any]:
    organisation_number = str(profile.get("organisation_number") or "")
    company_name = str(profile.get("name") or profile.get("legal_name") or profile.get("company_name") or "").strip()
    if not company_name:
        return evidence("google_news", "not_found", "google_news_rss", "", note="Missing legal name for exact-title Google News lookup")

    query = urllib.parse.quote(f'"{company_name}" when:{years}y')
    url = f"https://news.google.com/rss/search?q={query}&hl=no&gl=NO&ceid=NO:no"
    emit_crawl_event("google_news_search_started", organisation_number=organisation_number, company_name=company_name, url=url, years=years)

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "SignalpostResearchPOC/1.0 (+https://builderr.ai)", "Accept": "application/rss+xml, application/xml"})
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read(2_000_000)
        root = ET.fromstring(raw)

        mentions: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in root.findall(".//item"):
            title = str(item.findtext("title") or "").strip()
            link = str(item.findtext("link") or "").strip()
            publisher = str(item.findtext("source") or "").strip()
            if not link or not exact_title_match(company_name, title):
                continue

            key = (title.casefold(), publisher.casefold())
            if key in seen:
                continue
            seen.add(key)

            published = item.findtext("pubDate")
            try:
                published_at = parsedate_to_datetime(published).astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if published else None
            except Exception:
                published_at = None

            mention = {
                "id": "google-news-title-" + hashlib.sha256(f"{organisation_number}|{title}|{publisher}".encode("utf-8")).hexdigest()[:24],
                "organisation_number": organisation_number,
                "platform": "news",
                "signal_type": "public_mention",
                "source_url": link,
                "retrieved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "published_at": published_at,
                "content_sha256": hashlib.sha256(raw + title.encode("utf-8") + publisher.encode("utf-8")).hexdigest(),
                "exact_entity": True,
                "identity_proof": [
                    {"type": "exact_legal_name_in_news_title", "value": company_name},
                    {"type": "publisher_label", "value": publisher},
                ],
                "acquisition_mode": "rights_review_experiment",
                "rights_status": "review_required",
                "source_class": "public_news",
                "evidence_span": title,
                "text": title,
                "publisher": publisher,
                "strategy": "independent_news_discovery",
            }
            mentions.append(mention)
            if len(mentions) >= limit:
                break

        emit_crawl_event(
            "google_news_search_completed",
            organisation_number=organisation_number,
            company_name=company_name,
            url=url,
            accepted=len(mentions),
            total_items=len(root.findall(".//item")),
        )

        if mentions:
            return evidence("google_news", "available", "google_news_rss", url, value=mentions, note="Exact-title company mentions matched in Google News RSS; rights review still required before publishing or using as claim evidence.", effective_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), content_sha256=hashlib.sha256(raw).hexdigest())
        return evidence("google_news", "not_found", "google_news_rss", url, value=[], note="No exact-title company mentions matched the Google News RSS results for this company.", effective_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), content_sha256=hashlib.sha256(raw).hexdigest())
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:180]}"
        emit_crawl_event("google_news_search_failed", organisation_number=organisation_number, company_name=company_name, url=url, error=error)
        return evidence("google_news", "source_error", "google_news_rss", url, value=[], note=error)


def fetch_official_modules(org: str, modules: set[str], fetcher: Callable[[str], FetchResult] = fetch_json) -> tuple[dict[str, Any], list[FetchResult]]:
    records: dict[str, Any] = {}
    metrics: list[FetchResult] = []
    endpoints = {
        "registry_live": (BRREG_ENTITY.format(org=org), "official_registry_live"),
        "financials": (BRREG_ACCOUNTS.format(org=org), "official_annual_accounts"),
        "financial_history": (BRREG_ACCOUNT_YEARS.format(org=org), "official_annual_account_copies"),
        "roles": (BRREG_ROLES.format(org=org), "official_roles"),
        "group": (BRREG_GROUP.format(org=org), "official_group_structure"),
        "locations": (BRREG_SUBUNITS.format(org=org), "official_subunits"),
    }
    for module, (url, source_type) in endpoints.items():
        if module not in modules:
            continue
        if fetcher is fetch_json:
            result = fetch_json(url, module=module, organisation_number=org)
        else:
            result = fetcher(url)
        metrics.append(result)
        normalized = None
        if result.status == 200:
            normalized = normalize_entity(result.body) if module == "registry_live" else normalize_financials(result.body) if module == "financials" else normalize_financial_history(result.body, org) if module == "financial_history" else normalize_roles(result.body) if module == "roles" else normalize_locations(result.body) if module == "locations" else result.body
        records[module] = _classified(module, source_type, result, value=normalized)
    return records, metrics
