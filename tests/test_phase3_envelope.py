"""Phase 3: one vocabulary, claims-based public envelope, validator."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from norway_company_agent.batch import terminal_envelope, validate_envelopes
from norway_company_agent.evidence import evidence
from norway_company_agent.output_contract import (
    CLAIM_FIELD_ORDER,
    PUBLIC_EVIDENCE_KEYS,
    PUBLIC_TOP_LEVEL_KEYS,
    ClaimSchemaError,
    assert_claim_schema_integrity,
    conversion_failure_envelope,
    to_public_envelope,
)
from norway_company_agent.vocabulary import (
    MODULE_KEYS,
    MODULE_STATES,
    REQUIRED_MODULES,
    SCHEMA_VERSION,
    UnknownModuleStatusError,
    batch_state,
    derive_terminal_status,
    module_state,
    module_states,
    profile_terminal_status,
)

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load("validate_envelopes")
builder = _load("build_output_contract")

SHA = "a" * 64


def _record(module, status="available", **kwargs):
    kwargs.setdefault("value", {"name": "TestCo AS"} if module == "registry_live" else {})
    kwargs.setdefault("content_sha256", SHA)
    retry_count = kwargs.pop("retry_count", 0)
    record = evidence(module, status, "official_test", f"https://example.test/{module}", **kwargs)
    record["retry_count"] = retry_count
    return record


def _profile(org="123456789", **overrides):
    records = {
        module: _record(module)
        for module in ("registry_live", "financials", "financial_history", "roles", "group", "locations", "accounting_obligation")
    }
    records["registry"] = _record("registry", "not_applicable")
    records["website"] = _record("website", "available", value={
        "final_url": "https://testco.no/", "phones": ["+47 1"], "emails": [], "addresses": [],
        "social_profiles": [], "identity_assessment": {"publishable": True, "score": 0.95},
    })
    records.update(overrides)
    return {"organisation_number": org, "name": "TestCo AS", "evidence": records}


def _envelope(profile=None, run_id="run-1"):
    fat = terminal_envelope(
        profile or _profile(), run_id=run_id,
        started_at="2026-10-09T06:00:00Z", completed_at="2026-10-09T06:05:00Z",
    )
    return to_public_envelope(fat)


# --- vocabulary ------------------------------------------------------------

def test_module_state_is_the_evidence_status_verbatim():
    for status in MODULE_STATES:
        assert module_state({"status": status, "note": "x"})["state"] == status


def test_unknown_status_raises_instead_of_mapping():
    for bad in ("complete", "submission_error", "blocked_robots", "ok", None):
        with pytest.raises(UnknownModuleStatusError):
            module_state({"status": bad}, module="roles")


def test_blocked_carries_reason():
    assert module_state({"status": "blocked", "note": "robots.txt disallows this user agent"})["reason"] == "robots"
    assert module_state({"status": "blocked", "note": "Homepage exceeds byte limit"})["reason"] == "policy"
    assert "reason" not in module_state({"status": "not_found"})


def test_module_states_always_the_fixed_keys_in_order():
    for profile in (_profile(), {"organisation_number": "1", "evidence": {}}, {"organisation_number": "1"}):
        assert tuple(module_states(profile)) == MODULE_KEYS
    assert len(MODULE_KEYS) == len(set(MODULE_KEYS))


def test_missing_module_is_not_fetched_and_discovered_is_not_applicable_after_discovery():
    states = module_states(_profile())
    assert states["website_discovery"]["state"] == "not_fetched"
    assert states["website_discovered"]["state"] == "not_fetched"
    discovered = module_states(_profile(website_discovery=_record("website_discovery", "not_found")))
    assert discovered["website_discovery"]["state"] == "not_found"
    assert discovered["website_discovered"]["state"] == "not_applicable"


# --- terminal_status -------------------------------------------------------

def test_completed_when_required_modules_available_or_not_applicable():
    profile = _profile(group=_record("group", "not_applicable"))
    assert profile_terminal_status(profile) == "completed"
    assert batch_state(profile) == "ok"


@pytest.mark.parametrize("module,status", [("group", "not_found"), ("roles", "blocked"), ("website", "not_found"), ("website", "source_error")])
def test_gap_states_give_completed_with_gaps(module, status):
    assert profile_terminal_status(_profile(**{module: _record(module, status, note="x")})) == "completed_with_gaps"


def test_missing_required_module_is_a_gap_not_a_failure():
    profile = _profile()
    del profile["evidence"]["roles"]
    assert profile_terminal_status(profile) == "completed_with_gaps"


@pytest.mark.parametrize("module", REQUIRED_MODULES)
def test_required_source_error_is_failed_and_sets_batch_state(module):
    profile = _profile(**{module: _record(module, "source_error", note="boom", retry_count=2)})
    assert profile_terminal_status(profile) == "failed"
    assert batch_state(profile) == "failed"


def test_optional_module_source_error_does_not_fail_the_company():
    profile = _profile(website_discovered=_record("website_discovered", "source_error"))
    assert profile_terminal_status(profile) == "completed"


# --- fat envelope and validate_envelopes -----------------------------------

def test_fat_envelope_uses_fixed_modules_and_one_vocabulary():
    fat = terminal_envelope(_profile(), run_id="r", started_at="a", completed_at="b")
    assert tuple(fat["modules"]) == MODULE_KEYS
    assert fat["schema_version"] == SCHEMA_VERSION
    assert "state" not in fat and fat["terminal_status"] == "completed"
    assert {item["state"] for item in fat["modules"].values()} <= set(MODULE_STATES)


def test_validate_envelopes_rejects_old_vocabulary_and_unknown_values():
    good = _envelope()
    assert validate_envelopes([good], 1)["passed"]
    old = copy.deepcopy(good)
    old["modules"]["roles"]["state"] = "complete"
    assert not validate_envelopes([old], 1)["passed"]
    unknown = copy.deepcopy(good)
    unknown["run"]["terminal_status"] = "complete"
    assert not validate_envelopes([unknown], 1)["checks"]["all_terminal_statuses_valid"]
    blocked = copy.deepcopy(good)
    blocked["modules"]["website"] = {"state": "blocked", "retry_count": 0}
    assert not validate_envelopes([blocked], 1)["passed"]
    assert not validate_envelopes([good, good], 2)["checks"]["unique_organisation_numbers"]


# --- public envelope -------------------------------------------------------

def test_public_envelope_shape():
    envelope = _envelope()
    assert tuple(envelope) == PUBLIC_TOP_LEVEL_KEYS
    assert envelope["schema_version"] == "1.0"
    assert "profile" not in envelope
    assert tuple(envelope["modules"]) == MODULE_KEYS
    assert envelope["run"]["terminal_status"] == "completed"
    assert set(envelope["operations"]) == {"requests", "runtime_ms", "third_party_cost_usd"}


def test_evidence_ids_are_org_scoped_zero_based_and_carry_no_value():
    a, b = _envelope(_profile("111111111")), _envelope(_profile("222222222"))
    for envelope, org in ((a, "111111111"), (b, "222222222")):
        ids = [item["id"] for item in envelope["evidence"]]
        assert ids == [f"ev-{org}-{n}" for n in range(len(ids))]
        for item in envelope["evidence"]:
            assert tuple(item) == PUBLIC_EVIDENCE_KEYS
    assert not {i["id"] for i in a["evidence"]} & {i["id"] for i in b["evidence"]}
    assert "TestCo AS" in json.dumps(a["claims"])
    assert "value" not in json.dumps(a["evidence"])


def test_claim_fields_identical_for_every_company_even_with_no_data():
    full = _envelope()
    bare = _envelope({"organisation_number": "999999999", "evidence": {}})
    expected = list(CLAIM_FIELD_ORDER)
    assert [c["field"] for c in full["claims"]] == expected
    assert [c["field"] for c in bare["claims"]] == expected
    assert bare["evidence"] == []
    assert {c["availability"] for c in bare["claims"]} == {"not_available"}


def test_not_available_claims_have_null_confidence():
    envelope = _envelope(_profile(group=_record("group", "not_found")))
    missing = [c for c in envelope["claims"] if c["availability"] == "not_available"]
    assert missing and all(c["confidence"] is None for c in missing)
    with pytest.raises(ClaimSchemaError):
        assert_claim_schema_integrity({"claims": [
            {"field": "x", "value": None, "availability": "not_available", "confidence": 0.8, "evidence_ids": []}
        ]})


def test_two_observations_for_one_field_yield_one_claim():
    profile = _profile()
    profile["observations"] = [
        {"id": "o1", "signal_type": "workforce_snapshot", "source_url": "https://a", "metrics": {"workforce_value": 5}, "content_sha256": SHA},
        {"id": "o2", "signal_type": "workforce", "source_url": "https://b", "metrics": {"workforce_value": 9}, "content_sha256": SHA},
    ]
    claims = [c for c in _envelope(profile)["claims"] if c["field"] == "workforce"]
    assert len(claims) == 1 and claims[0]["value"] == {"workforce_value": 5}


def test_conversion_failure_envelope_is_conformant_and_visible():
    envelope = conversion_failure_envelope({"profile": _profile(roles=_record("roles", "complete"))}, ValueError("boom"))
    assert tuple(envelope) == PUBLIC_TOP_LEVEL_KEYS
    assert envelope["run"]["terminal_status"] == "failed"
    assert {c["availability"] for c in envelope["claims"]} == {"failed"}
    assert envelope["errors"][0]["error_type"] == "conversion_failed"


# --- builder ---------------------------------------------------------------

def test_builder_formats_and_public_input_rejection():
    args = dict(run_id="r", started_at="2026-10-09T06:00:00Z", completed_at="2026-10-09T06:01:00Z", observations=[])
    public = builder.build_envelope(_profile(), envelope_format="public", **args)
    fat = builder.build_envelope(_profile(), envelope_format="fat", **args)
    assert "profile" not in public and "profile" in fat
    assert tuple(fat["modules"]) == MODULE_KEYS and fat["terminal_status"] == public["run"]["terminal_status"]
    assert builder.is_public_envelope(public) and not builder.is_public_envelope(fat)
    with pytest.raises(ValueError):
        builder.build_envelope(_profile(), envelope_format="nope", **args)


def test_builder_rebuilds_old_fat_envelope_modules_from_profile():
    old_fat = {"run_id": "old", "state": "complete", "modules": {"roles": {"state": "complete"}}, "profile": _profile()}
    rebuilt = builder.build_envelope(old_fat, run_id="", started_at="", completed_at="", observations=[], envelope_format="fat")
    assert rebuilt["modules"]["roles"]["state"] == "available" and rebuilt["run_id"] == "old"


# --- validator -------------------------------------------------------------

def _rows(n=2):
    return [_envelope(_profile(f"12345678{i}")) for i in range(n)]


def _failing(rows, **kwargs):
    results = validator.validate(rows, **kwargs)
    return {name for name, violations in results.items() if violations}


def test_validator_passes_on_clean_envelopes():
    assert _failing(_rows(), run_id="run-1", expected_count=2) == set()


def test_validator_has_one_check_per_name():
    assert set(validator.validate(_rows())) == set(validator.CHECK_NAMES)


@pytest.mark.parametrize("mutate,check", [
    (lambda r: r[0].update(extra=1), "01_row_keysets_identical"),
    (lambda r: r[0]["modules"].pop("website_discovered"), "01_row_keysets_identical"),
    (lambda r: r[0]["claims"].pop(), "02_claim_keysets_and_fields_identical"),
    (lambda r: r[0]["claims"][0].pop("confidence"), "02_claim_keysets_and_fields_identical"),
    (lambda r: r[0]["claims"][0]["evidence_ids"].append("ev-123456780-99"), "03_evidence_ids_resolve"),
    (lambda r: r[0]["claims"][0].update(evidence_ids=[]), "04_available_claims_have_evidence"),
    (lambda r: r[0]["evidence"].append({**r[0]["evidence"][0], "id": "ev-123456780-77"}), "05_no_orphan_evidence"),
    (lambda r: r[0]["evidence"][0].update(content_sha256="xyz"), "06_content_sha256_valid"),
    (lambda r: r[0]["evidence"][0].update(content_sha256=""), "06_content_sha256_valid"),
    (lambda r: r[0]["evidence"][0].update(content_sha256=None), "06_content_sha256_valid"),
    (lambda r: r[1]["evidence"][0].update(id="ev-123456780-0"), "07_evidence_id_matches_org"),
    (lambda r: r[0]["modules"]["roles"].update(state="complete"), "08_enum_values_allowed"),
    (lambda r: r[0]["modules"]["roles"].update(state="blocked"), "08_enum_values_allowed"),
    (lambda r: r[0]["claims"][0].update(availability="maybe"), "08_enum_values_allowed"),
    (lambda r: r[0].update(schema_version="2.0"), "08_enum_values_allowed"),
    (lambda r: r[0]["run"].update(terminal_status="complete"), "08_enum_values_allowed"),
    (lambda r: r[1].update(organisation_number=r[0]["organisation_number"]), "09_org_numbers_unique_9_digit"),
    (lambda r: r[0].update(organisation_number="12345"), "09_org_numbers_unique_9_digit"),
    (lambda r: r[0]["run"].update(completed_at="2026-10-09T05:00:00Z"), "10_completed_not_before_started"),
    (lambda r: r[0]["run"].update(started_at=""), "10_completed_not_before_started"),
    (lambda r: r[0]["run"].update(run_id="other"), "11_single_run_id"),
    (lambda r: r[0]["run"].update(terminal_status="completed_with_gaps"), "12_terminal_status_consistent_with_modules"),
    (lambda r: r.pop(), "13_expected_count"),
])
def test_validator_catches_each_violation(mutate, check):
    rows = _rows()
    mutate(rows)
    assert check in _failing(rows, run_id="run-1", expected_count=2)


def test_validator_flags_conversion_failures():
    rows = _rows()
    rows[0] = conversion_failure_envelope({"profile": _profile("123456780", roles=_record("roles", "complete"))}, ValueError("x"))
    assert "14_no_conversion_failures" in _failing(rows)


def test_report_has_run_id_and_envelope_sha256(tmp_path):
    path = tmp_path / "envelopes.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in _rows()) + "\n", encoding="utf-8")
    rows, problems = validator.read_rows(path)
    report = validator.build_report(path, rows, problems, run_id="run-1", expected_count=2)
    assert report["passed"] and report["run_id"] == "run-1" and report["schema_version"] == "1.0"
    assert report["envelopes_sha256"] == validator.sha256_file(path) and len(report["envelopes_sha256"]) == 64
    path.write_text(path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    rows, problems = validator.read_rows(path)
    assert not validator.build_report(path, rows, problems, run_id="run-1", expected_count=2)["passed"]
