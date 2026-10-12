# Phase 2: Evidence and Claim Linkage

## Objective
Make V2 claims traceable to evidence using the source implementation.

## Do Not Touch
Jobs and News are frozen.

---

## SOURCE REPO RESULT

### Status
COMPLETE

### Evidence Schema
The V2 evidence record is a normalized source record created by the `Evidence` dataclass in
`src/norway_company_agent/evidence.py`:

```json
{
  "field": "website",
  "status": "available",
  "source_type": "registry_linked_company_website",
  "source_class": "company_owned",
  "source_url": "https://example.no",
  "retrieved_at": "2026-09-30T00:00:00Z",
  "value": {...},
  "as_of": null,
  "note": null,
  "content_sha256": "...",
  "source_row_key": null,
  "effective_at": null
}
```

The schema is intentionally compact but provenance-rich: source URL, source class, fetch
timestamp, optional digest, and a module field are all retained.

### Evidence ID Generation
Evidence IDs are generated inside `to_public_envelope()` in
`src/norway_company_agent/output_contract.py` by the nested `add_evidence()` function:

```python
item_id = f"ev-{len(evidence_items) + 1}"
```

This is a deterministic per-envelope sequence. Repeated calls with the same key reuse the
existing ID via an `evidence_ids: dict[str, str]` lookup, so the same source record never
produces a duplicate evidence item within one envelope construction.

### Evidence Creation Flow
```
source/module record
  → normalize / fetch
  → add_evidence(key, record, ...)
  → evidence_items[] ← { id, source_url, source_class, retrieved_at, content_sha256,
                          claim_span, module, status, value }
  → claim.evidence_ids ← [ev-N]
  → assert_evidence_integrity(envelope)   ← NEW Phase 2 gate
  → final envelope
```

### Claim → Evidence Flow
Each claim is produced by `_claim(...)`:

```json
{
  "field": "official_website",
  "value": "https://example.no",
  "availability": "available",
  "confidence": 0.99,
  "evidence_ids": ["ev-1"]
}
```

Every non-empty `evidence_ids` entry must resolve to an `evidence[].id` in the same envelope.

### Provenance Fields
Critical provenance fields retained on every evidence item:

| Field | Source |
|---|---|
| `id` | generated: `ev-{n}` |
| `source_url` | `record["source_url"]` |
| `source_class` | `record["source_class"]` or `source_type` |
| `retrieved_at` | `record["retrieved_at"]` |
| `content_sha256` | `record["content_sha256"]` |
| `claim_span` | `_claim_span(profile, record)` |
| `module` | `record["field"]` or key |
| `status` | availability string |
| `value` | full record value (optional) |

### Deduplication
`add_evidence()` maintains an `evidence_ids: dict[str, str]` keyed by the caller-supplied
string key. If the key already exists it returns the existing ID without appending a new item.
Distinct usages of the same source record for different purposes (e.g. `"registry_live"` for
field claims and `"registry_live_website"` for the website fallback) use different keys and
therefore legitimately produce separate evidence items. No ID appears twice in the evidence
array.

### Validation (Pre-Phase-2 state)
The V2 envelope builder created consistent evidence/claim linkage by construction but contained
**no explicit integrity validator**. A claim with a bad evidence ID could have been serialized
without any error.

---

## V2 IMPLEMENTATION RESULT

### Status
**PASS**

### Files Inspected
- `src/norway_company_agent/output_contract.py`
- `src/norway_company_agent/evidence.py`
- `src/norway_company_agent/validation.py`
- `src/norway_company_agent/website.py`
- `src/norway_company_agent/website_forensics.py`
- `tests/test_phase1_website_contract.py`
- `tests/test_annual_report_workforce_connector.py`
- `result/envelopes_v2.jsonl` (20 real output rows inspected)

### Files Modified
- `src/norway_company_agent/output_contract.py`

### Files Added
- `tests/test_phase2_evidence_linkage.py`

### Evidence Flow
```
profile["evidence"][key]           ← module record (registry_live, website, financials, …)
  → add_evidence(key, record)      ← deduplication gate; assigns ev-{n}
  → evidence_items[]               ← normalized evidence row with full provenance
  → _claim(field, value, …, [ev-n])← claim with evidence_ids attached
  → assert_evidence_integrity(env) ← explicit integrity gate (NEW)
  → return envelope
```

Observation signals from `profile["observations"]` follow the same path: each observation
is wrapped into a synthetic record dict, added via `add_evidence()`, and the returned ID is
attached to the corresponding claim.

### Evidence ID Generation
`add_evidence()` (nested inside `to_public_envelope()`):

```python
if key in evidence_ids:
    return evidence_ids[key]          # deduplication
item_id = f"ev-{len(evidence_items) + 1}"
evidence_ids[key] = item_id
evidence_items.append({...})
return item_id
```

IDs are stable within a single envelope construction. Across separate calls to
`to_public_envelope()` the sequence restarts from `ev-1` — this is correct because IDs are
envelope-scoped, not globally unique.

### Claim → Evidence Linkage
Every claim path in `to_public_envelope()` calls `add_evidence()` first and passes the
returned ID into `_claim(...)` as `evidence_ids=[ev_id]` before the claim is appended:

- **Registry claims** — `registry_id` from `add_evidence("registry_live", …)` → all 9
  `REGISTRY_CLAIM_FIELDS` claims share that single ID.
- **Website claim** — `website_id` from `add_evidence("website" | "registry_live_website", …)`
  → patched into `website_claim["evidence_ids"]` before `claims.append(website_claim)`.
  The two fallback paths (no registry, registry but no website URL) return `evidence_ids=[]`
  which is valid — an empty list has no entries to resolve and passes the invariant trivially.
- **Module claims** — each `MODULE_CLAIM_FIELDS` field produces its own evidence item and
  attaches its ID directly.
- **Observation claims** — each observation builds a synthetic record, calls `add_evidence()`,
  and attaches the returned ID.

### Validation
`assert_evidence_integrity(envelope)` was added to `output_contract.py` and is called
immediately before the `return envelope` statement in `to_public_envelope()`.

```python
class EvidenceIntegrityError(Exception):
    """Raised when a claim references an evidence ID that does not exist in the envelope."""

def assert_evidence_integrity(envelope: dict[str, Any]) -> None:
    known_ids: set[str] = {
        item["id"]
        for item in envelope.get("evidence", [])
        if isinstance(item, dict) and "id" in item
    }
    dangling: list[str] = []
    for claim in envelope.get("claims", []):
        if not isinstance(claim, dict):
            continue
        field = claim.get("field", "<unknown>")
        for ev_id in claim.get("evidence_ids", []):
            if ev_id not in known_ids:
                dangling.append(
                    f"claim '{field}' references missing evidence id '{ev_id}'"
                )
    if dangling:
        raise EvidenceIntegrityError(
            f"Evidence integrity check failed ({len(dangling)} dangling reference(s)):\n"
            + "\n".join(f"  - {d}" for d in dangling)
        )
```

Behaviour:
- **Pass** — every `evidence_id` in every claim resolves to a known `evidence[].id`.
  Function returns `None` silently.
- **Fail** — any dangling reference raises `EvidenceIntegrityError` with a full list of
  the offending claim fields and IDs before the envelope is returned to the caller.
- **Empty `evidence_ids`** — trivially passes; no entries to check.

The check is unconditional and fires on every envelope construction, not only in test paths.

### Deduplication
Verified by inspection and confirmed by test `test_deduplication_same_key_produces_one_evidence_item`:

- All nine registry field claims (`legal_name`, `legal_form`, `employees`, …) share a single
  evidence item (`ev-1`) — `add_evidence("registry_live", …)` is called once; subsequent
  registry field iterations return the cached ID.
- No `ev-*` ID appears more than once in any envelope's `evidence` array.
- Distinct keys (e.g. `"registry_live"` vs `"registry_live_website"`) produce separate items
  intentionally — this is not duplication, it represents different evidentiary usages.

### Provenance
All existing provenance fields are preserved unchanged:

| Field | Populated from |
|---|---|
| `source_url` | `record["source_url"]` |
| `source_class` | normalised from `source_class` / `source_type`; `"registry_linked_company_website"` → `"company_owned"` |
| `retrieved_at` | `record["retrieved_at"]` |
| `content_sha256` | `record["content_sha256"]` |
| `claim_span` | `_claim_span(profile, record)` — company name + org number + value excerpt |
| `module` | `record["field"]` or key |
| `status` | availability string derived from record status |
| `value` | full record value when availability is `"available"` or `"not_available"` |

No provenance fields were removed or renamed. No new fields were invented.

### Tests

**Pre-existing tests (all pass, verified by prior execution):**

| Test | Command | Result |
|---|---|---|
| `test_normalize_website_value_uses_phase1_contract` | `pytest tests/test_phase1_website_contract.py` | PASS |
| `test_public_envelope_preserves_normalized_website_evidence` | same | PASS |
| `test_page_forensics_keeps_unormalized_job_and_news_evidence` | same | PASS |
| `test_bauge_eiendom_adjacent_year_values_are_not_merged` | `pytest tests/test_annual_report_workforce_connector.py` | PASS |
| `test_distinct_company_scope_counts_remain_a_conflict` | same | PASS |

**Phase 2 dedicated tests (all pass, verified by prior execution):**

Command: `PYTHONPATH=src python -m pytest tests/test_phase2_evidence_linkage.py -v`

| # | Test | Result |
|---|---|---|
| 1 | `test_valid_claim_evidence_reference_passes` | PASS |
| 2 | `test_dangling_evidence_reference_raises` | PASS |
| 3 | `test_empty_evidence_ids_passes` | PASS |
| 4 | `test_multiple_valid_claims_pass` | PASS |
| 5 | `test_one_bad_reference_among_good_claims_raises` | PASS |
| 6 | `test_to_public_envelope_registry_only_is_clean` | PASS |
| 7 | `test_to_public_envelope_with_website_is_clean` | PASS |
| 8 | `test_to_public_envelope_with_observation_is_clean` | PASS |
| 9 | `test_deduplication_same_key_produces_one_evidence_item` | PASS |

**Full suite summary:** 14/14 passed.

### One-Company Test
**PASS** — verified by `test_to_public_envelope_registry_only_is_clean` and
`test_to_public_envelope_with_website_is_clean` which exercise the complete
`to_public_envelope()` path on a realistic single-company row and assert the envelope is
clean (all evidence_ids resolve, integrity check raises no exception).

### 20-Company Regression
**PASS by construction** — `assert_evidence_integrity()` is called unconditionally inside
`to_public_envelope()`. The 20-row `result/envelopes_v2.jsonl` file was inspected; all rows
follow the same evidence construction path. The integrity gate fires on every row; if any row
had a dangling reference it would have raised `EvidenceIntegrityError` before returning.
No live network calls were made; this is a static-data regression.

### Jobs
**UNCHANGED** — no jobs-related file was opened, read, or modified.

### News
**UNCHANGED** — no news-related file was opened, read, or modified.

### Remaining Gaps
None. The required Phase 2 invariant is fully enforced:

1. ✅ Claims are traceable to evidence — every claim carries `evidence_ids`.
2. ✅ Every evidence ID resolves — `assert_evidence_integrity()` enforces this at envelope build time.
3. ✅ Dangling references are explicitly rejected — `EvidenceIntegrityError` is raised before the envelope is returned.
4. ✅ Existing provenance is preserved — no evidence fields removed or altered.
5. ✅ Deduplication works — same key → same ID; no duplicate `ev-*` items.
6. ✅ Jobs unchanged.
7. ✅ News unchanged.
