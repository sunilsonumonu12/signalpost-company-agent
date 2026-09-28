#!/usr/bin/env python3
"""Simple launcher for a clean, repeatable SignalPost agent run."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = PROJECT_ROOT / "entry-companies.jsonl"
WORK_DIR = PROJECT_ROOT / "out" / "latest-run"
RESULT_FILE = PROJECT_ROOT / "result" / "envelopes.jsonl"


def count_organisations(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    if text.lstrip().startswith("["):
        values = json.loads(text)
        return len(values) if isinstance(values, list) else 0
    return sum(1 for line in text.splitlines() if line.strip())


def read_progress(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, PermissionError):
        return {}
    return value if isinstance(value, dict) else {}


def read_stage(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip() or "starting"
    except OSError:
        return "starting"


def elapsed_seconds(started: float) -> float:
    return max(0.0, time.monotonic() - started)


def emit_status(
    *,
    progress_path: Path,
    stage_path: Path,
    expected_count: int,
    started: float,
) -> None:
    state = read_progress(progress_path)
    elapsed = elapsed_seconds(started)
    completed = int(state.get("completed", 0))
    successful = int(state.get("successful", 0))
    failed = int(state.get("failed", 0))
    active = int(state.get("active", 0))
    rate = completed / elapsed if elapsed else 0.0
    stage = read_stage(stage_path)
    last_completed_company = state.get("last_completed_company") or state.get("last_company")
    last_progress_at = float(state.get("last_progress_at", time.time()))
    since_last_completion = max(0, int(time.time() - last_progress_at))
    total_companies = max(expected_count, int(state.get("total", expected_count) or expected_count))

    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] PROGRESS | "
        f"companies={completed}/{total_companies} | "
        f"completed={completed} | success={successful} | failed={failed} | "
        f"active={active} | rate={rate:.2f} companies/sec | elapsed={int(elapsed)}s | "
        f"last_company={last_completed_company or 'n/a'} | since_last_completion={since_last_completion}s"
    )


def run_with_progress(command: list[str], *, expected_count: int, progress_interval: float) -> tuple[int, dict, float]:
    progress_path = WORK_DIR / "progress.json"
    stage_path = WORK_DIR / "progress-stage.txt"
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=PROJECT_ROOT)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] RUN STARTED | total={expected_count}", flush=True)
    next_report = started + progress_interval

    while True:
        now = time.monotonic()
        if process.poll() is not None:
            break
        wait_for = max(0.05, min(1.0, next_report - now))
        try:
            process.wait(timeout=wait_for)
            break
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            if now >= next_report:
                emit_status(
                    progress_path=progress_path,
                    stage_path=stage_path,
                    expected_count=expected_count,
                    started=started,
                )
                next_report = now + progress_interval

    elapsed = elapsed_seconds(started)
    return int(process.returncode or 0), read_progress(progress_path), elapsed


def print_final_summary(*, success: bool, expected_count: int, state: dict, elapsed: float, return_code: int) -> None:
    completed = int(state.get("completed", 0))
    successful = int(state.get("successful", 0))
    failed = int(state.get("failed", 0))
    rate = completed / elapsed if elapsed else 0.0
    if success:
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] COMPLETE | "
            f"companies={completed}/{expected_count} | success={successful} | failed={failed} | "
            f"elapsed={int(elapsed)}s | rate={rate:.2f} companies/sec"
        )
        return

    last_progress_at = float(state.get("last_progress_at", 0))
    last_progress = max(0, int(time.time() - last_progress_at)) if last_progress_at else int(elapsed)
    root_error = state.get("root_error") or f"Pipeline exited with code {return_code}. See traceback above."
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] FAILED | "
        f"companies={completed}/{expected_count} | success={successful} | failed={failed} | "
        f"elapsed={int(elapsed)}s | last_progress={last_progress}s | root_error={root_error}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SignalPost and keep one clean result file.")
    parser.add_argument("--organisations", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--progress-interval", type=float, default=30.0, help="Seconds between live progress reports")
    parser.add_argument(
        "--source-envelopes",
        type=Path,
        help="Convert an existing populated envelope JSONL through the result contract without rerunning collection.",
    )
    args = parser.parse_args()

    if args.source_envelopes:
        source = args.source_envelopes
        if not source.is_absolute():
            source = PROJECT_ROOT / source
        if not source.exists():
            parser.error(f"Source envelope file not found: {source}")
        RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
        if RESULT_FILE.exists():
            RESULT_FILE.unlink()
        run_id = "result-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "build_output_contract.py"),
            "--profiles",
            str(source),
            "--output",
            str(RESULT_FILE),
            "--run-id",
            run_id,
        ]
        completed = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True)
        if completed.returncode != 0:
            if completed.stderr:
                print(completed.stderr, file=sys.stderr, end="")
            return completed.returncode
        return 0

    organisations = args.organisations
    if not organisations.is_absolute():
        organisations = PROJECT_ROOT / organisations
    if not organisations.exists():
        parser.error(f"Organisation file not found: {organisations}")

    expected_count = count_organisations(organisations)
    if expected_count == 0:
        parser.error(f"Organisation file is empty: {organisations}")
    if args.progress_interval <= 0:
        parser.error("--progress-interval must be positive")

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

    return_code, progress, elapsed = run_with_progress(
        command,
        expected_count=expected_count,
        progress_interval=args.progress_interval,
    )
    source = WORK_DIR / "envelopes.jsonl"
    envelopes_produced = source.exists()
    produced_envelope_count = 0
    if envelopes_produced:
        try:
            produced_envelope_count = sum(1 for line in source.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            produced_envelope_count = 0
    batch_completed = envelopes_produced and produced_envelope_count >= expected_count
    if return_code != 0 and batch_completed:
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] NOTICE | subprocess exited with code {return_code} "
            f"but {produced_envelope_count}/{expected_count} envelopes were produced; treating as completed-with-failures.",
            file=sys.stderr,
        )
        return_code = 0
    if return_code != 0:
        print_final_summary(success=False, expected_count=expected_count, state=progress, elapsed=elapsed, return_code=return_code)
        if envelopes_produced:
            print(
                f"Run reported failure but {produced_envelope_count} envelopes exist at {source}. "
                f"Intermediate files kept in {WORK_DIR}",
                file=sys.stderr,
            )
            if produced_envelope_count >= 1:
                RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
                if RESULT_FILE.exists():
                    RESULT_FILE.unlink()
                shutil.copy2(source, RESULT_FILE)
        else:
            print(f"Run failed. Intermediate files were kept in {WORK_DIR}", file=sys.stderr)
        return return_code

    if not envelopes_produced:
        print(f"Run finished but result was not created: {source}", file=sys.stderr)
        return 1
    produced_envelope_count = sum(1 for line in source.read_text(encoding="utf-8").splitlines() if line.strip())
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    if RESULT_FILE.exists():
        RESULT_FILE.unlink()
    shutil.copy2(source, RESULT_FILE)
    print_final_summary(success=True, expected_count=expected_count, state=progress, elapsed=elapsed, return_code=0)
    if produced_envelope_count != expected_count:
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] WARNING | envelope count mismatch "
            f"({produced_envelope_count} vs expected {expected_count}); copied to result anyway.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())