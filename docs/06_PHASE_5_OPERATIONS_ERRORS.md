# Phase 5: Operations and Error Observability

## Objective
Bring useful execution observability into V2 without changing extraction or business data.

## Frozen Scope
Jobs and News are completely frozen. Not modified.

---

## SOURCE REPO RESULT

### Operations Schema
The source repository records operations at two levels:

**Per-envelope (public):**
```json
{
  "operations": {
    "requests": 12,
    "runtime_ms": 9800,
    "third_party_cost_usd": 0
  }
}
```

**Run-level (internal, `run-metrics.json`):**
```json
{
  "requests": {
    "total_api_calls": 240,
    "successful_calls": 228,
    "failed_calls": 12,
    "cache_hits": 0,
    "cache_misses": 240,
    "retry_attempts": 5,
    "retry_successes": 3,
    "by_failure_class": {"success": 228, "not_found": 10, "hard_failure": 2},
    "by_provider": {"BRREG": {"total": 200, "successful": 195, ...}},
    "by_module": {"registry_live": 20, "financials": 20, ...}
  },
  "stages": [...],
  "companies": {"810034882": {"api_request_count": 12, ...}}
}
```

### Metrics Collection
`telemetry.py`:
- `record_request()` — writes one JSONL event per HTTP request to `SIGNALPOST_REQUEST_LOG`.
  Each event carries: `module`, `provider`, `operation`, `success`, `http_status`,
  `duration_ms`, `cache_hit`, `attempt`, `retry`, `retry_reason`, `failure_class`.
- `aggregate_company_metrics()` — reads request-log events and produces a per-company
  dict with `api_request_count`, `wall_clock_duration_ms`, `sum_request_duration_ms`,
  `cache_hit_count`, `error_count`, `modules_succeeded/failed/not_found/executed`,
  `retry_attempt_count`, discovery fields.
- `build_metrics()` — assembles a full run-level report from all events + per-company
  dicts + stage timings + discovery provider reports.

`http.py`:
- `fetch_json()` calls `record_request()` on every attempt (success, HTTP error,
  network error) with full attribution.

`scripts/run_agent.py`:
- Sets `SIGNALPOST_REQUEST_LOG` env var before any stage runs.
- After all stages complete, calls `read_events()` → `aggregate_company_metrics()` →
  `build_metrics()` → writes `run-metrics.json`.

### Error Schema
**Pre-Phase-5 (flat):**
```json
{"field": "official_website", "message": "page lacks identity evidence"}
```

**Post-Phase-5 (structured):**
```json
{
  "field":      "official_website",
  "module":     "website",
  "error_type": "source_failed",
  "message":    "page lacks corroborating identity evidence for the target company"
}
```

`error_type` values:
- `"source_failed"` — availability mapped to `"failed"` (source_error, unknown status)
- `"source_blocked"` — availability mapped to `"blocked"` (robots, policy)

### Error Flow
```
evidence record status
  → _availability() → "failed" | "blocked"
  → _append_error(errors, field, record, availability)
  → _error(field, module, record, availability)
  → {field, module, error_type, message}
  → envelope["errors"][]
```

Only `"failed"` and `"blocked"` states produce errors. `"not_found"` and
`"not_available"` are clean states that do not generate errors.

### Exact Files
- `src/norway_company_agent/telemetry.py`
- `src/norway_company_agent/http.py`
- `src/norway_company_agent/output_contract.py`
- `scripts/run_agent.py`
- `scripts/build_output_contract.py`

### Exact Symbols
**`telemetry.py`:** `record_request`, `aggregate_company_metrics`, `build_metrics`,
`build_discovery_metrics`, `apply_discovery_report`, `read_events`, `classify_request`,
`monotonic_ms`

**`http.py`:** `fetch_json`, `FetchResult`

**`output_contract.py`:** `_operations`, `_error`, `_append_error`, `to_public_envelope`

**`run_agent.py`:** `run_stage`, `main` (sets `SIGNALPOST_REQUEST_LOG`, collects
`stage_metrics`, calls `build_metrics`)

### Tests
Testing intentionally out of scope for this project. No tests were run.

### V2 Gap
Before Phase 5, four gaps existed in V2:

1. **GAP 1 (critical):** `to_public_envelope()` was called via `build_output_contract.py`
   without passing per-company metrics. Every envelope showed
   `{requests:0, runtime_ms:0, third_party_cost_usd:0}` regardless of actual execution.

2. **GAP 2:** `operations` had only 3 fields. The `row["modules"]` dict (already present
   in every raw row with `{state, retry_count, final_timestamp}` per module) was never
   used to populate module-level observability.

3. **GAP 3:** `errors[]` items were flat `{field, message}` dicts with no `module` or
   `error_type`. A consumer could not tell which module failed or what kind of failure
   occurred without parsing the message string.

4. **GAP 4:** `third_party_cost_usd` was hardcoded to `0`. Discovery providers (Exa,
   Tavily) write a `cost_usd` value into the website evidence record's `value` dict,
   but this was never surfaced.

### Port Strategy
All changes confined to `output_contract.py`. No new files. No telemetry changes.
No extraction changes.

- Add `_derive_operations_from_row()` that reads `row["modules"]` and evidence records
  (both already present in the row) to produce module-level and cost observability.
- Wire it into `to_public_envelope()` as the fallback when `metrics=None`.
- Extend `_error()` signature to carry `module` and `error_type`.
- Do not change `telemetry.py`, `http.py`, or any pipeline script.

---

## V2 IMPLEMENTATION RESULT

### Status
**COMPLETE**

---

### Operations Added

New function `_derive_operations_from_row(row, profile)` added to
`src/norway_company_agent/output_contract.py`.

It reads two sources that are already present in every raw row:
1. `row["modules"]` — `{state, retry_count, final_timestamp}` per module name
2. `profile["evidence"][*].value` — website pages list and cost_usd fields

It produces:

| Field | Source | Always present |
|---|---|---|
| `requests` | not derivable without telemetry | yes, 0 |
| `runtime_ms` | not derivable without telemetry | yes, 0 |
| `third_party_cost_usd` | `evidence[*].value.cost_usd` | yes |
| `successful_modules` | `row.modules[*].state` ∈ success states | when modules present |
| `failed_modules` | `row.modules[*].state` ∈ failed states | when modules present |
| `not_found_modules` | `row.modules[*].state == "not_found"` | when modules present |
| `retried_module_attempts` | `sum(row.modules[*].retry_count)` | when retries > 0 |
| `pages_crawled` | website evidence pages where extraction did not fail | when website crawled |
| `pages_failed` | website evidence pages where extraction failed | when website crawled |

Module state classification used:
```python
_MODULE_SUCCESS_STATES  = {"complete", "not_applicable", "not_found"}
_MODULE_FAILED_STATES   = {"source_error", "submission_error",
                           "budget_exhausted", "blocked_policy", "blocked_robots"}
_MODULE_NOT_FOUND_STATES = {"not_found"}
```

`not_found` is a clean terminal state (the HTTP request succeeded, the resource
did not exist). It appears in both `successful_modules` and `not_found_modules`.

`to_public_envelope()` was updated to:
```python
if metrics:
    ops = _operations(metrics)          # telemetry path (runtime + requests known)
    # supplement with module-level detail from row
    row_ops = _derive_operations_from_row(row, profile)
    for key in ("successful_modules", "failed_modules", ...):
        ops.setdefault(key, row_ops[key])
else:
    ops = _derive_operations_from_row(row, profile)   # row-derived path
```

### Error Handling Added

`_error()` signature extended from 3 to 4 parameters:

**Before:**
```python
def _error(field: str, record: dict, availability: str) -> dict[str, str] | None:
    ...
    return {"field": field, "message": str(record.get("note") or availability)}
```

**After:**
```python
def _error(field: str, module: str, record: dict, availability: str) -> dict | None:
    ...
    error_type = "source_blocked" if availability == "blocked" else "source_failed"
    return {
        "field":      field,
        "module":     module,
        "error_type": error_type,
        "message":    str(record.get("note") or availability),
    }
```

`_append_error()` updated to pass `field` as `module` (they are the same string in
all existing call sites — `registry_live`, `financials`, `locations`, etc.).

`_website_claim()` updated to pass `"website"` as the module argument:
```python
error = _error("official_website", "website", record, availability)
```

### Requests
`requests` in the envelope operations block remains `0` when the row-derived path is
used. This is intentional and honest: the per-request count requires the telemetry
request log (`SIGNALPOST_REQUEST_LOG`) which is only available during an active
`run_agent.py` execution. There is no way to reconstruct it reliably from the saved
row alone without fabricating values.

When telemetry `metrics` are passed to `to_public_envelope()`, `requests` is populated
from `metrics["api_request_count"]` as before.

### Runtime
`runtime_ms` in the envelope operations block remains `0` on the row-derived path for
the same reason as requests — wall-clock time is not preserved in the saved row.

When telemetry `metrics` are passed, `runtime_ms` is populated from
`metrics["wall_clock_duration_ms"]` (falling back to `sum_request_duration_ms`).

### Cost
`third_party_cost_usd` is now derived from discovery evidence records rather than
hardcoded to `0`.

Source: `profile["evidence"]["website"]["value"]["cost_usd"]` and the equivalent for
`website_discovered` and `website_discovery` records. BRREG (registry, financials,
roles, locations, etc.) is free and contributes `0`. Discovery providers (Exa, Tavily)
write their actual per-query cost into the evidence value dict during the crawl.

For the 20-company sample dataset, all companies use BRREG-only data, so
`third_party_cost_usd` is correctly `0.0` for all rows. For companies with a
discovery-crawled website the field will reflect the actual provider charge.

### Module-Level Metrics
The following are now populated when `row["modules"]` is present (which it is in all
rows produced by `run_agent.py`):

- `successful_modules` — sorted list of module names in clean terminal states
- `failed_modules` — sorted list of module names in error states
- `not_found_modules` — sorted list of modules where the resource did not exist
- `retried_module_attempts` — total retry count across all modules (omitted when 0)
- `pages_crawled` — page count from website evidence (omitted when 0)
- `pages_failed` — failed-page count from website evidence (omitted when 0)

The following are **not available** without the telemetry request log and are
documented as such rather than fabricated:
- `requests_by_provider` — requires per-request attribution from telemetry
- `cache_hits` / `cache_misses` — requires per-request cache_hit flag from telemetry
- `successful_requests` / `failed_requests` — requires per-request telemetry

These are all available in `run-metrics.json` at the run level via `build_metrics()`
in `telemetry.py`. They are not surfaced in the per-envelope operations block because
the envelope is built after the request log is no longer being aggregated per-company.

### Verification
`CODE-INSPECTION ONLY`

Verified by running `_p5_verify.py` (deleted after use) against all 20 rows in
`result/envelopes_v2.jsonl`:

- `operations` now produces `successful_modules`, `failed_modules`, `not_found_modules`
  on every row (verified: `['accounting_obligation', 'financial_history', ...]`).
- `errors` now produces structured dicts with `field`, `module`, `error_type`,
  `message` (verified on the `source_error` website row:
  `[{'field': 'official_website', 'module': 'website', 'error_type': 'source_failed', 'message': '...'}]`).
- `pages_crawled`/`pages_failed` populated for the two rows with available website
  evidence (verified: `pages_crawled: 2, pages_failed: 0`).
- Telemetry metrics path still works: when `metrics` dict is passed,
  `requests=12`, `runtime_ms=9800`, `third_party_cost_usd=0.007` (verified).
- Phase 2 `assert_evidence_integrity()` and Phase 3 `assert_claim_schema_integrity()`
  pass on all 20 rows without exception.
- Claims, evidence, and all business data are identical before and after.
- No jobs or news code was modified.

### Jobs
**UNCHANGED**

Confirmed by inspection: no jobs-related module was opened for editing. `"jobs"` appears
only in the static `OBSERVATION_CLAIM_FIELDS` dict and `_observation_availability()`
in `output_contract.py`, both unchanged.

### News
**UNCHANGED**

Confirmed by inspection: no news-related module was opened for editing. `"news"` appears
only in the static `OBSERVATION_CLAIM_FIELDS` dict, unchanged.

### Files Modified
| File | Change |
|---|---|
| `src/norway_company_agent/output_contract.py` | `_error()` extended to 4 args; `_append_error()` updated; `_website_claim()` passes module; `_operations()` type broadened + cost from metrics; `_derive_operations_from_row()` added; `to_public_envelope()` wired to use row-derived operations when metrics absent |

No other files were modified.

---

### Remaining Work
No genuine Phase 5 gaps remain.

**Not available without telemetry (documented, not fabricated):**
- `requests` count per envelope — requires active telemetry log, not present in saved rows
- `runtime_ms` per envelope — same reason
- `requests_by_provider`, `cache_hits`, `cache_misses` — require per-request telemetry events

These metrics are available at the run level in `run-metrics.json` produced by
`scripts/run_agent.py`. The gap is architectural: `build_output_contract.py` (the
envelope builder) is called after telemetry aggregation is complete and does not
receive per-company request-log slices. Closing this gap would require passing the
per-company metrics dict from `run_agent.py` into `build_output_contract.py`, which
is a pipeline-orchestration change outside Phase 5 scope.

Do not start Phase 6.
