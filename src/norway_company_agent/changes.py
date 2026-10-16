"""Claim-level change detection for V2 public envelopes.

Compares the normalized claims[] of a previous envelope against the normalized
claims[] of the current envelope and returns a list of change records.

Key design decisions
--------------------
1. Comparison is performed on NORMALIZED CLAIMS, not on raw evidence JSON or raw
   profile data.  The claim.field + claim.value + claim.availability triplet is
   the unit of comparison.

2. Comparison key: organisation_number + claim.field.
   Each field name is unique within one envelope, so this key is unambiguous.

3. Change types: "added", "updated", "removed".
   "unchanged" records are NOT emitted (no record = unchanged).

4. Scalar comparison: Python equality on the normalized value.
   Availability changes alone also constitute an "updated" change.

5. Array comparison: uses stable identifiers extracted from items, NOT array
   position.  Reordering an array MUST NOT produce a false change.

   Stable identifiers by field:
     social_profiles      → item["url"]  (canonical per-platform URL)
     roles                → item.get("organisation_number") or item.get("name")
     locations            → item.get("organisation_number") or item.get("name")
     emails               → the email string itself (strings ARE the identifier)
     phones               → the phone string itself
     website_addresses    → the address string itself
     financials.records   → item.get("record_id") or str(item.get("period"))
     financial_history.pdfs → item.get("year")
     any other array      → JSON-canonical stable form (sorted keys, stable repr)

6. Evidence IDs are NOT compared — they are per-envelope sequence numbers that
   change between runs even when the underlying data is identical.

7. Jobs and News fields are excluded from change tracking because those modules
   are frozen and must not be compared or reported.
"""
from __future__ import annotations

import json
from typing import Any

from .claim_builders import CLAIM_FAMILIES, MEMBER_TO_FAMILY, explode_legacy_claims


# ---------------------------------------------------------------------------
# Fields that must never appear in changes[] (frozen modules).
# ---------------------------------------------------------------------------

_FROZEN_FIELDS: frozenset[str] = frozenset({"jobs", "news"})

_UNCOMPARABLE_AVAILABILITY: frozenset[str] = frozenset({"failed", "blocked"})

_DEPENDENT_FIELDS: dict[str, str] = {
    "phones": "official_website",
    "emails": "official_website",
    "website_addresses": "official_website",
    "social_profiles": "official_website",
}

_PUBLIC_EVIDENCE_KEYS: tuple[str, ...] = (
    "id",
    "source_url",
    "source_class",
    "retrieved_at",
    "content_sha256",
    "claim_span",
)


def _is_placeholder(claim: dict[str, Any] | None) -> bool:
    """Return True if the claim is an unpopulated filler created by _complete_claims."""
    if not isinstance(claim, dict):
        return False
    return claim.get("availability") == "not_available" and not bool(claim.get("evidence_ids"))



# ---------------------------------------------------------------------------
# Stable-identifier extractors for known array-valued claim fields.
# ---------------------------------------------------------------------------

def _item_key(field: str, item: Any) -> str | None:
    """Return a stable string key for one item of an array-valued claim.

    Returns None when no stable key can be extracted; such items fall back to
    a canonical JSON serialisation as their key.
    """
    if not isinstance(item, dict):
        # Primitive values (email string, phone string, address string) are
        # their own stable key.
        return str(item) if item is not None else None

    if field == "social_profiles":
        url = item.get("url")
        return str(url) if url else None

    if field in ("roles",):
        # Prefer organisation number for legal entities, otherwise display name.
        org = item.get("organisation_number")
        if org:
            return f"org:{org}"
        name = item.get("name")
        return f"name:{name}" if name else None

    if field in ("locations",):
        org = item.get("organisation_number")
        if org:
            return f"org:{org}"
        name = item.get("name")
        return f"name:{name}" if name else None

    if field in ("financials", "financials.records"):
        record_id = item.get("record_id")
        if record_id is not None:
            return f"id:{record_id}"
        period = item.get("period")
        if isinstance(period, dict):
            return f"period:{period.get('fraDato')}:{period.get('tilDato')}"
        if period is not None:
            return f"period:{period}"
        return None

    if field in ("financial_history", "financial_history.pdfs"):
        year = item.get("year")
        return f"year:{year}" if year is not None else None

    # Generic fallback: canonical JSON.
    try:
        return json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(item)


def _stable_key(field: str, item: Any) -> str:
    """Always return a non-None stable key for any item."""
    key = _item_key(field, item)
    if key is not None:
        return key
    # Final fallback — str() of the item.
    try:
        return json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(item)


# ---------------------------------------------------------------------------
# Value normalisation helpers
# ---------------------------------------------------------------------------

def _is_array_field(field: str, value: Any) -> bool:
    """Return True when the claim value should be treated as an ordered
    collection that requires stable-identifier diffing rather than a scalar.
    """
    return isinstance(value, list)


def _array_by_key(field: str, items: list[Any]) -> dict[str, Any]:
    """Build a {stable_key: item} dict from an array claim value."""
    result: dict[str, Any] = {}
    for item in items:
        key = _stable_key(field, item)
        # If two items share the same stable key (degenerate data), keep the
        # first occurrence — it is the most stable behaviour.
        result.setdefault(key, item)
    return result


def _comparison_value(value: Any, field: str | None = None) -> Any:
    """Return a recursively order-insensitive representation of a claim value.

    Some normalized module claims are objects containing arrays (for example,
    ``financials.records`` and ``roles.roles``), rather than arrays themselves.
    Normalize those nested arrays too so a source's ordering never becomes a
    change by itself.
    """
    if isinstance(value, dict):
        return {key: _comparison_value(item, field) for key, item in value.items()}
    if isinstance(value, list):
        normalized = [_comparison_value(item, field) for item in value]
        return sorted(
            normalized,
            key=lambda item: (
                _stable_key(field or "", item),
                json.dumps(item, sort_keys=True, ensure_ascii=False, default=repr),
            ),
        )
    return value


def _scalar_equal(a: Any, b: Any, *, field: str | None = None) -> bool:
    """Equality for scalar claim values.

    Normalises None and missing to the same thing.  Uses Python == for
    everything else (works for str, int, float, bool, dict, None).
    """
    # Treat both-None as equal regardless of representation.
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return _comparison_value(a, field) == _comparison_value(b, field)


# ---------------------------------------------------------------------------
# Core diff function
# ---------------------------------------------------------------------------

def _family_entries(claims: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build {family: {"placeholder": claim_or_None, "members": {claim_key: claim}}} from a claim list."""
    entries: dict[str, dict[str, Any]] = {family: {"placeholder": None, "members": {}} for family in CLAIM_FAMILIES}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        field = claim.get("field")
        if field in CLAIM_FAMILIES:
            entries[field]["placeholder"] = claim
        elif field in MEMBER_TO_FAMILY and claim.get("claim_key"):
            entries[MEMBER_TO_FAMILY[field]]["members"][str(claim["claim_key"])] = claim
    return entries


def _family_state(entry: dict[str, Any]) -> str:
    if entry["members"]:
        return "members"
    placeholder = entry["placeholder"]
    if placeholder is None:
        return "absent"
    availability = placeholder.get("availability")
    if availability in _UNCOMPARABLE_AVAILABILITY:
        return "uncomparable"
    if _is_placeholder(placeholder):
        return "unchecked"
    if availability == "not_available":
        return "confirmed_empty"
    if availability == "available":
        return "blob"
    return "uncomparable"


def diff_claims(
    organisation_number: str,
    previous_claims: list[dict[str, Any]],
    current_claims: list[dict[str, Any]],
    *,
    previous_evidence: list[dict[str, Any]] | None = None,
    previous_run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Compare two lists of normalized claims and return change records.

    Parameters
    ----------
    organisation_number:
        The company being compared; embedded in every change record.
    previous_claims:
        claims[] from the previous run's public envelope.
    current_claims:
        claims[] from the current run's public envelope.
    previous_evidence:
        evidence[] list from the previous run's envelope.
    previous_run_id:
        run_id string from the previous run's envelope.

    Returns
    -------
    A list of change records with evidence references and previous run ID.
    """
    previous_claims = explode_legacy_claims(list(previous_claims or []), organisation_number)
    current_claims = explode_legacy_claims(list(current_claims or []), organisation_number)

    # Singletons: exclude family slots and member fields.
    prev_by_field: dict[str, dict[str, Any]] = {
        c["field"]: c
        for c in previous_claims
        if isinstance(c, dict) and c.get("field") and c["field"] not in _FROZEN_FIELDS
        and c["field"] not in CLAIM_FAMILIES and c["field"] not in MEMBER_TO_FAMILY
    }
    curr_by_field: dict[str, dict[str, Any]] = {
        c["field"]: c
        for c in current_claims
        if isinstance(c, dict) and c.get("field") and c["field"] not in _FROZEN_FIELDS
        and c["field"] not in CLAIM_FAMILIES and c["field"] not in MEMBER_TO_FAMILY
    }

    # Family entries for family-level diff.
    prev_families = _family_entries(previous_claims)
    curr_families = _family_entries(current_claims)

    prev_ev_by_id: dict[str, dict[str, Any]] = {}
    for item in previous_evidence or []:
        if isinstance(item, dict) and item.get("id"):
            prev_ev_by_id[str(item["id"])] = {
                k: item.get(k) for k in _PUBLIC_EVIDENCE_KEYS if k in item
            }

    all_fields = sorted(set(prev_by_field) | set(curr_by_field))
    changes: list[dict[str, Any]] = []

    for field in all_fields:
        prev_claim = prev_by_field.get(field)
        curr_claim = curr_by_field.get(field)

        # Skip if either side is an unpopulated placeholder (not checked in that run).
        if _is_placeholder(prev_claim) or _is_placeholder(curr_claim):
            continue

        # Skip if either side's availability is uncomparable (retrieval failure or blocked).
        if (prev_claim and prev_claim.get("availability") in _UNCOMPARABLE_AVAILABILITY) or (
            curr_claim and curr_claim.get("availability") in _UNCOMPARABLE_AVAILABILITY
        ):
            continue

        # Skip dependent contact fields if website retrieval was failed/blocked on either side.
        if field in _DEPENDENT_FIELDS:
            parent_field = _DEPENDENT_FIELDS[field]
            prev_parent = prev_by_field.get(parent_field)
            curr_parent = curr_by_field.get(parent_field)
            if (prev_parent and prev_parent.get("availability") in _UNCOMPARABLE_AVAILABILITY) or (
                curr_parent and curr_parent.get("availability") in _UNCOMPARABLE_AVAILABILITY
            ):
                continue

        # Schema-evolution: a field present on only one side means the envelopes come
        # from different schema versions (e.g. the four new registry fields).  Real
        # appear/disappear transitions are reported as availability changes, not as
        # missing fields.
        if prev_claim is None or curr_claim is None:
            continue

        curr_ev_ids = list(curr_claim.get("evidence_ids") or [])
        prev_ev_ids = list(prev_claim.get("evidence_ids") or [])
        field_prev_evidence = [prev_ev_by_id[pid] for pid in prev_ev_ids if pid in prev_ev_by_id]

        prev_avail = prev_claim.get("availability")
        curr_avail = curr_claim.get("availability")
        prev_val = prev_claim.get("value")
        curr_val = curr_claim.get("value")

        avail_changed = prev_avail != curr_avail

        # Array-valued claims need item-level stable-id diffing.
        if _is_array_field(field, prev_val) or _is_array_field(field, curr_val):
            prev_list: list[Any] = prev_val if isinstance(prev_val, list) else []
            curr_list: list[Any] = curr_val if isinstance(curr_val, list) else []
            array_changes = _diff_array(
                organisation_number, field, prev_list, curr_list,
                current_evidence_ids=curr_ev_ids,
                previous_evidence=field_prev_evidence,
                previous_run_id=previous_run_id,
            )
            changes.extend(array_changes)
            if avail_changed and not array_changes:
                changes.append(_change_record(
                    organisation_number, field, "updated",
                    prev_val, curr_val, prev_avail, curr_avail,
                    current_evidence_ids=curr_ev_ids,
                    previous_evidence=field_prev_evidence,
                    previous_run_id=previous_run_id,
                ))
            elif avail_changed and array_changes:
                array_changes[0]["previous_availability"] = prev_avail
                array_changes[0]["current_availability"] = curr_avail
            continue

        # Scalar comparison.
        value_changed = not _scalar_equal(prev_val, curr_val, field=field)

        if not value_changed and not avail_changed:
            continue

        changes.append(_change_record(
            organisation_number, field, "updated",
            prev_val, curr_val, prev_avail, curr_avail,
            current_evidence_ids=curr_ev_ids,
            previous_evidence=field_prev_evidence,
            previous_run_id=previous_run_id,
        ))

    # ── Family-level diff ───────────────────────────────────────────────────
    for family in sorted(CLAIM_FAMILIES):
        prev_entry = prev_families[family]
        curr_entry = curr_families[family]
        p_state = _family_state(prev_entry)
        c_state = _family_state(curr_entry)

        if p_state == "blob" and c_state == "blob":
            prev_ph = prev_entry["placeholder"]
            curr_ph = curr_entry["placeholder"]
            if prev_ph and curr_ph:
                prev_val = prev_ph.get("value")
                curr_val = curr_ph.get("value")
                prev_avail = prev_ph.get("availability")
                curr_avail = curr_ph.get("availability")
                if not _scalar_equal(prev_val, curr_val, field=family) or prev_avail != curr_avail:
                    curr_ev_ids = list(curr_ph.get("evidence_ids") or [])
                    prev_ev_ids = list(prev_ph.get("evidence_ids") or [])
                    field_prev_evidence = [prev_ev_by_id[pid] for pid in prev_ev_ids if pid in prev_ev_by_id]
                    changes.append(_change_record(
                        organisation_number, family, "updated",
                        prev_val, curr_val, prev_avail, curr_avail,
                        current_evidence_ids=curr_ev_ids,
                        previous_evidence=field_prev_evidence,
                        previous_run_id=previous_run_id,
                    ))
            continue

        if p_state in {"members", "confirmed_empty"} and c_state in {"members", "confirmed_empty"}:
            prev_members = prev_entry["members"]
            curr_members = curr_entry["members"]
            all_keys = sorted(set(prev_members) | set(curr_members))
            for key in all_keys:
                prev_m = prev_members.get(key)
                curr_m = curr_members.get(key)
                if prev_m is None and curr_m is not None:
                    curr_ev_ids = list(curr_m.get("evidence_ids") or [])
                    changes.append(_change_record(
                        organisation_number, curr_m["field"], "added",
                        None, curr_m.get("value"),
                        None, curr_m.get("availability"),
                        current_evidence_ids=curr_ev_ids,
                        previous_evidence=[],
                        previous_run_id=previous_run_id,
                        claim_key=key,
                    ))
                elif curr_m is None and prev_m is not None:
                    prev_ev_ids = list(prev_m.get("evidence_ids") or [])
                    field_prev_evidence = [prev_ev_by_id[pid] for pid in prev_ev_ids if pid in prev_ev_by_id]
                    changes.append(_change_record(
                        organisation_number, prev_m["field"], "removed",
                        prev_m.get("value"), None,
                        prev_m.get("availability"), None,
                        current_evidence_ids=[],
                        previous_evidence=field_prev_evidence,
                        previous_run_id=previous_run_id,
                        claim_key=key,
                    ))
                elif prev_m is not None and curr_m is not None:
                    member_field = curr_m["field"]
                    if not _scalar_equal(prev_m.get("value"), curr_m.get("value"), field=member_field) or prev_m.get("availability") != curr_m.get("availability"):
                        curr_ev_ids = list(curr_m.get("evidence_ids") or [])
                        prev_ev_ids = list(prev_m.get("evidence_ids") or [])
                        field_prev_evidence = [prev_ev_by_id[pid] for pid in prev_ev_ids if pid in prev_ev_by_id]
                        changes.append(_change_record(
                            organisation_number, member_field, "updated",
                            prev_m.get("value"), curr_m.get("value"),
                            prev_m.get("availability"), curr_m.get("availability"),
                            current_evidence_ids=curr_ev_ids,
                            previous_evidence=field_prev_evidence,
                            previous_run_id=previous_run_id,
                            claim_key=key,
                        ))
            continue

        # Any other combination (absent, unchecked, uncomparable, blob vs members): skip.

    return changes


def _diff_array(
    organisation_number: str,
    field: str,
    prev_list: list[Any],
    curr_list: list[Any],
    *,
    current_evidence_ids: list[str] | None = None,
    previous_evidence: list[dict[str, Any]] | None = None,
    previous_run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return item-level changes for one array-valued claim field.

    Comparison is keyed by stable identifier — array position is irrelevant.
    Reordering the array produces zero changes.
    """
    prev_by_key = _array_by_key(field, prev_list)
    curr_by_key = _array_by_key(field, curr_list)

    all_keys = sorted(set(prev_by_key) | set(curr_by_key))
    changes: list[dict[str, Any]] = []

    for key in all_keys:
        prev_item = prev_by_key.get(key)
        curr_item = curr_by_key.get(key)

        if prev_item is None:
            changes.append(_change_record(
                organisation_number, field, "added",
                None, curr_item, None, None,
                array_item_key=key,
                current_evidence_ids=current_evidence_ids,
                previous_evidence=previous_evidence,
                previous_run_id=previous_run_id,
            ))
        elif curr_item is None:
            changes.append(_change_record(
                organisation_number, field, "removed",
                prev_item, None, None, None,
                array_item_key=key,
                current_evidence_ids=current_evidence_ids,
                previous_evidence=previous_evidence,
                previous_run_id=previous_run_id,
            ))
        elif not _scalar_equal(prev_item, curr_item, field=field):
            changes.append(_change_record(
                organisation_number, field, "updated",
                prev_item, curr_item, None, None,
                array_item_key=key,
                current_evidence_ids=current_evidence_ids,
                previous_evidence=previous_evidence,
                previous_run_id=previous_run_id,
            ))
        # else: equal — no record.

    return changes


def _change_record(
    organisation_number: str,
    field: str,
    change_type: str,
    previous_value: Any,
    current_value: Any,
    previous_availability: str | None,
    current_availability: str | None,
    *,
    array_item_key: str | None = None,
    claim_key: str | None = None,
    current_evidence_ids: list[str] | None = None,
    previous_evidence: list[dict[str, Any]] | None = None,
    previous_run_id: str | None = None,
) -> dict[str, Any]:
    """Build one canonical change record."""
    record: dict[str, Any] = {
        "organisation_number": organisation_number,
        "field": field,
        "change_type": change_type,
        "previous_value": previous_value,
        "current_value": current_value,
        "previous_availability": previous_availability,
        "current_availability": current_availability,
        "current_evidence_ids": list(current_evidence_ids or []),
        "previous_evidence": list(previous_evidence or []),
        "previous_run_id": previous_run_id,
    }
    if array_item_key is not None:
        record["array_item_key"] = array_item_key
    if claim_key is not None:
        record["claim_key"] = claim_key
    return record
