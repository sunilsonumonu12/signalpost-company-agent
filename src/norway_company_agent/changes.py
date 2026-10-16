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
    prev_by_field: dict[str, dict[str, Any]] = {
        c["field"]: c
        for c in previous_claims
        if isinstance(c, dict) and c.get("field") and c["field"] not in _FROZEN_FIELDS
    }
    curr_by_field: dict[str, dict[str, Any]] = {
        c["field"]: c
        for c in current_claims
        if isinstance(c, dict) and c.get("field") and c["field"] not in _FROZEN_FIELDS
    }

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

        curr_ev_ids = list(curr_claim.get("evidence_ids") or []) if curr_claim else []
        prev_ev_ids = list(prev_claim.get("evidence_ids") or []) if prev_claim else []
        field_prev_evidence = [prev_ev_by_id[pid] for pid in prev_ev_ids if pid in prev_ev_by_id]

        # ── Field was present before but is absent now ──────────────────────
        if prev_claim is not None and curr_claim is None:
            changes.append(_change_record(
                organisation_number, field, "removed",
                prev_claim.get("value"), None,
                prev_claim.get("availability"), None,
                current_evidence_ids=curr_ev_ids,
                previous_evidence=field_prev_evidence,
                previous_run_id=previous_run_id,
            ))
            continue

        # ── Field is new — absent before, present now ────────────────────────
        if prev_claim is None and curr_claim is not None:
            changes.append(_change_record(
                organisation_number, field, "added",
                None, curr_claim.get("value"),
                None, curr_claim.get("availability"),
                current_evidence_ids=curr_ev_ids,
                previous_evidence=field_prev_evidence,
                previous_run_id=previous_run_id,
            ))
            continue

        # ── Field exists in both envelopes ───────────────────────────────────
        assert prev_claim is not None and curr_claim is not None  # narrowing

        prev_avail = prev_claim.get("availability")
        curr_avail = curr_claim.get("availability")
        prev_val = prev_claim.get("value")
        curr_val = curr_claim.get("value")

        avail_changed = prev_avail != curr_avail

        # Array-valued claims need item-level stable-id diffing.
        if _is_array_field(field, prev_val) or _is_array_field(field, curr_val):
            # Coerce both sides to list (may be None when availability is not_available).
            prev_list: list[Any] = prev_val if isinstance(prev_val, list) else []
            curr_list: list[Any] = curr_val if isinstance(curr_val, list) else []
            array_changes = _diff_array(
                organisation_number, field, prev_list, curr_list,
                current_evidence_ids=curr_ev_ids,
                previous_evidence=field_prev_evidence,
                previous_run_id=previous_run_id,
            )
            changes.extend(array_changes)
            # Availability change on the array claim itself (e.g. available → not_available)
            # is reported separately only when the array diff did not already capture it.
            if avail_changed and not array_changes:
                changes.append(_change_record(
                    organisation_number, field, "updated",
                    prev_val, curr_val, prev_avail, curr_avail,
                    current_evidence_ids=curr_ev_ids,
                    previous_evidence=field_prev_evidence,
                    previous_run_id=previous_run_id,
                ))
            elif avail_changed and array_changes:
                # Availability changed alongside item-level changes — attach availability
                # context to the first array change so it is not silently lost.
                array_changes[0]["previous_availability"] = prev_avail
                array_changes[0]["current_availability"] = curr_avail
            continue

        # Scalar comparison.
        value_changed = not _scalar_equal(prev_val, curr_val, field=field)

        if not value_changed and not avail_changed:
            # Truly unchanged — emit nothing.
            continue

        changes.append(_change_record(
            organisation_number, field, "updated",
            prev_val, curr_val, prev_avail, curr_avail,
            current_evidence_ids=curr_ev_ids,
            previous_evidence=field_prev_evidence,
            previous_run_id=previous_run_id,
        ))

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
    return record
