"""Phase 2: Evidence and Claim Linkage tests.

Invariant under test:
    Every value in every claim.evidence_ids MUST resolve to an existing
    evidence[].id in the same envelope.

Tests:
    1. Valid claim → evidence reference passes integrity check.
    2. Dangling evidence reference raises EvidenceIntegrityError.
    3. Empty evidence_ids list passes (no entries to resolve).
    4. Multiple claims, all referencing real evidence, pass.
    5. One bad reference among otherwise-good claims raises the error.
    6. to_public_envelope() with registry data produces a clean envelope.
    7. to_public_envelope() with website record produces a clean envelope.
    8. to_public_envelope() with observation produces a clean envelope.
    9. Deduplication: same evidence key produces only one evidence item.
"""
from __future__ import annotations

import pytest

from norway_company_agent.output_contract import (
    EvidenceIntegrityError,
    assert_evidence_integrity,
    to_public_envelope,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_registry_row() -> dict:
    """Row with a live registry record — no website, no observations."""
    return {
        "profile": {
            "organisation_number": "123456789",
            "name": "TestCo AS",
            "evidence": {
                "registry_live": {
                    "field": "registry_live",
                    "status": "available",
                    "source_type": "brreg_main_unit",
                    "source_class": "public_registry",
                    "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/123456789",
                    "retrieved_at": "2026-09-30T00:00:00Z",
                    "value": {
                        "name": "TestCo AS",
                        "legal_form": "AS",
                        "bankrupt": False,
                        "liquidating": False,
                        "employees": 5,
                        "industry": {"kode": "62.010"},
                        "business_address": {"kommune": "Oslo"},
                    },
                }
            },
        }
    }


def _minimal_website_row() -> dict:
    """Row with both a registry record and a website evidence record."""
    row = _minimal_registry_row()
    row["profile"]["evidence"]["website"] = {
        "field": "website",
        "status": "available",
        "source_type": "registry_linked_company_website",
        "source_class": "company_owned",
        "source_url": "https://testco.no",
        "retrieved_at": "2026-09-30T00:00:00Z",
        "value": {
            "final_url": "https://www.testco.no/",
            "requested_url": "https://testco.no",
            "registered_domain": "testco.no",
            "title": "TestCo",
            "description": "TestCo website",
            "main_text_excerpt": "We make things.",
            "pages": [],
            "identity_assessment": {"publishable": True, "score": 0.95},
            "jobs": "not_available",
            "news": "not_available",
            "phones": "not_available",
            "emails": "not_available",
            "addresses": "not_available",
            "locations": "not_available",
            "content_sha256": "abc123",
            "crawl_errors": [],
            "discovery": {
                "method": "direct",
                "providers_used": [],
                "candidates": [],
                "cost_usd": 0.0,
            },
            "cost_usd": 0.0,
            "org_number_found": False,
            "legal_name_match": False,
            "address_match": False,
            "structured_organisations": [],
            "extraction_state": "static_complete",
        },
    }
    return row


# ---------------------------------------------------------------------------
# Test 1: Valid claim → evidence reference passes
# ---------------------------------------------------------------------------

def test_valid_claim_evidence_reference_passes():
    """A well-formed envelope where every evidence_id exists must not raise."""
    envelope = {
        "claims": [
            {"field": "legal_name", "value": "TestCo AS", "availability": "available",
             "confidence": 0.99, "evidence_ids": ["ev-1"]},
        ],
        "evidence": [
            {"id": "ev-1", "module": "registry_live", "status": "available"},
        ],
    }
    # Must not raise
    assert_evidence_integrity(envelope)


# ---------------------------------------------------------------------------
# Test 2: Dangling evidence reference fails
# ---------------------------------------------------------------------------

def test_dangling_evidence_reference_raises():
    """A claim referencing a non-existent evidence ID must raise EvidenceIntegrityError."""
    envelope = {
        "claims": [
            {"field": "legal_name", "value": "TestCo AS", "availability": "available",
             "confidence": 0.99, "evidence_ids": ["ev-GHOST"]},
        ],
        "evidence": [
            {"id": "ev-1", "module": "registry_live", "status": "available"},
        ],
    }
    with pytest.raises(EvidenceIntegrityError) as exc_info:
        assert_evidence_integrity(envelope)
    msg = str(exc_info.value)
    assert "ev-GHOST" in msg
    assert "legal_name" in msg
    assert "dangling reference" in msg.lower() or "missing" in msg.lower()


# ---------------------------------------------------------------------------
# Test 3: Empty evidence_ids passes (nothing to resolve)
# ---------------------------------------------------------------------------

def test_empty_evidence_ids_passes():
    """A claim with an empty evidence_ids list has nothing to resolve — must pass."""
    envelope = {
        "claims": [
            {"field": "official_website", "value": None, "availability": "not_available",
             "confidence": 0.8, "evidence_ids": []},
        ],
        "evidence": [],
    }
    assert_evidence_integrity(envelope)


# ---------------------------------------------------------------------------
# Test 4: Multiple claims all referencing real evidence pass
# ---------------------------------------------------------------------------

def test_multiple_valid_claims_pass():
    """Multiple claims each referencing distinct real evidence IDs must pass."""
    envelope = {
        "claims": [
            {"field": "legal_name", "evidence_ids": ["ev-1"]},
            {"field": "legal_form", "evidence_ids": ["ev-1"]},
            {"field": "official_website", "evidence_ids": ["ev-2"]},
        ],
        "evidence": [
            {"id": "ev-1"},
            {"id": "ev-2"},
        ],
    }
    assert_evidence_integrity(envelope)


# ---------------------------------------------------------------------------
# Test 5: One bad reference among otherwise-good claims still raises
# ---------------------------------------------------------------------------

def test_one_bad_reference_among_good_claims_raises():
    """Even a single dangling ref among many valid refs must raise."""
    envelope = {
        "claims": [
            {"field": "legal_name", "evidence_ids": ["ev-1"]},
            {"field": "employees", "evidence_ids": ["ev-1"]},
            {"field": "financials", "evidence_ids": ["ev-MISSING"]},
        ],
        "evidence": [
            {"id": "ev-1"},
        ],
    }
    with pytest.raises(EvidenceIntegrityError) as exc_info:
        assert_evidence_integrity(envelope)
    assert "ev-MISSING" in str(exc_info.value)
    assert "financials" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 6: to_public_envelope with registry data produces a clean envelope
# ---------------------------------------------------------------------------

def test_to_public_envelope_registry_only_is_clean():
    """Registry-only envelope must pass integrity automatically."""
    row = _minimal_registry_row()
    envelope = to_public_envelope(row)

    # Integrity already asserted inside to_public_envelope; reaching here means it passed.
    known_ids = {item["id"] for item in envelope["evidence"]}
    for claim in envelope["claims"]:
        for ev_id in claim.get("evidence_ids", []):
            assert ev_id in known_ids, (
                f"Claim '{claim['field']}' has dangling evidence_id '{ev_id}'"
            )

    # Registry should produce claims
    assert len(envelope["claims"]) > 0
    # Every non-empty evidence_ids entry must resolve
    assert len(envelope["evidence"]) > 0


# ---------------------------------------------------------------------------
# Test 7: to_public_envelope with website record produces a clean envelope
# ---------------------------------------------------------------------------

def test_to_public_envelope_with_website_is_clean():
    """Envelope with both registry and website evidence must be clean."""
    row = _minimal_website_row()
    envelope = to_public_envelope(row)

    known_ids = {item["id"] for item in envelope["evidence"]}
    for claim in envelope["claims"]:
        for ev_id in claim.get("evidence_ids", []):
            assert ev_id in known_ids, (
                f"Claim '{claim['field']}' has dangling evidence_id '{ev_id}'"
            )

    # Should have a website claim
    website_claim = next((c for c in envelope["claims"] if c["field"] == "official_website"), None)
    assert website_claim is not None
    assert len(website_claim["evidence_ids"]) > 0


# ---------------------------------------------------------------------------
# Test 8: to_public_envelope with observation produces a clean envelope
# ---------------------------------------------------------------------------

def test_to_public_envelope_with_observation_is_clean():
    """Envelope with observation signal must be clean."""
    row = _minimal_registry_row()
    row["profile"]["observations"] = [
        {
            "id": "obs-1",
            "signal_type": "jobs",
            "source_url": "https://testco.no/careers",
            "retrieved_at": "2026-09-30T00:00:00Z",
            "evidence_span": "careers page detected",
            "metrics": {"has_careers_page": True, "job_count": 3},
        }
    ]
    envelope = to_public_envelope(row)

    known_ids = {item["id"] for item in envelope["evidence"]}
    for claim in envelope["claims"]:
        for ev_id in claim.get("evidence_ids", []):
            assert ev_id in known_ids, (
                f"Claim '{claim['field']}' has dangling evidence_id '{ev_id}'"
            )

    jobs_claim = next((c for c in envelope["claims"] if c["field"] == "jobs"), None)
    assert jobs_claim is not None
    assert len(jobs_claim["evidence_ids"]) == 1


# ---------------------------------------------------------------------------
# Test 9: Deduplication — same evidence key produces only one evidence item
# ---------------------------------------------------------------------------

def test_deduplication_same_key_produces_one_evidence_item():
    """Calling add_evidence with the same key twice must not create duplicate items.

    Registry claims (legal_name, legal_form, etc.) all share the "registry_live" key
    → exactly ONE ev-* item covers them all.

    The website fallback path uses a DIFFERENT key ("registry_live_website") so it
    legitimately gets its own evidence item — that is not duplication.
    """
    row = _minimal_registry_row()
    envelope = to_public_envelope(row)

    # Collect all unique evidence IDs produced
    ev_ids = [e["id"] for e in envelope["evidence"]]
    # No ID should appear more than once
    assert len(ev_ids) == len(set(ev_ids)), (
        f"Duplicate evidence IDs found: {ev_ids}"
    )

    # All nine registry field claims must share the SAME single evidence ID (ev-1)
    registry_claim_fields = {
        "legal_name", "legal_form", "employees", "industry_code",
        "municipality", "business_address", "bankrupt", "liquidating",
        "latest_submitted_accounts",
    }
    registry_ev_ids_used: set[str] = set()
    for claim in envelope["claims"]:
        if claim["field"] in registry_claim_fields:
            assert len(claim["evidence_ids"]) == 1, (
                f"Registry claim '{claim['field']}' should have exactly 1 evidence_id, "
                f"got {claim['evidence_ids']}"
            )
            registry_ev_ids_used.update(claim["evidence_ids"])

    assert len(registry_ev_ids_used) == 1, (
        f"All registry field claims should share ONE evidence ID, "
        f"but they used: {registry_ev_ids_used}"
    )
