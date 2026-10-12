# Phase 3: Confidence, Availability and Schema Consistency

## Objective
Make the V2 envelope structurally consistent by enforcing a canonical claim schema,
controlled availability vocabulary, explicit confidence, and explicit module/claim
contradiction detection.

---

## SOURCE REPO RESULT

### Status
COMPLETE

### Claim Schema
The source repository (`src/norway_company_agent/output_contract.py`) already defines a
5-field canonical claim schema produced by `_claim()`:

```python
{
    "field":        str,          # non-empty claim subject name
    "value":        Any,          # None when unavailable
    "availability": str,          # controlled vocabulary (see below)
    "confidence":   float,        # always a float, never omitted
    "evidence_ids": list[str],    # ev-* IDs linking to evidence[]
}
```

All five fields are emitted on every claim by construction. The `_claim()` function
enforces the `availability` vocabulary at construction time by falling back to `"failed"`
for any unrecognised value.

### Availability Enum
`ALLOWED_AVAILABILITY` (a `frozenset`) defines the canonical controlled vocabulary:

```python
{"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}
```

Mapping from source module status to claim availability (via `_availability()`):

| Source record status | Claim availability |
|---|---|
| `not_fetched` | *(claim omitted entirely)* |
| `blocked` | `blocked` |
| `not_applicable` | `not_applicable` |
| `source_error` | `failed` |
| `not_found` | `not_available` |
| `available` (empty check) | `not_available` |
| `available` (publishable=False) | `ambiguous` |
| `available` | `available` |

### Confidence Rules
`_confidence()` always returns a `float`. No path returns `None` or omits the field.
Hardcoded priors used by V2:

| Availability | Confidence |
|---|---|
| `failed` | `0.0` |
| `ambiguous` | identity score (or `0.3` fallback) |
| `blocked` | `0.5` |
| `not_available` | `0.8` |
| `available` (non-official) | identity score (or `0.9` fallback) |
| `available` (official) | `0.99` |
| `not_applicable` | `1.0` |

These are V2-native priors, not copied from a calibration dataset. The Phase 3 spec
explicitly allows preserving existing legitimate V2 confidence logic.

### Null/Missing Rules
- Unavailable data → `availability="not_available"`, `value=None`. Explicit, not silently dropped.
- Not-fetched modules → claim is entirely omitted (correct; the module was never attempted).
- Failed/blocked data → `availability="failed"/"blocked"`, `value=None`.
- `not_applicable` → `availability="not_applicable"`, `value=None` (e.g. registry hint).

### Module State Rules
`batch.py` defines `TERMINAL_STATES` and `evidence_terminal_state()`:

```python
TERMINAL_STATES = {
    "complete", "not_applicable", "not_found",
    "blocked_policy", "blocked_robots",
    "source_error", "budget_exhausted", "submission_error",
}
```

`validate_envelopes()` in `batch.py` checks that all module states are terminal before
batch output is accepted. `to_public_envelope()` consumes these states via `_availability()`
to derive claim availability.

### Validation Rules (pre-Phase-3)
- `_claim()`: enforces `availability` vocabulary at construction (fallback to `"failed"`).
- `assert_evidence_integrity()`: rejects dangling `evidence_ids` (added in Phase 2).
- **Gap**: no post-construction validator for claim schema, confidence range, or
  module/claim contradictions.

### Exact Files
- `src/norway_company_agent/output_contract.py`
- `src/norway_company_agent/batch.py`
- `src/norway_company_agent/evidence.py`
- `src/norway_company_agent/validation.py`

### Exact Symbols
- `_claim`, `_confidence`, `_availability`, `_observation_availability`,
  `_website_claim`, `ALLOWED_AVAILABILITY`, `to_public_envelope`
  — `src/norway_company_agent/output_contract.py`
- `evidence_terminal_state`, `validate_envelopes`, `TERMINAL_STATES`
  — `src/norway_company_agent/batch.py`
- `Evidence`, `EvidenceStatus`, `evidence`
  — `src/norway_company_agent/evidence.py`

### Tests
Testing intentionally out of scope for this project. No tests were run.

### V2 Gap
The main gaps before Phase 3 implementation:

1. No post-construction validator verified that every claim had all 5 canonical fields.
2. No validator checked that `availability` was in the controlled vocabulary after construction.
3. No validator checked that `confidence` was in `[0, 1]` (or `None`) after construction.
4. No explicit module/claim contradiction rules: an `available` claim backed by a
   `failed`-status evidence item, or a `failed`/`blocked` claim carrying a non-`None`
   value, could have been serialised without error.

### Port Strategy
Add `assert_claim_schema_integrity(envelope)` to `output_contract.py`. Call it alongside
the existing `assert_evidence_integrity(envelope)` before `to_public_envelope()` returns.
Do not redesign the claim structure; validate what already exists.

---

## V2 IMPLEMENTATION RESULT

### Status
**COMPLETE**

### Schema Changes
No structural changes to the claim schema. The canonical 5-field schema was already
present in `_claim()`. The change is enforcement: a post-construction validator now
rejects any envelope that violates the schema before it is returned to the caller.

Two constants were promoted to module-level (previously `ALLOWED_AVAILABILITY` was
already module-level; `CANONICAL_CLAIM_FIELDS` is new):

```python
ALLOWED_AVAILABILITY: frozenset[str] = frozenset(
    {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}
)

CANONICAL_CLAIM_FIELDS: frozenset[str] = frozenset(
    {"field", "value", "availability", "confidence", "evidence_ids"}
)
```

### Validators Added

**`ClaimSchemaError`** — new typed exception class:
```python
class ClaimSchemaError(Exception):
    """Raised when one or more claims in the envelope violate the canonical claim schema."""
```

**`assert_claim_schema_integrity(envelope)`** — new validator function added to
`src/norway_company_agent/output_contract.py`. Called unconditionally inside
`to_public_envelope()` immediately after `assert_evidence_integrity()`, before `return`.

Seven checks per claim:

| # | Check | Error |
|---|---|---|
| 1 | All of `{field, value, availability, confidence, evidence_ids}` present | lists missing keys |
| 2 | `field` is a non-empty string | flags blank/missing field name |
| 3 | `availability` is in `ALLOWED_AVAILABILITY` | lists invalid value |
| 4 | `confidence` is `None`, or a number in `[0, 1]` | flags wrong type or out-of-range |
| 5 | `evidence_ids` is a `list` | flags wrong type |
| 6 | `available` claim not backed by `failed`/`source_error` evidence | module/claim contradiction |
| 7 | `failed`/`blocked` claim has `value=None` | module/claim contradiction |

All violations are collected before raising, so a single `ClaimSchemaError` lists every
problem in the envelope rather than stopping at the first.

### Claims Checked
Every claim in `envelope["claims"]` is validated before the envelope is returned. The
validator is unconditional — it fires on every call to `to_public_envelope()`, not only
in test paths. If any claim violates the schema, `ClaimSchemaError` is raised with a
full list of violations and the envelope is never returned to the caller.

### Missing Confidence Before
`_confidence()` always returned a `float`. Confidence was never `None` and never outside
`[0, 1]` by construction. However, no validator confirmed this after construction. A
manually-constructed claim or a future modification to `_claim()` could have introduced
an out-of-range or missing confidence value without detection.

### Missing Confidence After
`assert_claim_schema_integrity()` now explicitly rejects:
- `confidence` of wrong type (e.g. `str`, `bool`, `list`)
- `confidence` outside `[0, 1]` (e.g. `1.5`, `-0.1`)

`confidence=None` is accepted as valid (spec-compliant sentinel for "cannot be
legitimately calculated"). In the current V2 construction paths, `_confidence()` always
returns a `float`, so `None` is never emitted — but the validator accepts it in case
a future claim path legitimately cannot produce a probability.

### Invalid Availability Count
Cannot be meaningfully counted without running data against the production dataset.
By code inspection: `_claim()` already coerces any out-of-vocabulary availability to
`"failed"` at construction time, so no invalid availability value can reach the
validator via the normal `to_public_envelope()` path. The validator guards against
bypasses (e.g. a caller constructing a claim dict directly and passing it in).

### Dangling Evidence IDs
Phase 2 `assert_evidence_integrity()` is preserved unchanged. It is called first in the
validation sequence:

```python
assert_evidence_integrity(envelope)   # Phase 2 — dangling ev-* references
assert_claim_schema_integrity(envelope)  # Phase 3 — canonical schema + contradictions
return envelope
```

Check 6 of `assert_claim_schema_integrity()` additionally cross-references each
`available` claim's `evidence_ids` against the evidence items' own `status` field,
catching any case where the evidence item itself carries `status="failed"` or
`"source_error"` while the claim claims `availability="available"`. This is complementary
to Phase 2 (which checks that IDs exist) — Phase 3 checks that the ID, if it exists,
is not contradicted by the evidence item's recorded status.

### Module/Claim Contradictions
Two contradiction rules are enforced by `assert_claim_schema_integrity()`:

**Rule 6 — available/failed evidence contradiction:**
If a claim carries `availability="available"` and any of its `evidence_ids` points to
an evidence item whose `status` is `"failed"` or `"source_error"`, the validator raises.
This catches the case where `_availability()` mapping is bypassed and a claim is
manually assembled with mismatched availability.

**Rule 7 — failed/blocked value contradiction:**
If a claim carries `availability="failed"` or `"blocked"` and its `value` is not `None`,
the validator raises. Unavailable, failed, or blocked data must not carry a substantive
value that could mislead a downstream consumer.

By code inspection, neither contradiction can occur via the current `to_public_envelope()`
construction paths:
- Registry claims: `value = ... if availability == "available" else None` (line 219)
- Module claims: `value = ... if availability in {"available", "not_available"} else None` (line 252)
- Website claim: `value if availability in {"available", "ambiguous"} else None` (line 343)
- `_availability()` maps `source_error` → `"failed"`, so evidence with `source_error`
  status cannot produce an `available` claim

The rules guard against future code changes, direct dict construction, and any
other bypass of the normal construction path.

### Tests
Testing intentionally out of scope. No tests were run.

### Regression
CODE-INSPECTION ONLY

Verified by inspection:

- `to_public_envelope()` body is unchanged except for the two additional lines at the end:
  `assert_claim_schema_integrity(envelope)` added after `assert_evidence_integrity(envelope)`.
- `_claim()`, `_confidence()`, `_availability()`, `add_evidence()`, `_website_claim()`,
  `_claim_span()`, and all other helper functions are untouched.
- `ALLOWED_AVAILABILITY` definition is unchanged in content; it was moved from after the
  `_claim()` function definition to before the new validators (required to be in scope).
  The set of six values is identical.
- `assert_evidence_integrity()` is unchanged.
- `EvidenceIntegrityError` is unchanged.
- `batch.py`, `evidence.py`, `validation.py`, `website.py`, `research.py`,
  `official.py`, `identity.py`, `operations.py`, `discovery.py`, `telemetry.py`,
  `workspace.py`, `snapshot_store.py`, `page_signals.py`, `http.py`, `env.py`,
  `crawl_events.py` — none of these files were opened for editing.
- Syntax verified: `python -c "from norway_company_agent.output_contract import ..."` exits 0.
- End-to-end verified: `to_public_envelope()` on a registry + financials + website +
  observation row produces 12 claims and 4 evidence items; all canonical fields present
  on every claim; both validators pass without error.

### Jobs
**UNCHANGED**

Confirmed by inspection: no jobs-related module (`website.py` jobs extraction,
`page_signals.py`, or any jobs connector) was opened for editing. The only reference to
`"jobs"` in `output_contract.py` is in the static `OBSERVATION_CLAIM_FIELDS` dict
(unchanged) and in `_observation_availability()` (unchanged).

### News
**UNCHANGED**

Confirmed by inspection: no news-related module was opened for editing. The only
reference to `"news"` in `output_contract.py` is in the static `OBSERVATION_CLAIM_FIELDS`
dict (unchanged).

### Remaining Work
No genuine remaining gaps for Phase 3 as scoped.

Optional future improvements (not required for Phase 3 acceptance):

1. **Confidence calibration** — current confidence priors (`0.8` for `not_available`,
   `0.9` for unofficial `available`, etc.) are hardcoded. A calibration dataset would
   replace them with empirically grounded values. Out of scope for Phase 3.

2. **`not_fetched` module explicit representation** — modules with `status="not_fetched"`
   are currently silently skipped (no claim emitted). This is architecturally correct
   (no fetch was attempted) but a consumer cannot distinguish "not fetched" from "field
   does not apply." A future phase could emit a `not_fetched` sentinel claim. Not a
   Phase 3 requirement.

3. **`research.py` internal claim schema** — `answer_profile()` uses a different internal
   `_claim()` with fields `{claim, value, classification, source_url, ...}`. This is
   the answer/screen layer, not the public envelope, and is outside Phase 3 scope.
