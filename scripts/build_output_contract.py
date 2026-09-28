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
        envelope["run_id"] = run_id
        return envelope

    profile = copy.deepcopy(row.get("profile") or row)
    if observations:
        profile["observations"] = observations
    modules = list((profile.get("evidence") or {}).keys())
    return terminal_envelope(
        profile,
        run_id=run_id,
        modules=modules,
        started_at=started_at,
        completed_at=completed_at,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Minimal output contract builder for V1")
    p.add_argument("--input", help="Compatible alias for input profiles/envelopes path")
    p.add_argument("--profiles", help="Profiles JSONL file to convert")
    p.add_argument("--output", required=True)
    p.add_argument("--report", help="Optional report output path")
    p.add_argument("--run-id", default="run-v1")
    p.add_argument("--started-at", default="")
    p.add_argument("--completed-at", default="")
    p.add_argument("--observations", action="append", default=[])
    args = p.parse_args()

    source_path = Path(args.input or args.profiles)
    if not source_path.exists():
        source_path = Path(args.profiles) if args.profiles else Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report or output_path.parent / "contract-report.json")

    rows = read_jsonl(source_path)
    observations = observations_by_organisation(args.observations)
    final_rows = [
        build_envelope(
            row,
            run_id=args.run_id,
            started_at=args.started_at,
            completed_at=args.completed_at,
            observations=observations.get(str(row.get("organisation_number")), []),
        )
        for row in rows
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in final_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report_path.write_text(
        json.dumps({"rows": len(final_rows), "status": "ok", "run_id": args.run_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
