#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from norway_company_agent.identity import _name_similarity, _tokens  # noqa: E402


ALLOWED_DECISIONS = {"ACCEPT", "REVIEW", "REJECT"}
ALLOWED_CORROBORATION = {"organisation_number", "domain", "address"}


def read_examples(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on line {line_number}: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Line {line_number} must contain a JSON object")
        expected = str(row.get("expected_decision") or "").upper()
        if expected not in ALLOWED_DECISIONS:
            raise ValueError(f"Line {line_number} expected_decision must be ACCEPT, REVIEW, or REJECT")
        if not str(row.get("target_name") or "").strip() or not str(row.get("candidate_name") or "").strip():
            raise ValueError(f"Line {line_number} requires target_name and candidate_name")
        evidence = row.get("corroborating_evidence") or []
        if not isinstance(evidence, list) or any(item not in ALLOWED_CORROBORATION for item in evidence):
            raise ValueError(
                f"Line {line_number} corroborating_evidence must be a list containing only "
                "organisation_number, domain, and/or address"
            )
        rows.append({**row, "expected_decision": expected, "corroborating_evidence": list(dict.fromkeys(evidence))})
    if not rows:
        raise ValueError("Calibration input contains no examples")
    if not any(row["expected_decision"] == "ACCEPT" for row in rows):
        raise ValueError("Calibration requires at least one expected ACCEPT example")
    if not any(row["expected_decision"] != "ACCEPT" for row in rows):
        raise ValueError("Calibration requires expected REVIEW/REJECT examples to measure false positives")
    return rows


def calibrate(examples: list[dict], dataset_sha256: str) -> dict:
    scored = []
    for index, row in enumerate(examples, start=1):
        similarity = _name_similarity(_tokens(row["target_name"]), row["candidate_name"])
        scored.append({
            "case_id": str(row.get("case_id") or index),
            "expected_decision": row["expected_decision"],
            "similarity": similarity,
            "corroborating_evidence": row["corroborating_evidence"],
        })

    accept_scores = [row["similarity"] for row in scored if row["expected_decision"] == "ACCEPT"]
    non_accept_scores = [row["similarity"] for row in scored if row["expected_decision"] != "ACCEPT"]
    review_scores = [row["similarity"] for row in scored if row["expected_decision"] != "REJECT"]
    minimum_accept = min(accept_scores)
    maximum_non_accept = max(non_accept_scores)
    accept_threshold = None
    calibration_status = "no_separating_threshold"
    if maximum_non_accept < minimum_accept:
        candidate_threshold = math.nextafter(maximum_non_accept, math.inf)
        if candidate_threshold <= minimum_accept:
            accept_threshold = candidate_threshold
            calibration_status = "calibrated"

    review_threshold = min(review_scores) if review_scores else None
    outcomes = Counter()
    false_positives = 0
    false_negatives = 0
    case_results = []
    for row in scored:
        enough_corroboration = bool(row["corroborating_evidence"])
        if accept_threshold is not None and row["similarity"] >= accept_threshold and enough_corroboration:
            actual = "ACCEPT"
        elif review_threshold is not None and row["similarity"] >= review_threshold:
            actual = "REVIEW"
        else:
            actual = "REJECT"
        false_positive = actual == "ACCEPT" and row["expected_decision"] != "ACCEPT"
        false_negative = row["expected_decision"] == "ACCEPT" and actual != "ACCEPT"
        false_positives += int(false_positive)
        false_negatives += int(false_negative)
        outcomes[f"{row['expected_decision']}->{actual}"] += 1
        case_results.append({**row, "actual_decision": actual, "false_positive": false_positive, "false_negative": false_negative})

    return {
        "schema_version": 1,
        "calibration_status": calibration_status,
        "threshold_method": "labeled_score_separation_with_one_corroborator",
        "thresholds": {"review_score": review_threshold, "accept_score": accept_threshold},
        "dataset_sha256": dataset_sha256,
        "examples": {
            "total": len(scored),
            "expected_accept": len(accept_scores),
            "expected_review_or_reject": len(non_accept_scores),
            "maximum_non_accept_similarity": maximum_non_accept,
            "minimum_accept_similarity": minimum_accept,
        },
        "outcomes": dict(sorted(outcomes.items())),
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "case_results": case_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive identity similarity thresholds from labeled JSONL examples.")
    parser.add_argument(
        "--examples",
        type=Path,
        required=True,
        help="JSONL rows with case_id, target_name, candidate_name, expected_decision, and corroborating_evidence",
    )
    parser.add_argument("--output", type=Path, required=True, help="Calibration JSON to load as identity-calibration.json")
    args = parser.parse_args()
    try:
        raw = args.examples.read_bytes()
        examples = read_examples(args.examples)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    result = calibrate(examples, hashlib.sha256(raw).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "case_results"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())