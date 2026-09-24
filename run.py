#!/usr/bin/env python3
"""Simple launcher for a clean, repeatable SignalPost agent run."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = PROJECT_ROOT / "entry-companies.jsonl"
WORK_DIR = PROJECT_ROOT / "out" / "latest-run"
RESULT_FILE = PROJECT_ROOT / "result" / "envelopes.jsonl"


def count_organisations(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SignalPost and keep one clean result file.")
    parser.add_argument("--organisations", type=Path, default=DEFAULT_INPUT)
    args = parser.parse_args()

    organisations = args.organisations
    if not organisations.is_absolute():
        organisations = PROJECT_ROOT / organisations
    if not organisations.exists():
        parser.error(f"Organisation file not found: {organisations}")

    expected_count = count_organisations(organisations)
    if expected_count == 0:
        parser.error(f"Organisation file is empty: {organisations}")

    run_id = "run-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    if RESULT_FILE.exists():
        RESULT_FILE.unlink()

    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_agent.py"),
        "--organisations",
        str(organisations),
        "--output-dir",
        str(WORK_DIR),
        "--run-id",
        run_id,
        "--expected-count",
        str(expected_count),
    ]

    print(f"Starting run: {run_id}")
    print(f"Companies: {expected_count}")
    completed = subprocess.run(command, cwd=PROJECT_ROOT)
    if completed.returncode != 0:
        print(f"Run failed. Intermediate files were kept in {WORK_DIR}", file=sys.stderr)
        return completed.returncode

    source = WORK_DIR / "envelopes.jsonl"
    if not source.exists():
        print(f"Run finished but result was not created: {source}", file=sys.stderr)
        return 1

    shutil.copy2(source, RESULT_FILE)
    shutil.rmtree(WORK_DIR)
    print(f"Result: {RESULT_FILE.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())