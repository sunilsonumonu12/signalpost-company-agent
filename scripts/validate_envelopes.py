#!/usr/bin/env python3
"""Validate a public envelopes JSONL file and write validation-report.json.

Checks 1-10 are the Phase 3 list. Checks 11-14 are added because a validator that
ignores run identity, terminal_status consistency, row count and conversion failures
proves little.

Exit code: 0 all checks passed, 1 at least one check failed, 2 file unreadable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from norway_company_agent.output_contract import (  # noqa: E402
    ALLOWED_AVAILABILITY,
    CANONICAL_CLAIM_FIELDS,
    CLAIM_FIELD_ORDER,
    PUBLIC_EVIDENCE_KEYS,
    PUBLIC_RUN_KEYS,
    PUBLIC_TOP_LEVEL_KEYS,
)
from norway_company_agent.vocabulary import (  # noqa: E402
    MODULE_KEYS,
    PUBLIC_MODULE_STATES,
    SCHEMA_VERSION,
    TERMINAL_STATUSES,
    derive_public_terminal_status,
    invalid_public_module_states,
)

HEX64 = re.compile(r"^[0-9a-f]{64}$")
ORG_NUMBER = re.compile(r"^\d{9}$")
EVIDENCE_ID = re.compile(r"^ev-(\d{9})-(\d+)$")
CLAIM_KEYSET = tuple(sorted(CANONICAL_CLAIM_FIELDS))
EXAMPLE_LIMIT = 5

CHECK_NAMES = (
    "01_row_keysets_identical",
    "02_claim_keysets_and_fields_identical",
    "03_evidence_ids_resolve",
    "04_available_claims_have_evidence",
    "05_no_orphan_evidence",
    "06_content_sha256_valid",
    "07_evidence_id_matches_org",
    "08_enum_values_allowed",
    "09_org_numbers_unique_9_digit",
    "10_completed_not_before_started",
    "11_single_run_id",
    "12_terminal_status_consistent_with_modules",
    "13_expected_count",
    "14_no_conversion_failures",
    "15_available_claims_have_value",
    "16_registry_sources_not_company_owned",
)


def _org(row: dict[str, Any]) -> str:
    return str(row.get("organisation_number"))


def _evidence_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in row.get("evidence") or [] if isinstance(item, dict)]


def _claims(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [claim for claim in row.get("claims") or [] if isinstance(claim, dict)]


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def check_row_keysets(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        keys = tuple(row)
        if keys != PUBLIC_TOP_LEVEL_KEYS:
            out.append(f"{_org(row)}: top-level keys {sorted(set(keys) ^ set(PUBLIC_TOP_LEVEL_KEYS))} differ from schema")
        run = row.get("run")
        if not isinstance(run, dict) or tuple(run) != PUBLIC_RUN_KEYS:
            out.append(f"{_org(row)}: run keys differ from {list(PUBLIC_RUN_KEYS)}")
        modules = row.get("modules")
        if not isinstance(modules, dict) or tuple(modules) != MODULE_KEYS:
            out.append(f"{_org(row)}: modules keys differ from the fixed {len(MODULE_KEYS)}-key list")
        for item in _evidence_items(row):
            if tuple(item) != PUBLIC_EVIDENCE_KEYS:
                out.append(f"{_org(row)}: evidence {item.get('id')} keys {list(item)} differ from {list(PUBLIC_EVIDENCE_KEYS)}")
    return out


def check_claims(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    expected = list(CLAIM_FIELD_ORDER)
    for row in rows:
        claims = _claims(row)
        for claim in claims:
            if tuple(sorted(claim)) != CLAIM_KEYSET:
                out.append(f"{_org(row)}: claim {claim.get('field')!r} keys {sorted(claim)} differ from {list(CLAIM_KEYSET)}")
        fields = [claim.get("field") for claim in claims]
        if fields != expected:
            duplicates = [name for name, count in Counter(fields).items() if count > 1]
            out.append(
                f"{_org(row)}: claim fields differ (missing={[f for f in expected if f not in fields]}, "
                f"extra={[f for f in fields if f not in expected]}, duplicates={duplicates})"
            )
    return out


def check_evidence_resolves(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        known = {item.get("id") for item in _evidence_items(row)}
        for claim in _claims(row):
            for evidence_id in claim.get("evidence_ids") or []:
                if evidence_id not in known:
                    out.append(f"{_org(row)}: claim {claim.get('field')!r} references missing evidence {evidence_id!r}")
    return out


def check_available_has_evidence(rows: list[dict[str, Any]]) -> list[str]:
    return [
        f"{_org(row)}: claim {claim.get('field')!r} is available with no evidence_ids"
        for row in rows
        for claim in _claims(row)
        if claim.get("availability") == "available" and not claim.get("evidence_ids")
    ]


def check_orphans(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        referenced = {evidence_id for claim in _claims(row) for evidence_id in claim.get("evidence_ids") or []}
        for item in _evidence_items(row):
            if item.get("id") not in referenced:
                out.append(f"{_org(row)}: evidence {item.get('id')!r} is referenced by no claim")
    return out


def check_sha256(rows: list[dict[str, Any]]) -> list[str]:
    """64 lowercase hex wherever the evidence backs an available claim; elsewhere
    null (nothing was fetched) or 64 hex. An empty string is never valid."""
    out: list[str] = []
    for row in rows:
        backing = {
            evidence_id
            for claim in _claims(row)
            if claim.get("availability") == "available"
            for evidence_id in claim.get("evidence_ids") or []
        }
        for item in _evidence_items(row):
            sha = item.get("content_sha256")
            if isinstance(sha, str) and HEX64.match(sha):
                continue
            if sha is None and item.get("id") not in backing:
                continue
            out.append(f"{_org(row)}: evidence {item.get('id')!r} content_sha256 {sha!r} invalid (backs available claim: {item.get('id') in backing})")
    return out


def check_evidence_ids(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: dict[str, str] = {}
    for row in rows:
        for item in _evidence_items(row):
            evidence_id = str(item.get("id"))
            match = EVIDENCE_ID.match(evidence_id)
            if not match:
                out.append(f"{_org(row)}: evidence id {evidence_id!r} does not match ev-<org>-<n>")
            elif match.group(1) != _org(row):
                out.append(f"{_org(row)}: evidence id {evidence_id!r} carries another organisation number")
            if evidence_id in seen:
                out.append(f"{_org(row)}: evidence id {evidence_id!r} already used by {seen[evidence_id]}")
            seen[evidence_id] = _org(row)
    return out


def check_enums(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        org = _org(row)
        if row.get("schema_version") != SCHEMA_VERSION:
            out.append(f"{org}: schema_version {row.get('schema_version')!r} != {SCHEMA_VERSION!r}")
        status = (row.get("run") or {}).get("terminal_status")
        if status not in TERMINAL_STATUSES:
            out.append(f"{org}: terminal_status {status!r} not in {list(TERMINAL_STATUSES)}")
        out.extend(f"{org}: {problem}" for problem in invalid_public_module_states(row.get("modules") or {}))
        for claim in _claims(row):
            field = claim.get("field")
            if claim.get("availability") not in ALLOWED_AVAILABILITY:
                out.append(f"{org}: claim {field!r} availability {claim.get('availability')!r} not in {sorted(ALLOWED_AVAILABILITY)}")
            confidence = claim.get("confidence")
            if confidence is not None and (
                isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1
            ):
                out.append(f"{org}: claim {field!r} confidence {confidence!r} outside [0, 1]")
            if claim.get("availability") == "not_available" and confidence is not None:
                out.append(f"{org}: claim {field!r} is not_available but confidence is {confidence!r}, must be null")
    return out


def check_org_numbers(rows: list[dict[str, Any]]) -> list[str]:
    out = [f"organisation_number {row.get('organisation_number')!r} is not 9 digits" for row in rows
           if not ORG_NUMBER.match(str(row.get("organisation_number")))]
    out.extend(
        f"organisation_number {org} appears {count} times"
        for org, count in Counter(_org(row) for row in rows).items() if count > 1
    )
    return out


def check_timestamps(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        run = row.get("run") or {}
        started, completed = _parse_time(run.get("started_at")), _parse_time(run.get("completed_at"))
        if started is None or completed is None:
            out.append(f"{_org(row)}: started_at {run.get('started_at')!r} / completed_at {run.get('completed_at')!r} not parseable")
        elif completed < started:
            out.append(f"{_org(row)}: completed_at {run.get('completed_at')} is before started_at {run.get('started_at')}")
    return out


def check_run_id(rows: list[dict[str, Any]], expected: str | None) -> list[str]:
    ids = {str((row.get("run") or {}).get("run_id") or "") for row in rows}
    out: list[str] = []
    if "" in ids:
        out.append("at least one row has an empty run_id")
    if len(ids - {""}) > 1:
        out.append(f"rows disagree on run_id: {sorted(ids)}")
    if expected and ids - {expected}:
        out.append(f"run_id {sorted(ids - {expected})} != expected {expected!r}")
    return out


def check_terminal_status(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        modules = row.get("modules") or {}
        if invalid_public_module_states(modules):
            continue  # check 8 already reports it
        expected = derive_public_terminal_status(modules)
        actual = (row.get("run") or {}).get("terminal_status")
        if actual != expected:
            out.append(f"{_org(row)}: terminal_status {actual!r} but modules imply {expected!r}")
    return out


def check_expected_count(rows: list[dict[str, Any]], expected: int | None) -> list[str]:
    if expected is None:
        return []
    return [f"{len(rows)} envelopes, expected {expected}"] if len(rows) != expected else []


def check_conversion_failures(rows: list[dict[str, Any]]) -> list[str]:
    """A row the builder could not convert (for example an unknown status) is a validation error."""
    return [
        f"{_org(row)}: {error.get('message')}"
        for row in rows
        for error in row.get("errors") or []
        if isinstance(error, dict) and error.get("error_type") == "conversion_failed"
    ]


def check_available_claims_have_value(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        org = _org(row)
        for claim in _claims(row):
            if claim.get("availability") == "available":
                val = claim.get("value")
                field = claim.get("field")
                if val is None or (isinstance(val, str) and not val.strip()) or (isinstance(val, dict) and not val):
                    out.append(f"{org}: claim {field!r} is available but has empty/null value: {val!r}")
    return out


def check_registry_sources_not_company_owned(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        org = _org(row)
        for item in _evidence_items(row):
            url = str(item.get("source_url") or "")
            host = (urllib.parse.urlparse(url).hostname or "").lower()
            source_class = item.get("source_class")
            if (host == "brreg.no" or host.endswith(".brreg.no")) and source_class == "company_owned":
                out.append(
                    f"{org}: evidence {item.get('id')!r} from host {host!r} has source_class 'company_owned'"
                )
    return out


def validate(rows: list[dict[str, Any]], *, run_id: str | None = None, expected_count: int | None = None) -> dict[str, list[str]]:
    results = {
        CHECK_NAMES[0]: check_row_keysets(rows),
        CHECK_NAMES[1]: check_claims(rows),
        CHECK_NAMES[2]: check_evidence_resolves(rows),
        CHECK_NAMES[3]: check_available_has_evidence(rows),
        CHECK_NAMES[4]: check_orphans(rows),
        CHECK_NAMES[5]: check_sha256(rows),
        CHECK_NAMES[6]: check_evidence_ids(rows),
        CHECK_NAMES[7]: check_enums(rows),
        CHECK_NAMES[8]: check_org_numbers(rows),
        CHECK_NAMES[9]: check_timestamps(rows),
        CHECK_NAMES[10]: check_run_id(rows, run_id),
        CHECK_NAMES[11]: check_terminal_status(rows),
        CHECK_NAMES[12]: check_expected_count(rows, expected_count),
        CHECK_NAMES[13]: check_conversion_failures(rows),
        CHECK_NAMES[14]: check_available_claims_have_value(rows),
        CHECK_NAMES[15]: check_registry_sources_not_company_owned(rows),
    }
    return results


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"line {number}: invalid JSON ({exc.msg})")
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            problems.append(f"line {number}: not a JSON object")
    return rows, problems


def build_report(path: Path, rows: list[dict[str, Any]], parse_problems: list[str], *, run_id: str | None, expected_count: int | None) -> dict[str, Any]:
    results = validate(rows, run_id=run_id, expected_count=expected_count)
    if parse_problems:
        results[CHECK_NAMES[0]] = parse_problems + results[CHECK_NAMES[0]]
    run_ids = sorted({str((row.get("run") or {}).get("run_id") or "") for row in rows} - {""})
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id or (run_ids[0] if len(run_ids) == 1 else None),
        "envelopes_file": str(path),
        "envelopes_sha256": sha256_file(path),
        "envelope_count": len(rows),
        "validated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "passed": not any(results.values()),
        "checks": {
            name: {
                "passed": not violations,
                "violation_count": len(violations),
                "examples": violations[:EXAMPLE_LIMIT],
            }
            for name, violations in results.items()
        },
    }


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate public envelopes and write validation-report.json")
    parser.add_argument("--input", "--envelopes", dest="input", required=True, type=Path, help="Public envelopes JSONL")
    parser.add_argument("--report", type=Path, help="Default: validation-report.json next to the input")
    parser.add_argument("--run-id", default=None, help="Run id every envelope must carry")
    parser.add_argument("--expected-count", type=int, default=None)
    args = parser.parse_args()

    try:
        rows, parse_problems = read_rows(args.input)
        report = build_report(args.input, rows, parse_problems, run_id=args.run_id, expected_count=args.expected_count)
    except OSError as exc:
        print(f"Cannot read {args.input}: {exc}", file=sys.stderr)
        return 2
    report_path = args.report or args.input.parent / "validation-report.json"
    atomic_write_json(report_path, report)

    failed = {name: check for name, check in report["checks"].items() if not check["passed"]}
    print(f"VALIDATION {'PASSED' if report['passed'] else 'FAILED'} | {report['envelope_count']} envelopes | report: {report_path}")
    for name, check in failed.items():
        print(f"  {name}: {check['violation_count']} violation(s), e.g. {check['examples'][0]}", file=sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
