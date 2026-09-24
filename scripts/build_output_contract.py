#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description="Minimal output contract builder for V1")
    p.add_argument("--input", help="Compatible alias for input profiles/envelopes path")
    p.add_argument("--profiles", help="Profiles JSONL file to convert")
    p.add_argument("--output", required=True)
    p.add_argument("--report", help="Optional report output path")
    p.add_argument("--run-id", default="run-v1")
    p.add_argument("--started-at", default="")
    p.add_argument("--completed-at", default="")
    args = p.parse_args()

    source_path = Path(args.input or args.profiles)
    if not source_path.exists():
        source_path = Path(args.profiles) if args.profiles else Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report or output_path.parent / "contract-report.json")

    rows = []
    if source_path.exists():
        with source_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))

    final_rows = []
    for row in rows:
        final_rows.append({
            "organisation_number": row.get("organisation_number"),
            "status": row.get("status", "available"),
            "evidence": row.get("evidence", {}),
            "run_id": args.run_id,
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in final_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report_path.write_text(json.dumps({"rows": len(final_rows), "status": "ok", "run_id": args.run_id}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
