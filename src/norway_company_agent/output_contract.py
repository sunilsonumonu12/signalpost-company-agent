from __future__ import annotations

from typing import Any

from .changes import diff_claims
from .website import normalize_website_value


class EvidenceIntegrityError(Exception):
    """Raised when a claim references an evidence ID that does not exist in the envelope."""


class ClaimSchemaError(Exception):
    """Raised when one or more claims in the envelope violate the canonical claim schema."""


def assert_evidence_integrity(envelope: dict[str, Any]) -> None:
    """Verify that every evidence_id in every claim resolves to an existing evidence[].id.

    Raises EvidenceIntegrityError listing all dangling references if any are found.
    Passes silently when the envelope is clean.
    """
    known_ids: set[str] = {item["id"] for item in envelope.get("evidence", []) if isinstance(item, dict) and "id" in item}
    dangling: list[str] = []
    for claim in envelope.get("claims", []):
        if not isinstance(claim, dict):
            continue
        field = claim.get("field", "<unknown>")
        for ev_id in claim.get("evidence_ids", []):
            if ev_id not in known_ids:
                dangling.append(f"claim '{field}' references missing evidence id '{ev_id}'")
    if dangling:
        raise EvidenceIntegrityError(
            f"Evidence integrity check failed ({len(dangling)} dangling reference(s)):\n"
            + "\n".join(f"  - {d}" for d in dangling)
        )


ALLOWED_AVAILABILITY = frozenset(
    {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}
)

# Canonical required fields for every public claim.
CANONICAL_CLAIM_FIELDS: frozenset[str] = frozenset({"field", "value", "availability", "confidence", "evidence_ids"})


def assert_claim_schema_integrity(envelope: dict[str, Any]) -> None:
    """Verify that every claim in the envelope satisfies the canonical claim schema.

    Canonical schema (all fields required):
        field        – non-empty string identifying the claim subject
        value        – any JSON-serialisable value, including None
        availability – one of ALLOWED_AVAILABILITY
        confidence   – float or int in [0, 1], or None
        evidence_ids – list (may be empty)

    Raises ClaimSchemaError with a full list of violations if any are found.
    Passes silently when the envelope is clean.
    """
    violations: list[str] = []
    for idx, claim in enumerate(envelope.get("claims", [])):
        if not isinstance(claim, dict):
            violations.append(f"claims[{idx}] is not a dict")
            continue
        field_name = claim.get("field") or f"<claims[{idx}]>"

        # 1. All canonical fields must be present.
        missing = CANONICAL_CLAIM_FIELDS - claim.keys()
        if missing:
            violations.append(
                f"claim '{field_name}': missing required field(s): {sorted(missing)}"
            )

        # 2. field must be a non-empty string.
        if not isinstance(claim.get("field"), str) or not claim["field"].strip():
            violations.append(f"claim '{field_name}': 'field' must be a non-empty string")

        # 3. availability must be in the controlled vocabulary.
        avail = claim.get("availability")
        if avail not in ALLOWED_AVAILABILITY:
            violations.append(
                f"claim '{field_name}': invalid availability '{avail}'; "
                f"must be one of {sorted(ALLOWED_AVAILABILITY)}"
            )

        # 4. confidence must be None, or a number in [0, 1].
        conf = claim.get("confidence")
        if conf is not None:
            if not isinstance(conf, (int, float)) or isinstance(conf, bool):
                violations.append(
                    f"claim '{field_name}': confidence must be a number or None, got {type(conf).__name__}"
                )
            elif not (0.0 <= float(conf) <= 1.0):
                violations.append(
                    f"claim '{field_name}': confidence {conf} is outside [0, 1]"
                )

        # 5. evidence_ids must be a list.
        if "evidence_ids" in claim and not isinstance(claim["evidence_ids"], list):
            violations.append(
                f"claim '{field_name}': 'evidence_ids' must be a list, "
                f"got {type(claim['evidence_ids']).__name__}"
            )

        # 6. Module/claim contradiction: available claim must not carry a source_error note.
        #    This guards against _availability() returning 'available' when the underlying
        #    evidence record's status disagrees — detectable when the evidence item's own
        #    status field is 'failed' or 'source_error'.
        if avail == "available":
            ev_ids = claim.get("evidence_ids") or []
            ev_map = {
                item["id"]: item
                for item in envelope.get("evidence", [])
                if isinstance(item, dict) and "id" in item
            }
            for ev_id in ev_ids:
                ev_item = ev_map.get(ev_id)
                if ev_item and ev_item.get("status") in {"failed", "source_error"}:
                    violations.append(
                        f"claim '{field_name}': availability='available' contradicts "
                        f"evidence '{ev_id}' status='{ev_item['status']}'"
                    )

        # 7. Module/claim contradiction: failed/blocked claim must not carry a substantive value.
        if avail in {"failed", "blocked"} and claim.get("value") is not None:
            violations.append(
                f"claim '{field_name}': availability='{avail}' but value is not None "
                f"({type(claim['value']).__name__}) — failed/blocked claims must have value=None"
            )

    if violations:
        raise ClaimSchemaError(
            f"Claim schema integrity check failed ({len(violations)} violation(s)):\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


REGISTRY_CLAIM_FIELDS = (
    ("legal_name", "name"),
    ("legal_form", "legal_form"),
    ("employees", "employees"),
    ("industry_code", ("industry", "kode")),
    ("municipality", ("business_address", "kommune")),
    ("business_address", "business_address"),
    ("bankrupt", "bankrupt"),
    ("liquidating", "liquidating"),
    ("latest_submitted_accounts", "latest_submitted_accounts"),
)

MODULE_CLAIM_FIELDS = (
    "financials",
    "financial_history",
    "roles",
    "group",
    "locations",
    "accounting_obligation",
)

OBSERVATION_CLAIM_FIELDS = {
    "careers_page_found": "jobs",
    "jobs": "jobs",
    "news": "news",
    "activity": "activity",
    "workforce": "workforce",
    "workforce_snapshot": "workforce",
    "profile_metrics": "activity",
    "prior_year_financials": "prior_year_financials",
}


def to_public_envelope(row: dict[str, Any], *, metrics: dict[str, Any] | None = None, previous_envelope: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = row.get("profile") if isinstance(row.get("profile"), dict) else row
    organisation_number = str(profile.get("organisation_number") or row.get("organisation_number") or "")
    records = profile.get("evidence") if isinstance(profile.get("evidence"), dict) else {}
    claims: list[dict[str, Any]] = []
    evidence_items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    evidence_ids: dict[str, str] = {}

    def add_evidence(
        key: str,
        record: dict[str, Any],
        *,
        claim_span: str,
        availability: str,
        value: Any = None,
    ) -> str:
        if key in evidence_ids:
            return evidence_ids[key]
        item_id = f"ev-{len(evidence_items) + 1}"
        evidence_ids[key] = item_id
        source_class = str(record.get("source_class") or record.get("source_type") or "unknown")
        if source_class in {"registry_linked_company_website", "company_site"}:
            source_class = "company_owned"
        item = {
            "id": item_id,
            "source_url": str(record.get("source_url") or ""),
            "source_class": source_class,
            "retrieved_at": str(record.get("retrieved_at") or ""),
            "content_sha256": record.get("content_sha256") or "",
            "claim_span": claim_span[:400],
            "module": str(record.get("field") or key),
            "status": availability,
        }
        if value is not None:
            item["value"] = value
        evidence_items.append(item)
        return item_id

    registry = records.get("registry_live") if isinstance(records.get("registry_live"), dict) else None
    registry_value = registry.get("value") if registry and isinstance(registry.get("value"), dict) else {}
    if registry:
        availability = _availability(registry)
        if availability:
            registry_span = _claim_span(profile, registry)
            registry_id = add_evidence(
                "registry_live", registry, claim_span=registry_span,
                availability=availability, value=registry.get("value"),
            )
            for field, path in REGISTRY_CLAIM_FIELDS:
                claims.append(_claim(
                    field, _nested_get(registry_value, path) if availability == "available" else None,
                    availability, _confidence(availability, official=True), [registry_id],
                ))
            _append_error(errors, "registry_live", registry, availability)

    website_record = records.get("website") if isinstance(records.get("website"), dict) else None
    website_claim, website_error, website_evidence_value = _website_claim(registry_value, website_record)
    if website_claim:
        website_evidence = website_record or registry
        if website_evidence:
            website_availability = website_claim["availability"]
            website_id = add_evidence(
                "website" if website_record else "registry_live_website",
                website_evidence,
                claim_span=_claim_span(profile, website_evidence),
                availability=website_availability,
                value=website_evidence_value,
            )
            website_claim["evidence_ids"] = [website_id]
        claims.append(website_claim)
    if website_error:
        errors.append(website_error)

    # --- Website-derived contact and social claims ---
    # These fields are extracted during the website crawl and stored in the website
    # evidence value.  They are only emitted when an available website evidence record
    # exists, because without a crawl the data does not exist at all.
    _website_contact_claims(
        claims, records, evidence_ids, evidence_items, profile,
        add_evidence=add_evidence,
    )

    for field in MODULE_CLAIM_FIELDS:
        record = records.get(field)
        if not isinstance(record, dict):
            continue
        availability = _availability(record, field=field)
        if availability is None:
            continue
        value = record.get("value") if availability in {"available", "not_available"} else None
        evidence_id = add_evidence(
            field, record, claim_span=_claim_span(profile, record),
            availability=availability, value=value,
        )
        claims.append(_claim(field, value, availability, _confidence(availability, official=True), [evidence_id]))
        _append_error(errors, field, record, availability)

    for observation in profile.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        signal = str(observation.get("signal_type") or observation.get("field") or "")
        field = OBSERVATION_CLAIM_FIELDS.get(signal)
        if not field:
            continue
        value = observation.get("metrics")
        availability = _observation_availability(field, value)
        record = {
            "field": field,
            "source_url": observation.get("source_url") or "",
            "source_class": observation.get("source_class") or "unknown",
            "retrieved_at": observation.get("retrieved_at") or "",
            "content_sha256": observation.get("content_sha256"),
            "note": observation.get("evidence_span") or observation.get("claim_boundary"),
            "value": value,
        }
        evidence_id = add_evidence(
            f"observation:{observation.get('id') or field}",
            record,
            claim_span=str(observation.get("evidence_span") or _claim_span(profile, record)),
            availability=availability,
            value=value,
        )
        claims.append(_claim(field, value, availability, _confidence(availability), [evidence_id]))

    # Build the operations block.  When telemetry metrics are supplied by the
    # caller (e.g. from run_agent.py), use them for requests/runtime.  When
    # they are not (e.g. build_output_contract.py path), derive what we can
    # from the row's modules dict and evidence records.
    if metrics:
        ops = _operations(metrics)
        # Supplement with module-level detail derived from the row when present.
        row_ops = _derive_operations_from_row(row, profile)
        for key in ("successful_modules", "failed_modules", "not_found_modules",
                    "pages_crawled", "pages_failed", "retried_module_attempts"):
            if key in row_ops:
                ops.setdefault(key, row_ops[key])
        # Use derived cost when telemetry doesn't carry it.
        if not ops.get("third_party_cost_usd"):
            ops["third_party_cost_usd"] = row_ops.get("third_party_cost_usd", 0)
    else:
        ops = _derive_operations_from_row(row, profile)

    # Compute changes[] by comparing current normalized claims against the
    # previous envelope's normalized claims.  When no previous envelope is
    # supplied, changes[] is empty (first-run semantics).
    if previous_envelope is not None and isinstance(previous_envelope.get("claims"), list):
        previous_organisation_number = str(previous_envelope.get("organisation_number") or "")
        if previous_organisation_number and previous_organisation_number != organisation_number:
            raise ValueError(
                "Change detection requires a previous envelope for the same organisation number"
            )
        computed_changes = diff_claims(
            organisation_number,
            previous_envelope["claims"],
            claims,
        )
    else:
        computed_changes = []

    envelope = {
        "organisation_number": organisation_number,
        "run": {
            "run_id": str(row.get("run_id") or ""),
            "started_at": str(row.get("started_at") or ""),
            "completed_at": str(row.get("completed_at") or ""),
            "terminal_status": _terminal_status(row),
        },
        "claims": claims,
        "evidence": evidence_items,
        "changes": computed_changes,
        "errors": errors,
        "operations": ops,
    }
    assert_evidence_integrity(envelope)
    assert_claim_schema_integrity(envelope)
    return envelope


def _availability(record: dict[str, Any], *, field: str | None = None) -> str | None:
    status = record.get("status")
    if status == "not_fetched":
        return None
    if status == "blocked":
        return "blocked"
    if status == "not_applicable":
        return "not_applicable"
    if status == "source_error":
        return "failed"
    if status == "not_found":
        return "not_available"
    if status != "available":
        return "failed"
    if _is_empty_checked(field or str(record.get("field") or ""), record.get("value")):
        return "not_available"
    return "available"


def _is_empty_checked(field: str, value: Any) -> bool:
    if field == "locations" and isinstance(value, dict):
        return isinstance(value.get("locations"), list) and not value["locations"]
    if field == "roles" and isinstance(value, dict):
        return isinstance(value.get("roles"), list) and not value["roles"]
    return False


def _website_claim(
    registry_value: dict[str, Any], record: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, dict[str, str] | None, Any]:
    if record:
        availability = _availability(record, field="website")
        if availability is None:
            return None, None, None
        raw_value = record.get("value") if isinstance(record.get("value"), dict) else {}
        normalized = normalize_website_value(raw_value) if record.get("status") == "available" else raw_value
        identity = normalized.get("identity_assessment") if isinstance(normalized, dict) else None
        value = (raw_value.get("final_url") or record.get("source_url")) if isinstance(raw_value, dict) else record.get("source_url")
        if availability == "available" and isinstance(identity, dict) and identity and not identity.get("publishable"):
            availability = "ambiguous"
        score = identity.get("score") if isinstance(identity, dict) else None
        claim = _claim("official_website", value if availability in {"available", "ambiguous"} else None,
                       availability, _confidence(availability, identity_score=score), [])
        error = _error("official_website", "website", record, availability)
        return claim, error, normalized

    registered_site = registry_value.get("website")
    if registered_site:
        return _claim("official_website", registered_site, "available", 0.99, []), None, registry_value
    if registry_value:
        return _claim("official_website", None, "not_available", 0.8, []), None, registry_value
    return None, None, None


def _observation_availability(field: str, value: Any) -> str:
    if field == "jobs" and isinstance(value, dict) and not value.get("has_careers_page"):
        return "not_available"
    if field == "jobs" and value in (None, [], ""):
        return "not_available"
    return "available"


def _append_error(errors: list[dict[str, Any]], field: str, record: dict[str, Any], availability: str) -> None:
    error = _error(field, field, record, availability)
    if error:
        errors.append(error)


def _error(field: str, module: str, record: dict[str, Any], availability: str) -> dict[str, Any] | None:
    if availability not in {"failed", "blocked"}:
        return None
    # Map availability to a stable error_type code.
    error_type = "source_blocked" if availability == "blocked" else "source_failed"
    return {
        "field": field,
        "module": module,
        "error_type": error_type,
        "message": str(record.get("note") or availability),
    }


def _claim(
    field: str,
    value: Any,
    availability: str,
    confidence: float,
    evidence_ids: list[str],
) -> dict[str, Any]:
    if availability not in ALLOWED_AVAILABILITY:
        availability = "failed"
    return {
        "field": field,
        "value": value,
        "availability": availability,
        "confidence": confidence,
        "evidence_ids": evidence_ids,
    }


def _claim_span(profile: dict[str, Any], record: dict[str, Any]) -> str:
    parts: list[str] = []
    name = str(profile.get("name") or "").strip()
    org = str(profile.get("organisation_number") or "").strip()
    if name and org:
        parts.append(f"{name}, organisation number {org}")
    elif org:
        parts.append(f"organisation number {org}")
    value = record.get("value")
    if isinstance(value, dict):
        title = str(value.get("title") or "").strip()
        excerpt = str(value.get("main_text_excerpt") or "").strip()
        if title:
            parts.append(title)
        elif excerpt:
            parts.append(excerpt[:180])
    note = str(record.get("note") or "").strip()
    if note:
        parts.append(note)
    return ". ".join(parts)[:400]


def _confidence(availability: str, *, identity_score: Any = None, official: bool = False) -> float:
    if availability == "failed":
        return 0.0
    if availability == "ambiguous":
        try:
            return round(float(identity_score), 2)
        except (TypeError, ValueError):
            return 0.3
    if availability == "blocked":
        return 0.5
    if availability == "not_applicable":
        return 1.0
    if availability == "not_available":
        return 0.8
    if official:
        return 0.99
    try:
        return round(float(identity_score), 2) if identity_score is not None else 0.9
    except (TypeError, ValueError):
        return 0.9


def _nested_get(value: dict[str, Any], path: str | tuple[str, ...]) -> Any:
    if isinstance(path, str):
        return value.get(path)
    current: Any = value
    for segment in path:
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _terminal_status(row: dict[str, Any]) -> str:
    state = str(row.get("state") or "")
    if state == "complete":
        return "completed"
    if state.startswith("blocked"):
        return "blocked"
    return "failed" if state else "completed"


def _operations(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Build the operations block from telemetry metrics when available.

    When telemetry metrics are passed (from aggregate_company_metrics in
    telemetry.py) the three core fields are populated from them.  When not,
    the caller is expected to have already called _derive_operations_from_row()
    and passed its result here via the metrics parameter.
    """
    if not metrics:
        return {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0}
    runtime = int(metrics.get("wall_clock_duration_ms") or 0)
    if runtime <= 0:
        runtime = int(metrics.get("sum_request_duration_ms") or metrics.get("duration_ms") or 0)
    return {
        "requests": int(metrics.get("api_request_count") or 0),
        "runtime_ms": runtime,
        "third_party_cost_usd": float(metrics.get("third_party_cost_usd") or 0),
    }


# Module states that map to "completed successfully" (clean terminal states).
_MODULE_SUCCESS_STATES = frozenset({"complete", "not_applicable", "not_found"})
# Module states that indicate an actual failure / retrieval error.
_MODULE_FAILED_STATES = frozenset({
    "source_error", "submission_error", "budget_exhausted",
    "blocked_policy", "blocked_robots",
})
# Module states where the resource was simply absent (clean, not an error).
_MODULE_NOT_FOUND_STATES = frozenset({"not_found"})


def _derive_operations_from_row(row: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Derive best-effort operations metadata from the raw row when telemetry
    metrics are not available (the common path through build_output_contract.py).

    Sources used (read-only; no mutation of extraction data):
    - ``row["modules"]``  – {module: {state, retry_count, final_timestamp}}
    - profile evidence records – for cost_usd (discovery) and pages (website)

    Nothing is fabricated.  Values that cannot be derived reliably are set to
    0 rather than guessed, and optional fields are omitted when empty.
    """
    modules: dict[str, Any] = row.get("modules") or {}
    records: dict[str, Any] = profile.get("evidence") or {}

    successful: list[str] = []
    failed: list[str] = []
    not_found: list[str] = []
    total_retries: int = 0

    for mod_name, mod_info in modules.items():
        if not isinstance(mod_info, dict):
            continue
        state = str(mod_info.get("state") or "")
        retry_count = int(mod_info.get("retry_count") or 0)
        total_retries += retry_count

        if state in _MODULE_FAILED_STATES:
            failed.append(mod_name)
        elif state in _MODULE_NOT_FOUND_STATES:
            # not_found is a clean terminal state — the HTTP request succeeded
            not_found.append(mod_name)
            successful.append(mod_name)
        elif state in _MODULE_SUCCESS_STATES:
            successful.append(mod_name)

    # -- pages_crawled / pages_failed from website evidence --
    pages_crawled: int = 0
    pages_failed: int = 0
    _failed_extraction_states = {
        "failed", "oversized", "unsupported_content",
        "redirect_outside_domain", "robots_blocked",
    }
    for key in ("website", "website_discovered"):
        rec = records.get(key)
        if not isinstance(rec, dict) or rec.get("status") != "available":
            continue
        val = rec.get("value") if isinstance(rec.get("value"), dict) else {}
        for page in val.get("pages") or []:
            if not isinstance(page, dict):
                continue
            if page.get("extraction_state") in _failed_extraction_states or page.get("errors"):
                pages_failed += 1
            else:
                pages_crawled += 1

    # -- third_party_cost_usd from discovery provider cost fields --
    # BRREG (official registry data) is free.  Discovery providers (Exa, Tavily)
    # write a cost_usd value into the website evidence record value dict.
    third_party_cost: float = 0.0
    for key in ("website", "website_discovered", "website_discovery"):
        rec = records.get(key)
        if not isinstance(rec, dict):
            continue
        val = rec.get("value") if isinstance(rec.get("value"), dict) else {}
        try:
            third_party_cost += float(val.get("cost_usd") or 0.0)
        except (TypeError, ValueError):
            pass

    ops: dict[str, Any] = {
        "requests": 0,            # not derivable without telemetry log; 0 is honest
        "runtime_ms": 0,           # not derivable without telemetry log; 0 is honest
        "third_party_cost_usd": round(third_party_cost, 6),
    }

    if modules:
        ops["successful_modules"] = sorted(set(successful))
        ops["failed_modules"] = sorted(set(failed))
        ops["not_found_modules"] = sorted(set(not_found))
        if total_retries:
            ops["retried_module_attempts"] = total_retries

    if pages_crawled or pages_failed:
        ops["pages_crawled"] = pages_crawled
        ops["pages_failed"] = pages_failed

    return ops


# ---------------------------------------------------------------------------
# Website-derived contact and social claims
# ---------------------------------------------------------------------------

def _website_contact_claims(
    claims: list[dict[str, Any]],
    records: dict[str, Any],
    evidence_ids_map: dict[str, str],
    evidence_items: list[dict[str, Any]],
    profile: dict[str, Any],
    *,
    add_evidence: Any,
) -> None:
    """Emit phones, emails, website_addresses and social_profiles claims from the
    website evidence value when the website was successfully crawled.

    These signals are extracted during the crawl and stored inside the website
    evidence record's value dict.  They reach the public envelope here.

    Only an *available* or *ambiguous* website record produces these claims.
    A not_found/blocked/failed website produces not_available claims for each field
    so that the absence of data is explicit rather than silent.
    """
    # Prefer the crawled website record; fall back to a discovery-crawled record.
    website_rec = records.get("website") if isinstance(records.get("website"), dict) else None
    if website_rec is None:
        website_rec = records.get("website_discovered") if isinstance(records.get("website_discovered"), dict) else None
    if website_rec is None:
        # No website record of any kind — do not emit contact/social claims at all.
        return

    status = website_rec.get("status")
    crawled = status in {"available"}
    website_value = website_rec.get("value") if isinstance(website_rec.get("value"), dict) else {}

    # Determine the evidence ID for this website record (already registered by the
    # main website-claim path, or register it now for the discovery variant).
    website_key = "website" if records.get("website") is website_rec else "website_discovered"
    if website_key not in evidence_ids_map:
        avail = _availability(website_rec, field="website") or "not_available"
        ev_id = add_evidence(
            website_key,
            website_rec,
            claim_span=_claim_span(profile, website_rec),
            availability=avail,
            value=website_value if crawled else None,
        )
    else:
        ev_id = evidence_ids_map[website_key]

    ev_ids = [ev_id]

    def _contact_availability(raw: Any) -> str:
        """available when we have a non-empty list, not_available otherwise."""
        if isinstance(raw, list) and raw:
            return "available"
        return "not_available"

    def _contact_value(raw: Any, availability: str) -> Any:
        return raw if availability == "available" else None

    # -- phones --
    raw_phones = website_value.get("phones") if crawled else None
    phones_avail = _contact_availability(raw_phones)
    claims.append(_claim(
        "phones",
        _contact_value(raw_phones, phones_avail),
        phones_avail,
        _confidence(phones_avail),
        ev_ids,
    ))

    # -- emails --
    raw_emails = website_value.get("emails") if crawled else None
    emails_avail = _contact_availability(raw_emails)
    claims.append(_claim(
        "emails",
        _contact_value(raw_emails, emails_avail),
        emails_avail,
        _confidence(emails_avail),
        ev_ids,
    ))

    # -- website_addresses --
    raw_addresses = website_value.get("addresses") if crawled else None
    addresses_avail = _contact_availability(raw_addresses)
    claims.append(_claim(
        "website_addresses",
        _contact_value(raw_addresses, addresses_avail),
        addresses_avail,
        _confidence(addresses_avail),
        ev_ids,
    ))

    # -- social_profiles --
    # normalize_website_value() now preserves social links as 'social_profiles'.
    raw_social = website_value.get("social_profiles") if crawled else None
    social_avail = _contact_availability(raw_social)
    claims.append(_claim(
        "social_profiles",
        _contact_value(raw_social, social_avail),
        social_avail,
        _confidence(social_avail),
        ev_ids,
    ))
