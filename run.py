#!/usr/bin/env python3
"""Launcher for resumable SignalPost agent runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = PROJECT_ROOT / "entry-companies.jsonl"
OUT_DIR = PROJECT_ROOT / "out"
WORK_DIR = OUT_DIR / "latest-run"  # Set to out/<run_id> in main().
RESULT_FILE = PROJECT_ROOT / "result" / "envelopes.jsonl"
VALIDATION_FILE = PROJECT_ROOT / "result" / "validation-report.json"
LATEST_RUN_ID_FILE = OUT_DIR / ".latest-run-id"
INPUT_HASH_FILE = "input.sha256"
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from norway_company_agent.batch import read_organisation_inputs  # noqa: E402
from norway_company_agent.env import load_local_env  # noqa: E402


def configure_tesseract_path() -> str | None:
    executable = shutil.which("tesseract")
    if executable:
        return executable
    tesseract_dir = Path(os.environ.get("TESSERACT_DIR", r"C:\Program Files\Tesseract-OCR"))
    candidate = tesseract_dir / "tesseract.exe"
    if candidate.is_file():
        os.environ["PATH"] = str(tesseract_dir) + os.pathsep + os.environ.get("PATH", "")
        return str(candidate)
    return None


def clear_public_outputs(directory: Path = OUT_DIR) -> None:
    """Delete legacy per-company JSONL outputs; called only in --fresh mode."""
    if not directory.exists():
        return
    for path in directory.iterdir():
        if path.is_file() and len(path.stem) == 9 and path.stem.isdigit() and path.suffix == ".jsonl":
            path.unlink()


def clear_run_outputs() -> None:
    """The only path that removes prior run output and the public result."""
    clear_public_outputs(OUT_DIR)
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    RESULT_FILE.unlink(missing_ok=True)
    VALIDATION_FILE.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def publish_validation_report() -> None:
    """Copy validation-report.json next to the final envelopes and print the verdict."""
    source = WORK_DIR / "validation-report.json"
    if not source.exists():
        print("WARNING | validation-report.json was not produced; envelopes are unvalidated.", file=sys.stderr)
        return
    atomic_copy_file(source, VALIDATION_FILE)
    try:
        report = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"WARNING | {VALIDATION_FILE} is unreadable.", file=sys.stderr)
        return
    failed = [name for name, check in (report.get("checks") or {}).items() if not check.get("passed")]
    verdict = "PASSED" if report.get("passed") else "FAILED (" + ", ".join(failed) + ")"
    print(f"Validation: {verdict} | report: {VALIDATION_FILE}")


def atomic_copy_file(source: Path, destination: Path) -> None:
    """Publish a complete copy atomically so a crash cannot truncate destination."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(source, temporary)
        with temporary.open("rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def valid_run_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)) and value not in {".", ".."}


def generate_run_id() -> str:
    return "run-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def resolve_run_id(requested: str | None, *, fresh: bool) -> str:
    if requested:
        if not valid_run_id(requested):
            raise ValueError("--run-id may contain only letters, digits, dot, underscore, and hyphen")
        return requested

    if not fresh:
        try:
            saved_id = LATEST_RUN_ID_FILE.read_text(encoding="utf-8").strip()
            if (
                valid_run_id(saved_id)
                and (OUT_DIR / saved_id).is_dir()
                and ((OUT_DIR / saved_id) / INPUT_HASH_FILE).is_file()
            ):
                return saved_id
        except OSError:
            pass

        # Recover the most recent managed run if its small pointer file was lost.
        candidates = [
            child for child in OUT_DIR.iterdir()
            if child.is_dir() and valid_run_id(child.name) and (child / INPUT_HASH_FILE).is_file()
        ] if OUT_DIR.exists() else []
        if candidates:
            return max(candidates, key=lambda item: item.stat().st_mtime).name

    candidate = generate_run_id()
    while (OUT_DIR / candidate).exists():
        candidate = generate_run_id()
    return candidate


def count_organisations(path: Path) -> int:
    """Count only validated, unique organisation numbers using the shared reader."""
    return len(read_organisation_inputs(path))


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
    global WORK_DIR

    parser = argparse.ArgumentParser(description="Run SignalPost and resume safely by default.")
    parser.add_argument("--organisations", type=Path, default=DEFAULT_INPUT,
                        help="Organisation input path (default: entry-companies.jsonl).")
    parser.add_argument("--run-id", help="Resume this run ID (stored under out/<run_id>).")
    parser.add_argument("--fresh", action="store_true",
                        help="Delete out/ and result/envelopes.jsonl before starting a new run.")
    parser.add_argument("--progress-interval", type=float, default=30.0, help="Seconds between live progress reports")
    parser.add_argument(
        "--source-envelopes",
        type=Path,
        help="Rebuild envelopes from a profiles or fat-envelope JSONL without rerunning collection. Public envelopes carry no profile and are rejected.",
    )
    parser.add_argument(
        "--previous-envelopes",
        type=Path,
        help="Path to previous envelopes JSONL file for change tracking.",
    )
    parser.add_argument("--include-workforce-ocr", dest="skip_workforce_ocr", action="store_false", default=True,
                        help="Enable annual-report OCR workforce extraction (default: disabled).")
    parser.add_argument("--skip-workforce-ocr", dest="skip_workforce_ocr", action="store_true",
                        help="Leave annual-report OCR workforce extraction disabled (default).")
    parser.add_argument("--include-deep-crawl", dest="skip_deep_crawl", action="store_false", default=False,
                        help="Enable the multi-page website crawl (default: enabled).")
    parser.add_argument("--skip-deep-crawl", dest="skip_deep_crawl", action="store_true",
                        help="Skip the multi-page website crawl (default: disabled).")
    args = parser.parse_args()
    load_local_env(PROJECT_ROOT)
    configure_tesseract_path()

    if args.run_id and not valid_run_id(args.run_id):
        parser.error("--run-id may contain only letters, digits, dot, underscore, and hyphen")

    previous_envelopes = args.previous_envelopes
    preserved_previous: Path | None = None
    if previous_envelopes is not None:
        if not previous_envelopes.is_absolute():
            previous_envelopes = PROJECT_ROOT / previous_envelopes
        if not previous_envelopes.exists():
            parser.error(f"--previous-envelopes file not found: {previous_envelopes}")

    def preserve_previous_if_needed() -> Path | None:
        nonlocal previous_envelopes
        if previous_envelopes is None:
            return None
        is_inside = False
        try:
            previous_envelopes.resolve().relative_to(OUT_DIR.resolve())
            is_inside = True
        except ValueError:
            pass
        if not is_inside:
            try:
                if previous_envelopes.resolve() == RESULT_FILE.resolve():
                    is_inside = True
            except Exception:
                pass
        if is_inside:
            temp_prev = PROJECT_ROOT / f".prev-envelopes-{uuid.uuid4().hex}.jsonl"
            shutil.copy2(previous_envelopes, temp_prev)
            previous_envelopes = temp_prev
            return temp_prev
        return None

    if args.source_envelopes:
        source = args.source_envelopes
        if not source.is_absolute():
            source = PROJECT_ROOT / source
        if not source.exists():
            parser.error(f"Source envelope file not found: {source}")
        if args.fresh:
            preserved_previous = preserve_previous_if_needed()
            # Preserve an input stored under out/ long enough to convert it.
            preserved_source: Path | None = None
            try:
                source.resolve().relative_to(OUT_DIR.resolve())
                preserved_source = PROJECT_ROOT / f".source-envelopes-{uuid.uuid4().hex}.jsonl"
                shutil.copy2(source, preserved_source)
                source = preserved_source
            except ValueError:
                pass
            clear_run_outputs()
            if preserved_source is not None:
                # The temporary source is outside out/ and will be removed below.
                pass
        source_work_id = args.run_id or "latest-run"
        WORK_DIR = OUT_DIR / source_work_id
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "build_output_contract.py"),
            "--profiles",
            str(source),
            "--output",
            str(RESULT_FILE),
            "--report",
            str(WORK_DIR / "source-contract-report.json"),
        ]
        if previous_envelopes is not None:
            command.extend(["--previous-envelopes", str(previous_envelopes)])
        try:
            completed = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True)
            if completed.returncode != 0:
                if completed.stderr:
                    print(completed.stderr, file=sys.stderr, end="")
                return completed.returncode
            produced = sum(1 for line in RESULT_FILE.read_text(encoding="utf-8").splitlines() if line.strip())
            expected = sum(1 for line in source.read_text(encoding="utf-8").splitlines() if line.strip())
            print(f"Converted {produced}/{expected} envelopes to {RESULT_FILE}.")
            return 0 if produced == expected else 1
        finally:
            if args.fresh and 'preserved_source' in locals() and preserved_source is not None:
                preserved_source.unlink(missing_ok=True)
            if preserved_previous is not None:
                preserved_previous.unlink(missing_ok=True)

    organisations = args.organisations
    if not organisations.is_absolute():
        organisations = PROJECT_ROOT / organisations
    if not organisations.exists():
        parser.error(f"Organisation file not found: {organisations}")

    try:
        expected_count = count_organisations(organisations)
        input_sha256 = sha256_file(organisations)
    except (OSError, ValueError) as exc:
        parser.error(f"Invalid organisation input {organisations}: {exc}")
    if expected_count == 0:
        parser.error("Organisation file contains no organisation numbers")
    if args.progress_interval <= 0:
        parser.error("--progress-interval must be positive")

    if args.fresh:
        preserved_previous = preserve_previous_if_needed()
        try:
            organisations.resolve().relative_to(OUT_DIR.resolve())
        except ValueError:
            pass
        else:
            parser.error("--fresh cannot be used when --organisations is stored inside out/, because out/ is deleted")
        clear_run_outputs()

    try:
        run_id = resolve_run_id(args.run_id, fresh=args.fresh)
    except ValueError as exc:
        parser.error(str(exc))
    WORK_DIR = OUT_DIR / run_id
    hash_path = WORK_DIR / INPUT_HASH_FILE
    run_dir_already_exists = WORK_DIR.exists()
    if run_dir_already_exists:
        if not hash_path.exists():
            parser.error(
                f"Run directory {WORK_DIR} has no saved input hash. Use --fresh or choose a new --run-id."
            )
        try:
            saved_hash = hash_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            parser.error(f"Could not read saved input hash from {hash_path}: {exc}")
        if saved_hash != input_sha256:
            parser.error(
                f"Input SHA-256 differs from the saved run input for {run_id}. Refusing to mix inputs; use --fresh or a new --run-id."
            )
    else:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_text(hash_path, input_sha256 + "\n")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(LATEST_RUN_ID_FILE, run_id + "\n")

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
    if not args.skip_workforce_ocr:
        command.append("--include-workforce-ocr")
    if not args.skip_deep_crawl:
        command.append("--include-deep-crawl")
    if previous_envelopes is not None:
        command.extend(["--previous-envelopes", str(previous_envelopes)])

    try:
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
        if return_code != 0 and envelopes_produced and produced_envelope_count >= expected_count:
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] NOTICE | RETURN-CODE OVERRIDE: subprocess exited "
                f"with code {return_code}, but {produced_envelope_count}/{expected_count} envelopes exist; "
                "treating the run as completed-with-failures.",
                file=sys.stderr,
                flush=True,
            )
            return_code = 0
        if return_code != 0:
            print_final_summary(success=False, expected_count=expected_count, state=progress, elapsed=elapsed, return_code=return_code)
            if source.exists():
                produced_envelope_count = sum(1 for line in source.read_text(encoding="utf-8").splitlines() if line.strip())
                print(
                    f"Run reported failure but {produced_envelope_count} envelopes exist at {source}. "
                    f"Intermediate files kept in {WORK_DIR}",
                    file=sys.stderr,
                )
                if produced_envelope_count:
                    atomic_copy_file(source, RESULT_FILE)
                    print(f"Partial envelope output preserved at {RESULT_FILE}", file=sys.stderr)
                    publish_validation_report()
            else:
                print(f"Run failed. Intermediate files were kept in {WORK_DIR}", file=sys.stderr)
            return return_code

        if not envelopes_produced:
            print(f"Run finished but envelope file was not created: {source}", file=sys.stderr)
            return 1
        atomic_copy_file(source, RESULT_FILE)
        publish_validation_report()
        print_final_summary(success=True, expected_count=expected_count, state=progress, elapsed=elapsed, return_code=0)
        if produced_envelope_count != expected_count:
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] WARNING | envelope count mismatch "
                f"({produced_envelope_count} vs expected {expected_count}); copied to result anyway.",
                file=sys.stderr,
            )
        print(f"Final output: {RESULT_FILE}")
        return 0
    finally:
        if preserved_previous is not None:
            preserved_previous.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
