from __future__ import annotations

import re
import unicodedata
import urllib.parse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .website import normalize_homepage


DISCOVERY_PROVIDER_ORDER = ("exa", "tavily")
DISCOVERY_COST_CAP_USD = 0.01


def make_discovery_summary(*, method: str, providers_used: list[str] | None = None, candidates: list[dict[str, Any]] | None = None, cost_usd: float = 0.0) -> dict[str, Any]:
    return {
        "method": method,
        "providers_used": list(providers_used or []),
        "candidates": [dict(item) for item in (candidates or [])],
        "cost_usd": float(cost_usd or 0.0),
    }


def apply_method2_discovery(website: dict[str, Any] | None, *, provider: str, selected_url: str | None, rank: int | None = None, cost_usd: float = 0.0) -> dict[str, Any]:
    record = website if isinstance(website, dict) else {}
    value = dict(record.get("value") or {})
    candidate = {
        "url": selected_url or "",
        "provider": provider,
        "rank": rank if rank is not None else 1,
    }
    if not selected_url:
        candidate = {}
    value["discovery"] = make_discovery_summary(
        method=provider,
        providers_used=[provider],
        candidates=[candidate] if candidate else [],
        cost_usd=float(cost_usd or 0.0),
    )
    value["cost_usd"] = float(cost_usd or 0.0)
    record["value"] = value
    return record


def mark_direct_discovery(website: dict[str, Any] | None) -> dict[str, Any]:
    record = website if isinstance(website, dict) else {}
    value = dict(record.get("value") or {})
    value["discovery"] = make_discovery_summary(method="direct", providers_used=[], candidates=[], cost_usd=0.0)
    value["cost_usd"] = 0.0
    record["value"] = value
    return record


BLOCKED_DISCOVERY_HOSTS = {
    "proff.no", "purehelp.no", "1881.no", "gulesider.no", "firmalisten.no", "companywall.no",
    "firmadatabasen.no", "sokfirma.no", "yra.no", "northdata.com", "nor47business.com",
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "tiktok.com",
}
GENERIC_NAME_TOKENS = {"as", "asa", "ans", "da", "enk", "sa", "nuf", "company", "norge", "norway", "gruppen", "group"}


def build_company_search_query(profile: dict[str, Any]) -> str:
    name = " ".join(str(profile.get("name") or "").split())
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    municipality = " ".join(str(profile.get("municipality") or "").split())
    if not name or not org:
        raise ValueError("Company discovery requires a legal name and organisation number")
    location = f" {municipality}" if municipality else ""
    return f'"{name}" {org}{location}'


def parse_brave_web_results(payload: dict[str, Any], *, query: str) -> list[dict[str, Any]]:
    results = (payload.get("web") or {}).get("results") or []
    parsed = []
    for rank, result in enumerate(results, start=1):
        if not isinstance(result, dict) or not result.get("url"):
            continue
        parsed.append({
            "url": result.get("url"),
            "title": result.get("title") or "",
            "snippet": result.get("description") or "",
            "rank": rank,
            "provider": "brave_search_api",
            "query": query,
        })
    return parsed


def parse_tavily_web_results(payload: dict[str, Any], *, query: str) -> list[dict[str, Any]]:
    results = payload.get("results") or []
    parsed = []
    for rank, result in enumerate(results, start=1):
        if not isinstance(result, dict) or not result.get("url"):
            continue
        parsed.append({
            "url": result.get("url"),
            "title": result.get("title") or "",
            "snippet": result.get("content") or "",
            "rank": rank,
            "provider": "tavily_search_api",
            "query": query,
        })
    return parsed


def parse_exa_web_results(payload: dict[str, Any], *, query: str) -> list[dict[str, Any]]:
    results = payload.get("results") or []
    parsed = []
    for rank, result in enumerate(results, start=1):
        if not isinstance(result, dict) or not result.get("url"):
            continue
        highlights = result.get("highlights") or []
        snippet = " ".join(highlights) if highlights else (result.get("summary") or result.get("text") or "")
        parsed.append({
            "url": result.get("url"),
            "title": result.get("title") or "",
            "snippet": snippet,
            "rank": rank,
            "provider": "exa_search_api",
            "query": query,
        })
    return parsed


def _tokens(value: Any) -> list[str]:
    text = str(value or "").translate(str.maketrans({"ø": "o", "å": "a", "æ": "ae", "Ø": "O", "Å": "A", "Æ": "AE"}))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return [token for token in re.findall(r"[a-z0-9]+", text) if len(token) > 1]


def score_search_candidate(profile: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_homepage(result.get("url"))
    if not normalized:
        return {"status": "rejected", "score": 0.0, "publishable_candidate": False, "reasons": ["invalid HTTP(S) candidate URL"]}
    host = (urllib.parse.urlparse(normalized).hostname or "").casefold().removeprefix("www.")
    if any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_DISCOVERY_HOSTS):
        return {"status": "rejected", "score": 0.0, "publishable_candidate": False, "url": normalized, "host": host, "reasons": ["directory, aggregator, or social host is not a company website candidate"]}

    name_tokens = [token for token in _tokens(profile.get("name")) if token not in GENERIC_NAME_TOKENS]
    title_tokens = _tokens(result.get("title"))
    snippet_tokens = _tokens(result.get("snippet"))
    evidence_tokens = set(title_tokens + snippet_tokens + _tokens(host))
    host_compact = "".join(_tokens(host))
    name_compact = "".join(name_tokens)
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    evidence_digits = re.sub(r"\D", "", f"{result.get('title', '')} {result.get('snippet', '')}")
    municipality_tokens = set(_tokens(profile.get("municipality")))

    org_match = bool(org and org in evidence_digits)
    all_name_tokens = bool(name_tokens and set(name_tokens).issubset(evidence_tokens))
    all_name_tokens_in_title = bool(name_tokens and set(name_tokens).issubset(set(title_tokens)))
    name_in_host = bool(name_compact and name_compact in host_compact)
    municipality_match = bool(municipality_tokens and municipality_tokens <= set(snippet_tokens))
    score = 0.0
    reasons = []
    if org_match:
        score += 0.75
        reasons.append("exact organisation number appears in result evidence")
    if all_name_tokens_in_title:
        score += 0.45
        reasons.append("all distinctive legal-name tokens appear in the result title")
    elif all_name_tokens:
        score += 0.25
        reasons.append("all distinctive legal-name tokens appear across result evidence")
    if name_in_host:
        score += 0.3
        reasons.append("normalized legal name appears in candidate hostname")
    if municipality_match:
        score += 0.1
        reasons.append("registry municipality appears in result snippet")
    score = min(score, 1.0)
    # Registry/directory pages routinely reproduce both the legal name and org
    # number. A candidate must therefore also have the distinctive company name
    # in its hostname before it is worth crawling as a company-owned website.
    publishable_candidate = score >= 0.75 and name_in_host and (org_match or all_name_tokens_in_title)
    return {
        "status": "accepted_for_crawl" if publishable_candidate else "review" if score >= 0.6 else "rejected",
        "score": score,
        "publishable_candidate": publishable_candidate,
        "url": normalized,
        "host": host,
        "rank": result.get("rank"),
        "provider": result.get("provider"),
        "query": result.get("query"),
        "reasons": reasons or ["insufficient exact-entity evidence"],
        "method": "deterministic_search_candidate_identity_v1",
    }


def choose_search_candidate(profile: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    assessed = [score_search_candidate(profile, result) for result in results]
    assessed.sort(key=lambda item: (-item.get("score", 0.0), item.get("rank") or 10_000, item.get("url") or ""))
    accepted = [item for item in assessed if item.get("publishable_candidate")]
    return {
        "selected": accepted[0] if accepted else None,
        "candidates": assessed,
        "abstained": not accepted,
        "policy": "A search result is only a crawl candidate. Publication still requires fetched-page exact-entity verification.",
    }


def discovery_budget_remaining(spent_usd: float, *, cap_usd: float = DISCOVERY_COST_CAP_USD) -> float:
    return max(0.0, float(cap_usd) - float(spent_usd or 0.0))


def should_try_method2(profile: dict[str, Any], *, website: dict[str, Any] | None = None, assessment: dict[str, Any] | None = None) -> bool:
    if not isinstance(profile, dict):
        return False
    value = website.get("value") if isinstance(website, dict) else {}
    if website and website.get("status") == "available":
        if assessment and assessment.get("publishable"):
            return False
        if value.get("final_url"):
            return False
    if not profile.get("name"):
        return False
    return True


def discover_with_provider_fallback(
    profile: dict[str, Any],
    *,
    provider_searchers: dict[str, Callable[[dict[str, Any]], tuple[list[dict[str, Any]], dict[str, Any]]]],
    fetch_website: Callable[..., tuple[dict[str, Any], dict[str, Any]]] | None = None,
    identity_gate: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
    provider_order: tuple[str, ...] = DISCOVERY_PROVIDER_ORDER,
    budget_cap_usd: float = DISCOVERY_COST_CAP_USD,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Execute Exa -> Tavily as a deterministic fallback ladder.

    The provider search result itself is transient and never persisted as claim evidence.
    Only the independently crawled and identity-gated page becomes the final website record.
    """
    providers_used: list[str] = []
    candidates: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    selected_url: str | None = None
    selected_provider: str | None = None
    spent_usd = 0.0
    last_decision: dict[str, Any] | None = None
    last_error: str | None = None
    final_assessment: dict[str, Any] | None = None

    for provider_name in provider_order:
        remaining_budget = discovery_budget_remaining(spent_usd, cap_usd=budget_cap_usd)
        if remaining_budget <= 0.0:
            break
        if provider_name not in provider_searchers:
            continue
        try:
            results, metadata = provider_searchers[provider_name](profile)
        except Exception as exc:  # pragma: no cover - defensive fallback wiring only
            last_error = f"{provider_name}_provider_error:{type(exc).__name__}"
            continue
        cost_usd = float((metadata or {}).get("cost_usd", 0.0) or 0.0)
        if cost_usd > remaining_budget:
            cost_usd = remaining_budget
        decision = choose_search_candidate(profile, results)
        last_decision = decision
        providers_used.append(provider_name)
        candidates.extend(decision.get("candidates") or [])
        if not decision.get("selected"):
            spent_usd += cost_usd
            continue
        selected_provider = provider_name
        selected = decision["selected"]
        selected_url = selected.get("url")
        if not selected_url:
            spent_usd += cost_usd
            continue
        if fetch_website is None:
            spent_usd += cost_usd
            break
        try:
            try:
                website, _ = fetch_website(selected_url, timeout=timeout, organisation_number=profile.get("organisation_number"))
            except TypeError:
                website, _ = fetch_website(selected_url)
        except Exception as exc:  # pragma: no cover - fallback orchestration only
            last_error = f"fetch_error:{type(exc).__name__}"
            spent_usd += cost_usd
            continue
        if identity_gate is not None:
            gated = identity_gate(profile, website)
            website = (gated or {}).get("website") or website
            assessment = (gated or {}).get("assessment")
            final_assessment = assessment
        else:
            assessment = None
            final_assessment = None
        spent_usd += cost_usd
        if assessment and assessment.get("publishable") and website and website.get("status") == "available":
            return {
                "method": provider_name,
                "providers_used": providers_used,
                "selected_url": selected_url,
                "candidate": selected,
                "website": website,
                "assessment": assessment,
                "candidates": candidates,
                "cost_usd": round(spent_usd, 4),
                "budget_cap_usd": float(budget_cap_usd),
                "budget_exhausted": spent_usd >= budget_cap_usd,
                "transient_results_only": True,
            }
        if not decision.get("selected"):
            continue
        # Candidate was valid enough for crawl, but the fetched page did not pass the exact-entity identity gate.
        # Continue to the next provider only if the budget remains.
        if discovery_budget_remaining(spent_usd, cap_usd=budget_cap_usd) <= 0.0:
            break
        continue

    return {
        "method": "direct" if not selected_url else selected_provider or "method2",
        "providers_used": providers_used,
        "selected_url": selected_url,
        "candidate": selected,
        "website": None,
        "assessment": final_assessment,
        "candidates": candidates,
        "cost_usd": round(spent_usd, 4),
        "budget_cap_usd": float(budget_cap_usd),
        "budget_exhausted": spent_usd >= budget_cap_usd,
        "transient_results_only": True,
        "last_error": last_error,
    }


EXA_ENDPOINT = "https://api.exa.ai/search"
TAVILY_ENDPOINT = "https://api.tavily.com/search"
METHOD2_COST_CAP_USD = 0.01
METHOD2_PROVIDER_RESERVE_USD = 0.005


def _provider_search(provider: str, profile: dict[str, Any], api_key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from .telemetry import record_request

    query = build_company_search_query(profile)
    if provider == "exa":
        endpoint = EXA_ENDPOINT
        payload = {
            "query": query,
            "numResults": 10,
            "type": "fast",
        }
        headers = {"x-api-key": api_key}
    else:
        endpoint = TAVILY_ENDPOINT
        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": 10,
            "include_answer": False,
            "include_raw_content": False,
            "include_usage": True,
            "country": "norway",
        }
        headers = {"Authorization": f"Bearer {api_key}"}
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=15.0) as response:
            raw = response.read()
            status = response.status
        data = json.loads(raw)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        record_request(
            module="website_discovery", provider=provider.title(), operation="POST search",
            success=True, status=status, duration_ms=elapsed_ms,
            organisation_number=str(profile.get("organisation_number") or ""),
        )
        if provider == "exa":
            results = parse_exa_web_results(data, query=query)
            cost_usd = float(((data.get("costDollars") or {}).get("total")) or 0.0)
            cost_basis = "provider_reported_costDollars"
        else:
            results = parse_tavily_web_results(data, query=query)
            usage = data.get("usage") or {}
            credits = float(usage.get("credits", 1) or 0)
            per_credit = float(os.environ.get("TAVILY_USD_PER_CREDIT", str(METHOD2_PROVIDER_RESERVE_USD)))
            cost_usd = credits * per_credit
            cost_basis = "usage.credits x TAVILY_USD_PER_CREDIT"
        return results, {
            "success": True,
            "status": status,
            "duration_ms": elapsed_ms,
            "cost_usd": cost_usd,
            "cost_basis": cost_basis,
        }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        status = getattr(exc, "code", 0) or 0
        elapsed_ms = int((time.monotonic() - started) * 1000)
        record_request(
            module="website_discovery", provider=provider.title(), operation="POST search",
            success=False, status=status, duration_ms=elapsed_ms,
            organisation_number=str(profile.get("organisation_number") or ""), error=type(exc).__name__,
        )
        return [], {
            "success": False,
            "status": status,
            "duration_ms": elapsed_ms,
            "cost_usd": 0.0,
            "error": type(exc).__name__,
        }


def discover_profiles_method2(
    profiles: list[dict[str, Any]],
    *,
    exa_api_key: str = "",
    tavily_api_key: str = "",
    cost_cap_usd: float = METHOD2_COST_CAP_USD,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run bounded Exa -> Tavily discovery for weak Method 1 profiles only."""
    from .crawl_events import emit_crawl_event
    from .identity import apply_website_identity_gate
    from .telemetry import utc_now
    from .website import fetch_website

    totals = {"exa_calls": 0, "tavily_calls": 0, "failures": 0, "triggered": 0, "cost_usd": 0.0}
    company_outcomes: list[dict[str, Any]] = []
    company_costs: list[dict[str, Any]] = []
    providers = (("exa", exa_api_key), ("tavily", tavily_api_key))

    eligible_count = 0
    for profile in profiles:
        org = str(profile.get("organisation_number") or "")
        website = (profile.get("evidence") or {}).get("website") or {}
        value = website.get("value") if isinstance(website.get("value"), dict) else {}
        identity = value.get("identity_assessment") or {}
        useful_text = len(str(value.get("main_text_excerpt") or "").strip()) >= 100
        strong_method1 = website.get("status") == "available" and bool(identity.get("publishable")) and useful_text
        emit_crawl_event(
            "method1_completed", organisation_number=org,
            status=website.get("status") or "not_available",
            publishable=bool(identity.get("publishable")), useful_text=useful_text,
        )
        if strong_method1:
            direct = {"method": "direct", "providers_used": [], "candidates": [], "cost_usd": 0.0}
            website["discovery"] = direct
            value["discovery"] = direct
            profile.setdefault("evidence", {})["website"] = website
            company_outcomes.append({"organisation_number": org, "outcome": "skipped", "provider": ""})
            company_costs.append({"organisation_number": org, "cost_usd": 0.0})
            continue

        if limit is not None and eligible_count >= limit:
            website["discovery"] = {"method": "direct", "providers_used": [], "candidates": [], "cost_usd": 0.0}
            profile.setdefault("evidence", {})["website"] = website
            company_outcomes.append({"organisation_number": org, "outcome": "skipped", "provider": ""})
            company_costs.append({"organisation_number": org, "cost_usd": 0.0})
            continue

        configured = [(name, key) for name, key in providers if key.strip()]
        if not configured or not profile.get("name"):
            website["discovery"] = {"method": "direct", "providers_used": [], "candidates": [], "cost_usd": 0.0}
            profile.setdefault("evidence", {})["website"] = website
            company_outcomes.append({"organisation_number": org, "outcome": "skipped", "provider": ""})
            company_costs.append({"organisation_number": org, "cost_usd": 0.0})
            continue

        eligible_count += 1
        totals["triggered"] += 1
        emit_crawl_event("method2_triggered", organisation_number=org, reason="method1_weak_or_failed")
        providers_used: list[str] = []
        candidates: list[dict[str, Any]] = []
        spent_usd = 0.0
        outcome = "abstained"
        failure_types: list[str] = []
        selected_method = "direct"

        for provider, api_key in configured:
            if len(providers_used) >= (1 if provider == "exa" else 2):
                break
            reserve = (
                float(os.environ.get("TAVILY_USD_PER_CREDIT", str(METHOD2_PROVIDER_RESERVE_USD)))
                if provider == "tavily" else METHOD2_PROVIDER_RESERVE_USD
            )
            remaining = max(0.0, cost_cap_usd - spent_usd)
            emit_crawl_event("budget_check", organisation_number=org, provider=provider, remaining_usd=round(remaining, 6))
            if remaining < reserve:
                break

            providers_used.append(provider)
            totals[f"{provider}_calls"] += 1
            emit_crawl_event(f"{provider}_search_started", organisation_number=org)
            try:
                results, metadata = _provider_search(provider, profile, api_key)
            except Exception as exc:
                results, metadata = [], {"success": False, "cost_usd": 0.0, "error": type(exc).__name__, "status": 0}
            cost_usd = float(metadata.get("cost_usd") or 0.0)
            spent_usd += cost_usd
            totals["cost_usd"] += cost_usd
            if not metadata.get("success"):
                totals["failures"] += 1
                failure_types.append(str(metadata.get("error") or "provider_error"))
            emit_crawl_event(
                f"{provider}_search_completed", organisation_number=org,
                status=metadata.get("status", 0), candidate_count=len(results),
                cost_usd=round(cost_usd, 6), success=bool(metadata.get("success")),
            )
            decision = choose_search_candidate(profile, results)
            accepted = [item for item in decision.get("candidates") or [] if item.get("publishable_candidate")]
            for candidate in accepted:
                candidates.append({
                    "url": candidate.get("url"),
                    "provider": provider,
                    "rank": candidate.get("rank") or 1,
                })
            selected = decision.get("selected")
            if not selected:
                continue

            emit_crawl_event("candidate_selected", organisation_number=org, provider=provider, url=selected.get("url"))
            try:
                fetched, _ = fetch_website(
                    selected["url"], organisation_number=org,
                )
                gated = apply_website_identity_gate(profile, fetched)
                fetched = gated["website"]
                assessment = gated.get("assessment") or {}
            except Exception as exc:
                failure_types.append(f"independent_crawl:{type(exc).__name__}")
                continue

            if fetched.get("status") == "available" and assessment.get("publishable"):
                selected_method = provider
                discovery = {
                    "method": selected_method,
                    "providers_used": list(providers_used),
                    "candidates": candidates,
                    "cost_usd": round(spent_usd, 6),
                }
                fetched_value = fetched.get("value") or {}
                fetched_value["discovery"] = discovery
                fetched_value["cost_usd"] = round(spent_usd, 6)
                fetched["value"] = fetched_value
                fetched["discovery"] = discovery
                profile.setdefault("evidence", {})["website"] = fetched
                outcome = "verified"
                break

        discovery = {
            "method": selected_method,
            "providers_used": list(providers_used),
            "candidates": candidates,
            "cost_usd": round(spent_usd, 6),
        }
        if outcome != "verified":
            if failure_types:
                outcome = "failed" if not candidates else "quarantined"
            website["discovery"] = discovery
            if isinstance(website.get("value"), dict):
                website["value"]["discovery"] = discovery
                website["value"]["cost_usd"] = round(spent_usd, 6)
            profile.setdefault("evidence", {})["website"] = website
        totals["cost_usd"] = round(totals["cost_usd"], 6)
        company_outcomes.append({"organisation_number": org, "outcome": outcome, "provider": providers_used[-1] if providers_used else ""})
        company_costs.append({"organisation_number": org, "cost_usd": round(spent_usd, 6), "failure_types": failure_types})
        emit_crawl_event("cost_recorded", organisation_number=org, cost_usd=round(spent_usd, 6), budget_cap_usd=cost_cap_usd)

    return {
        "generated_at": utc_now(),
        "provider_order": ["exa", "tavily"],
        "brave": "disabled",
        "budget_cap_usd_per_company": cost_cap_usd,
        "totals": totals,
        "company_costs": company_costs,
        "company_outcomes": company_outcomes,
    }
