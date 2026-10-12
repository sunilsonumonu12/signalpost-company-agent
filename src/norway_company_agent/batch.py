from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .evidence import evidence, utc_now
from .vocabulary import (
    SCHEMA_VERSION,
    TERMINAL_STATUSES,
    derive_terminal_status,
    invalid_module_states,
    module_states,
)


def read_organisation_inputs(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    values: list[Any]
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


def terminal_envelope(
    profile: dict[str, Any],
    *,
    run_id: str,
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
        "profile": profile,
    }


def validate_envelopes(envelopes: list[dict[str, Any]], expected_count: int) -> dict[str, Any]:
    """Count/vocabulary check that works on both fat and public envelopes."""
    orgs = [item.get("organisation_number") for item in envelopes]

    def status_of(item: dict[str, Any]) -> Any:
        return item.get("terminal_status", (item.get("run") or {}).get("terminal_status"))

    invalid_states = [
        {"organisation_number": item.get("organisation_number"), "problem": problem}
        for item in envelopes
        for problem in invalid_module_states(item.get("modules") or {})
    ]
    checks = {
        "exact_expected_count": len(envelopes) == expected_count,
        "unique_organisation_numbers": len(orgs) == len(set(orgs)),
        "all_terminal_statuses_valid": all(status_of(item) in TERMINAL_STATUSES for item in envelopes),
        "all_module_states_valid": not invalid_states,
        "zero_silent_drops": len(envelopes) == expected_count and len(orgs) == len(set(orgs)),
    }
    return {"passed": all(checks.values()), "checks": checks, "invalid_states": invalid_states}


def profile_complete_for_modules(profile: dict[str, Any], modules: Iterable[str]) -> bool:
    records = profile.get("evidence", {})
    return all(module in records and records[module].get("status") != "not_fetched" for module in modules)
