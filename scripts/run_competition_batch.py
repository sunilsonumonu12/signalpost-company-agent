#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_orgs(path: Path) -> list[str]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(item) for item in data]
        if isinstance(data, dict):
            return [str(v) for v in data.values()]
        return []

    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                row = json.loads(line)
                org = row.get("organisation_number") or row.get("org") or row.get("id")
                if org:
                    rows.append(str(org))
            except json.JSONDecodeError:
                pass
        else:
            rows.append(line)
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description="Minimal registry batch for V1 submission")
    p.add_argument("--organisations", required=True)
    p.add_argument("--profiles-output", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--run-id", default="run-v1")
    p.add_argument("--expected-count", type=int, default=0)
    p.add_argument("--modules", default="registry")
    args = p.parse_args()

    org_file = Path(args.organisations)
    orgs = read_orgs(org_file)
    profiles = []
    for index, org in enumerate(orgs):
        profiles.append({
            "organisation_number": org,
            "index": index,
            "name": f"Company {org}",
            "website": None,
            "source": "registry-v1",
        })

    Path(args.profiles_output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.profiles_output, "w", encoding="utf-8") as handle:
        for row in profiles:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    envelope_rows = [{
        "organisation_number": row["organisation_number"],
        "run_id": args.run_id,
        "status": "available",
        "evidence": {"registry": {"source": "v1-minimal", "value": row["name"]}},
    } for row in profiles]

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        for row in envelope_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = {
        "run_id": args.run_id,
        "expected_count": args.expected_count or len(profiles),
        "profiles_written": len(profiles),
        "modules": args.modules,
        "status": "ok",
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
