#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from norway_company_agent.batch import terminal_envelope  # noqa: E402
from norway_company_agent.output_contract import to_public_envelope  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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
    """Add the public claims/evidence projection without changing the fat envelope."""
    public = to_public_envelope(envelope)
    envelope["claims"] = public["claims"]
    envelope["evidence"] = public["evidence"]
    return envelope


def build_envelope(
    row: dict,
    *,
    run_id: str,
    started_at: str,
    completed_at: str,
    observations: list[dict],
) -> dict:
    if row.get("profile") is not None and row.get("modules") is not None:
        envelope = copy.deepcopy(row)
        if run_id:
            envelope["run_id"] = run_id
        if started_at:
            envelope["started_at"] = started_at
        if completed_at:
            envelope["completed_at"] = completed_at
        if observations:
            profile = copy.deepcopy(envelope.get("profile") or {})
            profile["observations"] = observations
            envelope["profile"] = profile
        return attach_claims(envelope)

    profile = copy.deepcopy(row.get("profile") or row)
    if observations:
        profile["observations"] = observations
    modules = list((profile.get("evidence") or {}).keys())
    envelope = terminal_envelope(
        profile,
        run_id=run_id or "run-v1",
        modules=modules,
        started_at=started_at,
        completed_at=completed_at,
    )
    return attach_claims(envelope)


def main() -> int:
    p = argparse.ArgumentParser(description="Minimal output contract builder for V1")
    p.add_argument("--input", help="Compatible alias for input profiles/envelopes path")
    p.add_argument("--profiles", help="Profiles JSONL file to convert")
    p.add_argument("--output", required=True, help="Fat envelopes JSONL output path")
    p.add_argument("--report", help="Optional report output path")
    p.add_argument("--run-id", default="")
    p.add_argument("--started-at", default="")
    p.add_argument("--completed-at", default="")
    p.add_argument("--observations", action="append", default=[])
    args = p.parse_args()

    source_path = Path(args.input or args.profiles or "")
    if not source_path.exists():
        source_path = Path(args.profiles) if args.profiles else Path(args.input or "")
    if not str(source_path) or not source_path.exists():
        p.error("Provide --input or --profiles pointing to an existing JSONL file")

    output_path = Path(args.output)
    report_path = Path(args.report) if args.report else output_path.parent / "contract-report.json"

    rows = read_jsonl(source_path)
    observations = observations_by_organisation(args.observations)
    fat_envelopes: list[dict] = []
    claims_failed_orgs: list[str] = []
    for row in rows:
        organisation_number = str(
            row.get("organisation_number") or (row.get("profile") or {}).get("organisation_number") or ""
        )
        try:
            envelope = build_envelope(
                row,
                run_id=args.run_id,
                started_at=args.started_at,
                completed_at=args.completed_at,
                observations=observations.get(organisation_number, []),
            )
        except Exception as exc:
            # Stage 6 is batch-critical, but a malformed company must not prevent
            # usable envelopes for every other company.
            if row.get("profile") is not None and row.get("modules") is not None:
                envelope = copy.deepcopy(row)
                if args.run_id:
                    envelope["run_id"] = args.run_id
                if args.started_at:
                    envelope["started_at"] = args.started_at
                if args.completed_at:
                    envelope["completed_at"] = args.completed_at
                profile = copy.deepcopy(envelope.get("profile") or {})
            else:
                profile = copy.deepcopy(row.get("profile") or row)
                envelope = None
            if observations.get(organisation_number):
                profile["observations"] = observations[organisation_number]
            if envelope is None:
                modules = list((profile.get("evidence") or {}).keys())
                envelope = terminal_envelope(
                    profile,
                    run_id=args.run_id or str(row.get("run_id") or "run-v1"),
                    modules=modules,
                    started_at=args.started_at or str(row.get("started_at") or ""),
                    completed_at=args.completed_at or str(row.get("completed_at") or ""),
                )
            else:
                envelope["profile"] = profile
            envelope["claims"] = []
            envelope["evidence"] = []
            envelope["claims_error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
            claims_failed_orgs.append(organisation_number)
        fat_envelopes.append(envelope)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in fat_envelopes:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_run_id = args.run_id or next(
        (str(row.get("run_id")) for row in fat_envelopes if row.get("run_id")),
        "run-v1",
    )
    report_path.write_text(
        json.dumps(
            {
                "rows": len(fat_envelopes),
                "status": "ok",
                "run_id": report_run_id,
                "claims_failed": len(claims_failed_orgs),
                "claims_failed_orgs": claims_failed_orgs,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
