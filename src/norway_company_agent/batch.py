from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .evidence import evidence, utc_now
<<<<<<< HEAD
from .vocabulary import (
    SCHEMA_VERSION,
    TERMINAL_STATUSES,
    derive_terminal_status,
    invalid_module_states,
    module_states,
)
=======


TERMINAL_STATES = {
    "complete",
    "not_applicable",
    "not_found",
    "blocked_policy",
    "blocked_robots",
    "source_error",
    "budget_exhausted",
    "submission_error",
}
>>>>>>> 970c2821c2cb2b51e112fba6317fab76e65e20b3


def read_organisation_inputs(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    values: list[Any]
<<<<<<< HEAD
    # Some existing inputs use a .jsonl filename but contain one JSON array.
    # Detect that by content so the shared validator accepts the current default.
    if source.suffix.lower() == ".json" or text.lstrip().startswith("["):
        body = json.loads(text)
        if isinstance(body, list):
            values = body
        elif isinstance(body, dict):
            values = body.get("organisation_numbers", [])
        else:
            raise ValueError("Organisation input must be a JSON array or object")
        if not isinstance(values, list):
            raise ValueError("'organisation_numbers' must be a JSON array")
    elif source.suffix.lower() == ".jsonl":
=======
    if source.suffix == ".json":
        body = json.loads(text)
        values = body if isinstance(body, list) else body.get("organisation_numbers", [])
    elif source.suffix == ".jsonl":
>>>>>>> 970c2821c2cb2b51e112fba6317fab76e65e20b3
        values = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        values = [line.strip() for line in text.splitlines() if line.strip()]
    records = []
    for value in values:
        org = value.get("organisation_number") if isinstance(value, dict) else value
        org = "".join(character for character in str(org or "") if character.isdigit())
        if len(org) != 9:
            raise ValueError(f"Invalid Norwegian organisation number: {value!r}")
        record = {"organisation_number": org}
        if isinstance(value, dict):
            for key in ("evaluation_split", "sample_slice"):
                if value.get(key) is not None:
                    record[key] = value[key]
        records.append(record)
    orgs = [record["organisation_number"] for record in records]
    if len(orgs) != len(set(orgs)):
        raise ValueError("Organisation-number input contains duplicates")
    return records


def read_organisation_numbers(path: str | Path) -> list[str]:
    return [record["organisation_number"] for record in read_organisation_inputs(path)]


def profiles_from_inputs(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create hint-only profiles; live BRREG is the only runtime registry source."""
    profiles = []
    for item in inputs:
        org = item["organisation_number"]
        profile = {
            "organisation_number": org,
            "name": item.get("name"),
            "legal_form": item.get("legal_form"),
            "employees": item.get("employees"),
            "bankrupt": None,
            "liquidating": None,
            "municipality": None,
            "municipality_number": None,
            "industry_code": item.get("industry_code"),
            "industry_label": None,
            "website": item.get("website"),
            "latest_submitted_accounts": None,
            "input_metadata": {key: item[key] for key in ("name", "legal_form", "employees", "industry_code", "website") if key in item},
            "evidence": {
                "registry": evidence(
                    "registry",
                    "not_applicable",
                    "official_registry_live",
                    "https://data.brreg.no/enhetsregisteret/api/enheter/" + org,
                    note="Runtime uses the live BRREG lookup; input metadata is hint-only.",
                    source_row_key=org,
                )
            },
        }
        profiles.append(profile)
    return profiles


def backfill_from_registry_live(profile: dict[str, Any]) -> dict[str, Any]:
    """Apply authoritative top-level fields from a successful live lookup."""
    live_record = (profile.get("evidence") or {}).get("registry_live") or {}
    if live_record.get("status") != "available":
        return profile
    live = live_record.get("value") or {}
    business_address = live.get("business_address") or {}
    industry = live.get("industry") or {}
    backfill = {
        "name": live.get("name"),
        "legal_form": live.get("legal_form"),
        "employees": live.get("employees"),
        "bankrupt": live.get("bankrupt"),
        "liquidating": live.get("liquidating"),
        "website": live.get("website"),
        "latest_submitted_accounts": live.get("latest_submitted_accounts"),
        "municipality": business_address.get("kommune"),
        "municipality_number": business_address.get("kommunenummer"),
        "industry_code": industry.get("kode"),
        "industry_label": industry.get("beskrivelse"),
    }
    for field, value in backfill.items():
        if value not in (None, ""):
            profile[field] = value
    return profile


<<<<<<< HEAD
=======
def evidence_terminal_state(record: dict[str, Any] | None) -> str:
    if not record:
        return "submission_error"
    status = record.get("status")
    if status == "available":
        return "complete"
    if status == "not_applicable":
        return "not_applicable"
    if status == "not_found":
        return "not_found"
    if status == "blocked":
        note = str(record.get("note") or "").casefold()
        return "blocked_robots" if "robot" in note else "blocked_policy"
    if status == "source_error":
        return "source_error"
    return "submission_error"


>>>>>>> 970c2821c2cb2b51e112fba6317fab76e65e20b3
def terminal_envelope(
    profile: dict[str, Any],
    *,
    run_id: str,
<<<<<<< HEAD
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    """Fat envelope. Module keys are the fixed MODULE_KEYS list, never the evidence keys."""
    states = module_states(profile)
    for module, state in states.items():
        record = (profile.get("evidence") or {}).get(module)
        state["final_timestamp"] = (record or {}).get("retrieved_at") or completed_at
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "organisation_number": profile["organisation_number"],
        "terminal_status": derive_terminal_status(states),
        "started_at": started_at,
        "completed_at": completed_at,
        "modules": states,
=======
    modules: Iterable[str],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    module_states = {}
    for module in modules:
        record = profile.get("evidence", {}).get(module)
        module_states[module] = {
            "state": evidence_terminal_state(record),
            "retry_count": int((record or {}).get("retry_count") or 0),
            "final_timestamp": (record or {}).get("retrieved_at") or completed_at,
        }
    entity_state = "submission_error" if any(item["state"] == "submission_error" for item in module_states.values()) else "complete"
    return {
        "run_id": run_id,
        "organisation_number": profile["organisation_number"],
        "state": entity_state,
        "started_at": started_at,
        "completed_at": completed_at,
        "modules": module_states,
>>>>>>> 970c2821c2cb2b51e112fba6317fab76e65e20b3
        "profile": profile,
    }


def validate_envelopes(envelopes: list[dict[str, Any]], expected_count: int) -> dict[str, Any]:
<<<<<<< HEAD
    """Count/vocabulary check that works on both fat and public envelopes."""
    orgs = [item.get("organisation_number") for item in envelopes]

    def status_of(item: dict[str, Any]) -> Any:
        return item.get("terminal_status", (item.get("run") or {}).get("terminal_status"))

    invalid_states = [
        {"organisation_number": item.get("organisation_number"), "problem": problem}
        for item in envelopes
        for problem in invalid_module_states(item.get("modules") or {})
=======
    orgs = [item.get("organisation_number") for item in envelopes]
    invalid_states = [
        {"organisation_number": item.get("organisation_number"), "state": state.get("state")}
        for item in envelopes
        for state in item.get("modules", {}).values()
        if state.get("state") not in TERMINAL_STATES
>>>>>>> 970c2821c2cb2b51e112fba6317fab76e65e20b3
    ]
    checks = {
        "exact_expected_count": len(envelopes) == expected_count,
        "unique_organisation_numbers": len(orgs) == len(set(orgs)),
<<<<<<< HEAD
        "all_terminal_statuses_valid": all(status_of(item) in TERMINAL_STATUSES for item in envelopes),
        "all_module_states_valid": not invalid_states,
=======
        "all_entity_states_terminal": all(item.get("state") in TERMINAL_STATES for item in envelopes),
        "all_module_states_terminal": not invalid_states,
>>>>>>> 970c2821c2cb2b51e112fba6317fab76e65e20b3
        "zero_silent_drops": len(envelopes) == expected_count and len(orgs) == len(set(orgs)),
    }
    return {"passed": all(checks.values()), "checks": checks, "invalid_states": invalid_states}


def profile_complete_for_modules(profile: dict[str, Any], modules: Iterable[str]) -> bool:
    records = profile.get("evidence", {})
    return all(module in records and records[module].get("status") != "not_fetched" for module in modules)
