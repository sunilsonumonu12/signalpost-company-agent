"""Regression tests for claim-level change detection."""
from __future__ import annotations

import copy

import pytest

from norway_company_agent.changes import diff_claims
from norway_company_agent.output_contract import to_public_envelope


ORG = "123456789"


def _claim(field: str, value, availability: str = "available") -> dict:
    return {
        "field": field,
        "value": value,
        "availability": availability,
        "confidence": 0.9,
        "evidence_ids": ["ev-1"],
    }


def test_scalar_update_is_keyed_by_organisation_and_field():
    changes = diff_claims(
        ORG,
        [_claim("legal_name", "Before AS")],
        [_claim("legal_name", "After AS")],
    )

    assert changes == [{
        "organisation_number": ORG,
        "field": "legal_name",
        "change_type": "updated",
        "previous_value": "Before AS",
        "current_value": "After AS",
        "previous_availability": "available",
        "current_availability": "available",
    }]


def test_claim_addition_and_removal_are_reported():
    assert diff_claims(ORG, [], [_claim("legal_name", "TestCo AS")])[0]["change_type"] == "added"
    assert diff_claims(ORG, [_claim("legal_name", "TestCo AS")], [])[0]["change_type"] == "removed"


def test_social_profile_array_uses_url_and_ignores_order():
    linkedin = {"platform": "linkedin", "url": "https://linkedin.com/company/testco"}
    facebook = {"platform": "facebook", "url": "https://facebook.com/testco"}

    assert diff_claims(ORG, [_claim("social_profiles", [linkedin, facebook])],
                      [_claim("social_profiles", [facebook, linkedin])]) == []

    added = diff_claims(ORG, [_claim("social_profiles", [linkedin])],
                        [_claim("social_profiles", [linkedin, facebook])])
    removed = diff_claims(ORG, [_claim("social_profiles", [linkedin, facebook])],
                          [_claim("social_profiles", [linkedin])])

    assert added[0]["change_type"] == "added"
    assert added[0]["array_item_key"] == facebook["url"]
    assert removed[0]["change_type"] == "removed"
    assert removed[0]["array_item_key"] == facebook["url"]


def test_nested_normalized_module_arrays_ignore_source_order():
    first = {"organisation_number": "111111111", "name": "First"}
    second = {"organisation_number": "222222222", "name": "Second"}

    assert diff_claims(ORG, [_claim("roles", {"roles": [first, second]})],
                      [_claim("roles", {"roles": [second, first]})]) == []


def test_unchanged_claims_and_frozen_jobs_news_emit_no_changes():
    claims = [_claim("legal_name", "TestCo AS")]
    assert diff_claims(ORG, claims, copy.deepcopy(claims)) == []
    assert diff_claims(ORG, [_claim("jobs", [{"title": "Old"}])],
                       [_claim("jobs", [{"title": "New"}])]) == []
    assert diff_claims(ORG, [_claim("news", [{"title": "Old"}])],
                       [_claim("news", [{"title": "New"}])]) == []


def _row(name: str = "TestCo AS") -> dict:
    return {
        "profile": {
            "organisation_number": ORG,
            "evidence": {
                "registry_live": {
                    "field": "registry_live",
                    "status": "available",
                    "source_type": "brreg_main_unit",
                    "source_url": "https://example.test/entity/123456789",
                    "retrieved_at": "2026-10-06T00:00:00Z",
                    "value": {"name": name},
                }
            },
        }
    }


def test_envelope_populates_changes_from_previous_normalized_claims():
    previous = to_public_envelope(_row("Before AS"))
    current = to_public_envelope(_row("After AS"), previous_envelope=previous)
    change = next(item for item in current["changes"] if item["field"] == "legal_name")

    assert change["change_type"] == "updated"
    assert change["previous_value"] == "Before AS"
    assert change["current_value"] == "After AS"


def test_previous_envelope_must_belong_to_the_same_organisation():
    previous = to_public_envelope(_row())
    previous["organisation_number"] = "987654321"

    with pytest.raises(ValueError, match="same organisation number"):
        to_public_envelope(_row(), previous_envelope=previous)
