#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from norway_company_agent.batch import terminal_envelope  # noqa: E402
from norway_company_agent.output_contract import conversion_failure_envelope, to_public_envelope  # noqa: E402
from norway_company_agent.vocabulary import SCHEMA_VERSION  # noqa: E402

ENVELOPE_FORMATS = ("public", "fat")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def observations_by_organisation(paths: list[str]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for raw_path in paths:
        for observation in read_jsonl(Path(raw_path)):
            organisation_number = observation.get("organisation_number")
            if organisation_number is None:
                continue
            grouped.setdefault(str(organisation_number), []).append(observation)
    return grouped


def attach_claims(envelope: dict) -> dict:
    """Fat format: add the public claims/evidence projection to the fat envelope."""
    public = to_public_envelope(envelope)
    envelope["claims"] = public["claims"]
    envelope["evidence"] = public["evidence"]
    return envelope


def build_fat(
    row: dict,
    *,
    run_id: str,
    started_at: str,
    completed_at: str,
    observations: list[dict],
) -> dict:
    """Rebuild the fat envelope from the profile. Module states are always recomputed
    from profile.evidence; whatever `modules` an input envelope carried is ignored."""
    profile = copy.deepcopy(row.get("profile") or row)
    if observations:
        profile["observations"] = observations
    return terminal_envelope(
        profile,
        run_id=run_id or str(row.get("run_id") or "") or "run-v1",
        started_at=started_at or str(row.get("started_at") or ""),
        completed_at=completed_at or str(row.get("completed_at") or ""),
    )


def load_previous_envelopes(path: Path | str | None) -> dict[str, dict]:
    """Load previous envelopes by organisation number for change detection."""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        print(f"Warning: --previous-envelopes file {p} does not exist", file=sys.stderr)
        return {}

    bad_lines = 0
    org_map: dict[str, dict] = {}
    duplicates: set[str] = set()

    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            bad_lines += 1
            continue
        if not isinstance(row, dict):
            bad_lines += 1
            continue

        org = str(row.get("organisation_number") or (row.get("profile") or {}).get("organisation_number") or "")
        if not org:
            bad_lines += 1
            continue

        if org in org_map or org in duplicates:
            duplicates.add(org)
            org_map.pop(org, None)
            continue

        if isinstance(row.get("claims"), list):
            org_map[org] = row
        elif isinstance(row.get("profile"), dict):
            try:
                derived = to_public_envelope(row)
                org_map[org] = derived
            except Exception as exc:
                print(f"Warning: could not derive claims for previous org {org}: {exc}", file=sys.stderr)
                continue
        else:
            bad_lines += 1

    if duplicates:
        for dup in sorted(duplicates):
            print(f"Warning: duplicate organisation number {dup} in previous envelopes; skipping org for changes", file=sys.stderr)

    if bad_lines:
        print(f"Warning: skipped {bad_lines} unparseable or invalid line(s) in previous envelopes", file=sys.stderr)

    return org_map


def build_envelope(
    row: dict,
    *,
    run_id: str,
    started_at: str,
    completed_at: str,
    observations: list[dict],
    envelope_format: str = "public",
    previous_envelope: dict | None = None,
) -> dict:
    if envelope_format not in ENVELOPE_FORMATS:
        raise ValueError(f"envelope_format must be one of {ENVELOPE_FORMATS}, got {envelope_format!r}")
    fat = build_fat(
        row, run_id=run_id, started_at=started_at, completed_at=completed_at, observations=observations,
    )
    if envelope_format == "fat":
        return attach_claims(fat)
    return to_public_envelope(fat, previous_envelope=previous_envelope)


def is_public_envelope(row: dict) -> bool:
    return isinstance(row.get("claims"), list) and isinstance(row.get("run"), dict) and "profile" not in row


def main() -> int:
    p = argparse.ArgumentParser(description="Output contract builder: profiles -> envelopes")
    p.add_argument("--input", help="Compatible alias for input profiles/envelopes path")
    p.add_argument("--profiles", help="Profiles JSONL file to convert")
    p.add_argument("--output", required=True, help="Envelopes JSONL output path")
    p.add_argument("--report", help="Optional report output path")
    p.add_argument("--run-id", default="")
    p.add_argument("--started-at", default="")
    p.add_argument("--completed-at", default="")
    p.add_argument("--observations", action="append", default=[])
    p.add_argument(
        "--envelope-format", choices=ENVELOPE_FORMATS, default="public",
        help="public: claims-based output (default). fat: legacy envelope with the full profile.",
    )
    p.add_argument("--previous-envelopes", help="Optional path to previous envelopes JSONL")
    args = p.parse_args()

    source_path = Path(args.input or args.profiles or "")
    if not source_path.exists():
        source_path = Path(args.profiles) if args.profiles else Path(args.input or "")
    if not str(source_path) or not source_path.exists():
        p.error("Provide --input or --profiles pointing to an existing JSONL file")

    output_path = Path(args.output)
    report_path = Path(args.report) if args.report else output_path.parent / "contract-report.json"

    previous_envelopes = load_previous_envelopes(args.previous_envelopes) if args.previous_envelopes else {}

    rows = read_jsonl(source_path)
    public_rows = [row for row in rows if is_public_envelope(row)]
    if public_rows:
        p.error(
            f"{len(public_rows)} input row(s) are already public envelopes; they carry no profile and "
            "cannot be rebuilt. Feed profiles.jsonl or fat envelopes instead."
        )
    observations = observations_by_organisation(args.observations)
    envelopes: list[dict] = []
    claims_failed_orgs: list[str] = []
    for row in rows:
        organisation_number = str(
            row.get("organisation_number") or (row.get("profile") or {}).get("organisation_number") or ""
        )
        prev_env = previous_envelopes.get(organisation_number)
        try:
            envelope = build_envelope(
                row,
                run_id=args.run_id,
                started_at=args.started_at,
                completed_at=args.completed_at,
                observations=observations.get(organisation_number, []),
                envelope_format=args.envelope_format,
                previous_envelope=prev_env,
            )
        except Exception as exc:
            # Stage 6 is batch-critical, but a malformed company must not prevent
            # usable envelopes for every other company. The failure stays visible:
            # terminal_status=failed, failed claims, and an errors[] entry.
            failed_row = dict(row)
            failed_row["run_id"] = args.run_id or str(row.get("run_id") or "")
            failed_row["started_at"] = args.started_at or str(row.get("started_at") or "")
            failed_row["completed_at"] = args.completed_at or str(row.get("completed_at") or "")
            envelope = conversion_failure_envelope(failed_row, exc)
            if args.envelope_format == "fat":
                envelope = {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": envelope["run"]["run_id"],
                    "organisation_number": organisation_number,
                    "terminal_status": "failed",
                    "started_at": envelope["run"]["started_at"],
                    "completed_at": envelope["run"]["completed_at"],
                    "modules": envelope["modules"],
                    "profile": row.get("profile") or row,
                    "claims": envelope["claims"],
                    "evidence": [],
                    "errors": envelope["errors"],
                }
            claims_failed_orgs.append(organisation_number)
        envelopes.append(envelope)

    atomic_write_jsonl(output_path, envelopes)

    report_path.parent.mkdir(parents=True, exist_ok=True)

    def run_id_of(item: dict) -> str:
        return str(item.get("run_id") or (item.get("run") or {}).get("run_id") or "")

    def status_of(item: dict) -> str:
        return str(item.get("terminal_status") or (item.get("run") or {}).get("terminal_status") or "")

    report_run_id = args.run_id or next((run_id_of(item) for item in envelopes if run_id_of(item)), "run-v1")
    status_counts: dict[str, int] = {}
    for item in envelopes:
        status_counts[status_of(item)] = status_counts.get(status_of(item), 0) + 1
    changes_total = sum(len(item.get("changes") or []) for item in envelopes)
    atomic_write_text(
        report_path,
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "envelope_format": args.envelope_format,
                "rows": len(envelopes),
                "status": "ok",
                "run_id": report_run_id,
                "terminal_status_counts": status_counts,
                "claims_failed": len(claims_failed_orgs),
                "claims_failed_orgs": claims_failed_orgs,
                "previous_envelopes": str(args.previous_envelopes) if args.previous_envelopes else None,
                "previous_envelopes_loaded": len(previous_envelopes),
                "changes_total": changes_total,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
