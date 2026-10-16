"""Claim families and member-claim builders for official BRREG data (P1-1).

Pure functions only. No imports from this package.
"""
from __future__ import annotations

from typing import Any

FINANCIAL_METRICS: tuple[str, ...] = (
    "revenue", "operating_result", "profit_before_tax", "annual_result",
    "assets", "equity", "debt",
)

CLAIM_FAMILIES: dict[str, tuple[str, ...]] = {
    "financials": tuple(f"financials.{metric}" for metric in FINANCIAL_METRICS),
    "roles": ("role",),
    "locations": ("registered_workplace",),
    "group": ("group_relation",),
}

MEMBER_TO_FAMILY: dict[str, str] = {
    member: family for family, members in CLAIM_FAMILIES.items() for member in members
}

OPTIONAL_CLAIM_KEYS: frozenset[str] = frozenset({"claim_key", "reporting_period", "effective_at"})


# ---------------------------------------------------------------------------
# Span helpers (P1-2)
# ---------------------------------------------------------------------------

REGISTRY_SOURCE_PATHS: dict[str, str] = {
    "legal_name": "navn",
    "legal_form": "organisasjonsform.kode",
    "employees": "antallAnsatte",
    "industry_code": "naeringskode1.kode",
    "municipality": "forretningsadresse.kommune",
    "business_address": "forretningsadresse",
    "bankrupt": "konkurs",
    "liquidating": "underAvvikling",
    "latest_submitted_accounts": "sisteInnsendteAarsregnskap",
    "industry": "naeringskode1.beskrivelse",
    "municipality_number": "forretningsadresse.kommunenummer",
    "postal_address": "postadresse",
    "registry_declared_website": "hjemmeside",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def render_span_value(value: Any) -> str:
    if value is None:
        return "(empty)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        parts: list[str] = []
        lines = value.get("adresse")
        if isinstance(lines, list):
            parts.extend(_text(line) for line in lines if _text(line))
        place = " ".join(p for p in (_text(value.get("postnummer")), _text(value.get("poststed"))) if p)
        if place:
            parts.append(place)
        for key in ("kommune", "land"):
            if _text(value.get(key)):
                parts.append(_text(value.get(key)))
        if parts:
            return ", ".join(parts)
        return ", ".join(f"{k}={_text(v)}" for k, v in sorted(value.items()) if not isinstance(v, (dict, list)))
    if isinstance(value, list):
        return "; ".join(render_span_value(item) for item in value)
    return _text(value)


def registry_span(field: str, value: Any) -> str:
    return f"{REGISTRY_SOURCE_PATHS.get(field, field)}: {render_span_value(value)}"[:400]


def module_singleton_span(field: str, value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    if field == "financial_history":
        years = [str(year) for year in value.get("years") or []]
        return ("Filed annual-account years: " + (", ".join(years) if years else "(none)"))[:400]
    if field == "accounting_obligation":
        return (f"classification: {_text(value.get('classification'))}; "
                f"legal_form: {_text(value.get('legal_form'))}; "
                f"ruleset: {_text(value.get('ruleset_version'))}")[:400]
    return None


# ---------------------------------------------------------------------------
# Member-spec builders (Phase 2)
# ---------------------------------------------------------------------------

def financial_member_specs(value: Any) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    records = value.get("records") if isinstance(value, dict) else None
    for record in records or []:
        if not isinstance(record, dict):
            continue
        period = record.get("period") if isinstance(record.get("period"), dict) else {}
        start = _text(period.get("fraDato")) or None
        end = _text(period.get("tilDato"))
        if not end:
            continue
        account_type = _text(record.get("account_type")) or "UNKNOWN"
        currency = _text(record.get("currency")) or None
        for metric in FINANCIAL_METRICS:
            amount = record.get(metric)
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                amount = None
            shown = "(empty)" if amount is None else f"{amount} {currency or ''}".strip()
            specs.append({
                "field": f"financials.{metric}",
                "value": None if amount is None else {"amount": amount, "currency": currency},
                "claim_key": f"financials.{metric}:{account_type}:{end}",
                "reporting_period": {"start": start, "end": end},
                "span": f"Regnskap {_text(record.get('record_id'))} {account_type} {start or '?'}..{end}: {metric} = {shown}",
            })
    return specs


def role_member_specs(value: Any) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    roles = value.get("roles") if isinstance(value, dict) else None
    for item in roles or []:
        if not isinstance(item, dict) or item.get("inactive"):
            continue
        code = _text(item.get("role_code"))
        who = _text(item.get("organisation_number")) or _text(item.get("name"))
        if not code or not who:
            continue
        key = f"role:{code}:{who}"
        if key in seen:
            continue
        seen.add(key)
        spec: dict[str, Any] = {
            "field": "role",
            "value": {name: item.get(name) for name in ("name", "organisation_number", "role_code", "role", "group_code", "group")},
            "claim_key": key,
            "span": f"{_text(item.get('role')) or code}: {_text(item.get('name')) or who}",
        }
        if _text(item.get("last_changed")):
            spec["effective_at"] = _text(item.get("last_changed"))
        specs.append(spec)
    return specs


def location_member_specs(value: Any) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    locations = value.get("locations") if isinstance(value, dict) else None
    for item in locations or []:
        if not isinstance(item, dict):
            continue
        org = _text(item.get("organisation_number"))
        if not org or org in seen:
            continue
        seen.add(org)
        address = item.get("address") if isinstance(item.get("address"), dict) else {}
        place = " ".join(part for part in (_text(address.get("postnummer")), _text(address.get("poststed"))) if part)
        specs.append({
            "field": "registered_workplace",
            "value": {name: item.get(name) for name in ("organisation_number", "name", "address", "industry", "employees")},
            "claim_key": f"registered_workplace:{org}",
            "span": f"Underenhet {org} {_text(item.get('name'))}" + (f", {place}" if place else ""),
        })
    return specs


_GROUP_SKIP_KEYS = frozenset({"organisasjonsform", "knytningsform"})


def _group_nodes(node: Any, parent_org: str, parent_name: str, out: list[tuple[dict[str, Any], str, str]]) -> None:
    if isinstance(node, list):
        for child in node:
            _group_nodes(child, parent_org, parent_name, out)
        return
    if not isinstance(node, dict):
        return
    org = _text(node.get("organisasjonsnummer"))
    name = _text(node.get("navn"))
    if org:
        out.append((node, parent_org, parent_name))
    for key, child in node.items():
        if key in _GROUP_SKIP_KEYS or not isinstance(child, (list, dict)):
            continue
        _group_nodes(child, org or parent_org, name or parent_name, out)


def group_member_specs(value: Any, organisation_number: str) -> list[dict[str, Any]]:
    nodes: list[tuple[dict[str, Any], str, str]] = []
    _group_nodes(value, "", "", nodes)
    target = _text(organisation_number)
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node, tree_parent_org, tree_parent_name in nodes:
        org = _text(node.get("organisasjonsnummer"))
        if not org or org == target:
            continue
        parent_org = _text(node.get("parentOrganisasjonsnummer")) or tree_parent_org
        parent_name = _text(node.get("parentNavn")) or tree_parent_name
        link = node.get("knytningsform") if isinstance(node.get("knytningsform"), dict) else {}
        key = f"group_relation:{parent_org or '-'}:{org}"
        if key in seen:
            continue
        seen.add(key)
        spec: dict[str, Any] = {
            "field": "group_relation",
            "value": {
                "organisation_number": org,
                "name": _text(node.get("navn")) or None,
                "parent_organisation_number": parent_org or None,
                "parent_name": parent_name or None,
                "link_code": _text(link.get("kode")) or None,
                "link_description": _text(link.get("beskrivelse")) or None,
                "ownership_basis": _text(node.get("grunnlag")) or None,
                "level": node.get("nivaa"),
            },
            "claim_key": key,
            "span": (
                f"Konsernstruktur: {_text(node.get('navn'))} ({org}), "
                f"{_text(link.get('beskrivelse')) or 'relasjon'} under {parent_name or '-'} ({parent_org or '-'})"
                + (f", grunnlag {_text(node.get('grunnlag'))}" if _text(node.get("grunnlag")) else "")
            ),
        }
        if _text(node.get("dato")):
            spec["effective_at"] = _text(node.get("dato"))
        specs.append(spec)
    return specs


def build_member_specs(family: str, value: Any, organisation_number: str) -> list[dict[str, Any]]:
    if family == "financials":
        return financial_member_specs(value)
    if family == "roles":
        return role_member_specs(value)
    if family == "locations":
        return location_member_specs(value)
    if family == "group":
        return group_member_specs(value, organisation_number)
    return []


def explode_legacy_claims(claims: list[dict[str, Any]], organisation_number: str) -> list[dict[str, Any]]:
    """Turn available legacy blob claims (field == family slot) into member claims.

    Used by change detection so a previous envelope in the old blob shape compares
    item-by-item with a current envelope in the member shape. Claims that are not
    available blobs, and blobs whose shape is not recognised, are returned unchanged.
    """
    out: list[dict[str, Any]] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        field = claim.get("field")
        if field in CLAIM_FAMILIES and claim.get("availability") == "available" and isinstance(claim.get("value"), dict):
            specs = build_member_specs(str(field), claim["value"], organisation_number)
            if specs:
                for spec in specs:
                    available = spec["value"] is not None
                    member: dict[str, Any] = {
                        "field": spec["field"],
                        "value": spec["value"],
                        "availability": "available" if available else "not_available",
                        "confidence": claim.get("confidence") if available else None,
                        "evidence_ids": list(claim.get("evidence_ids") or []),
                        "claim_key": spec["claim_key"],
                    }
                    for optional in ("reporting_period", "effective_at"):
                        if optional in spec:
                            member[optional] = spec[optional]
                    out.append(member)
                continue
        out.append(claim)
    return out
