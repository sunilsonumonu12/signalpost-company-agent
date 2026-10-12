# Phase 6: Changes / Change Detection

## Objective
Implement V2 change detection using normalized claims.

## Do Not Touch
Jobs and News are frozen.

## Source Repo Investigation Prompt
```text
Inspect how the source repository implements changes[].

Do not modify code.

Determine:
1. previous envelope input
2. current envelope input
3. comparison key
4. scalar comparison
5. array comparison
6. added/removed/updated detection
7. stable identifiers
8. serialization
9. tests

Confirm whether comparison uses normalized claims rather than raw JSON.
Do not inspect Jobs or News.
```

## Porting Prompt
```text
Implement changes[] in V2 using normalized claims.

Comparison key:
organisation_number + field

Support:
- added
- updated
- removed
- unchanged

Do not compare raw JSON.
For arrays, use stable identifiers such as URL rather than array position.
Do not modify extraction modules.
Do not touch Jobs or News.

Add tests for scalar update, addition, removal, array addition/removal, and unchanged values.
```

## Acceptance
- Changes are generated from normalized claims.
- Array ordering does not create false changes.
- Existing envelope generation remains unchanged.

## Mandatory Result Artifact

### Source Investigation Result

```text
## SOURCE REPO RESULT

### Status
COMPLETE

### Previous Envelope Input
Previous raw profile, including its evidence block.

### Current Envelope Input
Current raw profile, including its evidence block.

### Comparison Key
The source requires matching `organisation_number` values, then compares each configured field path.

### Scalar Comparison
Direct equality of values read from `TRACKED_FIELDS` paths.

### Array Comparison
Direct equality; the source implementation does not have order-independent item matching.

### Stable Identifiers
Organisation number is the dataset/profile identity. No per-array item identifiers are used.

### Change Types
One change record per changed configured path; unchanged values emit no record.

### Exact Files
`archive/v2-crawler/refresh.py`

### Exact Symbols
`TRACKED_FIELDS`, `diff_profile`, `diff_datasets`.

### Tests
No source tests for `refresh.py` were found during inspection.

### V2 Gap
V2 had no change detector and requires comparison of public normalized claims rather than raw profile/evidence paths, including order-independent arrays.

### Port Strategy
Compare normalized `claims[]` by organisation number and `claim.field`; use field-specific stable item keys for array claims.
```

### V2 Implementation Result

```text
## V2 IMPLEMENTATION RESULT

### Status
COMPLETE (runtime regression intentionally deferred)

### Files Added
`src/norway_company_agent/changes.py`; `tests/test_phase6_changes.py`.

### Files Modified
`src/norway_company_agent/output_contract.py`.

### Comparison Logic
`diff_claims()` compares only normalized claim `field`, `value`, and `availability`; evidence IDs and raw evidence JSON are not compared. `to_public_envelope()` accepts an optional `previous_envelope`, rejects a different non-empty organisation number, and inserts computed records into `changes`.

### Added Changes
Fields or stable array items present only in the current claims emit `change_type: added`.

### Updated Changes
Changed scalar values, availability changes, and changed array items with the same stable key emit `change_type: updated`.

### Removed Changes
Fields or stable array items absent from current claims emit `change_type: removed`.

### Array Diff Logic
Top-level arrays use stable keys: social URL, role/location organisation number or name, financial record ID/period, financial-history year, primitive value, or canonical JSON fallback. Nested normalized module arrays are compared order-independently.

### Tests
`tests/test_phase6_changes.py` covers scalar update, addition, removal, unchanged values, social-profile addition/removal/reordering, nested module array reordering, frozen Jobs/News, and envelope integration. Per user direction, no further test execution is performed.

### Regression
NOT RUN — explicitly deferred to avoid test execution.

### Jobs
UNCHANGED — excluded by `_FROZEN_FIELDS`.

### News
UNCHANGED — excluded by `_FROZEN_FIELDS`.

### Remaining Work
Run the focused and full regression suites only if validation is later requested.
```
